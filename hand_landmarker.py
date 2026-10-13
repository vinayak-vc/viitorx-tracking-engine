#!/usr/bin/env python3
"""H-01 - HAND LANDMARKS WITHOUT A BODY: MediaPipe's palm detector and hand-landmark network on ONNX Runtime.

WHY THIS EXISTS. RTMW3D (rtmw3d_pose.py) produces hands only as a by-product of posing a PERSON: a body is
detected, a crop is cut around it, and the hand points are read out of that crop. A frame with a hand and no
body gives it nothing to crop, and the video sender drops the frame. This module finds a hand directly.

THE TWO NETWORKS (OpenCV Zoo's ONNX conversions of Google's MediaPipe models, Apache-2.0; fetched by
tools/model/fetch_hand_models.py)
  palm detector   192x192 RGB, an SSD with 2016 fixed anchors. Finds a PALM anywhere in the picture and says
                  where the wrist and the middle-finger base are, which is enough to rotate the hand upright.
  hand landmarks  224x224 RGB crop of ONE hand, upright. 21 landmarks in crop pixels (x, y, z), 21 WORLD
                  landmarks in metres, a handedness score and a hand-presence confidence.

THE LOOP (MediaPipe's, and the reason it is fast). The palm detector is the expensive, rarely-needed stage. Once a
hand has been found, the next frame's crop is predicted from THIS frame's landmarks (`roi_from_landmarks`) and the
palm detector is skipped for that hand. hand_tracking.py owns that loop; this module owns the geometry and the
networks, and keeps no state between calls.

COORDINATES. Everything here is in the SOURCE IMAGE's pixels (x right, y down) unless a name says otherwise.
`world` is metres, hand-centred, axes parallel to the image's (x right, y down, z away from the camera). A crop is
described by a ROI (cx, cy, size, theta): its centre, its side length, and the angle at which it is turned so that
the hand stands upright inside it. With u = (cos theta, sin theta) and v = (-sin theta, cos theta) the crop's own
right and down axes expressed in the source image, crop point (x, y) in [0, 1]^2 is the source point
    c + (x - 0.5) * size * u + (y - 0.5) * size * v.
theta = 0 is a crop that is not turned; a hand pointing right of the picture has theta = +90 degrees.

NOT HERE: tracking, filtering, depth, gestures, the wire. No camera, no socket - so every rule in this file is
testable without hardware (tests/test_hand_landmarker.py).
"""

import math
import os

import numpy as np
import onnxruntime as ort

import evidence_paths as EV
import roi_geometry as RG
from roi_geometry import Roi, cut_roi, iou_xyxy, nms, normalize_angle, roi_warp_matrix  # noqa: F401  (re-exported)

PALM_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "palm_detection_mediapipe_2023feb.onnx")
HAND_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "handpose_estimation_mediapipe_2023feb.onnx")

NUM_LANDMARKS = 21

# MediaPipe hand landmark indices.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

#: The 21 landmarks as bones, for drawing and for anything that wants the skeleton.
HAND_BONES = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

PALM_INPUT = 192
HAND_INPUT = 224

# --- the palm detector (MediaPipe palm_detection_full SSD configuration) ---------------------------------------
#: Feature-map strides of the four anchor layers. Layers with equal stride are one grid with more anchors per cell.
PALM_STRIDES = (8, 16, 16, 16)
#: Two anchors per cell per layer (aspect ratio 1.0 plus the interpolated scale), and the three stride-16 layers
#: share one 12x12 grid: 24*24*2 + 12*12*6 = 2016, which is what the network emits and what _check_outputs asserts.
PALM_ANCHORS_PER_LAYER = 2
PALM_EXPECTED_ANCHORS = 2016

#: MediaPipe's own defaults for min_detection_confidence and the SSD non-maximum-suppression overlap.
PALM_SCORE_THRESHOLD = 0.5
PALM_NMS_IOU = 0.3

# --- palm detection -> hand ROI (MediaPipe palm_detection_to_roi) ------------------------------------------------
PALM_ROI_SCALE = 2.6
PALM_ROI_SHIFT_Y = -0.5
#: Index (in the detector's 7 keypoints) of the wrist and of the middle-finger base: the hand's "up" axis.
PALM_KP_WRIST = 0
PALM_KP_MIDDLE_MCP = 2

# --- hand landmarks -> next frame's ROI (MediaPipe hand_landmarks_to_roi) ----------------------------------------
LANDMARK_ROI_SCALE = 2.0
LANDMARK_ROI_SHIFT_Y = -0.1


def make_palm_anchors():
    """The (2016, 2) array of palm-detector anchor centres (see roi_geometry.make_ssd_anchors)."""
    return RG.make_ssd_anchors(PALM_INPUT, PALM_STRIDES, PALM_ANCHORS_PER_LAYER)


class Palm(object):
    """One palm found by the detector, in source-image pixels.

    box       [x1, y1, x2, y2]
    keypoints (7, 2); index 0 is the wrist centre, index 2 the middle-finger base
    score     0..1
    """
    __slots__ = ("box", "keypoints", "score")

    def __init__(self, box, keypoints, score):
        self.box = np.asarray(box, dtype=np.float64)
        self.keypoints = np.asarray(keypoints, dtype=np.float64)
        self.score = float(score)


def theta_from_up_vector(up_x, up_y):
    """The ROI turn that makes the image direction (up_x, up_y) the crop's UP (negative v).

    An upright hand has up = (0, -1) -> 0. A hand pointing to the right has up = (1, 0) -> +pi/2.
    """
    return math.atan2(up_x, -up_y)


def roi_from_palm(palm):
    """The crop for the landmark network, from a palm detection (MediaPipe palm_detection_to_roi)."""
    wrist = palm.keypoints[PALM_KP_WRIST]
    middle = palm.keypoints[PALM_KP_MIDDLE_MCP]
    theta = theta_from_up_vector(middle[0] - wrist[0], middle[1] - wrist[1])
    cx = (palm.box[0] + palm.box[2]) / 2.0
    cy = (palm.box[1] + palm.box[3]) / 2.0
    return RG.shifted_square_roi(cx, cy, palm.box[2] - palm.box[0], palm.box[3] - palm.box[1], theta,
                               PALM_ROI_SCALE, PALM_ROI_SHIFT_Y)


def roi_from_landmarks(xy):
    """The crop for the NEXT frame, from this frame's 21 landmarks (n=21, 2) in source pixels.

    Turned so the wrist-to-middle-finger-base axis is upright, sized from the landmarks' extent in that turned frame.
    This is what lets the palm detector be skipped for a hand that is already being tracked.
    """
    xy = np.asarray(xy, dtype=np.float64)
    wrist = xy[WRIST]
    middle = xy[MIDDLE_MCP]
    theta = theta_from_up_vector(middle[0] - wrist[0], middle[1] - wrist[1])
    probe = Roi(0.0, 0.0, 1.0, theta)
    crop = probe.source_to_crop(xy)
    lo = crop.min(axis=0)
    hi = crop.max(axis=0)
    centre = probe.crop_to_source(np.array([(lo + hi) / 2.0]))[0]
    return RG.shifted_square_roi(centre[0], centre[1], hi[0] - lo[0], hi[1] - lo[1], theta,
                               LANDMARK_ROI_SCALE, LANDMARK_ROI_SHIFT_Y)


class HandResult(object):
    """One hand, in source-image terms.

    image    (21, 3) x, y in source pixels; z in the same pixel scale, relative to the wrist (smaller = nearer)
    world    (21, 3) metres, hand-centred, axes parallel to the image's (x right, y down, z away)
    handed   raw handedness score 0..1 (see hand_tracking for which hand each end is)
    conf     hand-presence confidence 0..1
    roi      the Roi it was read from
    """
    __slots__ = ("image", "world", "handed", "conf", "roi")

    def __init__(self, image, world, handed, conf, roi):
        self.image = image
        self.world = world
        self.handed = float(handed)
        self.conf = float(conf)
        self.roi = roi


def decode_hand_outputs(landmarks, conf, handedness, world, roi):
    """The landmark network's four raw outputs -> a HandResult in source-image terms. Pure numpy.

    `landmarks` and `world` are the flat (1, 63) tensors. Landmark x and y are in the 224 px crop; z is in the same
    unit. World landmarks are in metres in the CROP's frame and are turned back into the image's frame (x and y by
    the crop's turn; z is unaffected by an in-plane rotation).
    """
    lm = np.asarray(landmarks, dtype=np.float64).reshape(NUM_LANDMARKS, 3)
    xy = roi.crop_to_source(lm[:, 0:2] / float(HAND_INPUT))
    z = lm[:, 2] * (roi.size / float(HAND_INPUT))
    image = np.concatenate([xy, z[:, None]], axis=1)

    w = np.asarray(world, dtype=np.float64).reshape(NUM_LANDMARKS, 3)
    u, v = roi.axes()
    wxy = w[:, 0:1] * u + w[:, 1:2] * v
    world_img = np.concatenate([wxy, w[:, 2:3]], axis=1)
    return HandResult(image, world_img, float(np.asarray(handedness).reshape(-1)[0]),
                      float(np.asarray(conf).reshape(-1)[0]), roi)


def _make_session(path, providers):
    if not os.path.isfile(path):
        raise FileNotFoundError("%s is missing - run tools/model/fetch_hand_models.py" % path)
    if providers is None:
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ort.InferenceSession(path, sess_options=ort.SessionOptions(), providers=providers)


class PalmDetector(object):
    """Finds palms anywhere in a BGR frame. Stateless."""

    def __init__(self, path=PALM_MODEL, providers=None, score_threshold=PALM_SCORE_THRESHOLD,
                 nms_iou=PALM_NMS_IOU):
        self.session = _make_session(path, providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_providers = self.session.get_providers()
        self.anchors = make_palm_anchors()
        self.score_threshold = score_threshold
        self.nms_iou = nms_iou

    def letterbox(self, frame):
        """(tensor, ratio, pad_left, pad_top): the frame fitted into 192x192 with its aspect ratio kept."""
        rgb, ratio, pad_left, pad_top = RG.letterbox(frame, PALM_INPUT)
        return (rgb.astype(np.float32) / 255.0)[np.newaxis, :, :, :], ratio, pad_left, pad_top

    def decode(self, raw, scores, ratio, pad_left, pad_top):
        """The detector's raw outputs -> palms in source-image pixels, best first, after NMS. Pure numpy."""
        raw = np.asarray(raw, dtype=np.float64).reshape(-1, 18)
        scores = RG.sigmoid(np.asarray(scores, dtype=np.float64).reshape(-1))
        if raw.shape[0] != len(self.anchors):
            raise ValueError("palm detector returned %d anchors, expected %d" % (raw.shape[0], len(self.anchors)))
        candidates = np.nonzero(scores >= self.score_threshold)[0]
        if candidates.size == 0:
            return []
        centre = raw[candidates, 0:2] / PALM_INPUT + self.anchors[candidates]
        size = raw[candidates, 2:4] / PALM_INPUT
        boxes_n = np.concatenate([centre - size / 2.0, centre + size / 2.0], axis=1)
        pad = np.array([pad_left, pad_top], dtype=np.float64)

        def to_source(norm_xy):
            return (norm_xy * PALM_INPUT - pad) / ratio

        boxes = np.concatenate([to_source(boxes_n[:, 0:2]), to_source(boxes_n[:, 2:4])], axis=1)
        keep = nms(boxes, scores[candidates], self.nms_iou)
        palms = []
        for k in keep:
            idx = candidates[k]
            kp = raw[idx, 4:18].reshape(7, 2) / PALM_INPUT + self.anchors[idx]
            palms.append(Palm(boxes[k], to_source(kp), scores[idx]))
        return palms

    def detect(self, frame):
        """Palms in a BGR frame, best first."""
        tensor, ratio, pad_left, pad_top = self.letterbox(frame)
        out = self.session.run(None, {self.input_name: tensor})
        return self.decode(out[0], out[1], ratio, pad_left, pad_top)


class HandLandmarkNet(object):
    """Reads the 21 landmarks of the one hand inside a ROI. Stateless."""

    def __init__(self, path=HAND_MODEL, providers=None):
        self.session = _make_session(path, providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_providers = self.session.get_providers()

    def infer(self, frame, roi):
        """A HandResult for the hand in `roi` of the BGR `frame`. Confidence gating is the caller's."""
        tensor = cut_roi(frame, roi, HAND_INPUT)
        landmarks, conf, handedness, world = self.session.run(None, {self.input_name: tensor})
        return decode_hand_outputs(landmarks, conf, handedness, world, roi)
