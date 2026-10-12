#!/usr/bin/env python3
"""F-60 - PARTIAL-BODY FRAMING: hips first, then shoulders, then the head, chosen frame by frame.

WHY IT EXISTS. video_udp_sender.py and host_multiperson.py dropped every frame in which both hip keypoints
were below --conf. That is right for an OAK-D (a full body, hips are the stereo anchor) and wrong for a
laptop webcam, where a person sits at a desk and the hips are simply not in the picture. Depth had the
same assumption: it came from nose-to-ankle span, so a frame without ankles fell back to a fixed 2.0 m.

WHAT IT DOES. One FramingTracker per person decides, from the keypoints of THIS frame, which body part
anchors the skeleton, and supplies the three things the hip used to supply:

  * the ROOT pixel (where the mid-hip is, or is estimated to be),
  * the ROOT depth (metres from the camera),
  * a TIER (0 hips, 1 shoulders, 2 head) so the consumer knows how far to trust that root.

    tier 0  both hips seen            root MEASURED. Identical to the pre-F-60 behaviour.
    tier 1  both shoulders seen       root ESTIMATED from the shoulder line and a torso offset the tracker
                                      learned from this same person while their hips were visible.
    tier 2  only the nose seen        root ESTIMATED from the nose and a head-to-hip ratio, likewise learned.

Tiers are decided every frame, so the fall-back works in the middle of a run: a person who leans over a
desk, or is hidden by something, drops from tier 0 to tier 1 and back with no restart.

HONESTY RULES (the sidecar's "never fabricate", AGENTS.md section 2). The tracker only ever moves the ROOT. A
joint below --conf is still emitted as absent ([0,0,0,0]); the lower body of an upper-body frame is never
invented. The tier travels on the wire (`fr`) so a consumer can show that the root is an estimate. A joint
that lies outside the image is not "seen", whatever the model's confidence says: RTMW3D keeps regressing
keypoints for body parts that are not in the crop.

DEPTH. height -> shoulders -> face -> 2.0 m, whichever the frame supports first. The shoulder and face
widths are in the SAME units as the existing nose-to-ankle rule (they are calibrated to it, see the
constants), so the depth does not step when a person moves between tiers. While the height rule is
available the tracker also LEARNS this person's shoulder and face widths from it (rising fast, falling
slowly, because turning sideways can only make a width look smaller).

NOT HERE: the decision to run the pose model, the crop, the wire encoding. Those stay in the senders. This
module has no model, no camera and no socket, so every rule in it is tested without any of them.
"""

import math

# COCO-17 keypoint indices (the first 17 of RTMW3D's 133).
NOSE = 0
L_EYE = 1
R_EYE = 2
L_EAR = 3
R_EAR = 4
L_SHOULDER = 5
R_SHOULDER = 6
L_HIP = 11
R_HIP = 12
L_ANKLE = 15
R_ANKLE = 16

#: RTMW3D WholeBody indices of everything below the waist: hips 11, 12; knees 13, 14; ankles 15, 16; and the six foot
#: keypoints 17-22 (big toe, small toe, heel, both sides).
LOWER_BODY = tuple(range(11, 23))

TIER_HIPS = 0
TIER_SHOULDERS = 1
TIER_HEAD = 2
TIER_NAMES = ("hips", "shoulders", "head")

DEPTH_HEIGHT = 0
DEPTH_SHOULDERS = 1
DEPTH_FACE = 2
DEPTH_DEFAULT = 3
DEPTH_NAMES = ("height", "shoulders", "face", "default")

#: Depth when nothing in the frame can measure it. The pre-F-60 value, kept so a frame that has no usable
#: scale behaves exactly as it always did.
DEFAULT_DEPTH_M = 2.0

#: The pre-F-60 depth band for the height rule (estimate_hip_depth's default). Kept for that rule only.
HEIGHT_DEPTH_BAND_M = (0.6, 6.0)

#: The band for the shoulder and face rules. A laptop webcam sits 0.4-0.8 m from a face, which the height
#: band would clamp to 0.6 m and lie about.
CLOSE_DEPTH_BAND_M = (0.25, 6.0)

#: Below this a nose-to-ankle span is noise (the pre-F-60 value, px).
MIN_HEIGHT_SPAN_PX = 40.0

#: An ankle only counts for the height rule if it is at least this many TORSO lengths (shoulder line to hip line)
#: below the hips. Measured hip-to-ankle over torso on 471 full-body frames: min 1.09, p1 1.12, median 1.41. On a
#: cut-off person RTMW3D squeezes its legs into the picture and puts an "ankle" at confidence 0.3-0.4 ABOVE the hips
#: (seen live in the multi-person path: a 6.0 m depth for a person at 2 m), so 0.8 rejects those and none of the
#: real ones.
MIN_LEG_OVER_TORSO = 0.8

#: Below this a shoulder or ear span is noise, px. At 1080p and a 70 deg lens a 0.4 m shoulder line is 20 px
#: at 28 m, far beyond anything the sidecar is asked to track.
MIN_WIDTH_PX = 20.0
MIN_FACE_PX = 8.0

#: Hysteresis on a body part's "seen" state: once lost it must clear the gate by this much to count as
#: seen again, so a keypoint hovering at --conf does not flip the tier every frame. Falling is immediate.
REGAIN_MARGIN = 0.10

#: A keypoint this far (fraction of the frame size) outside the image is not seen.
OUTSIDE_TOLERANCE = 0.02

#: Hips that are NOT yet counted must be this far (fraction of the frame size) INSIDE the bottom and side edges to
#: start counting. A hip on the border is the model's guess, pulled toward the picture (measured on waist-up crops:
#: median 0.07 of a torso off, hips at 0.96-1.05 of the frame height), and a person whose hips hover on the edge
#: otherwise flips the anchor 4 times a second (86 changes in 22 s, hip-relative shoulder x-jitter 0.033 m against
#: 0.015 m with the feature off - ADR-096). Once counted they stay counted until they leave the picture, so the
#: two edges of the band are 5% inside and 2% outside.
ENTER_MARGIN = 0.05

#: Population priors, see the measurements in docs/decisions.md ADR-096. The shoulder line (px) over the
#: nose-to-ankle span (px) is a ratio of two pixel measures, so it needs no focal length and it is what makes
#: the shoulder depth agree with the height depth. The face width (ear to ear) over the shoulder line, the
#: eye line over the ear line, and the hip offset in the shoulder frame (a along the shoulders, b down the
#: spine, both in shoulder widths) are the other priors. They are only used until this person has taught the
#: tracker their own.
PRIOR_SHOULDER_OVER_SPAN = 0.24
PRIOR_FACE_OVER_SHOULDER = 0.48
PRIOR_EYE_OVER_EAR = 0.41
PRIOR_HIP_A = 0.0
PRIOR_HIP_B = 1.45
PRIOR_HIP_FROM_NOSE_V = 4.1
PRIOR_HIP_FROM_NOSE_U = 0.0
#: Model root-relative depth of the shoulder line and of the nose, minus the hips' (zrel, metres). Learned
#: while the hips are in view; the prior is the measured mean. The MODEL'S OWN hip depth is not used once
#: the hips are out of frame: on crops it moved by +0.07 to +0.15 m (ADR-096).
PRIOR_DZ_SHOULDER = -0.05
PRIOR_DZ_NOSE = 0.0
DZ_RANGE = (-0.4, 0.4)

#: Plausible range of each learned quantity. A learned value outside it is a bad frame, not a person. The two
#: widths are fractions of --subject-height (the same unit as the span they are calibrated against).
SHOULDER_WIDTH_RANGE = (0.12, 0.32)
FACE_WIDTH_RANGE = (0.05, 0.13)
HIP_A_RANGE = (-0.5, 0.5)
HIP_B_RANGE = (0.9, 2.4)
HIP_FROM_NOSE_V_RANGE = (2.5, 8.0)
HIP_FROM_NOSE_U_RANGE = (-2.0, 2.0)

#: Learning rates per frame. The hip offset moves with posture, so it follows at 0.05 (~0.7 s at 30 fps). A
#: width is learned in metres from the MEASURED hip-plane depth (width_px * depth / fx), so the depth it gives
#: back when the hips are lost continues the last measured depth instead of stepping. A width can only look
#: smaller when someone turns, so it rises faster (0.15) than it falls (0.05).
OFFSET_RATE = 0.05
WIDTH_RISE_RATE = 0.15
WIDTH_FALL_RATE = 0.05

#: How fast a depth taken from a WIDTH (shoulders, face) may grow, as a fraction of itself per second.
#: Turning sideways shortens every width and so can only make a person look farther away: on the 147 dancers
#: the shoulder line fell to 0.04-0.14 of the body span against a normal 0.21-0.24 and the depth read up to
#: 6 m for a body at 3.7 m (ADR-096). Falling is not limited. 0.4 per second lets a person back away at
#: 0.24 m/s from 0.6 m (a laptop) and 1.5 m/s from 3.7 m, and a 1 s turn adds at most 40 %.
MAX_DEPTH_RISE_PER_S = 0.4
DEFAULT_DT_S = 1.0 / 30.0
MAX_DT_S = 1.0

#: A learned hip offset needs confident hips; below this the frame teaches nothing (raw SimCC scale).
LEARN_MIN_CONF = 0.5

#: Seconds with no answer after which the person is forgotten. A learned body belongs to whoever taught it;
#: someone who walks in after this long must be learned afresh, not measured against the last person. Same
#: order as the sender's own idle release (STATE_IDLE_SECONDS, 3 s) with a margin for a short walk out of shot.
FORGET_SECONDS = 5.0

#: Tier changes are reported at most this often, so a person standing on the edge of a tier does not fill the log.
EVENT_MIN_GAP_S = 1.0


def _clamp(value, lo, hi):
    return lo if value < lo else (hi if value > hi else value)


def _dist(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


class FramingResult:
    """What one frame decided. Reused by its tracker: read it before the next update().

    tier          TIER_HIPS / TIER_SHOULDERS / TIER_HEAD
    hip_u, hip_v  the root pixel (measured mid-hip, or estimated), frame pixels
    hip_z         the root's distance from the camera, metres
    depth_source  DEPTH_* rule that produced hip_z
    """

    __slots__ = ("tier", "hip_u", "hip_v", "hip_z", "depth_source")

    def __init__(self):
        self.tier = TIER_HIPS
        self.hip_u = 0.0
        self.hip_v = 0.0
        self.hip_z = DEFAULT_DEPTH_M
        self.depth_source = DEPTH_DEFAULT

    def describe(self):
        return "%s root, %s depth %.2f m" % (TIER_NAMES[self.tier], DEPTH_NAMES[self.depth_source], self.hip_z)


class FramingTracker:
    """The temporal memory of ONE person's framing. Not thread-safe; one per tracked person.

    conf_gate       --conf: a keypoint at or above it is a measurement.
    subject_height  --subject-height, metres: the scale of the height rule, and through the calibrated
                    ratios of the shoulder and face rules.
    zrel_sign       wholebody_udp_sender.ZREL_SIGN, so the depth plane of a shoulder converts to the hip's.
    """

    def __init__(self, conf_gate=0.3, subject_height=1.70, zrel_sign=1.0):
        self.gate = conf_gate
        self.height = subject_height
        self.zrel_sign = zrel_sign
        self.result = FramingResult()
        self.reset()

    def reset(self):
        """Forget the person. A learned width belongs to a body; a new body must not inherit it."""
        self.hips_seen = False
        self.shoulders_seen = False
        self.head_seen = False
        self.shoulder_m = PRIOR_SHOULDER_OVER_SPAN * self.height
        self.face_m = PRIOR_FACE_OVER_SHOULDER * self.shoulder_m
        self.learned_face = False
        self.hip_a = PRIOR_HIP_A
        self.hip_b = PRIOR_HIP_B
        self.nose_hip_u = PRIOR_HIP_FROM_NOSE_U
        self.nose_hip_v = PRIOR_HIP_FROM_NOSE_V
        self.dz_shoulder = PRIOR_DZ_SHOULDER
        self.dz_nose = PRIOR_DZ_NOSE
        self.learned_shoulder = False
        self.learned_hip = False
        self.last_tier = None
        self.tier_changes = 0
        self.tier_frames = [0, 0, 0]
        self._event = None
        self._event_at = None
        self._z_prev = None
        self._now_prev = None
        self._answered_at = None

    # ---------------------------------------------------------------- seen-ness

    def _seen(self, was_seen, c_a, c_b):
        """Both confidences clear the gate, with the regain margin when the part was lost."""
        bar = self.gate if was_seen else self.gate + REGAIN_MARGIN
        return c_a >= bar and c_b >= bar

    @staticmethod
    def _inside(point, width, height):
        tx = OUTSIDE_TOLERANCE * width
        ty = OUTSIDE_TOLERANCE * height
        return -tx <= point[0] <= width + tx and -ty <= point[1] <= height + ty

    @staticmethod
    def _clear_of_edge(point, width, height):
        """Inside the picture by ENTER_MARGIN of its size on the bottom and the sides (the top does not matter for a hip)."""
        return (ENTER_MARGIN * width <= point[0] <= (1.0 - ENTER_MARGIN) * width
                and point[1] <= (1.0 - ENTER_MARGIN) * height)

    # ---------------------------------------------------------------- measurements

    def _face_px(self, uv, conf, width, height):
        """Ear-to-ear pixels, or the eye line scaled to it, or 0. Yaw shortens both."""
        if (conf[L_EAR] >= self.gate and conf[R_EAR] >= self.gate
                and self._inside(uv[L_EAR], width, height) and self._inside(uv[R_EAR], width, height)):
            span = _dist(uv[L_EAR], uv[R_EAR])
            if span >= MIN_FACE_PX:
                return span
        if (conf[L_EYE] >= self.gate and conf[R_EYE] >= self.gate
                and self._inside(uv[L_EYE], width, height) and self._inside(uv[R_EYE], width, height)):
            span = _dist(uv[L_EYE], uv[R_EYE]) / PRIOR_EYE_OVER_EAR
            if span >= MIN_FACE_PX:
                return span
        return 0.0

    def _height_span_px(self, uv, conf, width, height, hips_ok, shoulders_ok):
        """The pre-F-60 rule's span: nose to ankle, needing two of the three seen, and now also in frame, with
        every ankle below the hips by MIN_LEG_OVER_TORSO torsos (or merely below them when there is no torso)."""
        hip_y = 0.5 * (float(uv[L_HIP][1]) + float(uv[R_HIP][1]))
        torso = 0.0
        if hips_ok and shoulders_ok:
            torso = hip_y - 0.5 * (float(uv[L_SHOULDER][1]) + float(uv[R_SHOULDER][1]))
        ys = []
        if conf[NOSE] >= self.gate and self._inside(uv[NOSE], width, height):
            ys.append(float(uv[NOSE][1]))
        for i in (L_ANKLE, R_ANKLE):
            if conf[i] >= self.gate and self._inside(uv[i], width, height):
                if float(uv[i][1]) - hip_y > MIN_LEG_OVER_TORSO * max(torso, 0.0):
                    ys.append(float(uv[i][1]))
        return (max(ys) - min(ys)) if len(ys) >= 2 else 0.0

    # ---------------------------------------------------------------- depth

    def _band(self, source, depth_range):
        """The clamp for one depth rule. --depth-range bounds the HEIGHT rule (and the default), which is what it
        was added for: a clip whose assumed subject height makes it read as too near or too far. It does NOT bound
        the shoulder and face rules: those are measured from the picture, and a laptop webcam at 0.6 m must not be
        moved to the 1.6 m the same switch (on by default in Unity) pins a full-body clip to."""
        if source in (DEPTH_SHOULDERS, DEPTH_FACE):
            return CLOSE_DEPTH_BAND_M
        return HEIGHT_DEPTH_BAND_M if depth_range is None else depth_range

    def _depth(self, uv, conf, zrel, fx, fy, width, height, shoulders_ok, shoulder_px, face_px, depth_range,
               hips_ok):
        """(hip_z, source). The first rule the frame supports; each is converted to the HIP's plane.

        The conversion uses the model's own shoulder-minus-hip depth only while the hips are in view; once
        they are not, the learned offset (dz_shoulder, dz_nose) stands in, because the model's hip depth is
        not a measurement there."""
        # The height rule needs the hips in view: ankles are below them, so with the hips out of frame an
        # "ankle" inside the image is the model's guess (measured on crops: it happens), not a measurement.
        span = self._height_span_px(uv, conf, width, height, hips_ok, shoulders_ok) if hips_ok else 0.0
        if span > MIN_HEIGHT_SPAN_PX:
            lo, hi = self._band(DEPTH_HEIGHT, depth_range)
            return _clamp(self.height * fy / span, lo, hi), DEPTH_HEIGHT
        if shoulders_ok and shoulder_px >= MIN_WIDTH_PX:
            z = self.shoulder_m * fx / shoulder_px
            if not self.learned_shoulder:
                # A prior width gives the depth of the shoulder PLANE; move it to the hip's.
                if hips_ok:
                    hip_ref = 0.5 * (float(zrel[L_HIP]) + float(zrel[R_HIP]))
                    offset = 0.5 * (float(zrel[L_SHOULDER]) + float(zrel[R_SHOULDER])) - hip_ref
                else:
                    offset = self.dz_shoulder
                z = z - self.zrel_sign * offset
            lo, hi = self._band(DEPTH_SHOULDERS, depth_range)
            return _clamp(z, lo, hi), DEPTH_SHOULDERS
        if face_px >= MIN_FACE_PX:
            z = self.face_m * fx / face_px
            if not self.learned_face:
                if hips_ok:
                    offset = float(zrel[NOSE]) - 0.5 * (float(zrel[L_HIP]) + float(zrel[R_HIP]))
                else:
                    offset = self.dz_nose
                z = z - self.zrel_sign * offset
            lo, hi = self._band(DEPTH_FACE, depth_range)
            return _clamp(z, lo, hi), DEPTH_FACE
        lo, hi = self._band(DEPTH_DEFAULT, depth_range)
        return _clamp(DEFAULT_DEPTH_M, lo, hi), DEPTH_DEFAULT

    # ---------------------------------------------------------------- learning

    @staticmethod
    def _toward(current, observed, rate_up, rate_down, lo, hi):
        rate = rate_up if observed > current else rate_down
        return _clamp(current + rate * (observed - current), lo, hi)

    def _learn_widths(self, hip_z, fx, shoulder_px, face_px):
        """From a frame whose depth is MEASURED (the height rule): this person's shoulder and face widths, m."""
        lo, hi = SHOULDER_WIDTH_RANGE
        if shoulder_px >= MIN_WIDTH_PX:
            self.shoulder_m = self._toward(self.shoulder_m, hip_z * shoulder_px / fx, WIDTH_RISE_RATE,
                                           WIDTH_FALL_RATE, lo * self.height, hi * self.height)
            self.learned_shoulder = True
        lo, hi = FACE_WIDTH_RANGE
        if face_px >= MIN_FACE_PX:
            self.face_m = self._toward(self.face_m, hip_z * face_px / fx, WIDTH_RISE_RATE, WIDTH_FALL_RATE,
                                       lo * self.height, hi * self.height)
            self.learned_face = True
        elif not self.learned_face:
            # No face seen yet: keep the face prior in proportion to the shoulder this person has shown.
            self.face_m = PRIOR_FACE_OVER_SHOULDER * self.shoulder_m

    def _learn_dz(self, conf, zrel, shoulders_ok):
        hip_z = 0.5 * (float(zrel[L_HIP]) + float(zrel[R_HIP]))
        if shoulders_ok:
            observed = 0.5 * (float(zrel[L_SHOULDER]) + float(zrel[R_SHOULDER])) - hip_z
            self.dz_shoulder = _clamp(self.dz_shoulder + OFFSET_RATE * (observed - self.dz_shoulder), *DZ_RANGE)
        if conf[NOSE] >= self.gate:
            observed = float(zrel[NOSE]) - hip_z
            self.dz_nose = _clamp(self.dz_nose + OFFSET_RATE * (observed - self.dz_nose), *DZ_RANGE)

    def _shoulder_frame(self, uv):
        """Unit vectors (u along the shoulders, v down the torso) and the width, or None."""
        du = float(uv[R_SHOULDER][0]) - float(uv[L_SHOULDER][0])
        dv = float(uv[R_SHOULDER][1]) - float(uv[L_SHOULDER][1])
        width = math.hypot(du, dv)
        if width < MIN_WIDTH_PX:
            return None
        ux, uy = du / width, dv / width
        vx, vy = -uy, ux
        if vy < 0.0:
            vx, vy = -vx, -vy
        return ux, uy, vx, vy, width

    def _learn_hip(self, uv, hip_u, hip_v, face_px):
        frame = self._shoulder_frame(uv)
        if frame is not None:
            ux, uy, vx, vy, width = frame
            mx = 0.5 * (float(uv[L_SHOULDER][0]) + float(uv[R_SHOULDER][0]))
            my = 0.5 * (float(uv[L_SHOULDER][1]) + float(uv[R_SHOULDER][1]))
            ox, oy = hip_u - mx, hip_v - my
            a = (ox * ux + oy * uy) / width
            b = (ox * vx + oy * vy) / width
            self.hip_a = _clamp(self.hip_a + OFFSET_RATE * (a - self.hip_a), *HIP_A_RANGE)
            self.hip_b = _clamp(self.hip_b + OFFSET_RATE * (b - self.hip_b), *HIP_B_RANGE)
            self.learned_hip = True
        if face_px >= MIN_FACE_PX:
            nu = (hip_u - float(uv[NOSE][0])) / face_px
            nv = (hip_v - float(uv[NOSE][1])) / face_px
            self.nose_hip_u = _clamp(self.nose_hip_u + OFFSET_RATE * (nu - self.nose_hip_u), *HIP_FROM_NOSE_U_RANGE)
            self.nose_hip_v = _clamp(self.nose_hip_v + OFFSET_RATE * (nv - self.nose_hip_v), *HIP_FROM_NOSE_V_RANGE)

    # ---------------------------------------------------------------- what the sender needs besides the root

    def zrel_hip(self, zrel):
        """The hip's reference depth for build_body_landmarks / _fallback_point, from the (smoothed) zrel.

        Tier 0 is the pre-F-60 value. Below it the hips are not in view, so the reference is the shoulder
        line (or the nose) minus the learned offset: every joint then keeps the model's own depth relative
        to the others, and only the root's absolute depth rests on the offset."""
        tier = self.result.tier
        if tier == TIER_HIPS:
            return 0.5 * (float(zrel[L_HIP]) + float(zrel[R_HIP]))
        if tier == TIER_SHOULDERS:
            return 0.5 * (float(zrel[L_SHOULDER]) + float(zrel[R_SHOULDER])) - self.dz_shoulder
        return float(zrel[NOSE]) - self.dz_nose

    def mask_outside(self, uv, conf, frame_size, out, tier=TIER_HIPS):
        """`out` (133,) <- conf with every keypoint that lies outside the image zeroed, and, when `tier` is not
        TIER_HIPS, the whole lower body (hips, knees, ankles, feet) too.

        Applied to every anchored frame: when the frame cuts the body, RTMW3D keeps answering for the part
        that is not there - on waist-up crops it returned wrists, elbows and knees below the image edge at
        confidence >= 0.3, and the old path sent them (ADR-096). On the 147 dancers (471 full-body frames) not
        one joint or hand point lies more than 2% outside the image, so for a full body in the picture this
        changes nothing.

        BELOW TIER 0 THE LOWER BODY IS ABSENT, not merely the part of it outside the image. A seated person at a desk
        (two phone recordings, 2026-10-12) had the model's hips, knees and feet answered INSIDE the picture at
        confidence 0.3-1.0 on 3-100% of frames, ABOVE the hips or within 0.2 m of them: the picture's bottom edge
        is the desk, not the floor. The root is an estimate on those frames, so a hip joint beside it is a second,
        contradicting answer, and the feet were taking the Unity floor for a ride."""
        width, height = frame_size
        tx = OUTSIDE_TOLERANCE * width
        ty = OUTSIDE_TOLERANCE * height
        for i in range(len(conf)):
            u = float(uv[i][0])
            v = float(uv[i][1])
            out[i] = conf[i] if (-tx <= u <= width + tx and -ty <= v <= height + ty) else 0.0
        if tier != TIER_HIPS:
            for i in LOWER_BODY:
                out[i] = 0.0
        return out

    # ---------------------------------------------------------------- the decision

    def _limit_rise(self, hip_z, source, now):
        """A depth taken from a width may not grow faster than MAX_DEPTH_RISE_PER_S (see the constant)."""
        if source not in (DEPTH_SHOULDERS, DEPTH_FACE) or self._z_prev is None:
            return hip_z
        dt = DEFAULT_DT_S if (now is None or self._now_prev is None) else _clamp(now - self._now_prev, 0.0, MAX_DT_S)
        return min(hip_z, self._z_prev * (1.0 + MAX_DEPTH_RISE_PER_S * dt))

    def update(self, uv, conf, zrel, intr, frame_size, depth_range=None, now=None):
        """Decide this frame's anchor, or return None when not even a head is seen.

        uv          (133, 2) keypoint pixels in the frame.
        conf        (133,) keypoint confidences (raw SimCC scale, the --conf scale).
        zrel        (133,) model root-relative z, metres (used only to move a shoulder or face depth to the hip).
        intr        (fx, fy, cx, cy) pixels of THIS frame.
        frame_size  (width, height) pixels of THIS frame.
        depth_range optional (min, max) metres, --depth-range: bounds the height rule and the default depth only.
        now         seconds on any steady clock, for the depth rise limit; None means one frame at 30 fps.
        Returns the reused FramingResult. A weak frame never raises: it falls to the next tier.
        """
        width, height = frame_size
        fx, fy = intr[0], intr[1]

        if now is not None and self._answered_at is not None and now - self._answered_at > FORGET_SECONDS:
            counts = self.tier_frames
            self.reset()
            self.tier_frames = counts

        hips_ok = (self._seen(self.hips_seen, float(conf[L_HIP]), float(conf[R_HIP]))
                   and self._inside(uv[L_HIP], width, height) and self._inside(uv[R_HIP], width, height)
                   and (self.hips_seen or (self._clear_of_edge(uv[L_HIP], width, height)
                                           and self._clear_of_edge(uv[R_HIP], width, height))))
        shoulders_ok = (self._seen(self.shoulders_seen, float(conf[L_SHOULDER]), float(conf[R_SHOULDER]))
                        and self._inside(uv[L_SHOULDER], width, height)
                        and self._inside(uv[R_SHOULDER], width, height))
        head_ok = (conf[NOSE] >= (self.gate if self.head_seen else self.gate + REGAIN_MARGIN)
                   and self._inside(uv[NOSE], width, height))
        self.hips_seen = hips_ok
        self.shoulders_seen = shoulders_ok
        self.head_seen = head_ok

        if not (hips_ok or shoulders_ok or head_ok):
            return None

        shoulder_px = _dist(uv[L_SHOULDER], uv[R_SHOULDER]) if shoulders_ok else 0.0
        face_px = self._face_px(uv, conf, width, height)
        hip_z, source = self._depth(uv, conf, zrel, fx, fy, width, height, shoulders_ok, shoulder_px, face_px,
                                    depth_range, hips_ok)
        if not math.isfinite(hip_z):
            hip_z, source = DEFAULT_DEPTH_M, DEPTH_DEFAULT
        hip_z = self._limit_rise(hip_z, source, now)

        res = self.result
        if hips_ok:
            res.tier = TIER_HIPS
            res.hip_u = 0.5 * (float(uv[L_HIP][0]) + float(uv[R_HIP][0]))
            res.hip_v = 0.5 * (float(uv[L_HIP][1]) + float(uv[R_HIP][1]))
            if min(float(conf[L_HIP]), float(conf[R_HIP])) >= LEARN_MIN_CONF:
                if source == DEPTH_HEIGHT:
                    self._learn_widths(hip_z, fx, shoulder_px, face_px)
                if shoulders_ok or face_px >= MIN_FACE_PX:
                    self._learn_hip(uv, res.hip_u, res.hip_v, face_px)
                self._learn_dz(conf, zrel, shoulders_ok)
        elif shoulders_ok:
            res.tier = TIER_SHOULDERS
            frame = self._shoulder_frame(uv)
            if frame is None:
                return self._head_only(uv, face_px, hip_z, source, head_ok, now)
            ux, uy, vx, vy, sw = frame
            mx = 0.5 * (float(uv[L_SHOULDER][0]) + float(uv[R_SHOULDER][0]))
            my = 0.5 * (float(uv[L_SHOULDER][1]) + float(uv[R_SHOULDER][1]))
            res.hip_u = mx + sw * (self.hip_a * ux + self.hip_b * vx)
            res.hip_v = my + sw * (self.hip_a * uy + self.hip_b * vy)
        else:
            return self._head_only(uv, face_px, hip_z, source, head_ok, now)

        res.hip_z = hip_z
        res.depth_source = source
        self._commit(res, now)
        return res

    def _head_only(self, uv, face_px, hip_z, source, head_ok, now):
        """Tier 2: only the face. The root hangs below the nose by the learned head-to-hip ratio, which
        needs a face width: with none, the person is present but there is nothing to place them with."""
        if not head_ok or face_px < MIN_FACE_PX:
            return None
        res = self.result
        res.tier = TIER_HEAD
        res.hip_u = float(uv[NOSE][0]) + self.nose_hip_u * face_px
        res.hip_v = float(uv[NOSE][1]) + self.nose_hip_v * face_px
        res.hip_z = hip_z
        res.depth_source = source
        self._commit(res, now)
        return res

    def _commit(self, res, now):
        """The frame produced an answer: remember its depth, count the tier and note a change of it."""
        if self.last_tier is not None and res.tier != self.last_tier:
            self.tier_changes += 1
            if now is None or self._event_at is None or now - self._event_at >= EVENT_MIN_GAP_S:
                self._event = "%s -> %s (%s)" % (TIER_NAMES[self.last_tier], TIER_NAMES[res.tier], res.describe())
                self._event_at = now
        self.last_tier = res.tier
        self.tier_frames[res.tier] += 1
        self._z_prev = res.hip_z
        self._now_prev = now
        self._answered_at = now

    def take_event(self):
        """The tier change since the last call as one line, or None. Healthy frames produce nothing."""
        event = self._event
        self._event = None
        return event

    def summary(self):
        """`hips/shoulders/head` frame counts, for the heartbeat."""
        return "%d/%d/%d" % tuple(self.tier_frames)
