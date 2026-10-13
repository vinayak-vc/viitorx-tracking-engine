#!/usr/bin/env python3
"""H-02 - FACE TRACKING: stable ids, detect-then-track, smoothing, 52 blendshapes, head pose and 3D position.

THE LOOP is the hand tracker's (hand_tracking.py). A face that is already tracked costs one mesh pass (and one blendshape pass),
because the crop for the next frame is predicted from this frame's landmarks; the detector runs only while there is room for
another face, at most once every `detect_interval_s` when a face is already tracked and every frame when none is. A face
detection that overlaps a tracked face's landmark box is that face, not a new one (the crop is wider than the face, so
overlap is judged on the face).

IDENTITY. A face gets an id when first found and keeps it while tracked. A face lost (presence below `keep_presence`: motion
blur, a turned-away head, leaving the frame) keeps its id for `reacquire_s`: a face found again near where it was lost takes the
old id back, one found anywhere else or later is NEW, and ids are never reused. When several faces are tracked each crop's
result is given to the track whose motion it continues, so two faces that pass each other do not swap ids.

WHAT IS SENT. A face is reported only if the network read it this frame, or within the last `hold_frames` frames (state HELD,
the last values, flagged stale). Nothing is predicted beyond that: a face that is not there is not in the list.

EXPRESSION. The 52 blendshapes are read from the RAW landmarks of the frame (smoothing the landmarks first would add their lag
to every expression) and smoothed per channel with a fast One-Euro filter: still enough to steady a resting face, quick enough
not to blunt a blink. They are clamped to [0, 1]. Head pose and position come from the SMOOTHED landmarks (face_geometry.py).

The filter constants are starting values, not measurements (H-02 NOT TESTED on a live face). Driven by an injected detector,
mesh network, blendshape network, camera factory and clock, so it is tested without hardware (tests/test_face_tracking.py).
"""

import math

import numpy as np

import assignment
import face_geometry as FG
import face_landmarker as F
import roi_geometry as RG
from landmark_filters import VectorFilter

TRACKED = 0
HELD = 1
STATE_NAMES = {TRACKED: "TRACKED", HELD: "HELD"}


class FaceTrackerConfig(object):
    """Every tunable of the tracker, with the value it ships with. None has been tuned on a live face (H-02 NOT TESTED)."""

    def __init__(self, max_faces=1, accept_presence=0.6, keep_presence=0.4, detect_interval_s=0.10, hold_frames=2,
                 reacquire_s=0.8, reacquire_radius=1.0, duplicate_fraction=0.3, min_roi_px=32.0, box_overlap=0.3,
                 assignment_bias=0.05, face_scale=1.0, smooth=True, blendshapes=True,
                 landmark_cutoff=1.5, landmark_beta=30.0, blend_cutoff=1.5, blend_beta=3.0,
                 position_cutoff=0.8, position_beta=5.0, depth_cutoff=0.4, depth_beta=2.0, reset_gap_s=0.25):
        self.max_faces = int(max_faces)
        #: Mesh presence a NEW face needs before it is tracked. Higher than keep_presence: a face is harder to find than to keep.
        self.accept_presence = accept_presence
        self.keep_presence = keep_presence
        self.detect_interval_s = detect_interval_s
        self.hold_frames = int(hold_frames)
        self.reacquire_s = reacquire_s
        #: A lost face is re-adopted when the new one's centre is within this fraction of the crop's side of where it was lost.
        self.reacquire_radius = reacquire_radius
        #: Two faces whose centres are closer than this fraction of a crop's side are the same face found twice.
        self.duplicate_fraction = duplicate_fraction
        self.min_roi_px = min_roi_px
        #: A detection whose box overlaps a tracked face's landmark box by more than this (IoU) is that face.
        self.box_overlap = box_overlap
        self.assignment_bias = assignment_bias
        self.face_scale = face_scale
        self.smooth = smooth
        #: False skips the blendshape network (and sends no blendshapes): the cheapest way to get landmarks and head pose only.
        self.blendshapes = blendshapes
        self.landmark_cutoff = landmark_cutoff
        self.landmark_beta = landmark_beta
        self.blend_cutoff = blend_cutoff
        self.blend_beta = blend_beta
        self.position_cutoff = position_cutoff
        self.position_beta = position_beta
        self.depth_cutoff = depth_cutoff
        self.depth_beta = depth_beta
        #: A face unseen for longer than this restarts its filters instead of smearing across the gap.
        self.reset_gap_s = reset_gap_s


class TrackedFace(object):
    """One face as the tracker reports it. The object is reused from frame to frame: read it (or copy it) before the next
    update(), do not keep it.

    id           stable, never reused
    conf         the mesh network's presence for this frame, 0..1
    state        TRACKED (read this frame) or HELD (last frame's values)
    image        (478, 3) normalised: x / width, y / height, z / width (smaller z is nearer the camera), smoothed
    blendshapes  (52,) in [0, 1], smoothed; None when the tracker runs without the blendshape network
    position     (3,) metres, camera space, the point between the eyes; None when refused (`reason` says why)
    yaw, pitch, roll   degrees (face_geometry.py for the signs); `rotation` the 3x3 behind them; `pose_ok` False when
                       the landmarks were degenerate
    """

    def __init__(self):
        self.id = 0
        self.conf = 0.0
        self.state = TRACKED
        self.image = None
        self.blendshapes = None
        self.position = None
        self.reason = ""
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.rotation = np.eye(3)
        self.pose_ok = False


class _Track(object):
    def __init__(self, track_id, now, cfg):
        self.id = track_id
        self.born = now
        self.roi = None
        self.last_seen = now
        self.last_time = now
        self.lost_at = None
        self.missed = 0
        self.last_centre = None      # pixels
        self.last_size = 0.0
        self.velocity = np.zeros(2)
        self.box = None              # [x1, y1, x2, y2] of the landmarks, pixels
        self.face = TrackedFace()
        self.face.id = track_id
        self.landmark_filter = VectorFilter(F.NUM_LANDMARKS * 3, cfg.landmark_cutoff, cfg.landmark_beta)
        self.blend_filter = VectorFilter(F.NUM_BLENDSHAPES, cfg.blend_cutoff, cfg.blend_beta)
        self.xy_filter = VectorFilter(2, cfg.position_cutoff, cfg.position_beta)
        self.depth_filter = VectorFilter(1, cfg.depth_cutoff, cfg.depth_beta)

    def reset_filters(self):
        self.landmark_filter.reset()
        self.blend_filter.reset()
        self.xy_filter.reset()
        self.depth_filter.reset()


class FaceTracker(object):
    """Owns the tracks. `detector.detect(frame)`, `mesh_net.infer(frame, roi)` and `blend_net.infer(landmarks_px)` are injected
    (`blend_net` may be None); so are `camera_factory(width, height)` (-> hand_geometry.Camera) and the clock."""

    def __init__(self, detector, mesh_net, blend_net, camera_factory, cfg=None):
        self.detector = detector
        self.mesh = mesh_net
        self.blend = blend_net
        self.camera_factory = camera_factory
        self.cfg = cfg or FaceTrackerConfig()
        self.camera = None
        self._size = None
        self._tracks = []
        self._next_id = 1
        self._last_now = None
        self._last_detect = -1e9
        self.frames = 0
        self.detector_runs = 0
        self.mesh_runs = 0
        self.blend_runs = 0

    # ------------------------------------------------------------------ public

    def update(self, frame, now):
        """Process one BGR frame taken at time `now` (seconds, monotonic); the faces to report, most established first."""
        h, w = frame.shape[:2]
        if self._size != (w, h):
            if self._size is not None:
                self._tracks = []      # pixel positions from another frame size mean nothing
            self._size = (w, h)
            self.camera = self.camera_factory(w, h)
        dt = (now - self._last_now) if self._last_now is not None else 1.0 / 30.0
        self._last_now = now
        freq = 1.0 / max(dt, 1e-3)
        self.frames += 1

        updated = set()
        reads = []
        for tr in self._tracks:
            if tr.roi is None:
                tr.missed += 1       # frames since it was lost, for HELD and for nothing else
            else:
                mesh = self._read(tr, frame, now)
                if mesh is not None:
                    reads.append((tr, mesh))
        reads = self._assign_reads(reads, dt)
        for tr, mesh in self._without_duplicates(reads):
            self._apply(tr, mesh, now, freq)
            updated.add(tr.id)
        live = [t for t in self._tracks if t.roi is not None]
        due = not live or now - self._last_detect >= self.cfg.detect_interval_s
        if len(live) < self.cfg.max_faces and due:
            self._last_detect = now
            self._detect(frame, now, freq, updated)
        self._tracks = [t for t in self._tracks
                        if t.roi is not None or (t.lost_at is not None and now - t.lost_at <= self.cfg.reacquire_s)]
        return self._report(updated)

    def reset(self):
        """Forget every track and the clock (a file that loops, a source that changed): ids keep counting up, never reused."""
        self._tracks = []
        self._last_now = None
        self._last_detect = -1e9

    @property
    def track_count(self):
        return sum(1 for t in self._tracks if t.roi is not None)

    # ------------------------------------------------------------------ internals

    def _read(self, tr, frame, now):
        """One mesh pass on a tracked face's predicted crop. The result, or None when the face was lost."""
        self.mesh_runs += 1
        mesh = self.mesh.infer(frame, tr.roi)
        if mesh.presence >= self.cfg.keep_presence and self._plausible(mesh):
            return mesh
        tr.roi = None
        tr.missed += 1
        tr.lost_at = now
        return None

    def _plausible(self, mesh):
        """False for a result that cannot be a face: not finite, or a crop too small to mean anything."""
        if not np.all(np.isfinite(mesh.landmarks)):
            return False
        return mesh.roi.size >= self.cfg.min_roi_px

    @staticmethod
    def _centre_of(mesh):
        return mesh.landmarks[:F.NUM_MESH_LANDMARKS, 0:2].mean(axis=0)

    def _assign_reads(self, reads, dt):
        """Give each result to the track whose motion it continues (see the module notes)."""
        if len(reads) < 2:
            return reads
        tracks = [t for t, _ in reads]
        results = [m for _, m in reads]
        cost = np.zeros((len(tracks), len(results)))
        for i, tr in enumerate(tracks):
            predicted = tr.last_centre + tr.velocity * dt
            scale = max(tr.last_size, 1.0)
            for j, mesh in enumerate(results):
                cost[i, j] = float(np.linalg.norm(self._centre_of(mesh) - predicted)) / scale
                if i != j:
                    cost[i, j] += self.cfg.assignment_bias
        return [(tracks[i], results[j]) for i, j in assignment.solve(cost)]

    def _without_duplicates(self, reads):
        """Drop the younger of two reads that landed on the same face; the dropped track is lost like any other."""
        kept = []
        for tr, mesh in sorted(reads, key=lambda p: p[0].born):
            centre = self._centre_of(mesh)
            twin = False
            for _, other in kept:
                gap = float(np.linalg.norm(centre - self._centre_of(other)))
                if gap < self.cfg.duplicate_fraction * max(mesh.roi.size, other.roi.size):
                    twin = True
                    break
            if twin:
                tr.roi = None
                tr.missed += 1
                tr.lost_at = tr.last_time
            else:
                kept.append((tr, mesh))
        return kept

    def _detect(self, frame, now, freq, updated):
        self.detector_runs += 1
        for det in sorted(self.detector.detect(frame), key=lambda d: -d.score):
            if sum(1 for t in self._tracks if t.roi is not None) >= self.cfg.max_faces:
                break
            if self._covered_by_live_track(det):
                continue
            roi = F.roi_from_detection(det)
            if roi.size < self.cfg.min_roi_px:
                continue
            self.mesh_runs += 1
            mesh = self.mesh.infer(frame, roi)
            if mesh.presence < self.cfg.accept_presence or not self._plausible(mesh):
                continue
            centre = self._centre_of(mesh)
            if self._is_duplicate_of_live(centre, mesh.roi.size):
                continue
            tr = self._reacquire(centre, now)
            if tr is None:
                tr = _Track(self._next_id, now, self.cfg)
                self._next_id += 1
                self._tracks.append(tr)
            self._apply(tr, mesh, now, freq)
            updated.add(tr.id)

    def _covered_by_live_track(self, det):
        """True when this detection is a face that is already being tracked: its box overlaps the tracked landmark box."""
        for t in self._tracks:
            if t.roi is None or t.box is None:
                continue
            if RG.iou_xyxy(det.box, t.box) > self.cfg.box_overlap:
                return True
        return False

    def _is_duplicate_of_live(self, centre, size):
        for t in self._tracks:
            if t.roi is None or t.last_centre is None:
                continue
            if float(np.linalg.norm(centre - t.last_centre)) < self.cfg.duplicate_fraction * max(size, t.last_size):
                return True
        return False

    def _reacquire(self, centre, now):
        """The lost track this face plausibly is, or None. Nearest wins."""
        best = None
        best_cost = None
        for t in self._tracks:
            if t.roi is not None or t.lost_at is None or t.last_centre is None:
                continue
            if now - t.lost_at > self.cfg.reacquire_s:
                continue
            radius = self.cfg.reacquire_radius * t.last_size
            d = float(np.linalg.norm(centre - t.last_centre))
            if d > radius:
                continue
            if best_cost is None or d / radius < best_cost:
                best, best_cost = t, d / radius
        return best

    def _apply(self, tr, mesh, now, freq):
        """Fold a read face into its track: filters, expression, pose, placement, next crop."""
        cfg = self.cfg
        if tr.lost_at is not None and now - tr.lost_at > cfg.reset_gap_s:
            tr.reset_filters()
        tr.lost_at = None
        tr.missed = 0
        tr.last_seen = now
        centre = self._centre_of(mesh)
        if tr.last_centre is not None and now > tr.last_time and now - tr.last_time <= cfg.reset_gap_s:
            tr.velocity = 0.5 * tr.velocity + 0.5 * (centre - tr.last_centre) / (now - tr.last_time)
        else:
            tr.velocity = np.zeros(2)
        tr.last_centre = centre
        tr.last_time = now
        tr.last_size = mesh.roi.size
        xy = mesh.landmarks[:F.NUM_MESH_LANDMARKS, 0:2]
        tr.box = np.array([xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()])
        tr.roi = F.roi_from_landmarks(mesh.landmarks[:, 0:2])

        w, h = self._size
        scale = np.array([w, h, w], dtype=np.float64)
        norm = mesh.landmarks / scale
        if cfg.smooth:
            norm = tr.landmark_filter.filter(norm.reshape(-1), freq).reshape(F.NUM_LANDMARKS, 3)
        smooth_px = norm * scale

        blend = None
        if self.blend is not None and cfg.blendshapes:
            self.blend_runs += 1
            blend = np.clip(self.blend.infer(mesh.landmarks), 0.0, 1.0)
            if cfg.smooth:
                blend = np.clip(tr.blend_filter.filter(blend, freq), 0.0, 1.0)

        pose = FG.head_pose(smooth_px)
        placement = FG.place_face(smooth_px, self.camera, cfg.face_scale)
        position = None
        if placement.ok:
            if cfg.smooth:
                ratio = tr.xy_filter.filter(placement.position[0:2] / placement.position[2], freq)
                z = math.exp(tr.depth_filter.filter(np.array([math.log(placement.position[2])]), freq)[0])
                position = np.array([ratio[0] * z, ratio[1] * z, z])
            else:
                position = placement.position.copy()

        face = tr.face
        face.conf = mesh.presence
        face.state = TRACKED
        face.image = norm
        face.blendshapes = blend
        face.position = position
        face.reason = placement.reason
        face.yaw, face.pitch, face.roll = pose.yaw, pose.pitch, pose.roll
        face.rotation = pose.rotation
        face.pose_ok = pose.ok

    def _report(self, updated):
        faces = []
        for tr in sorted(self._tracks, key=lambda t: t.born):
            if tr.face.image is None:
                continue
            if tr.id in updated:
                tr.face.state = TRACKED
            elif tr.missed >= 1 and tr.missed <= self.cfg.hold_frames and tr.roi is None:
                tr.face.state = HELD
            else:
                continue
            faces.append(tr.face)
        # max_faces is a promise to the receiver (it sizes its slots by it): a HELD face gives way to a tracked one.
        while len(faces) > self.cfg.max_faces:
            held = [f for f in faces if f.state == HELD]
            faces.remove(held[0] if held else faces[-1])
        return faces
