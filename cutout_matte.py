#!/usr/bin/env python3
"""S-01 - THE MATTE: turns the segmenter's raw probability canvas into the alpha that is sent to Unity.

    probability canvas (float, 0..1) --guided filter--> --temporal--> --ramp--> --cleanup--> alpha (uint8, 0..255)

The segmenter's probabilities are a good ANSWER and a poor MASK: its edge is a soft grey band a few pixels wide that does not follow the
picture's own edges, the band flickers from frame to frame, and the answer carries the odd speck of background and pinhole in a person.
Each stage here removes one of those and nothing else, and each has an off switch so it can be A/B'd against the stage before it.

  GUIDED FILTER (He et al.): the probabilities are re-fitted, window by window, as a linear function of the picture's own brightness, so
      the edge snaps to a real edge in the picture instead of staying where the 256 px network put it. Edge-preserving; it is the reason a
      640 px mask on a 1280 px picture still has a clean outline.
  TEMPORAL: per-pixel exponential smoothing whose rate rises with how far the new value is from the old one. Noise (small differences)
      is averaged away; a real movement (large differences) is followed at once, so an arm swung quickly does not leave a ghost.
  RAMP: probability -> alpha. Below `edge_low` is background, above `edge_high` is person, in between is a soft edge. Raising both moves
      the cut inward, which is what removes the thin halo of background colour around a cutout.
  CLEANUP: (1) any foreground group smaller than `min_blob_frac` of the picture is dropped UNLESS it is within `near_frac` of the picture
      of a bigger one, so a speck in the room goes but a cap, a hand or a foot that the network detached from its body stays; (2) a
      background pinhole entirely inside the foreground and smaller than `hole_frac` of the foreground is filled, so noise does not
      punch holes in a shirt but the gap between an arm and the torso (a real hole, several times larger) stays open.

PURE: numpy and OpenCV only; no network, no camera, no clock. The state is the previous smoothed canvas, nothing else.
"""

import cv2
import numpy as np

#: An alpha at or above this counts as solidly person when looking for enclosed gaps (the ramp reaches 1.0 at edge_high).
CORE_ALPHA = 0.9


class MatteConfig:
    """Every knob, with the reason for its default. They were set on the two test clips (a lone dancer, two dancers) by eye and by the
    flicker count in tools/cutout/cutout_eval.py; they are starting points, not measured optima (S-01 report s.5)."""

    __slots__ = ("guided", "guided_radius_frac", "guided_subsample", "guided_eps", "temporal", "base_rate", "rate_gain", "edge_low",
                 "edge_high", "cleanup", "min_blob_frac", "near_frac", "hole_frac", "solid_inset_frac", "solid_low")

    def __init__(self, guided=True, guided_radius_frac=0.008, guided_eps=2e-3, temporal=True, base_rate=0.30, rate_gain=2.5,
                 edge_low=0.50, edge_high=0.85, cleanup=True, min_blob_frac=0.0010, near_frac=0.045, hole_frac=0.008,
                 guided_subsample=2, solid_inset_frac=0.011, solid_low=0.50):
        #: SOLID INTERIOR: a pixel at least this fraction of the long side (0.007 = 4.5 px at 640) inside the region where the probability
        #: exceeds `solid_low` is opaque, whatever its own probability. The tight edge ramp (low 0.50, high 0.85) is what removes the
        #: halo of background colour at the outline, but it also turns the network's uncertain interior (dark trousers, a patterned
        #: dress: probability 0.5-0.7) see-through. The outline band keeps the tight ramp; the inside stops depending on it. 0 = off.
        self.solid_inset_frac = solid_inset_frac
        self.solid_low = solid_low
        #: Guided-filter window radius as a fraction of the canvas's long side (0.008 = 5 px at 640). Larger blurs the edge band into
        #: more of the picture; smaller leaves more of the network's own soft edge.
        self.guided = guided
        self.guided_radius_frac = guided_radius_frac
        #: The fast guided filter's picture reduction (see guided_filter): 2 costs 45% of the full filter for a 0.005 difference.
        self.guided_subsample = guided_subsample
        #: Edge-preservation: a picture edge steeper than sqrt(eps) in brightness (0..1) is kept. 2e-3 is ~0.045 of full scale.
        self.guided_eps = guided_eps
        self.temporal = temporal
        #: Smoothing rate for a pixel that did not change (0.30: a noisy pixel keeps ~30% of each new value) and how much faster it gets
        #: per unit of change (a jump of 0.28 or more is followed completely).
        self.base_rate = base_rate
        self.rate_gain = rate_gain
        self.edge_low = edge_low
        self.edge_high = edge_high
        self.cleanup = cleanup
        #: Smallest foreground group kept on its own, as a fraction of the canvas area (0.0010 = 230 px of 640 x 360: a hand at arm's
        #: length is bigger, a speck on a curtain is not).
        self.min_blob_frac = min_blob_frac
        #: A group within this fraction of the canvas's long side of a kept group is part of it (0.045 = 29 px at 640: a cap lifted off a
        #: head, a hand away from a wrist).
        self.near_frac = near_frac
        #: A pinhole fills when it is smaller than this fraction of the foreground area.
        self.hole_frac = hole_frac


def guided_filter(guide, src, radius, eps, subsample=1):
    """He, Sun and Tang's guided filter with a single-channel guide. `guide` and `src` are float32 [H, W]; the result is `src`
    re-expressed as a local linear function of `guide`, so it follows `guide`'s edges. Output may leave 0..1; the caller clips.

    `subsample` > 1 is the FAST guided filter (He and Sun 2015): the linear coefficients are fitted on a picture `subsample` times smaller
    and stretched back, which costs 1/subsample^2 of the work. Measured on a 640x360 canvas: 4.2 ms at 1, 1.9 ms at 2, 1.2 ms at 3. On a
    soft canvas (blobs and noise) the result differs from the full filter by at most 0.005 in probability at 2 (0.008 at 3); on a hard-edged
    rectangle the worst edge pixel differs by ~0.04 and the mean by under 0.005 (tests/test_cutout_matte.py)."""
    h, w = src.shape[:2]
    if subsample > 1 and min(h, w) // subsample >= 8:
        gi = cv2.resize(guide, (w // subsample, h // subsample), interpolation=cv2.INTER_AREA)
        pi = cv2.resize(src, (w // subsample, h // subsample), interpolation=cv2.INTER_AREA)
        radius = max(1, radius // subsample)
    else:
        subsample = 1
        gi, pi = guide, src
    k = (2 * radius + 1, 2 * radius + 1)

    def box(x):
        return cv2.boxFilter(x, cv2.CV_32F, k, normalize=True, borderType=cv2.BORDER_REPLICATE)

    mean_i = box(gi)
    mean_p = box(pi)
    cov_ip = box(gi * pi) - mean_i * mean_p
    var_i = box(gi * gi) - mean_i * mean_i
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    mean_a = box(a)
    mean_b = box(b)
    if subsample > 1:
        mean_a = cv2.resize(mean_a, (w, h), interpolation=cv2.INTER_LINEAR)
        mean_b = cv2.resize(mean_b, (w, h), interpolation=cv2.INTER_LINEAR)
    return mean_a * guide + mean_b


def ramp(p, low, high):
    """Probability -> alpha: 0 at or below `low`, 1 at or above `high`, linear between."""
    return np.clip((p - low) / max(1e-6, high - low), 0.0, 1.0)


class MatteRefiner:
    """Probability canvases in, alpha masks out, one canvas per frame. Holds the previous smoothed canvas, nothing else."""

    def __init__(self, cfg=None):
        self.cfg = cfg or MatteConfig()
        self._prev = None
        #: Last frame's numbers, read by the heartbeat. DIAG-ONLY: nothing here feeds a decision.
        self.last_people = 0
        self.last_fg_permille = 0

    def reset(self):
        """A new scene (a clip replayed from its start, a changed source): the old canvas must not bleed into it."""
        self._prev = None
        self.last_people = 0
        self.last_fg_permille = 0

    def update(self, prob, guide=None):
        """One frame. `prob` float32 [H, W] (0..1); `guide` float32 [H, W] (0..1 brightness of the same picture) or None to skip the
        guided filter. Returns alpha uint8 [H, W]; .last_people and .last_fg_permille describe it."""
        cfg = self.cfg
        h, w = prob.shape[:2]
        p = prob
        if cfg.guided and guide is not None and float(p.max()) >= 0.02:
            radius = max(1, int(round(cfg.guided_radius_frac * max(h, w))))
            p = np.clip(guided_filter(guide, p, radius, cfg.guided_eps, cfg.guided_subsample), 0.0, 1.0)
        if cfg.temporal:
            if self._prev is None or self._prev.shape != p.shape:
                self._prev = p.copy()
            else:
                rate = np.clip(cfg.base_rate + cfg.rate_gain * np.abs(p - self._prev), 0.0, 1.0)
                self._prev = self._prev + rate * (p - self._prev)
            p = self._prev
        alpha = ramp(p, cfg.edge_low, cfg.edge_high)
        if cfg.solid_inset_frac > 0 and float(alpha.max()) > 0.0:
            inset = max(1, int(round(cfg.solid_inset_frac * max(h, w))))
            region = (p > cfg.solid_low).astype(np.uint8)
            deep = cv2.distanceTransform(region, cv2.DIST_L2, 3) >= inset
            alpha = np.where(deep, np.float32(1.0), alpha).astype(np.float32)
        people = 0
        if cfg.cleanup:
            alpha, people = self._cleanup(alpha)
        else:
            people = 1 if float(alpha.max()) > 0.5 else 0
        self.last_people = people
        out = cv2.convertScaleAbs(alpha, alpha=255.0)
        self.last_fg_permille = int(round(1000.0 * cv2.countNonZero(cv2.threshold(out, 127, 255, cv2.THRESH_BINARY)[1]) / out.size))
        return out

    def _cleanup(self, alpha):
        """Drop far-away specks, fill pinholes. Returns (alpha, people): the number of foreground groups kept."""
        cfg = self.cfg
        h, w = alpha.shape[:2]
        fg = (alpha > 0.5).astype(np.uint8)
        if not fg.any():
            return alpha, 0

        # Group at a quarter of the canvas's size: the decision is about blobs, not pixels, and this is ~10x cheaper than at full size.
        shrink = 4
        sw, sh = max(1, w // shrink), max(1, h // shrink)
        small = (cv2.resize(fg * 255, (sw, sh), interpolation=cv2.INTER_AREA) > 40).astype(np.uint8)
        near = max(1, int(round(cfg.near_frac * max(sw, sh))))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near + 1, 2 * near + 1))
        grouped = cv2.dilate(small, kernel)
        count, labels = cv2.connectedComponents(grouped, connectivity=8, ltype=cv2.CV_16U)
        areas = np.bincount(labels[small > 0], minlength=count)
        keep_label = areas >= max(1.0, cfg.min_blob_frac * sw * sh)
        keep_label[0] = False
        people = int(keep_label.sum())
        if people == 0:
            return np.zeros_like(alpha), 0
        if not keep_label[1:].all():
            keep = cv2.resize(keep_label[labels].astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)
            alpha = alpha * keep
            fg = (alpha > 0.5).astype(np.uint8)

        # Pinholes: anything that is not solidly person (alpha below CORE_ALPHA, so a see-through patch counts as well as a true hole),
        # cannot be reached from the picture's border and is tiny next to the foreground. A flood fill from a padded border marks
        # everything that CAN be reached; what is left is enclosed. (Measured 3x cheaper than labelling every group at full size.)
        core = (alpha >= CORE_ALPHA).astype(np.uint8)
        padded = cv2.copyMakeBorder((1 - core) * 255, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
        cv2.floodFill(padded, np.zeros((h + 4, w + 4), dtype=np.uint8), (0, 0), 128)
        enclosed = (padded[1:-1, 1:-1] == 255).astype(np.uint8)
        enclosed_px = cv2.countNonZero(enclosed)
        if enclosed_px > 0:
            limit = max(4.0, cfg.hole_frac * float(cv2.countNonZero(fg)))
            if enclosed_px <= limit:
                fill = enclosed.astype(bool)
            else:
                # Several enclosed regions: fill only the small ones. A gap between an arm and the torso is one of the big ones.
                count_h, labels_h, stats_h, _centroids = cv2.connectedComponentsWithStats(enclosed, connectivity=4)
                small_hole = stats_h[:, cv2.CC_STAT_AREA] <= limit
                small_hole[0] = False
                fill = small_hole[labels_h]
            if fill.any():
                alpha = np.where(fill, np.float32(1.0), alpha).astype(np.float32)
        return alpha, people
