#!/usr/bin/env python3
"""H-02 - A FACE WITHOUT A BODY: MediaPipe's face detector, face mesh and blendshape networks on ONNX Runtime.

THE THREE NETWORKS (community ONNX conversions of the TFLite models inside Google's `face_landmarker.task`, Apache-2.0;
fetched by tools/model/fetch_face_models.py - see docs/H02_FACE_ONLY_2026-10-13.md in the Unity repo for what is and is not verified)
  face_detector.onnx      BlazeFace short range. 128x128 RGB in [-1, 1], 896 fixed anchors. Finds a face anywhere in the
                          picture: a box and six keypoints (right eye, left eye, nose, mouth, right ear, left ear).
  face_landmarks.onnx     Face Mesh V2. A 256x256 RGB crop in [0, 1] of ONE face, turned upright. 478 landmarks (468 mesh +
                          10 iris) as x, y, z in crop pixels, and a face-presence logit.
  face_blendshapes.onnx   An MLP-Mixer. Input is 146 of the 478 landmarks (BLENDSHAPE_LANDMARK_SUBSET) as x, y in IMAGE
                          pixels; output is 52 scores in [0, 1] (BLENDSHAPE_NAMES): index 0 is `_neutral`, the other 51 are
                          ARKit-compatible blendshapes. There is no tongueOut.

THE LOOP is the hand pipeline's (hand_landmarker.py / hand_tracking.py): the detector is the expensive stage and runs only
while no face is being tracked; once a face is found, the next frame's crop is predicted from this frame's landmarks
(`roi_from_landmarks`). face_tracking.py owns that loop; this module owns the geometry and the networks and keeps no state.

COORDINATES. Source-image pixels (x right, y down) unless a name says otherwise. A landmark's z is in the same pixel scale
as x and y (the mesh's own relative depth, smaller = nearer the camera). The crop maths is roi_geometry.py's.

The decode and crop contract (anchors, ROI rule, the 146 indices, the 52 names, the pixel-coordinate blendshape input) is
taken from the conversion script that produced the ONNX files, which pins it to MediaPipe's own sources; each rule is
pinned by an exact synthetic case in tests/test_face_landmarker.py.

NOT HERE: tracking, filtering, head pose, the wire. No camera, no socket.
"""

import math
import os

import numpy as np
import onnxruntime as ort

import evidence_paths as EV
import roi_geometry as RG
from roi_geometry import Roi

DETECTOR_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "face_detector.onnx")
MESH_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "face_landmarks.onnx")
BLENDSHAPE_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "face_blendshapes.onnx")

NUM_LANDMARKS = 478
NUM_MESH_LANDMARKS = 468
NUM_BLENDSHAPES = 52

# --- the detector -----------------------------------------------------------------------------------------------------
DETECTOR_INPUT = 128
DETECTOR_STRIDES = (8, 16, 16, 16)
DETECTOR_EXPECTED_ANCHORS = 896
DETECTOR_KEYPOINTS = 6
DETECTOR_SCORE_THRESHOLD = 0.5
DETECTOR_NMS_IOU = 0.3
#: Keypoint indices of the detector, in the order it emits them.
KP_RIGHT_EYE, KP_LEFT_EYE, KP_NOSE, KP_MOUTH, KP_RIGHT_EAR, KP_LEFT_EAR = range(6)

# --- the mesh ----------------------------------------------------------------------------------------------------------
MESH_INPUT = 256
#: MediaPipe's rect transformation for a face: the detection box made square on its longer side and scaled by 1.5.
ROI_SCALE = 1.5

# Landmark indices of the 468-point mesh that other modules refer to.
RIGHT_EYE_OUTER = 33      # the subject's right eye, which is on the image's LEFT in an upright frontal face
LEFT_EYE_OUTER = 263
RIGHT_EYE_INNER = 133
LEFT_EYE_INNER = 362
FOREHEAD = 10
CHIN = 152
NOSE_TIP = 1
MOUTH_LEFT = 291
MOUTH_RIGHT = 61
RIGHT_IRIS_CENTRE = 468
LEFT_IRIS_CENTRE = 473

#: The 146 landmarks (of 478) that feed the blendshape network, in the order it expects them. From MediaPipe's
#: face_blendshapes_graph.cc via the conversion script. 10 of them are the iris points 468-477.
BLENDSHAPE_LANDMARK_SUBSET = (
    0, 1, 4, 5, 6, 7, 8, 10, 13, 14, 17, 21, 33, 37, 39,
    40, 46, 52, 53, 54, 55, 58, 61, 63, 65, 66, 67, 70, 78, 80,
    81, 82, 84, 87, 88, 91, 93, 95, 103, 105, 107, 109, 127, 132, 133,
    136, 144, 145, 146, 148, 149, 150, 152, 153, 154, 155, 157, 158, 159, 160,
    161, 162, 163, 168, 172, 173, 176, 178, 181, 185, 191, 195, 197, 234, 246,
    249, 251, 263, 267, 269, 270, 276, 282, 283, 284, 285, 288, 291, 293, 295,
    296, 297, 300, 308, 310, 311, 312, 314, 317, 318, 321, 323, 324, 332, 334,
    336, 338, 356, 361, 362, 365, 373, 374, 375, 377, 378, 379, 380, 381, 382,
    384, 385, 386, 387, 388, 389, 390, 397, 398, 400, 402, 405, 409, 415, 454,
    466, 468, 469, 470, 471, 472, 473, 474, 475, 476, 477,
)

#: The network's 52 outputs, in order. `_neutral` is MediaPipe's own extra channel (how neutral the face is), not an ARKit
#: blendshape; the other 51 are ARKit's names. "Left" and "Right" are the SUBJECT's left and right, as in ARKit.
BLENDSHAPE_NAMES = (
    "_neutral", "browDownLeft", "browDownRight", "browInnerUp",
    "browOuterUpLeft", "browOuterUpRight", "cheekPuff", "cheekSquintLeft",
    "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft",
    "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft",
    "eyeLookOutRight", "eyeLookUpLeft", "eyeLookUpRight", "eyeSquintLeft",
    "eyeSquintRight", "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft",
    "jawOpen", "jawRight", "mouthClose", "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight", "mouthFunnel", "mouthLeft",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft",
    "mouthPressRight", "mouthPucker", "mouthRight", "mouthRollLower",
    "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthSmileLeft",
    "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthUpperUpLeft", "mouthUpperUpRight", "noseSneerLeft", "noseSneerRight",
)

BLENDSHAPE_INDEX = {name: i for i, name in enumerate(BLENDSHAPE_NAMES)}


def make_face_anchors():
    """The (896, 2) array of face-detector anchor centres (see roi_geometry.make_ssd_anchors)."""
    return RG.make_ssd_anchors(DETECTOR_INPUT, DETECTOR_STRIDES, 2)


class FaceDetection(object):
    """One face found by the detector, in source-image pixels.

    box        [x1, y1, x2, y2]
    keypoints  (6, 2): right eye, left eye, nose, mouth, right ear, left ear (subject's right/left)
    score      0..1
    """
    __slots__ = ("box", "keypoints", "score")

    def __init__(self, box, keypoints, score):
        self.box = np.asarray(box, dtype=np.float64)
        self.keypoints = np.asarray(keypoints, dtype=np.float64)
        self.score = float(score)


def weighted_nms(boxes, keypoints, scores, iou_threshold):
    """MediaPipe's weighted non-maximum suppression. Returns [(box, keypoints, score)], best first.

    The best remaining detection and every one overlapping it (IoU above the threshold) form a cluster; the output for the
    cluster is the score-weighted mean of its boxes and keypoints, with the best member's score. `boxes` is (n, 4)
    [x1, y1, x2, y2], `keypoints` (n, k, 2).
    """
    order = list(np.argsort(-scores))
    out = []
    while order:
        best = order[0]
        cluster = [j for j in order if RG.iou_xyxy(boxes[best], boxes[j]) > iou_threshold]
        if best not in cluster:
            cluster.append(best)
        order = [j for j in order if j not in cluster]
        weights = scores[cluster] / scores[cluster].sum()
        box = (boxes[cluster] * weights[:, None]).sum(axis=0)
        kp = (keypoints[cluster] * weights[:, None, None]).sum(axis=0)
        out.append((box, kp, float(scores[best])))
    return out


def theta_from_eyes(right_eye, left_eye):
    """The crop turn that makes the line from the subject's right eye to their left eye horizontal. 0 for an upright face.

    The right eye is the one on the image's left in an upright frontal face, so the eye-to-eye vector points right and
    theta = atan2(dy, dx); a head rolled so that line tilts clockwise on screen (dy > 0) has a positive theta.
    """
    return math.atan2(left_eye[1] - right_eye[1], left_eye[0] - right_eye[0])


def roi_from_detection(det):
    """The crop for the mesh network from a detection (MediaPipe's DetectionsToRects + RectTransformation)."""
    theta = theta_from_eyes(det.keypoints[KP_RIGHT_EYE], det.keypoints[KP_LEFT_EYE])
    cx = (det.box[0] + det.box[2]) / 2.0
    cy = (det.box[1] + det.box[3]) / 2.0
    return Roi(cx, cy, max(det.box[2] - det.box[0], det.box[3] - det.box[1]) * ROI_SCALE, theta)


def roi_from_landmarks(xy):
    """The crop for the NEXT frame, from this frame's landmarks ((n >= 264, 2) in source pixels).

    Turned so the eye-corner line (landmarks 33 to 263) is horizontal, sized from the extent of the landmarks in that turned
    frame, scaled by 1.5 and made square on the longer side. This is what lets the detector be skipped for a tracked face.
    """
    xy = np.asarray(xy, dtype=np.float64)
    theta = theta_from_eyes(xy[RIGHT_EYE_OUTER], xy[LEFT_EYE_OUTER])
    probe = Roi(0.0, 0.0, 1.0, theta)
    crop = probe.source_to_crop(xy[:NUM_MESH_LANDMARKS])
    lo = crop.min(axis=0)
    hi = crop.max(axis=0)
    centre = probe.crop_to_source(np.array([(lo + hi) / 2.0]))[0]
    return Roi(centre[0], centre[1], max(hi[0] - lo[0], hi[1] - lo[1]) * ROI_SCALE, theta)


class FaceMesh(object):
    """The mesh network's reading of one crop, in source-image terms.

    landmarks  (478, 3): x, y in source pixels; z in the same pixel scale, relative (smaller = nearer)
    presence   0..1, the network's own belief that the crop holds a face
    roi        the Roi it was read from
    """
    __slots__ = ("landmarks", "presence", "roi")

    def __init__(self, landmarks, presence, roi):
        self.landmarks = landmarks
        self.presence = float(presence)
        self.roi = roi


def decode_mesh_outputs(landmarks, presence_logit, roi):
    """The mesh network's raw outputs -> a FaceMesh in source-image terms. Pure numpy.

    `landmarks` is the flat 1434-vector: 478 x (x, y, z) in 256-pixel crop coordinates. x and y go back through the crop's
    mapping; z is scaled by the crop's side (in source pixels per unit of normalised crop width).
    """
    lm = np.asarray(landmarks, dtype=np.float64).reshape(NUM_LANDMARKS, 3) / float(MESH_INPUT)
    xy = roi.crop_to_source(lm[:, 0:2])
    z = lm[:, 2] * roi.size
    presence = float(RG.sigmoid(np.asarray(presence_logit).reshape(-1)[0]))
    return FaceMesh(np.concatenate([xy, z[:, None]], axis=1), presence, roi)


def blendshape_input(landmarks):
    """The (1, 146, 2) float32 tensor for the blendshape network: x and y, in image pixels, of the 146 subset landmarks."""
    pts = np.asarray(landmarks, dtype=np.float64)[list(BLENDSHAPE_LANDMARK_SUBSET), 0:2]
    return pts.astype(np.float32)[np.newaxis, :, :]


def _make_session(path, providers):
    if not os.path.isfile(path):
        raise FileNotFoundError("%s is missing - run tools/model/fetch_face_models.py" % path)
    if providers is None:
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ort.InferenceSession(path, sess_options=ort.SessionOptions(), providers=providers)


class FaceDetector(object):
    """Finds faces anywhere in a BGR frame. Stateless."""

    def __init__(self, path=DETECTOR_MODEL, providers=None, score_threshold=DETECTOR_SCORE_THRESHOLD,
                 nms_iou=DETECTOR_NMS_IOU):
        self.session = _make_session(path, providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_providers = self.session.get_providers()
        self.anchors = make_face_anchors()
        self.score_threshold = score_threshold
        self.nms_iou = nms_iou

    def prepare(self, frame):
        """(tensor, ratio, pad_left, pad_top): the frame letterboxed to 128x128 and scaled to [-1, 1]."""
        rgb, ratio, pad_left, pad_top = RG.letterbox(frame, DETECTOR_INPUT)
        tensor = (rgb.astype(np.float32) / 127.5 - 1.0)[np.newaxis, :, :, :]
        return tensor, ratio, pad_left, pad_top

    def decode(self, raw_boxes, raw_scores, ratio, pad_left, pad_top):
        """The detector's raw outputs -> faces in source-image pixels, best first, after weighted NMS. Pure numpy."""
        raw = np.asarray(raw_boxes, dtype=np.float64).reshape(-1, 4 + 2 * DETECTOR_KEYPOINTS)
        scores = RG.sigmoid(np.asarray(raw_scores, dtype=np.float64).reshape(-1))
        if raw.shape[0] != len(self.anchors):
            raise ValueError("face detector returned %d anchors, expected %d" % (raw.shape[0], len(self.anchors)))
        keep = np.nonzero(scores >= self.score_threshold)[0]
        if keep.size == 0:
            return []
        anchors = self.anchors[keep].astype(np.float64)
        centre = raw[keep, 0:2] / DETECTOR_INPUT + anchors
        size = raw[keep, 2:4] / DETECTOR_INPUT
        boxes_n = np.concatenate([centre - size / 2.0, centre + size / 2.0], axis=1)
        kp_n = np.stack([raw[keep, 4 + 2 * k:6 + 2 * k] / DETECTOR_INPUT + anchors for k in range(DETECTOR_KEYPOINTS)],
                        axis=1)
        pad = np.array([pad_left, pad_top], dtype=np.float64)

        def to_source(norm_xy):
            return (norm_xy * DETECTOR_INPUT - pad) / ratio

        boxes = np.concatenate([to_source(boxes_n[:, 0:2]), to_source(boxes_n[:, 2:4])], axis=1)
        keypoints = to_source(kp_n)
        return [FaceDetection(b, k, s) for b, k, s in weighted_nms(boxes, keypoints, scores[keep], self.nms_iou)]

    def detect(self, frame):
        """Faces in a BGR frame, best first."""
        tensor, ratio, pad_left, pad_top = self.prepare(frame)
        boxes, scores = self.session.run(None, {self.input_name: tensor})
        return self.decode(boxes, scores, ratio, pad_left, pad_top)


class FaceMeshNet(object):
    """Reads the 478 landmarks of the one face inside a ROI. Stateless."""

    def __init__(self, path=MESH_MODEL, providers=None):
        self.session = _make_session(path, providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_providers = self.session.get_providers()

    def infer(self, frame, roi):
        """A FaceMesh for the face in `roi` of the BGR `frame`. Presence gating is the caller's."""
        tensor = RG.cut_roi(frame, roi, MESH_INPUT)
        landmarks, presence, _unused = self.session.run(None, {self.input_name: tensor})
        return decode_mesh_outputs(landmarks, presence, roi)


class BlendshapeNet(object):
    """The 52 blendshape scores of one face's landmarks. Stateless."""

    def __init__(self, path=BLENDSHAPE_MODEL, providers=None):
        self.session = _make_session(path, providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_providers = self.session.get_providers()

    def infer(self, landmarks):
        """(52,) float64 scores in [0, 1] from the 478 landmarks in IMAGE pixels."""
        out = self.session.run(None, {self.input_name: blendshape_input(landmarks)})[0]
        return np.asarray(out, dtype=np.float64).reshape(-1)
