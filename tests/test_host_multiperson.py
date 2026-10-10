#!/usr/bin/env python3
"""F-52 tests: the host-side person detector and the multi-person layer that uses it.

Three layers, cheapest first, so a failure says WHICH layer broke:

  1. THE DETECTOR'S ARITHMETIC, with no model and no GPU. The released YOLOX-s ONNX is RAW: cell
     offsets and log sizes, not pixels. Reading it as pixels gives boxes that look plausible and are
     wrong, so decode(), nms() and the letterbox mapping are pinned against hand-computed values. A
     fake session is injected into the real HostPersonDetector.detect(), so the preprocessing and the
     inverse mapping back to the original frame are exercised too. Two tiny ONNX files built here
     prove the constructor refuses an export it cannot read.
  2. HostMultiPerson with a SCRIPTED detector and a SCRIPTED pose model. The tracker, the per-person
     state and the wire shape are real; only the two networks are stand-ins, so every case (a person
     who leaves, a cap, a reset, a hip nobody can see) is deterministic.
  3. THE SENDER'S DEFAULT IS UNTOUCHED: --max-poses 1 must not load the detector or change the path.
  4. THE REAL NETWORKS on a real clip, skipped with a message when the weights or the clip are absent
     (neither is in version control).

Run: .venv\\Scripts\\python.exe tests/test_host_multiperson.py   (exit 0 on pass)
"""
import math
import os
import re
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import sidecar_supervisor as SUP
import video_udp_sender as V

# Imported AFTER video_udp_sender on purpose: layer 1's last case asserts that importing the sender does
# not drag the detector in, which only means something if nothing here imported it first.
HERE_MODULES_LOADED_BY_SENDER = [m for m in ("host_person_detector", "host_multiperson") if m in sys.modules]

import host_multiperson as HMP
import host_person_detector as HPD
import person_tracker as PT

PASS = 0
FAIL = 0
SKIP = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-70s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-70s %s" % (label, detail))


def close(a, b, tol=1e-3):
    return abs(float(a) - float(b)) <= tol


print("== layer 1: the detector's arithmetic")

grids, strides = HPD.build_grid()
check(grids.shape == (8400, 2) and strides.shape == (8400, 1), "anchor grid is 8400 x (2, 1)",
      str(grids.shape))
check(int((strides[:, 0] == 8).sum()) == 6400 and int((strides[:, 0] == 16).sum()) == 1600
      and int((strides[:, 0] == 32).sum()) == 400, "80x80 + 40x40 + 20x20 cells at strides 8/16/32")
check(strides[0, 0] == 8 and strides[6399, 0] == 8 and strides[6400, 0] == 16 and strides[-1, 0] == 32,
      "stride 8 block first, then 16, then 32 (the head's concatenation order)")
check(tuple(grids[0]) == (0, 0) and tuple(grids[1]) == (1, 0) and tuple(grids[80]) == (0, 1),
      "cells run row-major: x fastest, then y")

# One anchor in the stride-8 block, cell (40, 30) -> index 30*80+40. Offsets chosen so the decoded box is
# centre (330, 250) px of the 640 input, size 80 x 200.
ANCHOR = 30 * 80 + 40
raw = np.zeros((8400, 85), dtype=np.float32)
raw[ANCHOR, 0] = 330.0 / 8.0 - 40
raw[ANCHOR, 1] = 250.0 / 8.0 - 30
raw[ANCHOR, 2] = math.log(80.0 / 8.0)
raw[ANCHOR, 3] = math.log(200.0 / 8.0)
raw[ANCHOR, 4] = 0.9
raw[ANCHOR, 5 + HPD.PERSON_CLASS] = 0.9
boxes, scores = HPD.decode(raw, grids, strides, 0.45)
check(boxes.shape == (1, 4) and scores.shape == (1,), "one confident row decodes to one box", str(boxes.shape))
check(np.allclose(boxes[0], [290.0, 150.0, 370.0, 350.0], atol=0.01), "box = (cell + offset) * stride, exp(size) * stride",
      str(boxes[0].tolist()))
check(close(scores[0], 0.81), "score = objectness x person-class probability", "%.3f" % scores[0])

raw_other = raw.copy()
raw_other[ANCHOR, 5 + HPD.PERSON_CLASS] = 0.0
raw_other[ANCHOR, 5 + 2] = 0.95      # COCO class 2 is "car": confident, and must not become a person
b2, s2 = HPD.decode(raw_other, grids, strides, 0.45)
check(b2.shape[0] == 0, "a confident NON-person class is not a detection")

raw_low = raw.copy()
raw_low[ANCHOR, 4] = 0.4             # 0.4 x 0.9 = 0.36 < 0.45
check(HPD.decode(raw_low, grids, strides, 0.45)[0].shape[0] == 0, "below the confidence bar is dropped")
check(HPD.decode(raw_low, grids, strides, 0.30)[0].shape[0] == 1, "the bar is a parameter, not a constant")
check(HPD.decode(np.zeros((8400, 85), np.float32), grids, strides, 0.45)[0].shape == (0, 4),
      "an empty scene decodes to an empty (0, 4) array, not an error")

# NMS
nb = np.array([[0, 0, 100, 200], [5, 5, 105, 205], [300, 0, 400, 200], [10, 0, 110, 200]], dtype=np.float32)
ns = np.array([0.8, 0.9, 0.7, 0.6], dtype=np.float32)
kept = HPD.nms(nb, ns, 0.45)
check(kept == [1, 2], "NMS keeps the best of overlapping boxes and the far one", str(kept))
check(HPD.nms(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), 0.45) == [], "NMS of nothing is nothing")
# Two shoulder-to-shoulder people: overlap 33 x 200 of a 33400 union is IoU 0.198, BELOW the 0.45 bar, so
# both survive - the case that matters.
side = np.array([[0, 0, 100, 200], [67, 0, 167, 200]], dtype=np.float32)
check(len(HPD.nms(side, np.array([0.9, 0.8], np.float32), 0.45)) == 2,
      "two people standing shoulder to shoulder are NOT merged")
check(HPD.nms(side, np.array([0.9, 0.8], np.float32), 0.15) == [0],
      "...and the same pair IS merged when the bar is lowered to 0.15 (the bar is what separates them)")

check(close(HPD.letterbox_scale(1080, 1920), 1.0 / 3.0, 1e-6) and close(HPD.letterbox_scale(1920, 1080), 1.0 / 3.0, 1e-6),
      "one scale fits portrait and landscape 1080p into 640")
check(close(HPD.letterbox_scale(320, 240), 2.0, 1e-6), "a small frame is scaled UP to fill the input")


class FakeSession:
    """Returns a fixed raw tensor and records what it was fed."""

    def __init__(self, tensor):
        self.tensor = tensor
        self.fed = None

    def run(self, names, feeds):
        self.fed = next(iter(feeds.values())).copy()
        return [self.tensor[None]]


def detector_with(tensor, conf=0.45):
    """A real HostPersonDetector.detect() over a fake session (no model file, no GPU)."""
    d = object.__new__(HPD.HostPersonDetector)
    d.session = FakeSession(tensor)
    d.input_name = "images"
    d.grids = grids
    d.strides = strides
    d.conf = conf
    d.nms_iou = HPD.DEFAULT_NMS_IOU
    d._canvas = np.empty((HPD.INPUT_SIZE, HPD.INPUT_SIZE, 3), dtype=np.uint8)
    d._blob = np.empty((1, 3, HPD.INPUT_SIZE, HPD.INPUT_SIZE), dtype=np.float32)
    d.last_ms = 0.0
    return d


# A box at input (170..230, 240..360): through a 1080x1920 frame (scale 1/3) it must come back as
# x 510..690 / y 720..1080 px, i.e. (0.4722, 0.375, 0.6389, 0.5625) normalised.
raw2 = np.zeros((8400, 85), dtype=np.float32)
raw2[ANCHOR, 0] = 200.0 / 8.0 - 40
raw2[ANCHOR, 1] = 300.0 / 8.0 - 30
raw2[ANCHOR, 2] = math.log(60.0 / 8.0)
raw2[ANCHOR, 3] = math.log(120.0 / 8.0)
raw2[ANCHOR, 4] = 0.9
raw2[ANCHOR, 5] = 0.9
white = np.full((1920, 1080, 3), 255, dtype=np.uint8)
det = detector_with(raw2)
found = det.detect(white)
check(len(found) == 1, "detect() over a fake network returns the one person", str(len(found)))
exp = (510.0 / 1080.0, 720.0 / 1920.0, 690.0 / 1080.0, 1080.0 / 1920.0)
check(len(found) == 1 and all(close(a, b, 1e-3) for a, b in zip(found[0].box, exp)),
      "the box is mapped back to the ORIGINAL frame, normalised",
      str([round(v, 4) for v in found[0].box]) if found else "")
check(len(found) == 1 and found[0].depth is None and close(found[0].score, 0.81),
      "a detection carries the score and no depth (a camera has none)")
# Landscape, same scale 1/3: the same input box means x 510..690 of 1920 and y 720..1080 of 1080 -> clipped.
land = det.detect(np.full((1080, 1920, 3), 255, dtype=np.uint8))
exp_l = (510.0 / 1920.0, 720.0 / 1080.0, 690.0 / 1920.0, 1.0)
check(len(land) == 1 and all(close(a, b, 1e-3) for a, b in zip(land[0].box, exp_l)),
      "landscape maps back correctly and a box past the picture is clipped to it",
      str([round(v, 4) for v in land[0].box]) if land else "")

# Pre-processing: top-left placement on 114 grey, BGR 0-255, no mean/std.
fed = det.session.fed
check(fed.shape == (1, 3, 640, 640) and fed.dtype == np.float32, "the tensor is float32 [1,3,640,640]")
nw = int(1920 * HPD.letterbox_scale(1920, 1080))
nh = int(1080 * HPD.letterbox_scale(1920, 1080))
check(nw == 640 and nh == 360, "a 1920x1080 frame fills the width: 640 x 360")
check(float(fed[0, 0, 5, 5]) == 255.0 and float(fed[0, 2, nh - 5, 5]) == 255.0,
      "the picture sits at the TOP-LEFT with raw 0-255 values (no normalisation)")
check(float(fed[0, 0, nh + 5, 5]) == float(HPD.PAD_VALUE) and float(fed[0, 2, 639, 639]) == float(HPD.PAD_VALUE),
      "below the picture is YOLOX's 114 grey")
bgr = np.zeros((360, 640, 3), dtype=np.uint8)
bgr[:, :, 0] = 10     # B
bgr[:, :, 1] = 20     # G
bgr[:, :, 2] = 30     # R
det.detect(bgr)
check(float(det.session.fed[0, 0, 5, 5]) == 10.0 and float(det.session.fed[0, 2, 5, 5]) == 30.0,
      "channel order stays BGR (the release was trained on cv2's order)")

# Constructor refusals, with real (tiny) ONNX files.
tmp = tempfile.mkdtemp(prefix="f52_test_")
try:
    import onnx
    from onnx import TensorProto, helper

    def tiny_model(path, out_shape):
        value = helper.make_tensor("c", TensorProto.FLOAT, out_shape,
                                   np.zeros(int(np.prod(out_shape)), dtype=np.float32).tolist())
        node = helper.make_node("Constant", [], ["output"], value=value)
        graph = helper.make_graph(
            [node], "g",
            [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT, out_shape)])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
        model.ir_version = 7
        onnx.save(model, path)

    bad = os.path.join(tmp, "decoded.onnx")
    tiny_model(bad, [1, 100, 6])
    try:
        HPD.HostPersonDetector(bad, providers=["CPUExecutionProvider"])
        check(False, "a decoded / different-size export is refused")
    except RuntimeError as e:
        check("unexpected detector output" in str(e), "a decoded / different-size export is refused, naming why", str(e)[:70])
    good = os.path.join(tmp, "raw.onnx")
    tiny_model(good, [1, 8400, 85])
    d_ok = HPD.HostPersonDetector(good, providers=["CPUExecutionProvider"])
    check(d_ok.detect(np.zeros((480, 640, 3), np.uint8)) == [], "an export of the right shape loads; an empty scene finds nobody")
    check("raw.onnx" in d_ok.describe() and "CPUExecutionProvider" in d_ok.describe(), "describe() names the weights and the provider")
except ImportError:
    print("  SKIP the two tiny-ONNX constructor cases: the onnx package is not installed")
try:
    HPD.HostPersonDetector(os.path.join(tmp, "nope.onnx"))
    check(False, "missing weights raise")
except RuntimeError as e:
    check("fetch_yolox_s.py" in str(e), "missing weights say how to get them", str(e)[:60])

print("")
print("== layer 2: HostMultiPerson with scripted networks")

W_PX, H_PX = 640, 360
FRAME = np.zeros((H_PX, W_PX, 3), dtype=np.uint8)
FX = 0.5 * W_PX / math.tan(math.radians(60.0) * 0.5)
INTR = (FX, FX, W_PX * 0.5, H_PX * 0.5)


class ScriptedDetector:
    """Returns the boxes scripted for each call (normalised x1,y1,x2,y2), then nobody."""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.last_ms = 2.0

    def detect(self, frame):
        boxes = self.script[self.calls] if self.calls < len(self.script) else []
        self.calls += 1
        return [PT.Detection(b, 0.9, None) for b in boxes]


class ScriptedPose:
    """A stand-in RTMW3D: a confident upright person laid out inside the crop it is given.

    `hips_conf` lets a case make the hips unreadable. The layout is fixed by the crop (cx, cy, w, h) so
    two people at different places produce different hips, which is what the id/position cases need.
    """

    def __init__(self, hips_conf=0.9):
        self.hips_conf = hips_conf
        self.calls = 0

    def infer(self, frame, bbox):
        self.calls += 1
        cx, cy, w, h = bbox
        uv = np.zeros((133, 2), dtype=np.float32)
        uv[:, 0] = cx
        uv[:, 1] = cy
        uv[0] = (cx, cy - 0.40 * h)
        uv[5] = (cx - 0.10 * w, cy - 0.25 * h)
        uv[6] = (cx + 0.10 * w, cy - 0.25 * h)
        uv[11] = (cx - 0.06 * w, cy + 0.05 * h)
        uv[12] = (cx + 0.06 * w, cy + 0.05 * h)
        uv[15] = (cx - 0.05 * w, cy + 0.40 * h)
        uv[16] = (cx + 0.05 * w, cy + 0.40 * h)
        conf = np.full(133, 0.9, dtype=np.float32)
        conf[11] = conf[12] = self.hips_conf
        return uv, np.zeros(133, dtype=np.float32), conf


def make(script, max_poses=3, pose=None, **kw):
    return HMP.HostMultiPerson(pose or ScriptedPose(), ScriptedDetector(script), V.estimate_hip_depth,
                               "test", max_poses=max_poses, **kw)


LEFT = (0.15, 0.15, 0.30, 0.90)
RIGHT = (0.65, 0.15, 0.80, 0.90)


def run(m, n, start=0, fps=30.0):
    out = []
    for i in range(start, start + n):
        out.append(m.process(FRAME, INTR, i / fps, 100.0 + i / fps, 100.0 + i / fps))
    return out


# --- one person
m = make([[LEFT]] * 12)
msgs = run(m, 12)
first = next((i for i, x in enumerate(msgs) if x is not None), None)
check(first is not None and first >= 2, "a person is NOT sent before the tracker confirms them", "first at frame %s" % first)
check(all(x is not None for x in msgs[first:]), "...and is sent on every frame after", "")
msg = msgs[-1]
check(msg["n"] == 1 and msg["persons"][0]["id"] == 1 and msg["sid"] == "test", "one person, id 1, the session id")
check(set(["persons", "n", "ndet", "ntrack", "seq", "sid", "t", "lat", "lm", "xyz", "src", "st"]).issubset(msg),
      "the datagram has the F-32 fields AND the single-person root fields")
p0 = msg["persons"][0]
check(len(p0["lm"]) == 33 and len(p0["st"]) == 33 and len(p0["src"]) == 33, "33 landmarks, 33 states, 33 depth sources")
check(msg["lm"] == p0["lm"] and msg["src"] == p0["src"], "the root IS persons[0] (legacy primary)")
check(close(msg["xyz"][0], p0["xyz"][0] * 0.45, 0.2) and close(msg["xyz"][1], p0["xyz"][1] * 0.45, 0.2)
      and close(msg["xyz"][2], p0["xyz"][2], 0.01),
      "root x and y are scaled by --sway-gain 0.45, z never is",
      "%s vs %s" % (msg["xyz"], p0["xyz"]))
check(abs(p0["xyz"][0]) > 1.0, "the person's hip is placed from the box, not at the origin", str(p0["xyz"]))
seqs = [x["seq"] for x in msgs if x is not None]
check(seqs == list(range(1, len(seqs) + 1)), "seq counts SENT datagrams, one per frame, from 1")
check(sum(1 for pt in p0["lm"] if pt[3] > 0) >= 7, "the scripted pose emits its confident landmarks",
      "%d of 33" % sum(1 for pt in p0["lm"] if pt[3] > 0))

m2 = make([[LEFT]] * 8, sway_gain=1.0)
last = run(m2, 8)[-1]
check(close(last["xyz"][0], last["persons"][0]["xyz"][0], 0.2), "--sway-gain 1.0 leaves the root unscaled")

# --- two people, the cap
m = make([[LEFT, RIGHT]] * 10, max_poses=2)
last = run(m, 10)[-1]
ids = [p["id"] for p in last["persons"]]
check(last["n"] == 2 and len(set(ids)) == 2, "two people -> two persons with distinct ids", str(ids))
xs = sorted(p["xyz"][0] for p in last["persons"])
check(xs[0] < 0 < xs[1], "left person is left of the optical axis, right person is right", str(xs))
check(last["xyz"][2] == last["persons"][0]["xyz"][2] and last["lm"] == last["persons"][0]["lm"],
      "the root follows persons[0] whichever person that is")
m = make([[LEFT, RIGHT]] * 10, max_poses=1)
last = run(m, 10)[-1]
check(last["n"] == 1 and last["ntrack"] == 2, "--max-poses 1 poses one person but still TRACKS both", "n=%d ntrack=%d" % (last["n"], last["ntrack"]))
check(HMP.HostMultiPerson(ScriptedPose(), ScriptedDetector([]), V.estimate_hip_depth, "t", max_poses=0).max_poses == 1,
      "a cap of 0 is clamped to 1 rather than posing nobody forever")
pose_counter = ScriptedPose()
m = make([[LEFT, RIGHT]] * 10, max_poses=1, pose=pose_counter)
run(m, 10)
check(pose_counter.calls <= 8, "the cap bounds the pose solves (the GPU budget)", "%d solves over 10 frames" % pose_counter.calls)

# --- stable ids through steady movement
walk = []
for i in range(40):
    dx = 0.004 * i
    walk.append([(0.15 + dx, 0.15, 0.30 + dx, 0.90), (0.65 - dx, 0.15, 0.80 - dx, 0.90)])
m = make(walk, max_poses=2)
seen = set()
for x in run(m, 40):
    if x is not None:
        seen.update(p["id"] for p in x["persons"])
check(seen == {1, 2}, "two people drifting toward each other keep ids 1 and 2", str(sorted(seen)))

# --- a person who leaves
# Gone for 4 s: longer than the tracker's 1.2 s hold AND the 3 s the per-person state is kept.
leave = [[LEFT]] * 10 + [[]] * 120 + [[LEFT]] * 12
m = make(leave)
msgs = run(m, len(leave))
ids_first = {p["id"] for x in msgs[:10] if x is not None for p in x["persons"]}
ids_after = {p["id"] for x in msgs[130:] if x is not None for p in x["persons"]}
check(ids_first == {1} and len(ids_after) == 1 and ids_after.isdisjoint(ids_first),
      "gone for 4 s (> the tracker's 1.2 s hold) -> a NEW id on return, the old id is never reused",
      "%s then %s" % (sorted(ids_first), sorted(ids_after)))
check(all(x is None for x in msgs[12:130]), "nobody is sent while nobody is detected (a held track is not posed)")
check(1 not in m.states, "the departed person's smoothing state is released", str(sorted(m.states)))

brief = [[LEFT]] * 10 + [[]] * 8 + [[LEFT]] * 12      # 0.27 s: shorter than the 1.2 s hold
m = make(brief)
msgs = run(m, len(brief))
ids_all = {p["id"] for x in msgs if x is not None for p in x["persons"]}
check(ids_all == {1}, "a 0.27 s occlusion keeps the SAME id", str(sorted(ids_all)))
held = [x for x in msgs[11:18]]
check(all(x is None for x in held[3:]) and m.states.get(1) is not None, "a held (LOST) track is not posed, and keeps its state")

# --- hips nobody can see
m = make([[LEFT]] * 10, pose=ScriptedPose(hips_conf=0.1))
msgs = run(m, 10)
check(all(x is None for x in msgs), "a person whose hips are not confident is simply absent from the datagram")
check(m.states == {}, "...and no filter state is created for them")

# --- reset and independence
m = make([[LEFT, RIGHT]] * 10 + [[RIGHT]] * 10, max_poses=2)
run(m, 10)
st_ids = sorted(m.states)
check(len(st_ids) == 2 and m.states[st_ids[0]] is not m.states[st_ids[1]]
      and m.states[st_ids[0]].zfilt is not m.states[st_ids[1]].zfilt
      and m.states[st_ids[0]].skel is not m.states[st_ids[1]].skel,
      "every person has their OWN filters and joint tracker, nothing shared")
m.reset()
check(m.states == {} and m.tracker.drain_events() == [], "reset() drops every identity and every filter")
m.detector.calls = 10
nxt = run(m, 6, start=10)
rid = [p["id"] for x in nxt if x is not None for p in x["persons"]]
check(rid and set(rid) == {1}, "after reset the person now in view is id 1, not the id 2 they held before (a replayed clip is a new scene)",
      str(sorted(set(rid))))

# --- the heartbeat fragment stays parseable by the supervisor
m = make([[LEFT]] * 8)
run(m, 8)
frag = m.summary()
check(re.search(r"people=\d+ tracks=\d+ dets=\d+ detect~[\d.]+ms pose~[\d.]+ms", frag) is not None, "summary() has the documented fields", frag)
line = "[vid] frames=8 sent=6 skipped=2 fps~29.9 replay=0 live=Y dropped=0 reconnects=0 %s" % frag
hb = SUP.parse_heartbeat(line)
check(hb is not None and hb["Frames"] == 8 and hb["Sent"] == 6 and hb["Skipped"] == 2,
      "the supervisor's heartbeat parser still reads the line with the extra fields", str(hb))
check(hb is not None and hb["Note"] == "" and hb["Live"] == "Y",
      "...and the new fields do not leak into the free-text note the Unity MODEL light reads", repr(hb and hb["Note"]))

print("")
print("== layer 3: the single-person default is untouched")
check(HERE_MODULES_LOADED_BY_SENDER == [], "importing video_udp_sender does NOT load the detector or the multi layer",
      str(HERE_MODULES_LOADED_BY_SENDER))
src = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check(re.search(r'add_argument\("--max-poses",\s*type=int,\s*default=1', src) is not None, "--max-poses defaults to 1")
check(re.search(r'add_argument\("--det-conf",\s*type=float,\s*default=0\.45', src) is not None, "--det-conf defaults to 0.45")
check('add_argument("--detector"' in src, "--detector exists")
det_src = open(HPD.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check('os.environ.get("VIRTUAL_MIRROR_DETECTOR")' in det_src and '"models", "yolox_s.onnx"' in det_src,
      "the weights path and environment variable are the ones Unity's VideoSourceSpec checks before launching")
check(SUP.VIDEO_MAX_PEOPLE == 8, "the supervisor's people cap is 8, as VideoSourceSpec.MaxPeople")
check(src.index("if multi is not None:\n                # F-52") < src.index("uv, zrel, conf = model.infer(frame, state[\"bbox\"])"),
      "the multi branch returns BEFORE the single-person model call (no double inference)")

print("")
print("== layer 4: the real networks (skipped when the weights or the clips are absent)")
DET_FILE = HPD.DEFAULT_DETECTOR_ONNX
VIDEO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "video")
CLIP_ONE = os.path.join(VIDEO_DIR, "video.webm")
CLIP_MANY = os.path.join(VIDEO_DIR, "456.webm")
if not os.path.isfile(DET_FILE):
    SKIP += 1
    print("  SKIP no detector weights at %s - run tools/model/fetch_yolox_s.py" % DET_FILE)
else:
    import cv2
    real = HPD.HostPersonDetector()
    if os.path.isfile(CLIP_ONE):
        cap = cv2.VideoCapture(CLIP_ONE)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 120)
        ok, f = cap.read()
        got = real.detect(f)
        check(ok and len(got) == 1, "the real detector finds the one person in video.webm", str(len(got)))
        if got:
            b = got[0].box
            check(0.0 <= b[0] < b[2] <= 1.0 and 0.0 <= b[1] < b[3] <= 1.0 and (b[3] - b[1]) > 1.5 * (b[2] - b[0]) * (f.shape[1] / float(f.shape[0])) * 0.9,
                  "...as an upright box inside the picture", str([round(v, 3) for v in b]))
            check(got[0].score > 0.6, "...with a confident score", "%.2f" % got[0].score)
        blank = real.detect(np.full((720, 1280, 3), 128, dtype=np.uint8))
        check(blank == [], "a flat grey frame finds nobody (no phantom people)")
    else:
        SKIP += 1
        print("  SKIP %s is not present" % CLIP_ONE)
    if os.path.isfile(CLIP_MANY):
        cap = cv2.VideoCapture(CLIP_MANY)
        counts = []
        for _ in range(30):
            ok, f = cap.read()
            counts.append(len(real.detect(f)))
        check(min(counts) >= 4 and max(counts) <= 9, "the real detector finds most of the 8 dancers, never a crowd of phantoms",
              "min %d max %d over 30 frames" % (min(counts), max(counts)))
    else:
        SKIP += 1
        print("  SKIP %s is not present" % CLIP_MANY)

print("")
print("-" * 78)
print("%d/%d assertions passed%s" % (PASS, PASS + FAIL, ("   (%d skipped)" % SKIP) if SKIP else ""))
sys.exit(0 if FAIL == 0 else 1)
