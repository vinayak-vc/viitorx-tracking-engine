#!/usr/bin/env python3
"""H-01 - HAND TRACKING: stable ids, the MediaPipe detect-then-track loop, smoothing, 3D placement and gestures.

THE LOOP. A frame costs one landmark pass per hand that is already being tracked, because the crop for the next frame
is predicted from this frame's landmarks (hand_landmarker.roi_from_landmarks). The palm detector, the expensive stage,
runs only while there is room for another hand (fewer than max_hands are being tracked), and at most once every
`detect_interval_s`, so one hand in view does not pay for a detector pass every frame it is looking for a second.

IDENTITY. A hand gets an id when it is first found and keeps it while it is tracked. A hand that is lost (the landmark
confidence falls below `keep_conf`: motion blur, a fist turned edge-on, the hand leaving the frame) keeps its id for
`reacquire_s`: a hand found again near where it was lost takes the old id back, and one found anywhere else, or later,
is a NEW hand with a new id. Ids are never reused, which is the rule the person tracker (person_tracker.py) arrived
at: anything keyed on an id must be able to trust that the id is one hand.

WHAT IS SENT AND WHAT IS NOT. A hand is reported only if the network found it this frame, or if it was found within the
last `hold_frames` frames (state HELD, the last smoothed values, flagged stale). Nothing is predicted past that: a hand
that is not there is not in the list. The sidecar's rule (AGENTS.md) is "never fabricate".

LEFT AND RIGHT. The network's handedness score is averaged per hand (a single frame's score flips on a blurred or
edge-on hand), and `side` is "R" above 0.5. MediaPipe's models label a hand as it appears in a MIRRORED (selfie)
picture; for an unmirrored camera the physical hand is the opposite one. `swap_sides` flips the label for that case.
Which setting matches which camera has NOT been checked against a known hand (H-01 NOT TESTED): raise your right hand
in front of the camera and the demo overlay says which letter it is.

NOT HERE: the networks (hand_landmarker, passed in), the camera, the socket. Everything is driven by an injected
detector and landmark net and an injected clock, so it is tested without hardware (tests/test_hand_tracking.py).
"""

import math

import numpy as np

import assignment
import hand_geometry as G
import hand_gestures as GE
import hand_landmarker as H
from landmark_filters import VectorFilter

TRACKED = 0
HELD = 1
STATE_NAMES = {TRACKED: "TRACKED", HELD: "HELD"}

#: Wrist and the four knuckles: the palm, which is what the palm detector boxes.
PALM_LANDMARKS = (H.WRIST, H.INDEX_MCP, H.MIDDLE_MCP, H.RING_MCP, H.PINKY_MCP)

SIDE_LEFT = "L"
SIDE_RIGHT = "R"


class HandTrackerConfig(object):
    """Every tunable of the tracker, with the value it ships with. None of these has been tuned against a ruler or a
    labelled recording (H-01 NOT TESTED); the filter numbers are One-Euro starting points, not measurements."""

    def __init__(self, max_hands=2, accept_conf=0.6, keep_conf=0.4, detect_interval_s=0.10, hold_frames=2,
                 reacquire_s=0.8, reacquire_radius=1.0, duplicate_fraction=0.12, min_roi_px=24.0,
                 palm_overlap=0.25, assignment_bias=0.05, gesture_min_conf=0.7,
                 hand_scale=1.0, swap_sides=False, handed_smoothing=0.2, gesture_hold=3, smooth=True,
                 image_cutoff=1.5, image_beta=30.0, world_cutoff=1.0, world_beta=10.0,
                 position_cutoff=0.8, position_beta=5.0, depth_cutoff=0.4, depth_beta=2.0,
                 reset_gap_s=0.25):
        self.max_hands = int(max_hands)
        #: Landmark confidence a NEW hand needs before it is tracked at all.
        self.accept_conf = accept_conf
        #: Landmark confidence below which a tracked hand is lost. Lower than accept_conf: a hand is harder to
        #: find than to keep, and a threshold of one value flickers at the boundary.
        self.keep_conf = keep_conf
        self.detect_interval_s = detect_interval_s
        self.hold_frames = int(hold_frames)
        self.reacquire_s = reacquire_s
        #: A lost hand is re-adopted when the new one's wrist is within this fraction of the crop's side (about two
        #: hand lengths at 1.0) of where it was lost. The side the network reports breaks a tie between two lost hands.
        self.reacquire_radius = reacquire_radius
        #: Two tracks whose wrists are closer than this fraction of a crop's side are the same hand found twice. Small
        #: on purpose: two real hands can have their wrists a hand-width apart.
        self.duplicate_fraction = duplicate_fraction
        #: A palm detection whose box overlaps a tracked hand's palm box by more than this (IoU) is that hand.
        self.palm_overlap = palm_overlap
        #: Added to the cost of pairing a result with a track other than the one whose crop produced it.
        self.assignment_bias = assignment_bias
        self.min_roi_px = min_roi_px
        self.hand_scale = hand_scale
        self.swap_sides = swap_sides
        self.handed_smoothing = handed_smoothing
        self.gesture_hold = int(gesture_hold)
        #: Below this landmark confidence the gesture reads NONE: a blurred hand still gets landmarks, but a pose read
        #: from them is a guess. The curls, the pinch and the position are still reported, with `conf` beside them.
        self.gesture_min_conf = gesture_min_conf
        self.smooth = smooth
        self.image_cutoff = image_cutoff
        self.image_beta = image_beta
        self.world_cutoff = world_cutoff
        self.world_beta = world_beta
        self.position_cutoff = position_cutoff
        self.position_beta = position_beta
        self.depth_cutoff = depth_cutoff
        self.depth_beta = depth_beta
        #: A hand unseen for longer than this restarts its filters instead of smearing across the gap.
        self.reset_gap_s = reset_gap_s


class TrackedHand(object):
    """One hand as the tracker reports it. The object is reused from frame to frame: read it (or copy it) before the
    next update(), do not keep it.

    id          stable, never reused
    side        "L" or "R" (see the module notes)
    handed      the averaged handedness score, 0..1
    conf        the landmark network's confidence this frame, 0..1
    state       TRACKED (read this frame) or HELD (last values, up to hold_frames old)
    image       (21, 3) normalised: x / width, y / height, z / width (smaller z is nearer the camera)
    world       (21, 3) metres, hand-centred, axes parallel to the image's (x right, y down, z away), smoothed
    position    (3,) metres, camera space (X right, Y down, Z forward), the hand's centre; None when the 3D fit was
                refused this frame (`reason` says why); a HELD hand reports the last frame's values
    fingers     hand_gestures.FingerState; its `gesture` is the stabilised one
    """

    def __init__(self):
        self.id = 0
        self.side = SIDE_RIGHT
        self.handed = 0.5
        self.conf = 0.0
        self.state = TRACKED
        self.image = None
        self.world = None
        self.position = None
        self.reason = ""
        self.fingers = None
        self.placement_residual = 0.0


class _Track(object):
    def __init__(self, track_id, now, cfg):
        self.id = track_id
        self.born = now
        self.roi = None              # the crop for the NEXT frame; None means "not being tracked right now"
        self.last_seen = now
        self.lost_at = None
        self.missed = 0
        self.last_wrist = None       # pixels
        self.last_time = now
        self.velocity = np.zeros(2)  # wrist, pixels per second, smoothed
        self.palm_box = None         # [x1, y1, x2, y2] of wrist + the four knuckles, pixels
        self.last_size = 0.0         # the last crop's side in pixels, a hand-size ruler for re-adoption
        self.handed = None
        self.hand = TrackedHand()
        self.hand.id = track_id
        self.stabilizer = GE.GestureStabilizer(cfg.gesture_hold)
        self.previous_extended = None
        self.image_filter = VectorFilter(63, cfg.image_cutoff, cfg.image_beta)
        self.world_filter = VectorFilter(63, cfg.world_cutoff, cfg.world_beta)
        self.xy_filter = VectorFilter(2, cfg.position_cutoff, cfg.position_beta)
        self.depth_filter = VectorFilter(1, cfg.depth_cutoff, cfg.depth_beta)
        self.has_filtered = False

    def has_filtered_gap(self, now, cfg):
        """True when the hand was unseen for long enough that its velocity means nothing."""
        return now - self.last_time > cfg.reset_gap_s

    def reset_filters(self):
        self.image_filter.reset()
        self.world_filter.reset()
        self.xy_filter.reset()
        self.depth_filter.reset()
        self.has_filtered = False


class HandTracker(object):
    """Owns the tracks. `palm_detector.detect(frame)` and `landmark_net.infer(frame, roi)` are injected; so is
    `camera_factory(width, height)` (-> hand_geometry.Camera) and the clock."""

    def __init__(self, palm_detector, landmark_net, camera_factory, cfg=None):
        self.palms = palm_detector
        self.net = landmark_net
        self.camera_factory = camera_factory
        self.cfg = cfg or HandTrackerConfig()
        self.camera = None
        self._size = None
        self._tracks = []
        self._next_id = 1
        self._last_now = None
        self._last_detect = -1e9
        # Counters for the heartbeat; they are the only way to see what the loop is doing without a debugger.
        self.frames = 0
        self.detector_runs = 0
        self.landmark_runs = 0

    # ------------------------------------------------------------------ public

    def update(self, frame, now):
        """Process one BGR frame taken at time `now` (seconds, monotonic) and return the hands to report,
        most established first."""
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
                res = self._read(tr, frame, now)
                if res is not None:
                    reads.append((tr, res))
        reads = self._assign_reads(reads, dt)
        for tr, res in self._without_duplicates(reads):
            self._apply(tr, res, now, freq)
            updated.add(tr.id)
        live = [t for t in self._tracks if t.roi is not None]
        # With nothing tracked the detector runs every frame (a hand that appears is found at once); with a hand
        # already tracked it only LOOKS for another, so it is rate-limited.
        due = not live or now - self._last_detect >= self.cfg.detect_interval_s
        if len(live) < self.cfg.max_hands and due:
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
        """One landmark pass on a tracked hand's predicted crop. The result, or None when the hand was lost."""
        self.landmark_runs += 1
        res = self.net.infer(frame, tr.roi)
        if res.conf >= self.cfg.keep_conf and self._plausible(res):
            return res
        tr.roi = None
        tr.missed += 1
        tr.lost_at = now
        return None

    def _assign_reads(self, reads, dt):
        """Give each result to the track whose motion it continues.

        Each track's crop was cut where that track's hand WAS, but with two hands close together a crop can contain
        both, and the network answers for whichever it prefers: track A's crop may return hand B. Taking each result
        at face value would swap the two ids at every crossing. Instead the results are matched to the tracks by
        optimal assignment on the distance between each track's PREDICTED wrist (last position plus its velocity)
        and each result's wrist, with a small bias toward the pairing the crops produced, so that hands which are not
        near each other keep the obvious pairing.
        """
        if len(reads) < 2:
            return reads
        tracks = [t for t, _ in reads]
        results = [r for _, r in reads]
        cost = np.zeros((len(tracks), len(results)))
        for i, tr in enumerate(tracks):
            predicted = tr.last_wrist + tr.velocity * dt
            scale = max(tr.last_size, 1.0)
            for j, res in enumerate(results):
                cost[i, j] = float(np.linalg.norm(res.image[H.WRIST, 0:2] - predicted)) / scale
                if i != j:
                    cost[i, j] += self.cfg.assignment_bias
        return [(tracks[i], results[j]) for i, j in assignment.solve(cost)]

    def _without_duplicates(self, reads):
        """Drop the younger of two reads that landed on the same hand (two crops converged). The dropped track is
        lost like any other: its hand is found again by the palm detector, which is why a palm is judged against the
        tracked PALM (see _covered_by_live_track) and not against the whole crop, and takes the old id back (_reacquire)
        because the track keeps the wrist and the side it had before the crops merged."""
        kept = []
        for tr, res in sorted(reads, key=lambda p: p[0].born):
            wrist = res.image[H.WRIST, 0:2]
            twin = False
            for other, other_res in kept:
                gap = float(np.linalg.norm(wrist - other_res.image[H.WRIST, 0:2]))
                if gap < self.cfg.duplicate_fraction * max(res.roi.size, other_res.roi.size):
                    twin = True
                    break
            if twin:
                tr.roi = None
                tr.missed += 1
                tr.lost_at = tr.last_time
            else:
                kept.append((tr, res))
        return kept

    def _plausible(self, res):
        """False for a result that cannot be a hand: not finite, or a crop too small to mean anything."""
        if not np.all(np.isfinite(res.image)) or not np.all(np.isfinite(res.world)):
            return False
        return res.roi.size >= self.cfg.min_roi_px

    def _detect(self, frame, now, freq, updated):
        self.detector_runs += 1
        palms = self.palms.detect(frame)
        for palm in sorted(palms, key=lambda p: -p.score):
            if sum(1 for t in self._tracks if t.roi is not None) >= self.cfg.max_hands:
                break
            roi = H.roi_from_palm(palm)
            if roi.size < self.cfg.min_roi_px:
                continue
            if self._covered_by_live_track(palm):
                continue
            self.landmark_runs += 1
            res = self.net.infer(frame, roi)
            if res.conf < self.cfg.accept_conf or not self._plausible(res):
                continue
            wrist = res.image[H.WRIST, 0:2]
            if self._is_duplicate_of_live(wrist, res.roi.size):
                continue
            tr = self._reacquire(wrist, res.handed, now)
            if tr is None:
                tr = _Track(self._next_id, now, self.cfg)
                self._next_id += 1
                self._tracks.append(tr)
            self._apply(tr, res, now, freq)
            updated.add(tr.id)

    def _covered_by_live_track(self, palm):
        """True when this palm detection is a hand that is already being tracked: its box overlaps the tracked hand's
        palm box. Compared against the PALM, not the crop - the crop is two hand-lengths wide, so a second hand held
        beside the first lies inside it and would never be found."""
        for t in self._tracks:
            if t.roi is None or t.palm_box is None:
                continue
            if H.iou_xyxy(palm.box, t.palm_box) > self.cfg.palm_overlap:
                return True
        return False

    def _is_duplicate_of_live(self, wrist, size):
        for t in self._tracks:
            if t.roi is None or t.last_wrist is None:
                continue
            if float(np.linalg.norm(wrist - t.last_wrist)) < self.cfg.duplicate_fraction * max(size, t.last_size):
                return True
        return False

    def _reacquire(self, wrist, handed, now):
        """The lost track this wrist plausibly belongs to, or None. Nearest wins; a track that last saw the OTHER side
        pays a penalty of a whole radius, so two hands lost together are not swapped when they come back."""
        best = None
        best_cost = None
        for t in self._tracks:
            if t.roi is not None or t.lost_at is None or t.last_wrist is None:
                continue
            if now - t.lost_at > self.cfg.reacquire_s:
                continue
            radius = self.cfg.reacquire_radius * t.last_size
            d = float(np.linalg.norm(wrist - t.last_wrist))
            if d > radius:
                continue
            cost = d / radius
            if t.handed is not None and (t.handed > 0.5) != (handed > 0.5):
                cost += 1.0
            if best_cost is None or cost < best_cost:
                best, best_cost = t, cost
        return best

    def _apply(self, tr, res, now, freq):
        """Fold a read hand into its track: filters, placement, fingers, side, next crop."""
        cfg = self.cfg
        if tr.lost_at is not None and now - tr.lost_at > cfg.reset_gap_s:
            tr.reset_filters()
        tr.lost_at = None
        tr.missed = 0
        tr.last_seen = now
        wrist = res.image[H.WRIST, 0:2].copy()
        if tr.last_wrist is not None and now > tr.last_time and not tr.has_filtered_gap(now, cfg):
            instant = (wrist - tr.last_wrist) / (now - tr.last_time)
            tr.velocity = 0.5 * tr.velocity + 0.5 * instant
        else:
            tr.velocity = np.zeros(2)
        tr.last_wrist = wrist
        tr.last_time = now
        palm_pts = res.image[PALM_LANDMARKS, 0:2]
        tr.palm_box = np.array([palm_pts[:, 0].min(), palm_pts[:, 1].min(), palm_pts[:, 0].max(), palm_pts[:, 1].max()])
        tr.last_size = res.roi.size
        tr.roi = H.roi_from_landmarks(res.image[:, 0:2])

        w, h = self._size
        norm = np.empty((21, 3), dtype=np.float64)
        norm[:, 0] = res.image[:, 0] / w
        norm[:, 1] = res.image[:, 1] / h
        norm[:, 2] = res.image[:, 2] / w
        world = res.world.copy()
        if cfg.smooth:
            norm = tr.image_filter.filter(norm.reshape(-1), freq).reshape(21, 3)
            world = tr.world_filter.filter(world.reshape(-1), freq).reshape(21, 3)
            tr.has_filtered = True

        # 3D placement is fitted on the RAW landmarks of this frame, then smoothed as a position: the world
        # landmarks it uses are already smooth in shape, and the fit must see the pixels the network produced.
        placement = G.place_hand(res.image[:, 0:2], res.world, self.camera, cfg.hand_scale)
        position = None
        reason = placement.reason
        if placement.ok:
            if cfg.smooth:
                xy = tr.xy_filter.filter(placement.position[0:2] / placement.position[2], freq)
                z = tr.depth_filter.filter(np.array([math.log(placement.position[2])]), freq)[0]
                zz = math.exp(z)
                position = np.array([xy[0] * zz, xy[1] * zz, zz])
            else:
                position = placement.position.copy()

        # Handedness: an average of the network's score, and the label from the average.
        a = cfg.handed_smoothing
        tr.handed = res.handed if tr.handed is None else (1.0 - a) * tr.handed + a * res.handed
        right = tr.handed > 0.5
        if cfg.swap_sides:
            right = not right

        fingers = GE.read_fingers(world, tr.previous_extended)
        tr.previous_extended = list(fingers.extended)
        fingers.gesture = tr.stabilizer.update(fingers.gesture if res.conf >= cfg.gesture_min_conf else GE.NONE)

        hand = tr.hand
        hand.side = SIDE_RIGHT if right else SIDE_LEFT
        hand.handed = tr.handed
        hand.conf = res.conf
        hand.state = TRACKED
        hand.image = norm
        hand.world = world
        hand.position = position
        hand.reason = reason
        hand.placement_residual = placement.residual
        hand.fingers = fingers

    def _report(self, updated):
        hands = []
        for tr in sorted(self._tracks, key=lambda t: t.born):
            if tr.hand.image is None:
                continue
            if tr.id in updated:
                tr.hand.state = TRACKED
            elif tr.missed >= 1 and tr.missed <= self.cfg.hold_frames and tr.roi is None:
                tr.hand.state = HELD
            else:
                continue
            hands.append(tr.hand)
        # max_hands is a promise to the receiver (it sizes its slots by it): a HELD hand gives way to a tracked one.
        while len(hands) > self.cfg.max_hands:
            held = [h for h in hands if h.state == HELD]
            hands.remove(held[0] if held else hands[-1])
        return hands
