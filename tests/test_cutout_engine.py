#!/usr/bin/env python3
"""S-01 tests for the crop geometry (cutout_segmenter), the engine (cutout_engine) and, last, the real networks on a real frame.

The engine is driven with a SCRIPTED detector and a FAKE network, so every rule is checked against a known answer: where a crop goes, when the
tracker lets a person in, how long a missed person is held, which network a whole-picture fallback uses, which picture a crop is cut from.
The real-network check at the end is skipped when the weights or the test clip are not on this machine.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import cv2
import numpy as np

import cutout_engine as E
import cutout_matte as M
import cutout_segmenter as S
import person_tracker as PT

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-86s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-86s %s" % (label, detail))


print("size_for: THE WORKING SIZE")
check(S.size_for(1280, 720, 640) == (640, 360), "1280x720 at a long side of 640 is 640x360")
check(S.size_for(720, 1280, 640) == (360, 640), "portrait: the long side is the height")
check(S.size_for(320, 180, 640) == (320, 180), "a picture already smaller is never enlarged")
check(S.size_for(1920, 1080, 0) == (1920, 1080), "0 means as it is")
check(S.size_for(1001, 563, 500) == (500, 281), "the aspect ratio is kept to within a pixel", str(S.size_for(1001, 563, 500)))

print("")
print("square_roi: THE CROP AROUND A PERSON")
x0, y0, s = S.square_roi((0.4, 0.2, 0.6, 0.8), 1000, 1000, 0.2)
check(s == 840 and (x0, y0) == (80, 80), "a tall box: side = height x (1 + 2 x margin), centred on the box", "%s" % ((x0, y0, s),))
x0, y0, s = S.square_roi((0.1, 0.4, 0.9, 0.6), 1000, 1000, 0.1)
check(s == 960 and x0 == 20 and y0 == 20, "a wide box (arms out): the side follows the wider dimension", "%s" % ((x0, y0, s),))
x0, y0, s = S.square_roi((0.0, 0.0, 0.1, 0.1), 1000, 1000, 0.18)
check(x0 < 0 and y0 < 0, "a person at the corner gives a crop that hangs over the picture (negative origin)", "%s" % ((x0, y0, s),))
check(S.square_roi((0.5, 0.5, 0.5001, 0.5001), 1000, 1000, 0.1)[2] == S.MIN_SIDE, "a box of a few pixels is raised to MIN_SIDE")
check(all(isinstance(v, int) for v in S.square_roi((0.3, 0.3, 0.6, 0.9), 1280, 720, 0.18)), "the crop is integers")

print("")
print("crop_square: CUTTING IT OUT")
img = np.zeros((100, 200, 3), np.uint8)
img[:, :, 0] = np.arange(200, dtype=np.uint8)[None, :]
img[:, :, 1] = np.arange(100, dtype=np.uint8)[:, None]
inside = S.crop_square(img, (50, 20, 40))
check(inside.shape == (40, 40, 3) and np.array_equal(inside, img[20:60, 50:90]), "a crop inside the picture is the exact slice")
over = S.crop_square(img, (-10, -10, 40))
check(over.shape == (40, 40, 3) and np.array_equal(over[10:, 10:], img[0:30, 0:30]), "a crop over the corner holds the real pixels where there are some")
check(np.all(over[:10, 12] == over[10, 12]) and np.all(over[12, :10] == over[12, 10]), "and repeats the border pixel where there are none (not black)")
check(S.crop_square(img, (500, 500, 30)).shape == (30, 30, 3) and not S.crop_square(img, (500, 500, 30)).any(), "a crop wholly outside the picture is an empty square")
check(S.crop_square(img, (150, 60, 100)).shape == (100, 100, 3), "a crop larger than the remaining picture still comes back square")

print("")
print("feather_window AND paste_max")
w = S.feather_window(100, 0.1)
check(w.shape == (100, 100) and abs(float(w[50, 50]) - 1.0) < 1e-6 and float(w[0, 50]) < 0.2 and float(w[50, 0]) < 0.2, "1 in the middle, small at the edge")
check(np.allclose(w, w[::-1, ::-1]) and float(w.min()) > 0.0, "symmetric, and never exactly zero")
canvas = np.zeros((90, 160), np.float32)
alpha = np.ones((256, 256), np.float32)
S.paste_max(canvas, alpha, (40, 20, 50), 1.0, 0.0)
check(canvas[45, 65] == 1.0 and canvas[10, 10] == 0.0 and canvas[70, 91] == 0.0, "a pasted crop fills its square and nothing else")
check(float((canvas > 0).sum()) == 50 * 50, "exactly side x side pixels", str(int((canvas > 0).sum())))
canvas = np.zeros((90, 160), np.float32)
canvas[30:40, 50:60] = 0.9
S.paste_max(canvas, np.full((256, 256), 0.4, np.float32), (40, 20, 50), 1.0, 0.0)
check(abs(float(canvas[35, 55]) - 0.9) < 1e-6 and abs(float(canvas[60, 45]) - 0.4) < 1e-6, "the larger of the old and new value wins")
canvas = np.zeros((90, 160), np.float32)
S.paste_max(canvas, alpha, (100, 40, 80), 0.5, 0.0)
check(canvas[30, 60] == 1.0 and canvas[59, 89] == 1.0 and canvas[19, 49] == 0.0 and canvas[60, 90] == 0.0 and float((canvas > 0).sum()) == 40 * 40,
      "scale 0.5 puts an 80 px crop at (100,40) as 40x40 at (50,20)", str(int((canvas > 0).sum())))
canvas = np.zeros((90, 160), np.float32)
S.paste_max(canvas, alpha, (-20, -20, 60), 1.0, 0.0)
check(canvas[0, 0] == 1.0 and canvas[39, 39] == 1.0 and canvas[40, 40] == 0.0, "a crop over the corner is clipped to the canvas")
S.paste_max(canvas, alpha, (1000, 1000, 60), 1.0, 0.0)
check(float(canvas.sum()) == 40 * 40, "a crop wholly outside the canvas changes nothing")
canvas = np.zeros((200, 200), np.float32)
S.paste_max(canvas, alpha, (20, 20, 100), 1.0, 0.2)
check(canvas[70, 70] == 1.0 and 0.0 < canvas[70, 21] < 0.5 and canvas[19, 70] == 0.0, "with feathering the pasted square fades at its own edge")

print("")
print("blank_neighbours: THE OTHER PEOPLE PAINTED OUT OF A CROP")


def scene(side=200, background=(20, 20, 20), person=(200, 50, 50), box=(0.5, 0.2, 0.8, 0.8)):
    """A flat background with one person-coloured rectangle at `box` (normalised), and that box."""
    pic = np.zeros((side, side, 3), np.uint8)
    pic[:, :] = background
    x1, y1, x2, y2 = box
    pic[int(y1 * side):int(y2 * side), int(x1 * side):int(x2 * side)] = person
    return pic, box


pic, box = scene()
original = pic.copy()
n = S.blank_neighbours(pic, [box])
check(n == 1, "one neighbour painted", str(n))
check(int(abs(pic[100, 130].astype(int) - 20).max()) <= 2, "its box now has the background's colour", str(pic[100, 130].tolist()))
check(np.array_equal(pic[:, :80], original[:, :80]) and np.array_equal(pic[:30], original[:30]), "and nothing far from it changed")
# A bright strip just OUTSIDE the neighbour's grown box (which starts at column 97): the paint's soft edge reaches into it, and the strip
# is thin enough that the ring's median is still the background.
pic, box = scene()
pic[40:160, 90:97] = (200, 50, 50)
S.blank_neighbours(pic, [box])
strip = [int(pic[100, x, 0]) for x in range(90, 100)]
check(all(strip[i] >= strip[i + 1] for i in range(len(strip) - 1)) and strip[0] >= 170 and strip[-1] <= 22 and any(30 < v < 150 for v in strip),
      "the painted edge is soft: it fades into the picture over a few pixels, not a hard rectangle", str(strip))
pic, box = scene()
n = S.blank_neighbours(pic, [])
check(n == 0 and np.array_equal(pic, scene()[0]), "no neighbours: nothing happens")
pic, box = scene()
n = S.blank_neighbours(pic, [(1.2, 0.2, 1.5, 0.8)])
check(n == 0 and np.array_equal(pic, scene()[0]), "a neighbour entirely outside the crop changes nothing")
pic, box = scene()
n = S.blank_neighbours(pic, [(-0.3, -0.3, 1.3, 1.3)])
check(n == 0 and np.array_equal(pic, scene()[0]), "a neighbour whose box fills the crop has no ring to take a colour from: left alone")
pic, box = scene()
n = S.blank_neighbours(pic, [box], keep=(0.4, 0.2, 0.6, 0.8))
check(tuple(pic[100, 110].tolist()) == (200, 50, 50), "this person's OWN box is never painted, even where it overlaps a neighbour's", str(pic[100, 110].tolist()))
check(int(abs(pic[100, 150].astype(int) - 20).max()) <= 2, "while the neighbour's pixels outside it are", str(pic[100, 150].tolist()))
pic = np.zeros((200, 200, 3), np.uint8)
pic[:, :] = (20, 20, 20)
pic[:, :100] = (60, 60, 60)
pic[40:160, 120:160] = (200, 50, 50)
S.blank_neighbours(pic, [(0.6, 0.2, 0.8, 0.8)])
check(int(abs(pic[100, 140].astype(int) - 20).max()) <= 2, "the paint is the colour of the ring beside the neighbour (the dark side), not of the far side of the crop", str(pic[100, 140].tolist()))
pic, box = scene()
two_boxes = [box, (0.05, 0.1, 0.25, 0.9)]
pic[20:180, 10:50] = (50, 200, 50)
n = S.blank_neighbours(pic, two_boxes)
check(n == 2 and int(abs(pic[100, 30].astype(int) - 20).max()) <= 3 and int(abs(pic[100, 130].astype(int) - 20).max()) <= 3, "several neighbours are each painted", "n=%d" % n)
pic, box = scene(side=64)
n = S.blank_neighbours(pic, [box])
check(n == 1 and pic.dtype == np.uint8 and pic.shape == (64, 64, 3), "the picture stays uint8 [h, w, 3] whatever its size")


class FakeNet:
    """Stands in for a SelfieSegmenter: returns the picture's RED channel (so the preprocessing is observable), or a fixed ellipse."""

    def __init__(self, width=256, height=256, mode="red"):
        self.width = width
        self.height = height
        self.mode = mode
        self.calls = []
        #: (neighbours, keep) of every call, in step with `calls`: what the engine asked to be painted out of each crop.
        self.isolation = []
        self.last_ms = 0.0

    def infer(self, bgr, flip_average=False, neighbours=None, keep=None):
        self.calls.append((bgr.shape[:2], flip_average))
        self.isolation.append((neighbours, keep))
        if self.mode == "red":
            return cv2.resize(bgr[:, :, 2].astype(np.float32) / 255.0, (self.width, self.height), interpolation=cv2.INTER_AREA)
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        e = ((xx - self.width / 2.0) / (self.width * 0.30)) ** 2 + ((yy - self.height / 2.0) / (self.height * 0.42)) ** 2
        return (e <= 1.0).astype(np.float32)

    def describe(self):
        return "fake"


class ScriptedDetector:
    """Returns the scripted detections, one list per detect() call (the last list repeats). Records what picture it was given."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.calls = 0

    def detect(self, picture):
        self.seen.append(picture.shape[:2])
        i = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return list(self.script[i])


def det(x1, y1, x2, y2, score=0.9):
    return PT.Detection((x1, y1, x2, y2), score)


def frame_of(w=1280, h=720):
    f = np.zeros((h, w, 3), np.uint8)
    f[:, :, :] = 40
    return f


def engine(detector, general=None, landscape=None, **kw):
    cfg = E.CutoutConfig(matte=M.MatteConfig(guided=False, temporal=False, cleanup=True, solid_inset_frac=0.0), **kw)
    return E.CutoutEngine(general or FakeNet(mode="ellipse"), landscape, detector, cfg)


print("")
print("SelfieSegmenter: PREPROCESSING (a fake ONNX session)")


class FakeSession:
    def __init__(self, h, w):
        self.h, self.w = h, w
        self.fed = []

    def run(self, _outputs, feeds):
        blob = list(feeds.values())[0]
        self.fed.append(blob.copy())
        return [blob[:, 0:1, :, :].copy()]        # echo the first channel (R after BGR->RGB) as the 'probability'


seg = object.__new__(S.SelfieSegmenter)
seg.model_path = "fake.onnx"
seg.width, seg.height, seg.input_name = 64, 32, "pixel_values"
seg.session = FakeSession(32, 64)
seg._blob = np.empty((1, 3, 32, 64), np.float32)
seg.last_ms = 0.0
seg.calls = 0
pic = np.zeros((90, 160, 3), np.uint8)
pic[:, :, 2] = 255          # pure red in BGR
pic[:, :80, 2] = 0          # left half not red
out = seg.infer(pic)
check(out.shape == (32, 64) and out.dtype == np.float32, "the output is [height, width] float32 at the network's size", str(out.shape))
check(float(out[:, 5].max()) < 0.01 and float(out[:, 58].min()) > 0.99, "red is channel 0 (BGR was swapped to RGB) and scaled to 0..1")
fed = seg.session.fed[-1]
check(fed.shape == (1, 3, 32, 64) and fed.dtype == np.float32, "the tensor is NCHW float32", str(fed.shape))
out_flip = seg.infer(pic, flip_average=True)
check(len(seg.session.fed) == 3, "flip_average makes a second network call (two sequential, not one batch)", str(len(seg.session.fed)))
check(np.array_equal(seg.session.fed[2], seg.session.fed[1][:, :, :, ::-1]), "the second call is given the MIRROR IMAGE of the first picture's tensor")
expected = 0.5 * (seg.session.fed[1][0, 0] + seg.session.fed[2][0, 0][:, ::-1])
check(np.allclose(out_flip, np.clip(expected, 0, 1), atol=1e-6), "and the result is the mean of the two answers, the second un-mirrored")
big = np.zeros((1000, 1000, 3), np.uint8)
seg.infer(big)
check(seg.last_ms >= 0.0 and seg.calls == 3 and seg.session.fed[-1].shape == (1, 3, 32, 64), "a large crop goes through the two-step shrink and still returns", "calls=%d" % seg.calls)
# The right half is a red 'person' beside a dark 'background'; the neighbour's box covers that half. The echoed red channel is the probability.
seg.session.fed.clear()
alone = seg.infer(pic)
with_neighbour = seg.infer(pic, False, [(0.5, 0.0, 1.0, 1.0)], None)
check(float(alone[:, 58].min()) > 0.99 and float(with_neighbour[:, 58].max()) < 0.05, "a neighbour's box is painted over before the network reads the picture", "%.2f -> %.2f" % (float(alone[:, 58].min()), float(with_neighbour[:, 58].max())))
check(float(with_neighbour[:, 5].max()) < 0.01, "and the far side of the picture is left as it was")
kept = seg.infer(pic, False, [(0.5, 0.0, 1.0, 1.0)], (0.6, 0.0, 1.0, 1.0))
check(float(kept[:, 58].min()) > 0.99, "the person's own box (keep) is protected from it", "%.2f" % float(kept[:, 58].min()))
check(np.array_equal(pic[:, 80:, 2], np.full((90, 80), 255, np.uint8)), "the caller's picture is never modified (the painting is on the network-sized copy)")

print("")
print("THE ENGINE WITH A SCRIPTED DETECTOR")
two = [det(0.12, 0.10, 0.30, 0.90), det(0.70, 0.10, 0.88, 0.90)]
d = ScriptedDetector([two])
eng = engine(d)
f = frame_of()
res = None
modes = []
for i in range(5):
    res = eng.update(f, i / 30.0)
    modes.append(res.mode)
check(modes[:2] == [E.MODE_NONE, E.MODE_NONE] or modes[:2] == [E.MODE_WHOLE, E.MODE_WHOLE], "a person is not shown before the tracker confirms them (three detections)", str(modes))
eng2 = engine(ScriptedDetector([two]), fallback=False)
modes = [eng2.update(f, i / 30.0).mode for i in range(5)]
check(modes[:2] == [E.MODE_NONE, E.MODE_NONE] and modes[2:] == [E.MODE_ROI] * 3, "with fallback off: nothing for two frames, then crops from the third", str(modes))
check(res.mode == E.MODE_ROI and res.people == 2 and len(res.boxes) == 2, "two confirmed people -> two crops, two people in the mask", "people=%d boxes=%d" % (res.people, len(res.boxes)))
check(res.alpha.shape == (360, 640) and res.alpha.dtype == np.uint8, "the mask is at the working size", str(res.alpha.shape))
left = res.alpha[:, :320]
right = res.alpha[:, 320:]
check(left.max() == 255 and right.max() == 255, "each person's blob is in its own half of the picture")
check(int(res.alpha[:, 260:380].max()) == 0, "and the space between them is empty")
check(res.fg_permille > 30 and res.model_id == S.MODEL_GENERAL, "fg_permille and the network id are reported", "fg=%d model=%d" % (res.fg_permille, res.model_id))
check(len(res) == res.people, "len(result) is the number of people (the shared sender loop counts frames 'with a result' by it)")
net = eng2.general
check(all(c[0][0] == c[0][1] for c in net.calls) and len(net.calls) == 6, "every crop handed to the network is square", str(net.calls[:2]))
check(not any(c[1] for c in net.calls), "flip averaging is off by default")

eng3 = engine(ScriptedDetector([two]), fallback=False, tta=True)
for i in range(4):
    eng3.update(f, i / 30.0)
check(all(c[1] for c in eng3.general.calls), "with tta on every network call asks for flip averaging")

print("")
print("A PERSON THE DETECTOR MISSES FOR A WHILE IS HELD, THEN RELEASED")
d = ScriptedDetector([two] * 5 + [[]] * 10)
eng = engine(d, fallback=False)
res = [eng.update(f, i / 30.0) for i in range(15)]
check(res[4].mode == E.MODE_ROI and res[6].mode == E.MODE_ROI and res[6].people == 2, "two frames after the detector lost them they still have their crops", res[6].mode)
check(res[14].mode == E.MODE_ROI, "and nine frames (0.3 s) after: still held (the tracker holds 1.2 s)")
eng = engine(ScriptedDetector([two] * 5 + [[]] * 80), fallback=False)
last = None
for i in range(85):
    last = eng.update(f, i / 30.0)
check(last.mode == E.MODE_NONE and last.people == 0 and int(last.alpha.max()) == 0, "after 1.2 s without a detection the crops are released and the mask is empty", last.mode)

print("")
print("NOBODY DETECTED: THE WHOLE-PICTURE FALLBACK")
land = FakeNet(256, 144, mode="red")
gen = FakeNet(256, 256, mode="red")
eng = engine(ScriptedDetector([[]]), general=gen, landscape=land)
r = eng.update(frame_of(1280, 720), 0.0)
check(r.mode == E.MODE_WHOLE and r.model_id == S.MODEL_LANDSCAPE and len(land.calls) == 1 and len(gen.calls) == 0,
      "a 16:9 picture goes to the landscape network, whole", "model=%d" % r.model_id)
eng = engine(ScriptedDetector([[]]), general=gen, landscape=land)
r = eng.update(frame_of(640, 480), 0.0)
check(r.model_id == S.MODEL_GENERAL and len(gen.calls) == 1, "a 4:3 picture goes to the square network", "model=%d" % r.model_id)
eng = engine(ScriptedDetector([[]]), general=FakeNet(mode="red"), landscape=None)
r = eng.update(frame_of(1280, 720), 0.0)
check(r.mode == E.MODE_WHOLE and r.model_id == S.MODEL_GENERAL, "without the landscape file the square network serves 16:9 too")
eng = engine(ScriptedDetector([[]]), fallback=False)
r = eng.update(frame_of(), 0.0)
check(r.mode == E.MODE_NONE and r.people == 0 and int(r.alpha.max()) == 0, "with the fallback off and nobody detected the mask is empty")
d = ScriptedDetector([[]])
eng = engine(d, roi=False, fallback=True)
eng.detector = d
eng.update(frame_of(), 0.0)
eng.update(frame_of(), 0.1)
check(d.calls == 0, "roi=False never calls the detector")
eng = E.CutoutEngine(FakeNet(mode="red"), None, None, E.CutoutConfig(matte=M.MatteConfig(guided=False, temporal=False)))
check(eng.update(frame_of(), 0.0).mode == E.MODE_WHOLE, "no detector at all: the whole-picture path")

print("")
print("WHICH PICTURE THE DETECTOR AND THE CROPS ARE READ FROM")
d = ScriptedDetector([two])
eng = engine(d, fallback=False)
eng.update(frame_of(1280, 720), 0.0)
check(d.seen[0] == (360, 640), "a 1280x720 frame: the detector reads the 640x360 area-averaged copy", str(d.seen[0]))
d = ScriptedDetector([two])
eng = engine(d, fallback=False, mask_long_side=320)
eng.update(frame_of(1280, 720), 0.0)
check(d.seen[0] == (720, 1280), "a working size under the detector's own 640: it reads the full frame instead", str(d.seen[0]))
d = ScriptedDetector([[det(0.1, 0.05, 0.6, 0.95)]])
gen = FakeNet(mode="ellipse")
eng = engine(d, general=gen, fallback=False)
for i in range(3):
    eng.update(frame_of(1920, 1080), i / 30.0)
full_side = S.square_roi((0.1, 0.05, 0.6, 0.95), 1920, 1080, 0.18)[2]
scaled_side = int(round(full_side * 640.0 / 1920.0))
check(len(gen.calls) == 1 and abs(gen.calls[0][0][0] - scaled_side) <= 2 and gen.calls[0][0][0] < full_side // 2,
      "a big person is cut from the shrunken picture (the crop is a third the size of a full-size one)",
      "%s vs full-size %d" % (gen.calls[0][0], full_side))
d = ScriptedDetector([[det(0.40, 0.40, 0.46, 0.60)]])
gen = FakeNet(mode="ellipse")
eng = engine(d, general=gen, fallback=False)
for i in range(3):
    eng.update(frame_of(1920, 1080), i / 30.0)
side_full = S.square_roi((0.40, 0.40, 0.46, 0.60), 1920, 1080, 0.18)[2]
check(len(gen.calls) == 1 and abs(gen.calls[0][0][0] - side_full) <= 2, "a far (small) person is cut from the FULL picture, so the network gets every pixel there is",
      "%s vs full-frame crop %d" % (gen.calls[0][0], side_full))

print("")
print("max_people, detect_every, reset AND THE CROP'S OWN SMOOTHING")
many = [det(0.05 + 0.1 * i, 0.2, 0.12 + 0.1 * i, 0.9) for i in range(6)]
gen = FakeNet(mode="ellipse")
eng = engine(ScriptedDetector([many]), general=gen, fallback=False, max_people=2)
for i in range(4):
    eng.update(f, i / 30.0)
check(len(gen.calls) == 2 * 2 and len(eng.update(f, 0.2).boxes) == 2, "max_people caps the networks' calls per frame", str(len(gen.calls)))
d = ScriptedDetector([two])
eng = engine(d, fallback=False, detect_every=3)
for i in range(3):
    eng.update(f, i / 30.0)
check(d.calls == 3, "until somebody is confirmed the detector runs every frame, whatever detect_every says (a person must not wait)", str(d.calls))
before = d.calls
for i in range(3, 12):
    eng.update(f, i / 30.0)
check(d.calls - before == 3, "once people are confirmed detect_every=3 runs the detector on every third frame", str(d.calls - before))
d = ScriptedDetector([two])
eng = engine(d, fallback=False)
for i in range(4):
    eng.update(f, i / 30.0)
eng.reset()
r = eng.update(f, 0.2)
snap = eng.tracker.snapshot()
check(r.mode == E.MODE_NONE and snap["confirmed"] == 0 and snap["tracks"] == 2, "reset() forgets the tracks: each person must be confirmed all over again", str(snap))
eng = engine(ScriptedDetector([[det(0.3, 0.1, 0.5, 0.9)]]), fallback=False)
for i in range(3):
    eng.update(f, i / 30.0)
a = eng._cached[0]
eng.detector.script = [[det(0.32, 0.1, 0.52, 0.9)]]
eng.update(f, 0.1)
b = eng._cached[0]
raw_shift = S.square_roi((0.32, 0.1, 0.52, 0.9), 1280, 720, 0.18)[0] - S.square_roi((0.3, 0.1, 0.5, 0.9), 1280, 720, 0.18)[0]
check(0 < b[0] - a[0] < raw_shift, "a small move of the detector's box moves the crop only part of the way (no tremble)", "box %d px, crop %d px" % (raw_shift, b[0] - a[0]))
eng.detector.script = [[det(0.60, 0.1, 0.80, 0.9)]]
eng.update(f, 0.13)
c = eng._cached[0]
check(c[0] - b[0] > 100, "but a big jump is followed at once", "%d px" % (c[0] - b[0]))

print("")
print("ISOLATION: EACH CROP IS TOLD WHO ELSE IS IN THE PICTURE")
close = [det(0.30, 0.10, 0.42, 0.90), det(0.50, 0.10, 0.62, 0.90)]


def normalised(box, roi, w=1280, h=720):
    """A normalised detector box as the crop `roi` (x0, y0, side), in frame pixels, sees it."""
    x0, y0, side = roi
    return ((box[0] * w - x0) / side, (box[1] * h - y0) / side, (box[2] * w - x0) / side, (box[3] * h - y0) / side)


gen = FakeNet(mode="ellipse")
eng = engine(ScriptedDetector([close]), general=gen, fallback=False)
for i in range(5):
    eng.update(f, i / 30.0)
first, second = gen.isolation[-2], gen.isolation[-1]
crop_a, crop_b = eng._cached[0], eng._cached[1]
want_a = normalised((0.30, 0.10, 0.42, 0.90), crop_a)
want_b = normalised((0.50, 0.10, 0.62, 0.90), crop_b)
got_a = first[1]
got_b = second[1]
check(first[0] is not None and len(first[0]) == 1 and len(second[0]) == 1, "each of two people is told about exactly one other", "%s / %s" % (first[0], second[0]))
check(got_a is not None and max(abs(a - b) for a, b in zip(got_a, want_a)) < 0.02, "the first crop's own box is the first person's, in the crop's own units", "%s vs %s" % (got_a, want_a))
check(got_b is not None and max(abs(a - b) for a, b in zip(got_b, want_b)) < 0.02, "the second's likewise", "%s vs %s" % (got_b, want_b))
want_other = normalised((0.50, 0.10, 0.62, 0.90), crop_a)
check(max(abs(a - b) for a, b in zip(first[0][0], want_other)) < 0.02, "the neighbour handed to the first crop is the second person, as that crop sees them", "%s vs %s" % (first[0][0], want_other))
check(0.5 < first[0][0][0] < 1.0, "and in this scene they stand inside the first crop (the case the painting is for)", "%.2f" % first[0][0][0])

eng = engine(ScriptedDetector([close]), general=FakeNet(mode="ellipse"), fallback=False)
res = None
for i in range(5):
    res = eng.update(f, i / 30.0)
check(eng.matte.last_people == 1 and res.people == 2, "two people standing close are one foreground GROUP but two PEOPLE: the count is of the people being cut out",
      "groups=%d people=%d" % (eng.matte.last_people, res.people))
check("people=2" in eng.summary(), "and the heartbeat says the same", eng.summary())
check(engine(ScriptedDetector([[]]), fallback=False).update(f, 0.0).people == 0, "nobody in the mask is nobody")

gen = FakeNet(mode="ellipse")
eng = engine(ScriptedDetector([close]), general=gen, fallback=False, isolate=False)
for i in range(5):
    eng.update(f, i / 30.0)
check(len(gen.isolation) > 0 and all(n is None and k is None for n, k in gen.isolation), "isolate=False asks for nothing to be painted")

gen = FakeNet(mode="ellipse")
eng = engine(ScriptedDetector([[det(0.30, 0.10, 0.42, 0.90)]]), general=gen, fallback=False)
for i in range(5):
    eng.update(f, i / 30.0)
check(len(gen.isolation) > 0 and all(n is None and k is None for n, k in gen.isolation), "one person alone: nothing to paint out, so no work is asked for")

gen = FakeNet(mode="ellipse")
three = close + [det(0.70, 0.10, 0.82, 0.90)]
eng = engine(ScriptedDetector([three]), general=gen, fallback=False, max_people=2)
for i in range(5):
    eng.update(f, i / 30.0)
last_two = gen.isolation[-2:]
check(len(gen.calls) % 2 == 0 and all(n is not None and len(n) == 2 for n, k in last_two),
      "a third person past max_people has no crop of their own but is still painted out of the other two", "%s" % [None if n is None else len(n) for n, k in last_two])

gen = FakeNet(mode="ellipse")
eng = engine(ScriptedDetector([close]), general=gen, fallback=False, detect_every=3)
for i in range(12):
    eng.update(f, i / 30.0)
check(all(n is not None and len(n) == 1 for n, k in gen.isolation[-6:]), "frames that reuse the cached crops reuse their boxes too", "%d calls" % len(gen.isolation))
eng.reset()
check(eng._boxes == [] and eng._cached == [], "reset() forgets the boxes with the crops")

print("")
print("raw() AND update() AGREE")
d1 = ScriptedDetector([two])
d2 = ScriptedDetector([two])
e1 = engine(d1, fallback=False)
e2 = engine(d2, fallback=False)
for i in range(4):
    r1 = e1.update(f, i / 30.0)
    canvas, guide, crops, mode, mid = e2.raw(f, i / 30.0)
    r2 = e2.matte.update(canvas, guide)
check(np.array_equal(r1.alpha, r2) and mode == r1.mode, "update() is raw() followed by the matte")
check(guide.dtype == np.float32 and guide.shape == canvas.shape and 0.0 <= float(guide.min()) and float(guide.max()) <= 1.0, "the guide is a 0..1 float picture the size of the canvas")
check("people=" in e1.summary() and "mode=roi" in e1.summary(), "summary() is a one-line heartbeat fragment", e1.summary())

print("")
print("THE REAL NETWORKS ON A REAL FRAME (skipped when the weights or the clip are absent)")
clip = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "Games", "video", "video.webm")
if not (os.path.isfile(S.GENERAL_MODEL) and os.path.isfile(S.LANDSCAPE_MODEL) and os.path.isfile(clip)):
    print("  SKIP the segmenter weights or %s are not here" % clip)
else:
    providers = ["CPUExecutionProvider"]
    gen = S.SelfieSegmenter(S.GENERAL_MODEL, providers)
    land = S.SelfieSegmenter(S.LANDSCAPE_MODEL, providers)
    check((gen.width, gen.height, land.width, land.height) == (256, 256, 256, 144), "the two networks have the sizes the module documents", "%dx%d %dx%d" % (gen.width, gen.height, land.width, land.height))
    out = gen.infer(np.zeros((300, 300, 3), np.uint8))
    check(out.shape == (256, 256) and out.dtype == np.float32 and float(out.min()) >= 0.0 and float(out.max()) <= 1.0, "a blank picture gives a 256x256 map in 0..1")
    check(float(out.max()) < 0.1, "and finds nobody in a blank picture", "max %.3f" % float(out.max()))
    cap = cv2.VideoCapture(clip)
    cap.set(cv2.CAP_PROP_POS_MSEC, 6000)
    ok, real = cap.read()
    cap.release()
    check(ok, "the test clip decodes", clip)
    if ok:
        # No detector: the whole-picture path on a portrait frame. The dancer fills ~16% of the picture (measured in the S-01 report).
        eng = E.CutoutEngine(gen, land, None, E.CutoutConfig(matte=M.MatteConfig(temporal=False)))
        r = eng.update(real, 0.0)
        fg = r.fg_permille / 1000.0
        check(0.08 < fg < 0.30 and r.people == 1, "the whole-picture path finds one person covering 8-30% of the picture", "fg=%.3f people=%d" % (fg, r.people))
        ys, xs = np.nonzero(r.alpha > 127)
        top, bottom = ys.min(), ys.max()
        head = r.alpha[top:top + max(2, int(0.06 * (bottom - top))), :]
        check(int((head > 127).sum()) > 5, "the top of the figure (the head) is part of the mask")
        check(bottom > 0.85 * r.alpha.shape[0] * 0.9 or (bottom - top) > 0.55 * r.alpha.shape[0], "and the figure is tall: the legs and feet are in it", "%d of %d rows" % (bottom - top, r.alpha.shape[0]))

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
