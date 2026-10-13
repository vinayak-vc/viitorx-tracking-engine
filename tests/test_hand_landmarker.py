#!/usr/bin/env python3
"""H-01 tests for hand_landmarker.py: the geometry (ROIs, warps, anchors, NMS, decoding) and, when the models and the
sample clip are present, one real-network smoke test.

The geometry is where a hand model silently goes wrong - a half-pixel shift, a transposed rotation, a letterbox
offset - and a network will happily return plausible-looking landmarks for a crop that is slightly wrong, so each of
those is pinned with an exact synthetic case.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np

import evidence_paths as EV
import hand_landmarker as H
import _synthetic_hand as S

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-64s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-64s %s" % (label, detail))


print("ANCHORS")
anchors = H.make_palm_anchors()
check(anchors.shape == (H.PALM_EXPECTED_ANCHORS, 2), "2016 anchors, the count the network emits", str(anchors.shape))
check(float(anchors.min()) > 0.0 and float(anchors.max()) < 1.0, "every centre is inside the picture")
check(np.allclose(anchors[0], anchors[1]) and not np.allclose(anchors[1], anchors[2]),
      "the two anchors of a cell coincide, the next cell does not")
check(abs(anchors[2][0] - anchors[0][0] - 1.0 / 24.0) < 1e-6, "stride-8 cells are 1/24 of the picture apart")
check(abs(anchors[24 * 24 * 2][0] - 0.5 / 12.0) < 1e-6, "the stride-16 grid (12 x 12) starts after the 24 x 24 one")
check(len({tuple(a) for a in anchors}) == 24 * 24 + 12 * 12, "distinct centres: 576 + 144")

print("")
print("ANGLES")
check(abs(H.theta_from_up_vector(0, -1)) < 1e-9, "an upright hand needs no turn")
check(abs(H.theta_from_up_vector(1, 0) - math.pi / 2) < 1e-9, "a hand pointing right is turned +90 degrees")
check(abs(H.theta_from_up_vector(-1, 0) + math.pi / 2) < 1e-9, "a hand pointing left is turned -90 degrees")
check(abs(abs(H.theta_from_up_vector(0, 1)) - math.pi) < 1e-9, "a hand pointing down is turned 180 degrees")
check(abs(H.normalize_angle(3 * math.pi / 2) + math.pi / 2) < 1e-9, "angles wrap into [-pi, pi)")

print("")
print("ROI GEOMETRY")
roi = H.Roi(300.0, 200.0, 100.0, math.radians(30))
u, v = roi.axes()
check(abs(np.linalg.norm(u) - 1) < 1e-12 and abs(u.dot(v)) < 1e-12 and u[0] * v[1] - u[1] * v[0] > 0,
      "the crop axes are orthonormal and right-handed (no mirroring)")
pts = np.array([[0.0, 0.0], [1.0, 0.0], [0.5, 0.5], [0.25, 0.9]])
check(np.allclose(roi.source_to_crop(roi.crop_to_source(pts)), pts, atol=1e-12), "crop -> source -> crop is the identity")
check(np.allclose(roi.crop_to_source(np.array([[0.5, 0.5]]))[0], [300.0, 200.0]), "crop centre is the ROI centre")
check(np.allclose(np.linalg.norm(roi.corners()[0] - roi.corners()[1]), 100.0), "a side of the ROI is `size` long")

print("")
print("THE WARP AGREES WITH THE MAPPING")
# A bright dot at a known source position must land at the crop pixel the mapping predicts, for several turns.
for turn in (0, 30, 90, 180, -60):
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    roi = H.Roi(320.0, 240.0, 200.0, math.radians(turn))
    target_crop = np.array([[0.75, 0.30]])
    sx, sy = roi.crop_to_source(target_crop)[0]
    cv2.circle(img, (int(round(sx)), int(round(sy))), 3, (255, 255, 255), -1)
    patch = (H.cut_roi(img, roi, 224)[0, :, :, 0] > 0.5)
    ys, xs = np.nonzero(patch)
    found = np.array([xs.mean() + 0.5, ys.mean() + 0.5]) / 224.0 if xs.size else np.array([-1.0, -1.0])
    check(xs.size > 0 and np.linalg.norm(found - target_crop[0]) < 0.012,
          "turn %4d deg: a dot at crop (0.75, 0.30) lands there" % turn, "found %s" % np.round(found, 3))
# An unturned ROI is exactly the sub-image.
img = (np.random.RandomState(0).rand(300, 400, 3) * 255).astype(np.uint8)
roi = H.Roi(200.0, 150.0, 100.0, 0.0)
patch = H.cut_roi(img, roi, 100)[0]
want = cv2.cvtColor(img[100:200, 150:250], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
check(np.abs(patch - want).max() < 1e-5, "an unturned crop at 1:1 is the sub-image, pixel for pixel",
      "max diff %.2e" % np.abs(patch - want).max())
outside = H.cut_roi(img, H.Roi(-500.0, -500.0, 100.0, 0.0), 32)
check(float(outside.max()) == 0.0, "a ROI outside the picture is black, not an error")

print("")
print("PALM -> ROI")
palm_up = H.Palm([180, 180, 220, 230], [[200, 225], [0, 0], [200, 190], [0, 0], [0, 0], [0, 0], [0, 0]], 0.9)
r = H.roi_from_palm(palm_up)
check(abs(r.theta) < 1e-9, "an upright palm gives an unturned ROI")
check(abs(r.size - 2.6 * 50.0) < 1e-9, "size is 2.6 x the box's longer side", "%.1f" % r.size)
check(r.cy < 205.0 and abs(r.cx - 200.0) < 1e-9, "the ROI moves UP the hand toward the fingers", "cy %.1f (box centre 205)" % r.cy)
palm_right = H.Palm([180, 180, 230, 220], [[185, 200], [0, 0], [225, 200], [0, 0], [0, 0], [0, 0], [0, 0]], 0.9)
r = H.roi_from_palm(palm_right)
check(abs(r.theta - math.pi / 2) < 1e-9, "a palm pointing right gives +90 degrees")
check(r.cx > 205.0 and abs(r.cy - 200.0) < 1e-9, "and the shift follows the turn: toward the fingers, to the right",
      "cx %.1f (box centre 205)" % r.cx)

print("")
print("LANDMARKS -> NEXT ROI")
hand = S.center(S.make_hand(**S.OPEN))
for turn in (0, 25, 90, -140, 180):
    pts2d = S.rotate(hand, 0, 0, turn)[:, :2] * 1500.0 + np.array([320.0, 240.0])
    roi = H.roi_from_landmarks(pts2d)
    crop = roi.source_to_crop(pts2d)
    check(crop.min() >= 0.0 and crop.max() <= 1.0, "turn %4d deg: every landmark is inside the next crop" % turn,
          "range %.2f..%.2f" % (crop.min(), crop.max()))
    wrist_to_middle = pts2d[H.MIDDLE_MCP] - pts2d[H.WRIST]
    up = -roi.axes()[1]
    cosine = float(wrist_to_middle.dot(up) / np.linalg.norm(wrist_to_middle))
    check(cosine > 0.999, "turn %4d deg: the crop's up is the wrist-to-knuckle axis" % turn, "cos %.4f" % cosine)
check(0.4 < roi.size / (np.ptp(pts2d, axis=0).max()) < 2.5, "the next crop is about the hand's size, scaled by 2")

print("")
print("DECODING THE LANDMARK NETWORK")
roi = H.Roi(300.0, 200.0, 224.0, 0.0)
lm = np.zeros((21, 3))
lm[:, 0] = 112.0
lm[:, 1] = 112.0
lm[5] = (212.0, 12.0, 30.0)
world = np.zeros((21, 3))
world[5] = (0.04, -0.02, 0.01)
res = H.decode_hand_outputs(lm.reshape(1, 63), [[0.9]], [[0.7]], world.reshape(1, 63), roi)
check(np.allclose(res.image[0, :2], [300.0, 200.0]), "the crop centre decodes to the ROI centre")
check(np.allclose(res.image[5, :2], [400.0, 100.0]), "a landmark 100 crop-px right and 100 up decodes to (+100, -100)")
check(abs(res.image[5, 2] - 30.0) < 1e-9, "z is scaled like x and y (here 1:1)")
check(np.allclose(res.world[5], [0.04, -0.02, 0.01]), "world landmarks are unchanged for an unturned crop")
check(abs(res.conf - 0.9) < 1e-9 and abs(res.handed - 0.7) < 1e-9, "confidence and handedness come through")
roi = H.Roi(300.0, 200.0, 112.0, math.pi / 2)
res = H.decode_hand_outputs(lm.reshape(1, 63), [[0.9]], [[0.7]], world.reshape(1, 63), roi)
check(np.allclose(res.world[5], [0.02, 0.04, 0.01], atol=1e-12), "a +90 degree crop turns the world x/y back into the image's",
      str(np.round(res.world[5], 4)))
check(np.allclose(res.image[5, :2], roi.crop_to_source(np.array([[212.0 / 224, 12.0 / 224]]))[0]),
      "landmarks go through the same mapping as the crop")
check(abs(res.image[5, 2] - 30.0 * 0.5) < 1e-9, "z scales with the crop's size (112 / 224)")

print("")
print("THE PALM DETECTOR'S DECODE")


class _NoSession(H.PalmDetector):
    """A PalmDetector without a network: decode() is pure numpy, so it is tested on its own."""

    def __init__(self):
        self.anchors = H.make_palm_anchors()
        self.score_threshold = 0.5
        self.nms_iou = 0.3


det = _NoSession()
raw = np.zeros((2016, 18))
scores = np.full(2016, -10.0)
k = 700
scores[k] = 3.0
raw[k, 2:4] = (48.0, 48.0)                      # box 48 x 48 input-px, centred on the anchor
raw[k, 4:6] = (0.0, 0.0)                         # keypoint 0 on the anchor centre
raw[k, 8:10] = (0.0, -19.2)                      # keypoint 2, 19.2 input-px above it
ratio = 192.0 / 640.0
pad_top = (192 - int(round(480 * ratio))) // 2
palms = det.decode(raw, scores, ratio, 0, pad_top)
check(len(palms) == 1, "one confident anchor gives one palm")
ax, ay = anchors[k]
cx_src = ax * 192.0 / ratio
cy_src = (ay * 192.0 - pad_top) / ratio
check(abs((palms[0].box[0] + palms[0].box[2]) / 2 - cx_src) < 1e-6 and abs((palms[0].box[1] + palms[0].box[3]) / 2 - cy_src) < 1e-6,
      "the box is centred on the anchor, mapped back through the letterbox", "centre (%.1f, %.1f)" % (cx_src, cy_src))
check(abs((palms[0].box[2] - palms[0].box[0]) - 48.0 / ratio) < 1e-6, "its width is 48 input-px / ratio", "%.1f px" % (palms[0].box[2] - palms[0].box[0]))
check(abs(palms[0].keypoints[2][1] - (cy_src - 19.2 / ratio)) < 1e-6, "keypoint 2 is 19.2 input-px above the centre")
check(abs(palms[0].score - 1.0 / (1.0 + math.exp(-3.0))) < 1e-9, "the score is the sigmoid of the logit")
scores[k + 1] = 2.5
raw[k + 1, 2:4] = (48.0, 48.0)
check(len(det.decode(raw, scores, ratio, 0, pad_top)) == 1, "two near-identical boxes are merged by NMS")
scores[k + 400] = 2.0
raw[k + 400, 2:4] = (48.0, 48.0)
far = det.decode(raw, scores, ratio, 0, pad_top)
check(len(far) == 2 and far[0].score > far[1].score, "a separate palm survives and the list is best-first", str(len(far)))
check(det.decode(raw, np.full(2016, -10.0), ratio, 0, pad_top) == [], "nothing above threshold gives an empty list")
try:
    det.decode(raw[:100], scores[:100], ratio, 0, pad_top)
    check(False, "a wrong anchor count is refused")
except ValueError:
    check(True, "a wrong anchor count is refused, not silently mis-decoded")

print("")
print("NMS")
boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float64)
check(H.nms(boxes, np.array([0.9, 0.8, 0.7]), 0.3) == [0, 2], "overlapping boxes keep the best")
check(abs(H.iou_xyxy([0, 0, 10, 10], [0, 0, 10, 10]) - 1.0) < 1e-12 and H.iou_xyxy([0, 0, 1, 1], [5, 5, 6, 6]) == 0.0, "IoU")

print("")
print("THE REAL NETWORKS")
models_present = os.path.isfile(H.PALM_MODEL) and os.path.isfile(H.HAND_MODEL)
clip = os.path.normpath(os.path.join(EV.UNITY_PROJECT_ROOT, "..", "video", "video.webm"))
if not models_present:
    print("  SKIP the hand models are not installed (tools/model/fetch_hand_models.py)")
else:
    pd = H.PalmDetector(providers=["CPUExecutionProvider"])
    net = H.HandLandmarkNet(providers=["CPUExecutionProvider"])
    check(pd.session.get_outputs()[0].shape[1] == 2016, "the palm model emits 2016 anchors, as the anchor table assumes")
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    check(pd.detect(blank) == [], "a black frame has no palms")
    noise = (np.random.RandomState(3).rand(480, 640, 3) * 255).astype(np.uint8)
    check(len(pd.detect(noise)) == 0, "random noise has no palms")
    if os.path.isfile(clip):
        cap = cv2.VideoCapture(clip)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 336)
        ok, frame = cap.read()
        cap.release()
        crop = cv2.resize(frame[150:850, 0:720], None, fx=1.4, fy=1.4)
        best = 0.0
        for palm in pd.detect(crop):
            result = net.infer(crop, H.roi_from_palm(palm))
            best = max(best, result.conf)
        check(best >= 0.8, "a real frame of a dancing person yields a hand at confidence >= 0.8", "best %.2f" % best)
    else:
        print("  SKIP the sample clip is not present: %s" % clip)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
