#!/usr/bin/env python3
"""H-01 - WHERE IS THE HAND IN METRES, FROM ONE CAMERA.

A webcam or a phone has no stereo, so there is no MEASURED depth. What the hand network does give is a set of
WORLD landmarks: the hand's shape in metres, hand-centred, in axes parallel to the image's. Their size is the
network's idea of an average adult hand. Compare that metric shape with how big the same points are in the picture
and the distance follows from the pinhole model:

    pixel_i  =  c  +  s * world_i.xy            c: where the hand's centre lands in the picture (pixels)
                                                s: pixels per metre AT THE HAND'S DEPTH
    Z        =  focal_px / s                    metres from the camera
    X, Y     =  (c - principal_point) * Z / focal_px

`s` and `c` are the least-squares fit of that line through all 21 points, so a hand that is tilted away (foreshortened)
is not mistaken for a hand that is far away: the world landmarks already contain the tilt.

WHAT THIS IS NOT. It is not measured. It assumes the hand is the size of the network's average hand, so a hand
10 % larger reads 10 % too near. `hand_scale` is the correction for a known hand (1.0 = average). It also needs the
camera's field of view, which a webcam or a phone does not report: `Camera.from_hfov` takes it as a number, exactly as
the video sender's --hfov does. The accuracy against a ruler has not been measured on this machine (H-01 NOT TESTED);
the fit's own consistency is tested with synthetic projections in tests/test_hand_geometry.py.

NOT HERE: the networks, tracking, filtering or the wire. Pure numpy, no camera.
"""

import math

import numpy as np

#: A fit whose RMS residual exceeds this fraction of the hand's pixel extent is not a hand that agrees with its own
#: world landmarks (a bad crop, a false positive). The depth it gives is refused rather than reported.
MAX_RESIDUAL_FRACTION = 0.12

#: Metres. A hand nearer than this or further than this is outside what a webcam hand model can say anything about.
MIN_DEPTH_M = 0.10
MAX_DEPTH_M = 3.00


class Camera(object):
    """A pinhole camera: focal length and principal point in pixels, for a frame of width x height."""
    __slots__ = ("width", "height", "focal_px", "cx", "cy")

    def __init__(self, width, height, focal_px, cx=None, cy=None):
        if width <= 0 or height <= 0 or focal_px <= 0:
            raise ValueError("camera needs a positive size and focal length")
        self.width = int(width)
        self.height = int(height)
        self.focal_px = float(focal_px)
        self.cx = width / 2.0 if cx is None else float(cx)
        self.cy = height / 2.0 if cy is None else float(cy)

    @classmethod
    def from_hfov(cls, width, height, hfov_degrees, axis="width"):
        """The camera whose field of view across the WIDTH (or the longer side, axis='long') is `hfov_degrees`.

        'long' is what keeps one phone one focal length in portrait and in landscape (the video sender's
        --hfov-axis long).
        """
        if not 5.0 <= hfov_degrees <= 175.0:
            raise ValueError("field of view must be between 5 and 175 degrees, got %s" % hfov_degrees)
        span = max(width, height) if axis == "long" else width
        return cls(width, height, (span / 2.0) / math.tan(math.radians(hfov_degrees) / 2.0))

    def project(self, points):
        """Camera-space metres (n, 3) -> pixels (n, 2)."""
        p = np.asarray(points, dtype=np.float64)
        return np.stack([self.focal_px * p[:, 0] / p[:, 2] + self.cx,
                         self.focal_px * p[:, 1] / p[:, 2] + self.cy], axis=1)


class HandPlacement(object):
    """Where one hand is: `position` is the hand's centre in camera space (X right, Y down, Z forward), metres;
    `scale` is pixels per metre at that depth; `residual` the fit's RMS error in pixels; `ok` False when the fit was
    refused (`reason` says why) and `position` must not be used."""
    __slots__ = ("position", "scale", "residual", "ok", "reason")

    def __init__(self, position, scale, residual, ok, reason=""):
        self.position = position
        self.scale = scale
        self.residual = residual
        self.ok = ok
        self.reason = reason


def place_hand(image_xy, world, camera, hand_scale=1.0):
    """Fit the hand's world landmarks to its pixels and return a HandPlacement.

    image_xy : (21, 2) landmark pixels in the SOURCE frame
    world    : (21, 3) metres, hand-centred, axes parallel to the image's (only x and y enter the fit)
    hand_scale : the user's hand relative to the network's average hand (1.0). A hand twice as big as average would
                 be 2.0; its world landmarks are multiplied by it before the fit.
    """
    px = np.asarray(image_xy, dtype=np.float64)
    w = np.asarray(world, dtype=np.float64)[:, 0:2] * float(hand_scale)
    if px.shape != w.shape or px.shape[0] < 3:
        return HandPlacement(None, 0.0, 0.0, False, "shape")
    wm = w.mean(axis=0)
    pm = px.mean(axis=0)
    wc = w - wm
    denominator = float((wc * wc).sum())
    if denominator < 1e-9:
        return HandPlacement(None, 0.0, 0.0, False, "degenerate")
    s = float((wc * (px - pm)).sum()) / denominator
    if s <= 0.0:
        return HandPlacement(None, s, 0.0, False, "scale")
    centre_px = pm - s * wm
    fitted = centre_px + s * w
    residual = float(math.sqrt(((fitted - px) ** 2).sum(axis=1).mean()))
    extent = float(np.ptp(px, axis=0).max())
    if extent <= 0.0 or residual > MAX_RESIDUAL_FRACTION * extent:
        return HandPlacement(None, s, residual, False, "residual")
    z = camera.focal_px / s
    if not MIN_DEPTH_M <= z <= MAX_DEPTH_M:
        return HandPlacement(None, s, residual, False, "range")
    x = (centre_px[0] - camera.cx) * z / camera.focal_px
    y = (centre_px[1] - camera.cy) * z / camera.focal_px
    return HandPlacement(np.array([x, y, z]), s, residual, True)


def camera_space_points(world, position):
    """The 21 landmarks in camera space: the hand's centre plus each world offset. Perspective across the hand's
    own depth (a few centimetres) is ignored, which is far below the depth estimate's own uncertainty."""
    return np.asarray(world, dtype=np.float64) + np.asarray(position, dtype=np.float64)
