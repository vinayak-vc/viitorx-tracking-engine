#!/usr/bin/env python3
"""F-52 - HOST-SIDE PERSON DETECTOR: YOLOX-s on the PC's GPU, for sources that have no OAK-D.

WHY THIS EXISTS. The multi-person chain (PersonTracker -> per-person filters -> RTMW3D per crop) needs
one thing from the camera side: a list of person boxes per frame. On the OAK-D that list comes from
person-detection-retail-0013 running on the VPU, and a webcam, a phone or a video file has no VPU.
video_udp_sender.py therefore posed ONE crop and ignored everybody else. This module is the missing
box source, and nothing else: it does not track, does not pose, does not know about Unity.

WHAT IT IS. The official Megvii YOLOX-s ONNX (Apache-2.0), 640x640 input, 8400 anchors, 80 COCO classes
of which only class 0 (person) is read. Fetch it with tools/model/fetch_yolox_s.py. The file is not in
version control (*.onnx is ignored) and lives at python-sidecar~/models/yolox_s.onnx, which Unity never
imports because of the trailing '~'. VIRTUAL_MIRROR_DETECTOR overrides the path.

THE EXPORT IS RAW. The released ONNX stops before YOLOX's decode step: columns 0-1 are cell-relative
centre offsets, 2-3 are log-space sizes, 4 is objectness and 5.. are class scores. Reading them as
pixels yields boxes that are plausible-looking and wrong, so decode() below applies the grid and the
stride exactly as YOLOX's own demo_postprocess does, and the constructor refuses any model whose
output is not [1, 8400, 85] rather than guess at another export.

THE LETTERBOX. YOLOX was trained with the picture resized by one scale and pinned to the TOP-LEFT of a
114-grey square, so that is what is done. An aspect-distorting resize is the easiest way to make a good
detector look bad: this repo measured 0.95 people per frame squashed against 7.06 letterboxed on the
same detector and clip (multiperson_udp_sender.letterbox). One scale, one inverse, both in this file.
"""

import os
import time

import cv2
import numpy as np

import evidence_paths as EV
import person_tracker as PT

#: The network's square input side, pixels. Fixed by the exported graph.
INPUT_SIZE = 640

#: YOLOX's own padding grey, because the model was trained against it.
PAD_VALUE = 114

#: The three feature-map strides of YOLOX-s: 80x80, 40x40 and 20x20 cells = 8400 anchors.
STRIDES = (8, 16, 32)

#: Column of the person score inside the 80 class scores (COCO class 0).
PERSON_CLASS = 0

#: 4 box values + 1 objectness + 80 COCO class scores.
OUTPUT_COLUMNS = 85

#: Where the weights are expected. models/ sits inside python-sidecar~, which Unity does not import.
DEFAULT_DETECTOR_ONNX = (os.environ.get("VIRTUAL_MIRROR_DETECTOR")
                         or os.path.join(EV.PROJECT_ROOT, "models", "yolox_s.onnx"))

#: The score below which a box is not a person. 0.45 is the value the OAK-D path already ships
#: (multiperson_udp_sender --det-conf), kept so the two box sources are compared at the same bar; it
#: is NOT yet tuned for this detector, and the F-52 measurement says how it behaves.
DEFAULT_CONF = 0.45

#: Overlap above which two boxes are the same person. 0.45 is YOLOX's own demo default.
DEFAULT_NMS_IOU = 0.45


def build_grid(size=INPUT_SIZE, strides=STRIDES):
    """The cell offsets and strides decode() needs, for every anchor, in the model's own order.

    Returns (grids float32 [N,2], expanded_strides float32 [N,1]); N is 8400 for 640 and the three
    YOLOX strides. The order is stride 8 first, row-major, which is how the head concatenates them.
    """
    grids = []
    expanded = []
    for stride in strides:
        cells = size // stride
        xv, yv = np.meshgrid(np.arange(cells), np.arange(cells))
        grid = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(grid)
        expanded.append(np.full((grid.shape[0], 1), stride))
    return (np.concatenate(grids, 0).astype(np.float32),
            np.concatenate(expanded, 0).astype(np.float32))


def letterbox_scale(frame_w, frame_h, size=INPUT_SIZE):
    """The one scale that fits a (w,h) picture inside the square input without distortion."""
    return min(size / float(frame_w), size / float(frame_h))


def decode(raw, grids, expanded_strides, conf):
    """Raw YOLOX rows -> (boxes_xyxy in INPUT pixels [K,4], scores [K]) for the rows that clear `conf`.

    The score is objectness x person-class probability, YOLOX's own definition. Filtering BEFORE the
    exp/stride arithmetic keeps the per-frame cost to the handful of rows that matter rather than all
    8400.
    """
    scores = raw[:, 4] * raw[:, 5 + PERSON_CLASS]
    keep = scores >= conf
    if not np.any(keep):
        return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32)
    rows = raw[keep]
    g = grids[keep]
    s = expanded_strides[keep]
    centre = (rows[:, 0:2] + g) * s
    size = np.exp(rows[:, 2:4]) * s
    boxes = np.concatenate((centre - size * 0.5, centre + size * 0.5), axis=1)
    return boxes.astype(np.float32), scores[keep].astype(np.float32)


def nms(boxes, scores, iou_threshold):
    """Greedy non-maximum suppression, highest score first. Returns the kept indices, best first.

    Greedy is right HERE even though person_tracker rejects greedy for ASSIGNMENT: this only removes
    duplicate boxes of one person, and the neighbours that matter (dancers shoulder to shoulder) are
    separated by the score order and by an IoU bar well above their real overlap.
    """
    if boxes.shape[0] == 0:
        return []
    order = np.argsort(-scores)
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    kept = []
    while order.size > 0:
        i = int(order[0])
        kept.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        union = areas[i] + areas[rest] - inter
        iou = inter / np.maximum(union, 1e-9)
        order = rest[iou <= iou_threshold]
    return kept


class HostPersonDetector:
    """Finds people in a BGR frame and returns person_tracker.Detection boxes (normalised, no depth).

    Not thread-safe: one instance owns one ONNX session and two reusable buffers.
    """

    def __init__(self, model_path=None, conf=DEFAULT_CONF, nms_iou=DEFAULT_NMS_IOU, providers=None):
        import onnxruntime as ort
        self.model_path = model_path or DEFAULT_DETECTOR_ONNX
        if not os.path.isfile(self.model_path):
            raise RuntimeError("person detector weights not found: %s - run "
                               "tools/model/fetch_yolox_s.py" % self.model_path)
        if providers is None:
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(self.model_path, sess_options=ort.SessionOptions(),
                                            providers=providers)
        self.active_providers = self.session.get_providers()
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_outputs()[0].shape
        grids, strides = build_grid()
        if list(shape[1:]) != [grids.shape[0], OUTPUT_COLUMNS]:
            raise RuntimeError("unexpected detector output %s in %s: this reads the raw YOLOX-s export "
                               "[1, %d, %d]; a decoded or different-size export would be misread"
                               % (shape, self.model_path, grids.shape[0], OUTPUT_COLUMNS))
        self.grids = grids
        self.strides = strides
        self.conf = float(conf)
        self.nms_iou = float(nms_iou)
        # Reused every frame: a 1.2 MB canvas and a 4.9 MB tensor are not worth an allocation at 30 fps.
        self._canvas = np.empty((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        self._blob = np.empty((1, 3, INPUT_SIZE, INPUT_SIZE), dtype=np.float32)
        #: ms spent in the last detect(), for the heartbeat. perf_counter, never time.time().
        self.last_ms = 0.0

    def describe(self):
        """One line for the startup banner, so the weights and the provider are never invisible."""
        return "%s (%.1f MB) on %s" % (os.path.basename(self.model_path),
                                       os.path.getsize(self.model_path) / 1e6,
                                       self.active_providers[0])

    def detect(self, frame_bgr):
        """People in `frame_bgr`, best score first, as person_tracker.Detection (normalised boxes)."""
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        scale = letterbox_scale(w, h)
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        self._canvas.fill(PAD_VALUE)
        self._canvas[:nh, :nw] = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        # BGR, 0-255, no mean/std: that is what the YOLOX release was trained on.
        np.copyto(self._blob[0], self._canvas.transpose(2, 0, 1))
        raw = self.session.run(None, {self.input_name: self._blob})[0][0]
        boxes, scores = decode(raw, self.grids, self.strides, self.conf)
        out = []
        for i in nms(boxes, scores, self.nms_iou):
            # Back to the ORIGINAL frame (undo the one scale), then to 0-1, clipped to the picture.
            x1 = min(1.0, max(0.0, float(boxes[i, 0]) / scale / w))
            y1 = min(1.0, max(0.0, float(boxes[i, 1]) / scale / h))
            x2 = min(1.0, max(0.0, float(boxes[i, 2]) / scale / w))
            y2 = min(1.0, max(0.0, float(boxes[i, 3]) / scale / h))
            if x2 > x1 and y2 > y1:
                out.append(PT.Detection((x1, y1, x2, y2), float(scores[i]), None))
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return out
