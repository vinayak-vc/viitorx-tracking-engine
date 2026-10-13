#!/usr/bin/env python3
"""H-02 tests for face_landmarker.py (and roi_geometry.py, which hands and faces share): anchors, the detector's decode and weighted
NMS, the crop rules, the mesh decode, the blendshape input, the constants, and - when the models and the sample clip are present -
one real three-network smoke test.

The geometry is where a landmark model silently goes wrong (a half-pixel shift, a transposed rotation, a letterbox offset), and a
network will return plausible-looking landmarks for a slightly wrong crop, so each rule is pinned with an exact synthetic case.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np

import _synthetic_face as SF
import evidence_paths as EV
import face_landmarker as F
import roi_geometry as RG

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-66s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-66s %s" % (label, detail))


print("THE CONSTANTS")
check(len(F.BLENDSHAPE_NAMES) == 52 and len(set(F.BLENDSHAPE_NAMES)) == 52, "52 distinct blendshape names")
check(F.BLENDSHAPE_NAMES[0] == "_neutral" and "tongueOut" not in F.BLENDSHAPE_NAMES, "index 0 is _neutral and there is no tongueOut: 51 ARKit shapes + 1")
check(F.BLENDSHAPE_NAMES[F.BLENDSHAPE_INDEX["jawOpen"]] == "jawOpen" and F.BLENDSHAPE_INDEX["jawOpen"] == 25, "the name index maps back (jawOpen is 25)")
left_right = [n for n in F.BLENDSHAPE_NAMES if n.endswith("Left")]
check(all(n[:-4] + "Right" in F.BLENDSHAPE_NAMES for n in left_right) and len(left_right) == 20, "every Left shape has its Right (20 pairs, 11 unpaired)", "%d left" % len(left_right))
check(len(F.BLENDSHAPE_LANDMARK_SUBSET) == 146 and len(set(F.BLENDSHAPE_LANDMARK_SUBSET)) == 146, "146 distinct blendshape landmarks")
check(max(F.BLENDSHAPE_LANDMARK_SUBSET) == 477 and min(F.BLENDSHAPE_LANDMARK_SUBSET) == 0, "all within 0..477")
check(sorted(F.BLENDSHAPE_LANDMARK_SUBSET) == list(F.BLENDSHAPE_LANDMARK_SUBSET), "in ascending order")
check(sum(1 for i in F.BLENDSHAPE_LANDMARK_SUBSET if i >= 468) == 10, "ten of them are the iris points 468-477")

print("")
print("ANCHORS")
anchors = F.make_face_anchors()
check(anchors.shape == (F.DETECTOR_EXPECTED_ANCHORS, 2), "896 anchors, the count the detector emits", str(anchors.shape))
check(len({tuple(a) for a in anchors}) == 16 * 16 + 8 * 8, "distinct centres: a 16 x 16 grid and an 8 x 8 grid")
check(np.allclose(anchors[0], anchors[1]) and abs(anchors[2][0] - anchors[0][0] - 1.0 / 16.0) < 1e-6, "stride-8 cells are 1/16 of the picture apart, two anchors each")
check(abs(anchors[16 * 16 * 2][0] - 0.5 / 8.0) < 1e-6, "the stride-16 grid (8 x 8, six anchors a cell) starts after the 16 x 16 one")
check(RG.make_ssd_anchors(192, (8, 16, 16, 16)).shape == (2016, 2), "the same generator gives the palm detector's 2016")

print("")
print("THE ROI RULES")
check(abs(F.theta_from_eyes((100, 100), (200, 100))) < 1e-12, "level eyes need no turn")
check(abs(F.theta_from_eyes((100, 100), (200, 200)) - math.pi / 4) < 1e-12, "the image-right eye lower by as much as it is right: +45 degrees")
check(abs(F.theta_from_eyes((200, 100), (100, 100))) - math.pi < 1e-12, "an upside-down face is turned half a revolution")
det = F.FaceDetection([200, 150, 300, 270], [[225, 190], [275, 190], [250, 215], [250, 245], [205, 200], [295, 200]], 0.9)
roi = F.roi_from_detection(det)
check(abs(roi.theta) < 1e-12 and abs(roi.size - 120 * 1.5) < 1e-9, "an upright detection: no turn, side = longer box side x 1.5", "%.1f" % roi.size)
check(abs(roi.cx - 250.0) < 1e-9 and abs(roi.cy - 210.0) < 1e-9, "centred on the box (the face ROI is NOT shifted, unlike the palm's)")
tilted = F.FaceDetection([200, 150, 300, 270], [[225, 180], [275, 200], [250, 215], [250, 245], [205, 200], [295, 200]], 0.9)
check(abs(F.roi_from_detection(tilted).theta - math.atan2(20, 50)) < 1e-12, "the turn follows the keypoints' eye line")

face = SF.make_face()
cam_scale = 1500.0
for turn in (0, 20, -35, 90, 180):
    pts = SF.posed(face, roll=turn)[:, :2] * cam_scale + np.array([640.0, 360.0])
    roi = F.roi_from_landmarks(pts)
    crop = roi.source_to_crop(pts[:468])
    check(crop.min() >= 0.0 and crop.max() <= 1.0, "roll %4d deg: every mesh landmark is inside the next crop" % turn, "range %.2f..%.2f" % (crop.min(), crop.max()))
    eye_line = pts[F.LEFT_EYE_OUTER] - pts[F.RIGHT_EYE_OUTER]
    cosine = float(eye_line.dot(roi.axes()[0]) / np.linalg.norm(eye_line))
    check(cosine > 0.999999, "roll %4d deg: the crop's right is the eye line" % turn, "cos %.6f" % cosine)
check(1.2 < roi.size / np.ptp(pts[:468], axis=0).max() < 2.2, "the next crop is about 1.5x the face's extent")

print("")
print("THE WARP AGREES WITH THE MAPPING (shared with the hands)")
for turn in (0, 30, 90, 180, -60):
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    roi = RG.Roi(320.0, 240.0, 300.0, math.radians(turn))
    target = np.array([[0.65, 0.35]])
    sx, sy = roi.crop_to_source(target)[0]
    cv2.circle(img, (int(round(sx)), int(round(sy))), 4, (255, 255, 255), -1)
    patch = (RG.cut_roi(img, roi, 256)[0, :, :, 0] > 0.5)
    ys, xs = np.nonzero(patch)
    found = np.array([xs.mean() + 0.5, ys.mean() + 0.5]) / 256.0 if xs.size else np.array([-1.0, -1.0])
    check(xs.size > 0 and np.linalg.norm(found - target[0]) < 0.012, "turn %4d deg: a dot at crop (0.65, 0.35) lands there" % turn, "found %s" % np.round(found, 3))

print("")
print("THE MESH DECODE")
roi = RG.Roi(400.0, 300.0, 256.0, 0.0)
raw = np.zeros((478, 3))
raw[:, 0] = 128.0
raw[:, 1] = 128.0
raw[33] = (28.0, 78.0, -20.0)
res = F.decode_mesh_outputs(raw.reshape(1, 1, 1, 1434), [[[[4.0]]]], roi)
check(res.landmarks.shape == (478, 3), "478 x 3")
check(np.allclose(res.landmarks[0, :2], [400.0, 300.0]), "the crop centre decodes to the ROI centre")
check(np.allclose(res.landmarks[33, :2], [300.0, 250.0]), "a landmark 100 crop-px left and 50 up decodes to (-100, -50)")
check(abs(res.landmarks[33, 2] - (-20.0 / 256.0) * 256.0) < 1e-9, "z is scaled by the crop's side over its width (here 1:1)")
check(abs(res.presence - 1.0 / (1.0 + math.exp(-4.0))) < 1e-9, "presence is the sigmoid of the logit")
roi2 = RG.Roi(400.0, 300.0, 128.0, math.pi / 2)
res2 = F.decode_mesh_outputs(raw.reshape(1, 1, 1, 1434), [[[[0.0]]]], roi2)
check(np.allclose(res2.landmarks[33, :2], roi2.crop_to_source(np.array([[28.0 / 256, 78.0 / 256]]))[0]), "landmarks go through the same mapping as the crop, turned")
check(abs(res2.landmarks[33, 2] - (-20.0 / 256.0) * 128.0) < 1e-9, "z scales with the crop's size (128 / 256)")

print("")
print("THE BLENDSHAPE INPUT")
lm = np.zeros((478, 3))
lm[:, 0] = np.arange(478)
lm[:, 1] = 1000.0 + np.arange(478)
tensor = F.blendshape_input(lm)
check(tensor.shape == (1, 146, 2) and tensor.dtype == np.float32, "(1, 146, 2) float32")
check(list(tensor[0, :, 0]) == list(F.BLENDSHAPE_LANDMARK_SUBSET), "the rows are the subset's landmarks, in the subset's order (x)")
check(list(tensor[0, :, 1]) == [1000.0 + i for i in F.BLENDSHAPE_LANDMARK_SUBSET], "and y is paired with x (z is dropped)")

print("")
print("THE DETECTOR'S DECODE")


class _NoSession(F.FaceDetector):
    """A FaceDetector without a network: decode() is pure numpy, so it is tested on its own."""

    def __init__(self):
        self.anchors = F.make_face_anchors()
        self.score_threshold = 0.5
        self.nms_iou = 0.3


det_net = _NoSession()
raw = np.zeros((896, 16))
scores = np.full(896, -10.0)
k = 300
scores[k] = 3.0
raw[k, 2:4] = (40.0, 40.0)                       # box 40 x 40 input-px centred on the anchor
raw[k, 4:6] = (-10.0, -5.0)                      # keypoint 0 (right eye): 10 left, 5 up
raw[k, 6:8] = (10.0, -5.0)                       # keypoint 1 (left eye)
ratio = 128.0 / 640.0
pad_top = (128 - int(round(480 * ratio))) // 2
faces = det_net.decode(raw, scores, ratio, 0, pad_top)
ax, ay = anchors[k]
cx_src = ax * 128.0 / ratio
cy_src = (ay * 128.0 - pad_top) / ratio
check(len(faces) == 1, "one confident anchor gives one face")
check(abs((faces[0].box[0] + faces[0].box[2]) / 2 - cx_src) < 1e-6 and abs((faces[0].box[1] + faces[0].box[3]) / 2 - cy_src) < 1e-6,
      "the box is centred on the anchor, mapped back through the letterbox")
check(abs((faces[0].box[2] - faces[0].box[0]) - 40.0 / ratio) < 1e-6, "its width is 40 input-px / ratio")
check(abs(faces[0].keypoints[0][0] - (cx_src - 10.0 / ratio)) < 1e-6 and abs(faces[0].keypoints[1][1] - (cy_src - 5.0 / ratio)) < 1e-6,
      "keypoints are offsets from the anchor: right eye 10 px left, left eye 5 px up")
check(abs(faces[0].score - 1.0 / (1.0 + math.exp(-3.0))) < 1e-9, "the score is the sigmoid of the logit")
check(faces[0].keypoints.shape == (6, 2), "six keypoints")

# Weighted NMS: two overlapping anchors with different boxes merge into their score-weighted mean.
scores[k + 1] = 1.0
raw[k + 1, 2:4] = (40.0, 40.0)
raw[k + 1, 0:2] = (8.0, 0.0)                     # a box shifted 8 input-px to the right
merged = det_net.decode(raw, scores, ratio, 0, pad_top)
w_hi = 1.0 / (1.0 + math.exp(-3.0))
w_lo = 1.0 / (1.0 + math.exp(-1.0))
c0 = (anchors[k] * 128.0 - np.array([0, pad_top])) / ratio
c1 = ((anchors[k + 1] + np.array([8.0 / 128.0, 0.0])) * 128.0 - np.array([0, pad_top])) / ratio
want_cx = (w_hi * c0[0] + w_lo * c1[0]) / (w_hi + w_lo)
got_cx = (merged[0].box[0] + merged[0].box[2]) / 2.0
check(len(merged) == 1 and abs(got_cx - want_cx) < 1e-6, "overlapping detections are merged into the score-weighted mean, not just the best",
      "centre x %.3f (want %.3f)" % (got_cx, want_cx))
check(abs(merged[0].score - w_hi) < 1e-9, "and keep the best member's score")
scores[k + 400] = 2.0
raw[k + 400, 2:4] = (40.0, 40.0)
two = det_net.decode(raw, scores, ratio, 0, pad_top)
check(len(two) == 2 and two[0].score > two[1].score, "a separate face survives and the list is best-first")
check(det_net.decode(raw, np.full(896, -10.0), ratio, 0, pad_top) == [], "nothing above threshold gives an empty list")
try:
    det_net.decode(raw[:50], scores[:50], ratio, 0, pad_top)
    check(False, "a wrong anchor count is refused")
except ValueError:
    check(True, "a wrong anchor count is refused, not silently mis-decoded")

boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float64)
kps = np.zeros((3, 6, 2))
out = F.weighted_nms(boxes, kps, np.array([0.9, 0.3, 0.7]), 0.3)
check(len(out) == 2 and abs(out[0][0][0] - (0.9 * 0 + 0.3 * 1) / 1.2) < 1e-9, "weighted_nms: clusters by IoU and averages by score")

print("")
print("THE REAL NETWORKS")
models_present = all(os.path.isfile(p) for p in (F.DETECTOR_MODEL, F.MESH_MODEL, F.BLENDSHAPE_MODEL))
clip = os.path.normpath(os.path.join(EV.UNITY_PROJECT_ROOT, "..", "video", "video.webm"))
if not models_present:
    print("  SKIP the face models are not installed (tools/model/fetch_face_models.py)")
else:
    cpu = ["CPUExecutionProvider"]
    detector = F.FaceDetector(providers=cpu)
    mesh = F.FaceMeshNet(providers=cpu)
    blend = F.BlendshapeNet(providers=cpu)
    check(detector.session.get_outputs()[0].shape[1] == 896, "the detector emits 896 anchors, as the anchor table assumes")
    check(detector.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == [], "a black frame has no face")
    noise = (np.random.RandomState(3).rand(480, 640, 3) * 255).astype(np.uint8)
    check(len(detector.detect(noise)) == 0, "random noise has no face")
    if os.path.isfile(clip):
        cap = cv2.VideoCapture(clip)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 100)
        ok, frame = cap.read()
        cap.release()
        crop = frame[60:560, 110:610]
        found = detector.detect(crop)
        check(len(found) == 1 and found[0].score > 0.9, "a real frame, cropped to the face: one face at score > 0.9", "%d faces" % len(found))
        result = mesh.infer(crop, F.roi_from_detection(found[0]))
        check(result.presence > 0.9, "its mesh presence is above 0.9", "%.3f" % result.presence)
        inside = ((result.landmarks[:468, 0] > 0) & (result.landmarks[:468, 0] < crop.shape[1])).all()
        check(inside, "all 468 mesh landmarks fall inside the picture")
        eye_gap = np.linalg.norm(result.landmarks[F.LEFT_EYE_OUTER, :2] - result.landmarks[F.RIGHT_EYE_OUTER, :2])
        face_w = found[0].box[2] - found[0].box[0]
        check(0.4 < eye_gap / face_w < 0.9, "the outer eye corners span about half the detected face width", "%.2f" % (eye_gap / face_w))
        scores52 = blend.infer(result.landmarks)
        check(scores52.shape == (52,) and scores52.min() >= 0.0 and scores52.max() <= 1.0, "52 blendshape scores in [0, 1]")
        # A neutral, closed-mouth face: jawOpen low. (This clip is a woman smiling with a closed mouth.)
        check(scores52[F.BLENDSHAPE_INDEX["jawOpen"]] < 0.2, "a closed mouth reads jawOpen < 0.2", "%.3f" % scores52[25])
        tracked_roi = F.roi_from_landmarks(result.landmarks[:, :2])
        again = mesh.infer(crop, tracked_roi)
        check(again.presence > 0.9 and np.abs(again.landmarks[:, :2] - result.landmarks[:, :2]).mean() < 1.5,
              "the next-frame crop predicted from the landmarks reads the same face (mean landmark shift < 1.5 px)",
              "%.2f px" % np.abs(again.landmarks[:, :2] - result.landmarks[:, :2]).mean())
        full = detector.detect(frame)
        check(len(full) == 0, "(and the whole 720x1280 frame, where the face is a few percent of the width, finds none: the detector is SHORT RANGE)")
    else:
        print("  SKIP the sample clip is not present: %s" % clip)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
