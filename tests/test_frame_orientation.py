#!/usr/bin/env python3
"""F-57 tests for frame_orientation.py: the rotation decision, the geometry reading and the lens rule.

Everything here runs without a model, a GPU or a camera. The decision logic takes an injected scorer, so
it is driven with a fake whose score depends only on a marker painted into the frame: a frame is "upright"
when the marker sits in the top-left pixel. Rotating a frame clockwise moves that marker, so the fake sees
exactly what the real scorer would see: one of the four rotations looks right and the others do not.

What is pinned:
  * EVERY DELIVERED ROTATION IS UNDONE. Frames delivered at 0/90/180/270 each lock to the rotation that
    makes them upright, and nothing is returned before the votes agree.
  * NO PERSON, NO DECISION. An empty room or an ambiguous frame casts no vote; after the time-out it
    keeps the operator's rotation instead of guessing or refusing frames forever.
  * A SIZE CHANGE (a phone turned between portrait and landscape) re-opens the decision.
  * THE SELF-CHECK IS SLOW AND CAUTIOUS: it never runs while poses are being emitted, and needs several
    consecutive agreeing checks to switch.
  * THE GEOMETRY READS A BODY STANDING UP, and says "no opinion" when it cannot see enough of one.
  * THE LENS RULE: with axis "long" a portrait frame and a landscape frame of one phone share a focal
    length; with "width" (the default) they do not, which is the F-49 behaviour.

Run: .venv\\Scripts\\python.exe tests/test_frame_orientation.py   (exit 0 on pass)
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import frame_orientation as FO

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-70s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-70s %s" % (label, detail))


def upright_frame(h=480, w=640):
    """A frame whose top-left pixel is the 'head' marker; everything else is zero."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[0, 0] = 255
    return frame


class MarkerScorer:
    """Scores 0.9 when the marker is top-left (upright), 0.2 otherwise, 0.0 when `empty` is set."""

    def __init__(self):
        self.empty = False
        self.calls = 0
        self.flat = None

    def score(self, frame):
        self.calls += 1
        if self.empty:
            return 0.0
        if self.flat is not None:
            return self.flat
        return 0.9 if frame[0, 0, 0] == 255 else 0.2


def delivered_at(degrees, h=480, w=640):
    """The upright frame as a source delivers it when it is turned `degrees` clockwise."""
    return FO.rotate_clockwise(upright_frame(h, w), degrees)


def feed(auto, frame, start, count, step=0.04):
    """Feed `count` frames; returns (the list of results, the clock after the last one)."""
    results = []
    now = start
    for _ in range(count):
        results.append(auto.rotation_for(frame, now))
        now += step
    return results, now


UNDO = {0: 0, 90: 270, 180: 180, 270: 90}
#: Square frames for tests that mix rotations: a rotation of a non-square frame changes its shape, and a
#: shape change is (correctly) a reason to re-open the decision, which would mask what those tests pin.
SQ = 480

print("rotate_clockwise")
src = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
check(FO.rotate_clockwise(src, 0) is src, "0 degrees returns the frame itself")
check(FO.rotate_clockwise(src, 90).shape[:2] == (3, 2), "90 degrees swaps height and width")
check(FO.rotate_clockwise(src, 180).shape[:2] == (2, 3), "180 degrees keeps the shape")
check(FO.rotate_clockwise(src, 270).shape[:2] == (3, 2), "270 degrees swaps height and width")
check(np.array_equal(FO.rotate_clockwise(FO.rotate_clockwise(src, 90), 270), src), "90 then 270 is the identity")
check(FO.rotate_clockwise(upright_frame(), 90)[0, -1, 0] == 255, "clockwise 90 moves the top-left marker to the top-right")
check(FO.rotate_clockwise(src, 45) is src, "a degree that is not 0/90/180/270 changes nothing")

print("focal_px")
f_land_w = FO.focal_px(1280, 720, 60.0, "width")
f_port_w = FO.focal_px(720, 1280, 60.0, "width")
f_land_l = FO.focal_px(1280, 720, 60.0, "long")
f_port_l = FO.focal_px(720, 1280, 60.0, "long")
check(abs(f_land_w - 0.5 * 1280 / math.tan(math.radians(30.0))) < 1e-9, "width rule on landscape is the F-49 formula exactly", "%.3f" % f_land_w)
check(abs(f_port_w - 0.5 * 720 / math.tan(math.radians(30.0))) < 1e-9, "width rule on portrait is the F-49 formula exactly", "%.3f" % f_port_w)
check(f_land_w != f_port_w, "width rule: turning the phone changes the focal length (the old behaviour)")
check(abs(f_land_l - f_port_l) < 1e-9, "long rule: portrait and landscape of one phone share a focal length", "%.3f vs %.3f" % (f_land_l, f_port_l))
check(abs(f_land_l - f_land_w) < 1e-9, "long rule does not change a landscape frame at all")
check(abs(FO.focal_px(1000, 1000, 90.0, "long") - 500.0) < 1e-9, "a 90 degree FOV across 1000 px is 500 px")

print("upright_geometry")
UP = np.zeros((133, 2))
CONF = np.zeros(133)
for idx, y in ((0, 100), (5, 200), (6, 200), (11, 300), (12, 300), (13, 400), (14, 400), (15, 500), (16, 500)):
    UP[idx] = (320, y)
    CONF[idx] = 0.9
check(FO.upright_geometry(UP, CONF) == 1.0, "head above shoulders above hips above knees above ankles is 1.0")
DOWN = UP.copy()
DOWN[:, 1] = 600 - DOWN[:, 1]
check(FO.upright_geometry(DOWN, CONF) == 0.0, "the same body upside down is 0.0")
SIDE = UP.copy()
SIDE[:, 1] = 300.0
check(FO.upright_geometry(SIDE, CONF) == 0.0, "a body lying sideways (all the same height) has no downward step")
low = CONF.copy()
low[[15, 16, 13, 14]] = 0.05
check(FO.upright_geometry(UP, low) == 1.0, "knees and ankles that were not seen are ignored, not counted as wrong")
few = np.zeros(133)
few[0] = 0.9
check(FO.upright_geometry(UP, few) == 0.5, "a single visible group gives no opinion (0.5)")
check(FO.upright_geometry(UP, np.zeros(133)) == 0.5, "no visible group gives no opinion (0.5)")
mixed = UP.copy()
mixed[15:17, 1] = 250.0
check(abs(FO.upright_geometry(mixed, CONF) - 0.75) < 1e-9, "one wrong step of four is 0.75")

print("candidate_score")
check(abs(FO.candidate_score(0.9, 0.8, 1.0) - 0.72) < 1e-9, "an upright body keeps its full detector x pose value")
check(abs(FO.candidate_score(0.9, 0.8, 0.0) - 0.72 * FO.GEOMETRY_FLOOR) < 1e-9, "an upside-down reading keeps only the floor")
check(0.0 < FO.GEOMETRY_FLOOR < 1.0, "the geometry floor leaves geometry a real say without letting it erase a strong detection", str(FO.GEOMETRY_FLOOR))
check(FO.candidate_score(0.9, 0.8, 0.0) < FO.candidate_score(0.9, 0.8, 0.5) < FO.candidate_score(0.9, 0.8, 1.0), "an upside-down reading always scores below a half-right one, and that below an upright one")
check(FO.candidate_score(0.0, 0.9, 1.0) == 0.0 and FO.candidate_score(0.9, 0.0, 1.0) == 0.0, "no detection or no pose scores zero")

print("AutoOrient decides the rotation that makes the frame upright")
for d in (0, 90, 180, 270):
    scorer = MarkerScorer()
    logs = []
    auto = FO.AutoOrient(scorer, initial=0, log=logs.append)
    results, now = feed(auto, delivered_at(d), 10.0, 10)
    first = next((i for i, r in enumerate(results) if r is not None), None)
    check(results[-1] == UNDO[d], "delivered at %3d: locks to %d" % (d, UNDO[d]), "results %s" % results)
    check(first == FO.VOTES_NEEDED - 1, "delivered at %3d: undecided for exactly %d frames, then decided" % (d, FO.VOTES_NEEDED - 1), "first decision at frame %s" % first)
    check(auto.locked and any("rotate %d" % UNDO[d] in line for line in logs), "delivered at %3d: the decision is logged" % d)

print("no person, no decision")
scorer = MarkerScorer()
scorer.empty = True
auto = FO.AutoOrient(scorer, initial=90, log=lambda t: None)
results, now = feed(auto, delivered_at(0), 0.0, 20, step=0.1)
check(all(r is None for r in results), "an empty room never locks and every frame is refused during the probe", str(set(results)))
results, now = feed(auto, delivered_at(0), FO.PROBE_TIMEOUT_SECONDS + 0.1, 1)
check(results[-1] == 90 and auto.locked, "after the time-out it keeps the operator's rotation", str(results))
scorer = MarkerScorer()
scorer.flat = 0.6
auto = FO.AutoOrient(scorer, initial=0, log=lambda t: None)
results, now = feed(auto, delivered_at(90), 0.0, 20, step=0.1)
check(all(r is None for r in results), "four equal scores are not a clear winner: no vote is cast")
scorer = MarkerScorer()
scorer.flat = FO.MIN_WIN_SCORE * 0.9
auto = FO.AutoOrient(scorer, initial=0, log=lambda t: None)
results, now = feed(auto, delivered_at(0), 0.0, 10, step=0.1)
check(all(r is None for r in results), "a winner below the minimum score is not a clear winner")

print("one odd frame cannot decide")
scorer = MarkerScorer()
auto = FO.AutoOrient(scorer, initial=0, log=lambda t: None)
frames = [delivered_at(90, SQ, SQ), delivered_at(90, SQ, SQ), delivered_at(0, SQ, SQ), delivered_at(90, SQ, SQ), delivered_at(90, SQ, SQ), delivered_at(90, SQ, SQ)]
out = [auto.rotation_for(f, 1.0 + 0.04 * i) for i, f in enumerate(frames)]
check(out[-1] == 270 and auto.locked, "five frames for one rotation and one for another still lock to the majority", str(out))
scorer = MarkerScorer()
auto = FO.AutoOrient(scorer, initial=0, log=lambda t: None)
frames = [delivered_at(90, SQ, SQ), delivered_at(0, SQ, SQ)] * 6
out = [auto.rotation_for(f, 1.0 + 0.04 * i) for i, f in enumerate(frames)]
check(all(r is None for r in out), "a source alternating between two rotations (50/50) never locks inside the time-out")

print("a size change re-opens the decision")
scorer = MarkerScorer()
logs = []
auto = FO.AutoOrient(scorer, initial=0, log=logs.append)
results, now = feed(auto, delivered_at(0, 480, 640), 1.0, 8)
check(auto.locked and auto.rotation == 0, "landscape upright locks to 0")
results, now = feed(auto, delivered_at(0, 640, 480), now, 1)
check(results[0] is None and not auto.locked, "the first portrait-shaped frame is refused and the decision is open again")
check(any("frame size changed 640x480 -> 480x640" in line for line in logs), "the reason names the old and new size")
results, now = feed(auto, delivered_at(90, 640, 480), now, 8)
check(auto.locked and auto.rotation == 270, "and it settles on the rotation the NEW frame needs", str(results))
results, now = feed(auto, delivered_at(90, 640, 480), now, 8)
check(all(r == 270 for r in results), "the same size again does not re-open it")

print("the self-check")
scorer = MarkerScorer()
auto = FO.AutoOrient(scorer, initial=0, log=lambda t: None)
results, now = feed(auto, delivered_at(0, SQ, SQ), 1.0, 8)
locked_at = now
calls = scorer.calls
for i in range(50):
    auto.note_sent(locked_at + i * 0.1)
    auto.rotation_for(delivered_at(90, SQ, SQ), locked_at + i * 0.1)
check(scorer.calls == calls, "while poses are being emitted the scorer is never called", "%d calls" % (scorer.calls - calls))
t = locked_at + 10.0
auto.note_sent(t)
sideways = delivered_at(90, SQ, SQ)
rots = []
for i in range(40):
    rots.append(auto.rotation_for(sideways, t + 0.1 * i))
check(set(rots) == {0}, "a few stalled seconds with a sideways picture do not switch yet", str(sorted(set(rots))))
t2 = t + FO.STALL_SECONDS + 0.5
rots = []
for i in range(FO.SWITCH_CHECKS + 1):
    rots.append(auto.rotation_for(sideways, t2 + i * (FO.RECHECK_INTERVAL_SECONDS + 0.05)))
check(rots[FO.SWITCH_CHECKS - 2] == 0 and rots[-1] == 270, "it switches only after %d agreeing checks, not before" % FO.SWITCH_CHECKS, str(rots))
scorer = MarkerScorer()
auto = FO.AutoOrient(scorer, initial=0, log=lambda t_: None)
feed(auto, delivered_at(0, SQ, SQ), 1.0, 8)
t = 100.0
seq = [delivered_at(90, SQ, SQ), delivered_at(0, SQ, SQ), delivered_at(90, SQ, SQ), delivered_at(0, SQ, SQ), delivered_at(90, SQ, SQ), delivered_at(0, SQ, SQ)]
rots = []
for i, f in enumerate(seq):
    rots.append(auto.rotation_for(f, t + i * (FO.RECHECK_INTERVAL_SECONDS + 0.05)))
check(set(rots) == {0}, "checks that disagree with each other never accumulate into a switch", str(rots))

print("seed_bbox")


class Det(object):
    def __init__(self, box, score):
        self.box = box
        self.score = score


class FakeDetector(object):
    def __init__(self, dets):
        self.dets = dets

    def detect(self, frame):
        return self.dets


frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
check(FO.seed_bbox(FakeDetector([]), frame) is None, "no person gives None, so the caller keeps its fallback")
near = Det((0.70, 0.10, 0.90, 0.90), 0.8)
far = Det((0.10, 0.40, 0.14, 0.60), 0.95)
bbox = FO.seed_bbox(FakeDetector([far, near]), frame)
check(bbox is not None and abs(bbox[0] - 0.8 * 1920) < 1.0, "it picks the larger, nearer person over the more confident small one", str(bbox))
check(bbox is not None and abs(bbox[2] / bbox[3] - 0.75) < 1e-6, "the crop has the pose model's 3:4 aspect", str(bbox))

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
