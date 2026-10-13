#!/usr/bin/env python3
"""H-01 / H-02 - THE GEOMETRY EVERY MEDIAPIPE-STYLE LANDMARK NETWORK SHARES: rotated crops, SSD anchors, NMS.

The hand pipeline (hand_landmarker.py) and the face pipeline (face_landmarker.py) both work the same way: a detector
finds the thing in the whole picture, a ROTATED square crop is cut around it, a landmark network reads the crop, and the
landmarks are mapped back to the picture. The maths for that is identical, so it lives here once.

A crop is described by a Roi (cx, cy, size, theta): its centre, its side length (pixels) and the angle at which it is turned
so that the subject stands upright inside it. With u = (cos theta, sin theta) and v = (-sin theta, cos theta) the crop's own
right and down axes expressed in the source image, crop point (x, y) in [0, 1]^2 is the source point
    c + (x - 0.5) * size * u + (y - 0.5) * size * v.
theta = 0 is a crop that is not turned. (This is MediaPipe's rotated-rect convention, written out in tests/test_hand_landmarker.py.)

Everything is in the SOURCE image's pixels (x right, y down). No camera, no model, no state: tested without either.
"""

import math

import cv2
import numpy as np


def sigmoid(x):
    """Logistic function, safe for large magnitudes."""
    x = np.clip(np.asarray(x, dtype=np.float64), -100.0, 100.0)
    return 1.0 / (1.0 + np.exp(-x))


def normalize_angle(a):
    """`a` radians wrapped to [-pi, pi)."""
    return a - 2.0 * math.pi * math.floor((a + math.pi) / (2.0 * math.pi))


def iou_xyxy(a, b):
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0.0 else 0.0


def nms(boxes, scores, iou_threshold):
    """Indices kept by greedy score-ordered non-maximum suppression. `boxes` is (n, 4) [x1, y1, x2, y2]."""
    order = list(np.argsort(-scores))
    keep = []
    while order:
        i = order.pop(0)
        keep.append(int(i))
        order = [j for j in order if iou_xyxy(boxes[i], boxes[j]) <= iou_threshold]
    return keep


def make_ssd_anchors(input_size, strides, anchors_per_layer=2):
    """The (n, 2) array of anchor centres, normalised to the network's square input, in the order it emits them.

    MediaPipe's SSD anchors have a FIXED width and height of 1.0, so only the centres matter: the box the network returns
    is an offset from the centre, in input pixels. Cell order is row-major (y then x) and the anchors of one cell are
    adjacent. Consecutive layers with the same stride are ONE grid with more anchors per cell. The palm detector
    (192, strides 8/16/16/16) has 2016 anchors, the face detector (128, same strides) 896.
    """
    anchors = []
    layer = 0
    while layer < len(strides):
        stride = strides[layer]
        per_cell = 0
        last = layer
        while last < len(strides) and strides[last] == stride:
            per_cell += anchors_per_layer
            last += 1
        cells = int(math.ceil(input_size / float(stride)))
        for y in range(cells):
            for x in range(cells):
                for _ in range(per_cell):
                    anchors.append(((x + 0.5) / cells, (y + 0.5) / cells))
        layer = last
    return np.asarray(anchors, dtype=np.float32)


def letterbox(frame, size):
    """The BGR frame fitted into a size x size square with its aspect ratio kept, centred, padded with black.

    Returns (rgb uint8 canvas, ratio, pad_left, pad_top): a source pixel (x, y) is at (x * ratio + pad_left, y * ratio +
    pad_top) in the canvas. Plain bilinear / area resampling, which is what MediaPipe's own tensor conversion does.
    """
    h, w = frame.shape[:2]
    ratio = size / float(max(h, w))
    nw = max(1, int(round(w * ratio)))
    nh = max(1, int(round(h * ratio)))
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_LINEAR)
    pad_left = (size - nw) // 2
    pad_top = (size - nh) // 2
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    canvas[pad_top:pad_top + nh, pad_left:pad_left + nw] = small
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), ratio, pad_left, pad_top


class Roi(object):
    """A rotated square crop of the source image: centre, side length (pixels) and turn (radians)."""
    __slots__ = ("cx", "cy", "size", "theta")

    def __init__(self, cx, cy, size, theta):
        self.cx = float(cx)
        self.cy = float(cy)
        self.size = float(size)
        self.theta = float(theta)

    def axes(self):
        """(u, v): the crop's right and down axes expressed in the source image."""
        c = math.cos(self.theta)
        s = math.sin(self.theta)
        return np.array([c, s]), np.array([-s, c])

    def corners(self):
        """The four corners in the source image, clockwise from the crop's top-left."""
        u, v = self.axes()
        c = np.array([self.cx, self.cy])
        h = self.size / 2.0
        return np.array([c - h * u - h * v, c + h * u - h * v, c + h * u + h * v, c - h * u + h * v])

    def crop_to_source(self, xy):
        """Crop coordinates in [0, 1]^2 (an (n, 2) array) -> source-image pixels."""
        u, v = self.axes()
        xy = np.asarray(xy, dtype=np.float64)
        return (np.array([self.cx, self.cy])
                + (xy[:, 0:1] - 0.5) * self.size * u
                + (xy[:, 1:2] - 0.5) * self.size * v)

    def source_to_crop(self, pts):
        """Source-image pixels (an (n, 2) array) -> crop coordinates in [0, 1]^2. The inverse of crop_to_source."""
        u, v = self.axes()
        d = np.asarray(pts, dtype=np.float64) - np.array([self.cx, self.cy])
        return np.stack([d.dot(u) / self.size + 0.5, d.dot(v) / self.size + 0.5], axis=1)


def roi_warp_matrix(roi, out_size):
    """The 2x3 matrix for cv2.warpAffine(..., flags=WARP_INVERSE_MAP) that cuts `roi` out as an out_size square.

    Destination pixel i has its centre at crop coordinate (i + 0.5) / out_size; OpenCV puts a pixel's centre at the
    integer coordinate, so the source side is shifted by half a pixel.
    """
    u, v = roi.axes()
    k = roi.size / float(out_size)
    m = np.zeros((2, 3), dtype=np.float64)
    m[:, 0] = u * k
    m[:, 1] = v * k
    half = out_size / 2.0
    m[:, 2] = np.array([roi.cx, roi.cy]) - 0.5 + (0.5 - half) * k * u + (0.5 - half) * k * v
    return m


def cut_roi(frame, roi, out_size):
    """`roi` of `frame` as an out_size x out_size RGB float32 NHWC tensor in [0, 1], ready for a network."""
    patch = cv2.warpAffine(frame, roi_warp_matrix(roi, out_size), (out_size, out_size),
                           flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
    return (rgb.astype(np.float32) / 255.0)[np.newaxis, :, :, :]


def shifted_square_roi(cx, cy, width, height, theta, scale, shift_y):
    """MediaPipe's rect transformation: shift along the rect's own (turned) y axis by `shift_y` of its height,
    make it a square of its longer side, and scale it. `width` and `height` are the box BEFORE squaring."""
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    # The shift is in the turned frame: moving "up" (shift_y < 0) moves along -v.
    cx += -height * shift_y * sin_t
    cy += height * shift_y * cos_t
    return Roi(cx, cy, max(width, height) * scale, theta)
