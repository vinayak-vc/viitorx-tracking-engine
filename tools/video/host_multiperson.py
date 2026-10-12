#!/usr/bin/env python3
"""F-52 - MULTI-PERSON FOR SOURCES WITHOUT AN OAK-D: host detector -> PersonTracker -> one pose per person.

    frame -> HostPersonDetector (YOLOX-s, GPU) -> PersonTracker (stable ids) -> RTMW3D per tracked person

WHY THIS IS NOT multiperson_udp_sender. That sender is an OAK-D program: its detector runs on the VPU,
its depth is stereo, and it imports a device pipeline. A phone, a webcam and a video file have none of
those, and video_udp_sender.py (the F-49/F-50 source layer) posed ONE crop for them. This class is the
missing middle: the same tracker and the same wire shape as F-32, fed by a host detector.

WHICH PER-PERSON CHAIN, AND WHY NOT F-33'S. F-33's filters (P0 smoothing, P1-1, F-22) work on a STEREO
depth frame. There is none here. The only way to run them is the synthetic depth frame of
f32_multiperson_video.py, which paints every box with ONE flat distance plus noise: every joint would
then read `measured` and take that flat z, throwing away the model's own coherent root-relative z, which
is the thing that made single-person video tracking good (see the F-49 FINDING). So each person gets the
chain video_udp_sender.py already uses for one: hip depth from apparent height, a One-Euro filter on the
model's per-joint z, and P1-1's per-joint states for the TRUST HUD. The calls are the same builders
(build_body_landmarks / build_hand / build_joint_states), so the wire contract has one definition.

THE ROOT. The legacy primary person (persons[0], the most established) is also published at the root in
the single-person shape, so every Unity scene consumes this sender unchanged. Its translation is scaled
by --sway-gain exactly as video_udp_sender scales it, so a scene sees the same root motion whether one
or several people are being tracked.

KNOWN LIMITS, stated rather than hidden: with partial_body OFF (the F-52 rule) hip depth comes from
nose-to-ankle span, so a person whose ankles are hidden (a back-row dancer) falls back to the 2.0 m default
and sits at the wrong distance, and a person without confident hips is not sent at all. With partial_body ON
(F-60, what the sender passes by default) each person carries a framing.FramingTracker: the hips anchor the
skeleton while they are in the picture, then the shoulders, then the nose, and depth comes from the shoulder or
face width when there are no ankles. There is no ownership layer (F-21 is superseded for multi-person, see
person_tracker.py); and N people cost N sequential pose solves.
"""

import time

import numpy as np

import framing as FR
import joint_tracker as JT
import multiperson_udp_sender as MP
import person_tracker as PT
import smoothing
import wholebody_udp_sender as W

#: Seconds a track's smoothing and joint-trust state outlives the track's last appearance in the tracker's
#: output. The tracker holds a LOST track for 1.2 s (PersonTracker max_misses_seconds) before retiring
#: it, so 3 s means a person who is briefly occluded and re-acquired keeps their history, while a person
#: who has gone leaves nothing behind.
STATE_IDLE_SECONDS = 3.0


class PersonState:
    """The temporal memory one tracked person carries: a z filter per keypoint and P1-1's joint trust."""

    __slots__ = ("zfilt", "skel", "last_seen", "framing", "conf_buf")

    def __init__(self, src_fps, now, framing=None):
        # min_cutoff 0.5 / beta 0.4 are production's trunk parameters, as in video_udp_sender.
        self.zfilt = [smoothing.OneEuro(freq=src_fps, min_cutoff=0.5, beta=0.4) for _ in range(133)]
        self.skel = JT.SkeletonTracker()
        self.last_seen = now
        #: F-60: this person's framing memory (the hip offset and body widths learned from THEM), or None.
        self.framing = framing
        self.conf_buf = np.zeros(133, dtype=np.float32) if framing is not None else None


class HostMultiPerson:
    """Owns the detector, the tracker and the per-person state; turns one frame into one datagram."""

    def __init__(self, model, detector, hip_depth_fn, session_id, conf=0.3, max_poses=3,
                 subject_height=1.70, depth_range=None, smooth_zrel=True, sway_gain=0.45,
                 flatten_trunk=False, assume_measured=False, src_fps=30.0, partial_body=False):
        self.model = model
        self.partial_body = partial_body
        self.detector = detector
        self.hip_depth_fn = hip_depth_fn
        self.session_id = session_id
        self.conf = conf
        self.max_poses = max(1, int(max_poses))
        self.subject_height = subject_height
        self.depth_range = depth_range
        self.smooth_zrel = smooth_zrel
        self.sway_gain = sway_gain
        self.flatten_trunk = flatten_trunk
        self.assume_measured = assume_measured
        self.src_fps = src_fps
        self.seq = 0
        self.tracker = PT.PersonTracker()
        self.states = {}
        # Reused across people: this path has no stereo, so nothing is ever measured and no depth
        # point is ever written to xyz_cam (the same two arrays video_udp_sender keeps all-zero).
        self._measured = np.zeros(133, dtype=bool)
        self._xyz_zero = np.zeros((133, 3), dtype=np.float32)
        self._xyz_track = np.zeros((133, 3), dtype=np.float32)
        self._src_all_measured = [1] * 33
        #: Last frame's numbers, read by the heartbeat. DIAG-ONLY: nothing here feeds a decision.
        self.last = {"ndet": 0, "ntrack": 0, "posed": 0, "detect_ms": 0.0, "pose_ms": 0.0}

    def reset(self):
        """A clip replayed from its start is a new scene: identities and filters must not carry over."""
        self.tracker = PT.PersonTracker()
        self.states = {}

    def summary(self):
        """One short fragment for the sender's heartbeat line."""
        return ("people=%d tracks=%d dets=%d detect~%.1fms pose~%.1fms"
                % (self.last["posed"], self.last["ntrack"], self.last["ndet"],
                   self.last["detect_ms"], self.last["pose_ms"]))

    def _state_for(self, track_id, now):
        st = self.states.get(track_id)
        if st is None:
            framing = FR.FramingTracker(self.conf, self.subject_height, W.ZREL_SIGN) if self.partial_body else None
            st = PersonState(self.src_fps, now, framing)
            self.states[track_id] = st
        st.last_seen = now
        return st

    def _release_idle(self, now):
        stale = [i for i, st in self.states.items() if now - st.last_seen > STATE_IDLE_SECONDS]
        for i in stale:
            del self.states[i]

    def _solve_person(self, frame, track, w, h, intr, now):
        """Pose ONE tracked person and build their slice of the wire payload, or None.

        Returns (person_dict_or_None, pose_ms).
        """
        fx, fy = intr[0], intr[1]
        bbox = MP.track_to_bbox(track, w, h)
        t0 = time.perf_counter()
        uv, zrel, conf = self.model.infer(frame, bbox)
        pose_ms = (time.perf_counter() - t0) * 1000.0
        anchor = None
        if self.partial_body:
            # F-60: this person's own framing memory decides the anchor; a person with no hips is still
            # posed from the shoulders or the nose, and only a frame with not even a head is dropped.
            state = self._state_for(track.id, now)
            anchor = state.framing.update(uv, conf, zrel, intr, (w, h), self.depth_range, now)
            event = state.framing.take_event()
            if event:
                print("[vid]   person %d framing: %s" % (track.id, event), flush=True)
            if anchor is None:
                return None, pose_ms
            conf = state.framing.mask_outside(uv, conf, (w, h), state.conf_buf, anchor.tier)
            hip_z = anchor.hip_z
            hip_uv = (anchor.hip_u, anchor.hip_v)
        else:
            if conf[11] < self.conf or conf[12] < self.conf:
                return None, pose_ms
            state = self._state_for(track.id, now)
            hip_z = self.hip_depth_fn(uv, conf, self.conf, self.subject_height, fy, self.depth_range)
            hip_uv = 0.5 * (uv[11] + uv[12])
        if self.smooth_zrel:
            zrel = np.array([state.zfilt[i].filter(float(zrel[i])) for i in range(len(zrel))],
                            dtype=np.float32)
        if anchor is not None:
            zrel_hip = state.framing.zrel_hip(zrel)
        else:
            zrel_hip = 0.5 * (float(zrel[11]) + float(zrel[12]))
        mid_hip = np.array([(hip_uv[0] - intr[2]) * hip_z / fx,
                            (hip_uv[1] - intr[3]) * hip_z / fy, hip_z], dtype=np.float32)

        # P1-1 per-joint states, for the TRUST HUD. States only; geometry is never written back, so
        # this cannot turn an unmeasured joint into a "measured" one (same rule as video_udp_sender).
        pos_in = {}
        cnf_in = {}
        dv_in = {}
        for j in state.skel.indices:
            p = W._fallback_point(j, uv, zrel, zrel_hip, mid_hip, hip_z, intr, True) + mid_hip
            self._xyz_track[j] = p
            pos_in[j] = (float(p[0]), float(p[1]), float(p[2]))
            cnf_in[j] = float(conf[j])
            dv_in[j] = True
        track_res = state.skel.update(pos_in, cnf_in, now, depth_valid=dv_in, collect_events=False)

        lm, src = W.build_body_landmarks(uv, self._xyz_zero, self._measured, conf, zrel, zrel_hip,
                                         mid_hip, hip_z, intr, self.conf,
                                         flatten_trunk=self.flatten_trunk, use_zrel=True)
        lh = W.build_hand(uv, self._xyz_zero, self._measured, conf, zrel, zrel_hip, mid_hip, hip_z,
                          intr, 91, self.conf, use_zrel=True)
        rh = W.build_hand(uv, self._xyz_zero, self._measured, conf, zrel, zrel_hip, mid_hip, hip_z,
                          intr, 112, self.conf, use_zrel=True)
        person = {
            "id": track.id,
            "lm": [[round(v, 4) for v in pt] for pt in lm],
            "xyz": [round(float(mid_hip[0] * 1000.0), 1),
                    round(float(mid_hip[1] * 1000.0), 1),
                    round(float(mid_hip[2] * 1000.0), 1)],
            "src": self._src_all_measured if self.assume_measured else src,
            "st": W.build_joint_states(track_res),
            "state": track.state,
        }
        if anchor is not None:
            person["fr"] = anchor.tier
        if lh is not None:
            person["lh"] = lh
        if rh is not None:
            person["rh"] = rh
        return person, pose_ms

    def process(self, frame, intr, clock, now, t_frame):
        """One frame in, one datagram out.

        frame   BGR image, already oriented.
        intr    (fx, fy, cx, cy) in pixels for THIS frame's size.
        clock   the TRACKER's time, seconds: the video's own timeline for a file (so a replay is
                reproducible and a faster machine does not change identities), the wall clock for a
                live source, where the wall clock IS the frame clock.
        now     wall-clock seconds, for the per-person filters.
        t_frame wall-clock seconds the frame was grabbed, for the `lat` field.

        Returns the message dict, or None when nobody was posed. Events never raise: a person whose
        hips are not confident is simply absent from this datagram.
        """
        h, w = frame.shape[0], frame.shape[1]
        dets = self.detector.detect(frame)
        tracks = self.tracker.update(dets, clock)
        self.tracker.drain_events()
        # ONLY CONFIRMED TRACKS ARE POSED: a LOST track coasts on prediction with no detection behind
        # it, and posing one runs the model on a box drifting away from any body (the same rule as
        # multiperson_udp_sender).
        posable = [t for t in tracks if t.state == PT.CONFIRMED]
        persons = []
        pose_ms = 0.0
        solved = 0
        for track in posable[:self.max_poses]:
            person, ms = self._solve_person(frame, track, w, h, intr, now)
            pose_ms += ms
            solved += 1
            if person is not None:
                persons.append(person)
        self._release_idle(now)
        self.last = {"ndet": len(dets), "ntrack": len(tracks), "posed": len(persons),
                     "detect_ms": self.detector.last_ms,
                     "pose_ms": (pose_ms / solved) if solved else 0.0}
        if not persons:
            return None
        self.seq += 1
        message = {
            "persons": persons,
            "n": len(persons),
            "ndet": len(dets),
            "ntrack": len(tracks),
            "seq": self.seq,
            "sid": self.session_id,
            "t": round(time.time(), 4),
            "lat": round((time.time() - t_frame) * 1000.0, 1),
        }
        MP.add_legacy_primary(message, persons)
        if "fr" in persons[0]:
            message["fr"] = persons[0]["fr"]
        # The root keeps the single-person shape AND scale: lateral/vertical sway is damped by
        # --sway-gain exactly as video_udp_sender does, z is never scaled.
        x, y, z = persons[0]["xyz"]
        message["xyz"] = [round(x * self.sway_gain, 1), round(y * self.sway_gain, 1), z]
        return message
