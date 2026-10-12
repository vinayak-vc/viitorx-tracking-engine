#!/usr/bin/env python3
"""F-57 - FRAME ORIENTATION FOR SOURCES WITH NO OAK-D: find which way is up, and the lens that goes with it.

    raw frame -> AutoOrient (4 candidate rotations, scored) -> rotation to apply -> the sender's normal chain

WHY THIS EXISTS. video_udp_sender.py takes a rotation from the operator (`--rotate`). That is right for a
file, and wrong for the sources people actually move: a phone on the LAN camera server and a webcam are
turned between portrait and landscape by hand, and an app may deliver the picture upright, sideways or
upside down depending on how the phone was held and which app is in between. The person detector and
the pose model were trained on upright people, so a sideways frame does not fail loudly: it yields
low-confidence boxes and a skeleton that is wrong in a plausible way.

THE SIGNAL. For each of the four rotations the frame is run through the person detector and, for the
best box, the pose model. A rotation scores well when a person is detected there AND the pose reads as a
body standing up (head above shoulders above hips above knees above ankles). Measured in F-57 on 60
sampled frames of three clips: pose confidence alone picked the upright rotation 45-50% of the time,
pose geometry alone 85-90%, and detector x pose x geometry 100%. The detector alone also scored 100% on
those two-dancer clips but its per-frame margin is thin (a rotated frame's best box reached 0.91 against
0.84-0.94 for the upright one), so the pose terms are kept to add margin; on five real clips (212
frames) the product picked the upright rotation in 210, and in the two misses nobody was confidently
detected upright. See decisions.md ADR-093 for the numbers and the decision test (96 of 96 locks right).

WHEN IT RUNS. Never on every frame. It runs (1) at the start, (2) whenever the delivered frame changes
size (a phone turned from portrait to landscape does exactly that), and (3) as a slow self-check while
the sender is emitting nothing, which covers a rotation that does not change the frame size. A decision
needs several agreeing frames, so one odd frame cannot turn the picture. While it is undecided the frame
is not used (the caller skips it), up to PROBE_TIMEOUT_SECONDS, after which it settles on the operator's
`--rotate` value and says so.

THE LENS. A rotated phone has the same lens and the same pixels per radian, so its focal length in
pixels must not depend on which side of the frame is the width. `focal_px` with axis "long" derives it
from the long side, which is what makes a portrait frame and a landscape frame of the same phone agree
about distance. The old rule (axis "width") stays the default for everything that does not opt in.

Pure logic where possible: the scorer is injected, no clock is read (every method takes `now`), so the
decision rules are unit-tested without a model, a GPU or a camera (tests/test_frame_orientation.py).
"""

import math

import cv2
import numpy as np

#: The clockwise rotations a sender can apply, degrees.
ROTATIONS = (0, 90, 180, 270)

#: Rotation OpenCV applies for each of them; 0 applies none.
_CV2_CODE = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}

#: COCO-17 indices (the first 17 of RTMW3D's 133 keypoints) grouped head to feet. A group is used only
#: when one of its keypoints is confident, so a webcam that frames the upper body is judged on the
#: groups it can see instead of on hallucinated ankles.
_BODY_GROUPS = ((0,), (5, 6), (11, 12), (13, 14), (15, 16))

#: A keypoint below this does not count as seen. 0.3 is the sender's own --conf.
KEYPOINT_CONF = 0.3

#: Weight of the geometry term floor. The product is det * pose * (floor + (1 - floor) * geometry): an
#: upright body scores its full detector x pose value, a body that reads upside down keeps a quarter
#: of it, so a bad geometry reading lowers a candidate without erasing a strong detection.
GEOMETRY_FLOOR = 0.25

#: A frame casts a vote only when the winning rotation scores at least this much. Measured upright
#: scores on the F-57 clips start at 0.5; a frame with no clear person (an empty room, someone half out
#: of shot) must not decide anything.
MIN_WIN_SCORE = 0.35

#: ... and only when it beats the runner-up by this factor. Measured upright / best-wrong ratio: see ADR-093.
MIN_WIN_RATIO = 1.3

#: Agreeing votes needed to decide, and the share of all votes the leader must hold.
VOTES_NEEDED = 4
VOTE_SHARE = 0.75

#: Seconds of probing before it gives up and keeps the operator's rotation. A person who has not stood in
#: view for this long gives it nothing to read, and refusing every frame in the meantime would also refuse
#: a person the detector cannot see but the pose model could.
PROBE_TIMEOUT_SECONDS = 4.0

#: Self-check: with nothing emitted for this long, look again, at most once per interval, and switch only
#: after this many consecutive checks that all prefer the same other rotation.
STALL_SECONDS = 3.0
RECHECK_INTERVAL_SECONDS = 1.0
SWITCH_CHECKS = 3


def rotate_clockwise(frame, degrees):
    """`frame` turned clockwise by 0, 90, 180 or 270 degrees (the same convention as `--rotate`)."""
    code = _CV2_CODE.get(degrees)
    if code is None:
        return frame
    return cv2.rotate(frame, code)


def focal_px(width, height, hfov_degrees, axis):
    """Pixel focal length for a frame of (width x height) and a field of view in degrees.

    axis "width": the FOV is across the frame's width (the F-49 rule; a rotated phone changes it).
    axis "long": the FOV is across the frame's LONGER side, so a phone turned from landscape to portrait
    keeps the same focal length in pixels, as the real lens does.
    """
    side = max(width, height) if axis == "long" else width
    return 0.5 * side / math.tan(math.radians(hfov_degrees) * 0.5)


def upright_geometry(uv, conf):
    """How much a pose reads as a body standing up, 0..1, from the 17 COCO keypoints.

    uv    (133, 2) keypoint pixels in the frame that was scored; y grows downward.
    conf  (133,) keypoint confidences.
    Returns the share of head-to-feet steps (between the groups that were seen) that go downward, or 0.5
    (no opinion) when fewer than two groups were seen.
    """
    ys = []
    for group in _BODY_GROUPS:
        seen = [uv[i][1] for i in group if conf[i] >= KEYPOINT_CONF]
        if seen:
            ys.append(float(np.mean(seen)))
    if len(ys) < 2:
        return 0.5
    down = sum(1 for a, b in zip(ys[:-1], ys[1:]) if b > a)
    return down / float(len(ys) - 1)


def candidate_score(det_score, body_conf, geometry):
    """One rotation's score from the detector, the pose confidence and the geometry (all 0..1)."""
    return det_score * body_conf * (GEOMETRY_FLOOR + (1.0 - GEOMETRY_FLOOR) * geometry)


class DetectorPoseScorer:
    """Scores a frame AS DELIVERED: detector first, pose model on the best box only.

    Returns candidate_score() or 0.0 when nobody is detected. Not thread-safe (it shares the sender's
    detector and pose model, which are called from the one frame loop).
    """

    def __init__(self, detector, model, min_det=0.30):
        # Imported here, not at module level: multiperson_udp_sender pulls in depthai, and the decision
        # rules in this file are tested without a camera stack.
        import multiperson_udp_sender as MP
        self.detector = detector
        self.model = model
        self.min_det = min_det
        self._to_bbox = MP.track_to_bbox

    def score(self, frame):
        h, w = frame.shape[0], frame.shape[1]
        dets = self.detector.detect(frame)
        if not dets or dets[0].score < self.min_det:
            return 0.0
        best = dets[0]
        uv, _zrel, conf = self.model.infer(frame, self._to_bbox(best, w, h))
        body_conf = float(np.mean([conf[i] for i in range(17)]))
        return candidate_score(best.score, body_conf, upright_geometry(uv, conf))


class AutoOrient:
    """Decides the rotation to apply to each delivered frame.

    scorer    object with score(frame) -> float for a frame AS DELIVERED (DetectorPoseScorer, or a fake).
    initial   rotation to use until a decision is made and as the fallback after a time-out (the
              operator's `--rotate`).
    log       callable(str) for the decisions; they are rare and worth reading.
    """

    def __init__(self, scorer, initial=0, log=None):
        self.scorer = scorer
        self.initial = initial
        self.log = log or (lambda text: None)
        self.rotation = initial
        self.locked = False
        self._shape = None
        self._votes = {}
        self._probe_started = None
        self._last_sent = 0.0
        self._last_check = 0.0
        self._contrary = {}
        self._begin_probe("start")

    def describe(self):
        """One short fragment for the sender's heartbeat line."""
        return "orient=%s rot=%d" % ("locked" if self.locked else "probing", self.rotation)

    def note_sent(self, now):
        """The sender emitted a pose at `now`: the current rotation is demonstrably working."""
        self._last_sent = now
        self._contrary = {}

    def rotation_for(self, frame, now):
        """The rotation to apply to `frame`, or None while undecided (the caller skips the frame)."""
        shape = (frame.shape[0], frame.shape[1])
        if self._shape is not None and shape != self._shape:
            self._begin_probe("frame size changed %dx%d -> %dx%d"
                              % (self._shape[1], self._shape[0], shape[1], shape[0]))
        self._shape = shape
        if not self.locked:
            self._probe_step(frame, now)
            if not self.locked:
                return None
            return self.rotation
        self._self_check(frame, now)
        return self.rotation

    def _begin_probe(self, reason):
        self.locked = False
        self._votes = {}
        self._contrary = {}
        # Stamped by the first probe step, not here: the clock belongs to the caller, and a probe that
        # began "at time zero" would already be past its time-out on the first real frame.
        self._probe_started = None
        self.log("orientation: probing (%s)" % reason)

    def _scores(self, frame):
        return {r: self.scorer.score(rotate_clockwise(frame, r)) for r in ROTATIONS}

    @staticmethod
    def _winner(scores):
        """(rotation, score) of a CLEAR winner, or None. Clear means strong enough and well ahead."""
        order = sorted(scores, key=lambda r: scores[r], reverse=True)
        best, second = scores[order[0]], scores[order[1]]
        if best < MIN_WIN_SCORE or best < MIN_WIN_RATIO * second:
            return None
        return order[0], best

    def _probe_step(self, frame, now):
        if self._probe_started is None:
            self._probe_started = now
        scores = self._scores(frame)
        win = self._winner(scores)
        if win is not None:
            self._votes[win[0]] = self._votes.get(win[0], 0) + 1
        total = sum(self._votes.values())
        if self._votes:
            leader = max(self._votes, key=lambda r: self._votes[r])
            if self._votes[leader] >= VOTES_NEEDED and self._votes[leader] >= VOTE_SHARE * total:
                self._lock(leader, now, "decided by %d of %d votes" % (self._votes[leader], total))
                return
        if now - self._probe_started >= PROBE_TIMEOUT_SECONDS:
            self._lock(self.initial, now, "no clear person for %.0f s, keeping rotate %d"
                       % (PROBE_TIMEOUT_SECONDS, self.initial))

    def _lock(self, rotation, now, why):
        self.rotation = rotation
        self.locked = True
        self._last_sent = now
        self._last_check = now
        self.log("orientation: rotate %d (%s)" % (rotation, why))

    def _self_check(self, frame, now):
        if now - self._last_sent < STALL_SECONDS or now - self._last_check < RECHECK_INTERVAL_SECONDS:
            return
        self._last_check = now
        win = self._winner(self._scores(frame))
        if win is None or win[0] == self.rotation:
            self._contrary = {}
            return
        self._contrary[win[0]] = self._contrary.get(win[0], 0) + 1
        for rotation in list(self._contrary):
            if rotation != win[0]:
                del self._contrary[rotation]
        if self._contrary[win[0]] >= SWITCH_CHECKS:
            self._lock(win[0], now, "nothing emitted for %.0f s and %d checks agree" % (STALL_SECONDS, SWITCH_CHECKS))
            self._contrary = {}


def seed_bbox(detector, frame):
    """The pixel crop for the pose model around the most prominent person, or None when nobody is found.

    For the single-person path, which otherwise starts from the centred 3:4 region: in a landscape frame
    that region is only ~42% of the width, so a person standing to one side is never in it. "Most
    prominent" is detector score x box area, i.e. the nearest clear person. Used only when the crop has
    been lost, never to override a crop that is working.
    """
    import multiperson_udp_sender as MP
    dets = detector.detect(frame)
    if not dets:
        return None
    best = max(dets, key=lambda d: d.score * (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]))
    return MP.track_to_bbox(best, frame.shape[1], frame.shape[0])
