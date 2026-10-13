#!/usr/bin/env python3
"""S-01 - THE CUTOUT ENGINE: one camera picture in, one person mask out. No sockets, no camera, no Unity.

    frame ──► person detector ──► PersonTracker ──► one square crop per person ──► selfie network ──► canvas ──► matte ──► alpha
                  (injected)       stable ids,         (cutout_segmenter)                        (max-merge)  (cutout_matte)
                                   held through
                                   dropouts

WHY THE DETECTOR. The selfie network sees 256 pixels across whatever it is handed, so a person who is a quarter of the picture reaches it
~65 pixels tall and, with a second person beside them, comes back as one blob with the background attached. Handing it a SQUARE CROP of
each detected person (they are then ~256 pixels tall) gives two clean silhouettes instead (cutout_segmenter.py). The boxes come from the
project's YOLOX-s (tools/video/host_person_detector.py, F-52), injected here as any object with `detect(frame) -> [Detection]`, and are
followed by the F-32 PersonTracker so a person the detector misses for a frame or two keeps their crop (held up to 1.2 s) instead of
flickering out, and a one-frame false positive never starts one (three detections confirm a person).

WHEN THERE IS NO DETECTOR, or it sees nobody, the engine can fall back to the WHOLE frame through the landscape network (16:9) or the square
one. That is what a laptop webcam with a face filling the picture needs, and it is the network's home ground; it is also the lower quality
path for anybody far from the camera, so the result says which one made it (`mode`).

STATE: the tracker, a smoothed crop per person (a detector's boxes jitter by a few percent, and a jittering crop jitters the mask), and the
matte's previous canvas. reset() clears all of it; a source that restarts or a clip that loops must call it.
"""

import time

import cv2
import numpy as np

import cutout_matte as M
import cutout_segmenter as S
import person_tracker as PT

#: Which path made a frame's mask.
MODE_ROI = "roi"
MODE_WHOLE = "whole"
MODE_NONE = "none"

#: How strongly a person's crop follows the newest box (0.5: half old, half new). A box that jumps by more than JUMP_FRACTION of its side
#: is a different person or a detector fault, and is taken as it is.
ROI_FOLLOW = 0.5
JUMP_FRACTION = 0.35

#: Crops older than this many seconds without being asked for are forgotten.
ROI_IDLE_SECONDS = 3.0

#: The detector's own input is 640 px on the long side; a shrunken picture at least that long is handed to it instead of the full frame.
DETECTOR_MIN_LONG_SIDE = 640


class CutoutConfig:
    """Everything the engine decides, with the reason for its default."""

    __slots__ = ("mask_long_side", "roi", "roi_margin", "max_people", "detect_every", "fallback", "feather", "matte", "tta", "isolate")

    def __init__(self, mask_long_side=640, roi=True, roi_margin=0.18, max_people=4, detect_every=1, fallback=True,
                 feather=S.DEFAULT_FEATHER, matte=None, tta=False, isolate=True):
        #: Paint every OTHER person's box with the background around it before a person's crop reaches the network (cutout_segmenter
        #: .blank_neighbours). A tall box makes a wide square crop, so the neighbour is usually in it; asked about a pair, the network
        #: joins them with whatever dark background lies between. Only does anything when two or more people are tracked.
        self.isolate = isolate
        #: Average each answer with the answer on the mirrored picture (cutout_segmenter.infer flip_average): a cleaner mask for ~2 ms
        #: more per person.
        self.tta = tta
        #: The long side of the mask the engine produces, pixels. 640 keeps the guided filter and the cleanup well under 3 ms; the picture
        #: it is stretched over is usually twice that.
        self.mask_long_side = mask_long_side
        #: Cut a crop per detected person (True) or run the network on the whole frame only (False).
        self.roi = roi
        self.roi_margin = roi_margin
        #: The most people segmented per frame; each is one ~3 ms network call, the tracker orders them most-established first.
        self.max_people = max(1, int(max_people))
        #: Run the detector every N frames and reuse its crops in between (1 = every frame, ~8 ms each).
        self.detect_every = max(1, int(detect_every))
        self.fallback = fallback
        self.feather = feather
        self.matte = matte or M.MatteConfig()


class CutoutResult:
    """What one frame produced. len() is the number of people, which is what the shared sender loop counts as 'a frame with a result'."""

    __slots__ = ("alpha", "people", "fg_permille", "mode", "model_id", "boxes", "infer_ms")

    def __init__(self, alpha, people, fg_permille, mode, model_id, boxes, infer_ms):
        #: uint8 [Hm, Wm], 255 = person, at the working size (the long side is CutoutConfig.mask_long_side at most).
        self.alpha = alpha
        self.people = people
        self.fg_permille = fg_permille
        self.mode = mode
        self.model_id = model_id
        #: The crops used this frame, frame pixels (x0, y0, side), for the demo window.
        self.boxes = boxes
        self.infer_ms = infer_ms

    def __len__(self):
        return self.people


class CutoutEngine:
    """Owns the networks' use, the tracker and the matte; turns one frame into one CutoutResult. Not thread-safe."""

    def __init__(self, general, landscape, detector, cfg=None):
        self.general = general
        self.landscape = landscape
        self.detector = detector
        self.cfg = cfg or CutoutConfig()
        self.matte = M.MatteRefiner(self.cfg.matte)
        self.tracker = PT.PersonTracker()
        self._rois = {}
        self._rois_seen = {}
        self._cached = []
        #: Every tracked person's box, (x1, y1, x2, y2) in frame pixels, from the same detection as `_cached`. It holds the people the crops
        #: were NOT cut for too (past max_people): they are still somebody else in the picture, and are painted out of the crops that are.
        self._boxes = []
        self._frame_index = 0
        #: Last frame's numbers, read by the heartbeat. DIAG-ONLY: nothing here feeds a decision.
        self.last = {"mode": MODE_NONE, "tracks": 0, "people": 0, "detect_ms": 0.0, "seg_ms": 0.0, "matte_ms": 0.0}

    def reset(self):
        """A clip replayed from its start is a new scene: identities, crops and the matte's memory must not carry over."""
        self.tracker = PT.PersonTracker()
        self.matte.reset()
        self._rois = {}
        self._rois_seen = {}
        self._cached = []
        self._boxes = []
        self._frame_index = 0

    def summary(self):
        """One short fragment for the sender's heartbeat line."""
        return ("people=%d mode=%s tracks=%d detect~%.1fms seg~%.1fms matte~%.1fms fg=%d/1000"
                % (self.last["people"], self.last["mode"], self.last["tracks"], self.last["detect_ms"], self.last["seg_ms"],
                   self.last["matte_ms"], self.matte.last_fg_permille))

    def _crops(self, frame, now, detector_picture):
        """The crops (x0, y0, side), in FRAME pixels, for this frame, most established person first; [] when there is no detector or
        nobody was seen. `detector_picture` is what the detector reads (the frame itself or its shrunken copy: same field of view)."""
        cfg = self.cfg
        if not cfg.roi or self.detector is None:
            return []
        h, w = frame.shape[:2]
        if self._frame_index % cfg.detect_every == 0 or not self._cached:
            t0 = time.perf_counter()
            detections = self.detector.detect(detector_picture)
            self.last["detect_ms"] = (time.perf_counter() - t0) * 1000.0
            tracks = self.tracker.update(detections, now)
            crops = []
            boxes = []
            for index, tr in enumerate(tracks):
                bx1, by1, bx2, by2 = tr.box
                half_w = 0.5 * (bx2 - bx1)
                half_h = 0.5 * (by2 - by1)
                # The CENTRE is the tracker's (a held person coasts); the SIZE is the last box's.
                box = (tr.cx - half_w, tr.cy - half_h, tr.cx + half_w, tr.cy + half_h)
                boxes.append((box[0] * w, box[1] * h, box[2] * w, box[3] * h))
                if index < cfg.max_people:
                    crops.append(self._follow(tr.id, S.square_roi(box, w, h, cfg.roi_margin), now))
            self._release_idle(now)
            self.last["tracks"] = len(tracks)
            self._cached = crops
            self._boxes = boxes
        return self._cached

    def _isolation(self, index, roi):
        """(neighbours, own) for the crop `index`: the other tracked people's boxes and this person's own, each (x1, y1, x2, y2) normalised
        to the crop `roi` (x0, y0, side), for cutout_segmenter.infer. (None, None) when isolation is off or this person is alone."""
        if not self.cfg.isolate or len(self._boxes) < 2 or index >= len(self._boxes):
            return None, None
        x0, y0, side = roi
        inverse = 1.0 / float(side)
        rects = [((bx1 - x0) * inverse, (by1 - y0) * inverse, (bx2 - x0) * inverse, (by2 - y0) * inverse) for (bx1, by1, bx2, by2) in self._boxes]
        own = rects[index]
        return [r for i, r in enumerate(rects) if i != index], own

    def _follow(self, track_id, roi, now):
        """Blend a person's new crop with their last one so the crop does not tremble with the detector's box."""
        x0, y0, s = roi
        prev = self._rois.get(track_id)
        self._rois_seen[track_id] = now
        if prev is not None and abs(s - prev[2]) < JUMP_FRACTION * prev[2] and abs(x0 - prev[0]) < JUMP_FRACTION * prev[2] \
                and abs(y0 - prev[1]) < JUMP_FRACTION * prev[2]:
            f = ROI_FOLLOW
            x0 = prev[0] + f * (x0 - prev[0])
            y0 = prev[1] + f * (y0 - prev[1])
            s = prev[2] + f * (s - prev[2])
        self._rois[track_id] = (x0, y0, s)
        return int(round(x0)), int(round(y0)), max(S.MIN_SIDE, int(round(s)))

    def _release_idle(self, now):
        stale = [i for i, seen in self._rois_seen.items() if now - seen > ROI_IDLE_SECONDS]
        for i in stale:
            self._rois.pop(i, None)
            self._rois_seen.pop(i, None)

    def raw(self, frame, now):
        """Everything before the matte: (canvas, guide, crops, mode, model_id).

        `canvas` is the people's probability map, float32 [Hm, Wm]; `guide` the same picture's brightness, float32 [Hm, Wm] in 0..1.
        Split from update() so a measurement can feed ONE network answer to several matte settings (tools/cutout/cutout_eval.py)."""
        cfg = self.cfg
        h, w = frame.shape[:2]
        wm, hm = S.size_for(w, h, cfg.mask_long_side)
        scale = wm / float(w)
        small = frame if (wm, hm) == (w, h) else cv2.resize(frame, (wm, hm), interpolation=cv2.INTER_AREA)
        guide = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) * (1.0 / 255.0)

        # The detector reads the area-averaged copy when it is big enough for it (640 is its own input size): better anti-aliased than
        # its own bilinear shrink of a 1080p frame, and it skips that shrink.
        detector_picture = small if max(wm, hm) >= DETECTOR_MIN_LONG_SIDE else frame
        crops = self._crops(frame, now, detector_picture)
        canvas = np.zeros((hm, wm), dtype=np.float32)
        model_id = S.MODEL_GENERAL
        t1 = time.perf_counter()
        if crops:
            mode = MODE_ROI
            net_side = self.general.width
            for index, roi in enumerate(crops):
                # Everything the network sees is 256 px, so a crop that is already at least that big in the SHRUNKEN picture loses
                # nothing by being cut from it (a 3 MB full-size copy per person avoided); a smaller one is cut from the full picture.
                scaled = (int(round(roi[0] * scale)), int(round(roi[1] * scale)), max(1, int(round(roi[2] * scale))))
                if scaled[2] >= net_side:
                    crop = S.crop_square(small, scaled)
                else:
                    crop = S.crop_square(frame, roi)
                neighbours, own = self._isolation(index, roi)
                S.paste_max(canvas, self.general.infer(crop, cfg.tta, neighbours, own), scaled, 1.0, cfg.feather)
        elif cfg.fallback:
            mode = MODE_WHOLE
            net = self.general
            if self.landscape is not None and w >= S.LANDSCAPE_MIN_ASPECT * h:
                net = self.landscape
                model_id = S.MODEL_LANDSCAPE
            canvas = cv2.resize(net.infer(frame, cfg.tta), (wm, hm), interpolation=cv2.INTER_LINEAR)
        else:
            mode = MODE_NONE
        self.last["mode"] = mode
        self.last["seg_ms"] = (time.perf_counter() - t1) * 1000.0
        self._frame_index += 1
        return canvas, guide, list(crops), mode, model_id

    def update(self, frame, now):
        """One BGR uint8 frame -> CutoutResult. `now` is a monotonic clock in seconds (the tracker's time)."""
        t0 = time.perf_counter()
        canvas, guide, crops, mode, model_id = self.raw(frame, now)
        t2 = time.perf_counter()
        alpha = self.matte.update(canvas, guide)
        t3 = time.perf_counter()
        self.last["matte_ms"] = (t3 - t2) * 1000.0
        # The matte counts foreground GROUPS, and two people standing within ~10% of the picture's width of each other are one group
        # (measured on the two-dancer clip: 1 group on 808 of 894 frames with both of them tracked on every frame). The number a person
        # expects on a screen is how many people are being cut out, so it is the larger of the two; nobody in the mask is still nobody.
        people = self.matte.last_people
        if people > 0:
            people = max(people, len(crops))
        self.last["people"] = people
        return CutoutResult(alpha, people, self.matte.last_fg_permille, mode, model_id, crops, (t3 - t0) * 1000.0)
