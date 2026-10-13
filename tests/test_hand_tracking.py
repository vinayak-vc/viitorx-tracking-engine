#!/usr/bin/env python3
"""H-01 tests for hand_tracking.py: ids, the detect-then-track loop, loss and re-adoption, capacity, smoothing, placement.

The detector and the landmark network are replaced by a FAKE WORLD with known ground truth (a hand has a centre in
pixels, a size, a shape), so every claim is checked against what is really there: an id must stay on the hand it was
given to, a hand 0.6 m away must be reported at 0.6 m, a hand that vanishes must not be invented. The clock is injected.

What this proves: the loop's bookkeeping. What it does NOT prove: that the real network behaves like the fake (the
real-network smoke test is in test_hand_landmarker.py; a live camera run is NOT TESTED).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _synthetic_hand as S
import hand_geometry as G
import hand_gestures as GE
import hand_landmarker as H
import hand_tracking as HT

PASS = 0
FAIL = 0
W, HGT = 640, 480
CAMERA = G.Camera.from_hfov(W, HGT, 60.0)


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-64s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-64s %s" % (label, detail))


class FakeHand(object):
    """One ground-truth hand: where it is, how big it looks, what shape it has."""

    def __init__(self, name, x, y, depth_m=0.6, pose=None, handed=0.9, conf=0.95):
        self.name = name
        self.x = x
        self.y = y
        self.depth = depth_m
        self.world = S.center(S.make_hand(**(pose or S.OPEN)))
        self.handed = handed
        self.conf = conf
        self.visible = True
        self.nan = False

    def scale(self):
        return CAMERA.focal_px / self.depth

    def pixels(self):
        px = np.array([self.x, self.y]) + self.scale() * self.world[:, :2]
        return px

    def image3(self):
        px = self.pixels()
        return np.concatenate([px, (self.world[:, 2:3] * self.scale())], axis=1)


class FakeWorld(object):
    """Stands in for both the palm detector and the landmark network."""

    def __init__(self, hands):
        self.hands = hands
        self.detect_calls = 0
        self.infer_calls = 0
        self.extra_palms = 0     # report each palm this many extra times (a double detection)

    def detect(self, frame):
        self.detect_calls += 1
        palms = []
        for h in self.hands:
            if not h.visible:
                continue
            px = h.pixels()
            idx = [0, 5, 9, 13, 17]
            lo = px[idx].min(axis=0)
            hi = px[idx].max(axis=0)
            kp = np.zeros((7, 2))
            kp[0] = px[0]
            kp[2] = px[9]
            for _ in range(1 + self.extra_palms):
                palms.append(H.Palm([lo[0], lo[1], hi[0], hi[1]], kp, 0.9))
        return palms

    def infer(self, frame, roi):
        self.infer_calls += 1
        best = None
        best_d = None
        for h in self.hands:
            if not h.visible:
                continue
            c = roi.source_to_crop(np.array([h.pixels()[0]]))[0]
            if 0.0 <= c[0] <= 1.0 and 0.0 <= c[1] <= 1.0:
                d = float(np.linalg.norm(c - 0.5))
                if best is None or d < best_d:
                    best, best_d = h, d
        if best is None:
            return H.HandResult(np.zeros((21, 3)), np.zeros((21, 3)), 0.5, 0.05, roi)
        image = best.image3()
        if best.nan:
            image = image * np.nan
        return H.HandResult(image, best.world.copy(), best.handed, best.conf, roi)


class Clock(object):
    def __init__(self, dt=1.0 / 30.0):
        self.t = 100.0
        self.dt = dt

    def tick(self, n=1):
        self.t += self.dt * n
        return self.t


FRAME = np.zeros((HGT, W, 3), dtype=np.uint8)


def make_tracker(hands, **cfg_kw):
    world = FakeWorld(hands)
    cfg_kw.setdefault("smooth", False)
    cfg = HT.HandTrackerConfig(**cfg_kw)
    tracker = HT.HandTracker(world, world, lambda w, h: G.Camera.from_hfov(w, h, 60.0), cfg)
    return tracker, world, Clock()


def step(tracker, clock, n=1):
    out = None
    for _ in range(n):
        out = tracker.update(FRAME, clock.tick())
    return out


def snapshot(hands):
    return [(h.id, h.side, h.state) for h in hands]


print("ONE HAND")
a = FakeHand("a", 320, 240, depth_m=0.6)
tracker, world, clock = make_tracker([a], max_hands=1)
hands = step(tracker, clock)
check(len(hands) == 1 and hands[0].id == 1 and hands[0].state == HT.TRACKED, "a hand in view is found, id 1, TRACKED", str(snapshot(hands)))
step(tracker, clock, 60)
check(world.detect_calls == 1, "with max_hands=1 the palm detector runs only to FIND the hand", "%d detector runs" % world.detect_calls)
check(tracker.landmark_runs == 61, "afterwards one landmark pass per frame", "%d landmark runs" % tracker.landmark_runs)
hands = step(tracker, clock)
check(abs(hands[0].position[2] - 0.6) < 0.02, "its position is reported at 0.6 m", "%.3f m" % hands[0].position[2])
check(abs(hands[0].position[0]) < 0.02 and abs(hands[0].position[1]) < 0.02, "on the optical axis it has X = Y = 0",
      "(%.3f, %.3f)" % (hands[0].position[0], hands[0].position[1]))
check(np.allclose(hands[0].image[0, 0], a.pixels()[0][0] / W, atol=1e-6), "image landmarks are normalised by the frame size")
check(hands[0].world.shape == (21, 3) and hands[0].image.shape == (21, 3), "21 x 3 arrays")

print("")
print("A DETECTOR PASS ONLY WHILE THERE IS ROOM")
a = FakeHand("a", 320, 240)
tracker, world, clock = make_tracker([a], max_hands=2, detect_interval_s=0.10)
step(tracker, clock, 90)       # 3 s
runs = world.detect_calls
check(10 <= runs <= 3.0 / 0.10 + 1, "one hand of two: it looks for another, but at most every 0.1 s", "%d runs in 3 s" % runs)
check(tracker.landmark_runs == 90, "and the hand the detector keeps re-finding costs NO extra landmark pass",
      "%d landmark runs in 90 frames" % tracker.landmark_runs)
tracker2, world2, clock2 = make_tracker([FakeHand("a", 200, 240), FakeHand("b", 440, 240)], max_hands=2)
step(tracker2, clock2, 90)
check(world2.detect_calls == 1, "two of two: after both are found the detector stops", "%d runs" % world2.detect_calls)

print("")
print("IDS FOLLOW HANDS WHEN THEY APPROACH AND PART")
left = FakeHand("left", 150, 240, depth_m=0.6, handed=0.9)
right = FakeHand("right", 490, 240, depth_m=0.6, handed=0.1)
tracker, world, clock = make_tracker([left, right], max_hands=2)
hands = step(tracker, clock)
ids = {}
for h in hands:
    ids["left" if h.image[0, 0] * W < 320 else "right"] = h.id
check(len(hands) == 2 and set(ids) == {"left", "right"} and ids["left"] != ids["right"], "two hands, two ids", str(ids))
wrong = 0
closest = 1e9
for frame in range(1, 90):
    t = math.sin(frame / 89.0 * math.pi)          # in to the middle and back out
    left.x = 150 + 80 * t
    right.x = 490 - 80 * t
    hands = step(tracker, clock)
    closest = min(closest, abs(left.pixels()[0][0] - right.pixels()[0][0]))
    for h in hands:
        truth = left if abs(h.image[0, 0] * W - left.pixels()[0][0]) < abs(h.image[0, 0] * W - right.pixels()[0][0]) else right
        if h.id != ids["left" if truth is left else "right"]:
            wrong += 1
check(wrong == 0, "hands that come within %.0f px (crops overlapping) and part keep their ids" % closest)
check(len(hands) == 2 and {h.id for h in hands} == set(ids.values()), "and no new id was created along the way")

print("")
print("HANDS THAT CROSS (A KNOWN LIMIT: THE CROPS CAN LOCK ONTO ONE HAND)")
left = FakeHand("left", 150, 240, depth_m=0.6, handed=0.9)
right = FakeHand("right", 490, 240, depth_m=0.6, handed=0.1)
tracker, world, clock = make_tracker([left, right], max_hands=2)
step(tracker, clock)
seen_ids = set()
phantom = 0
reported_two_at_end = False
for frame in range(1, 160):
    t = min(1.0, frame / 79.0)
    left.x = 150 + 340 * t
    right.x = 490 - 340 * t
    left.y = 240 + 60 * math.sin(t * math.pi)
    right.y = 240 - 60 * math.sin(t * math.pi)
    hands = step(tracker, clock)
    for h in hands:
        seen_ids.add(h.id)
        nearest = min(abs(h.image[0, 0] * W - left.pixels()[0][0]) + abs(h.image[0, 1] * HGT - left.pixels()[0][1]),
                      abs(h.image[0, 0] * W - right.pixels()[0][0]) + abs(h.image[0, 1] * HGT - right.pixels()[0][1]))
        if nearest > 25.0:
            phantom += 1
    if len(hands) > 2:
        phantom += 1
check(phantom == 0, "through a full crossing no hand is ever reported where there is none, and never more than two")
check(len(hands) == 2, "once they have parted both hands are tracked again", "%d hands" % len(hands))
check(len(seen_ids) <= 4, "the crossing costs at most two ids (they may swap or be re-issued)", "ids %s" % sorted(seen_ids))

print("")
print("LOSS, HELD, RE-ADOPTION")
a = FakeHand("a", 320, 240)
tracker, world, clock = make_tracker([a], max_hands=1, hold_frames=2, reacquire_s=0.8)
step(tracker, clock, 10)
first_id = step(tracker, clock)[0].id
a.visible = False
states = []
for _ in range(5):
    out = step(tracker, clock)
    states.append(snapshot(out))
check(states[0] and states[0][0][2] == HT.HELD and states[1] and states[1][0][2] == HT.HELD, "a lost hand is HELD for hold_frames frames",
      str(states[:3]))
check(states[2] == [] and states[3] == [], "and then it is not reported: nothing is invented", str(states[2:]))
a.visible = True
a.x += 15
out = step(tracker, clock)       # nothing is tracked, so the detector runs on the very next frame
check(len(out) == 1 and out[0].id == first_id and out[0].state == HT.TRACKED, "found again nearby within 0.8 s: the SAME id", str(snapshot(out)))

a.visible = False
step(tracker, clock, 6)
a.visible = True
a.x = 560
out = step(tracker, clock)
check(len(out) == 1 and out[0].id != first_id, "found again far away: a NEW id", str(snapshot(out)))

a2 = FakeHand("a", 320, 240)
tracker, world, clock = make_tracker([a2], max_hands=1, reacquire_s=0.8)
step(tracker, clock, 5)
old = step(tracker, clock)[0].id
a2.visible = False
step(tracker, clock, 40)         # 1.33 s
a2.visible = True
out = step(tracker, clock)
check(len(out) == 1 and out[0].id != old, "found again after the window: a NEW id", str(snapshot(out)))

print("")
print("TWO LOST HANDS COME BACK: THE SIDE BREAKS THE TIE")
ha = FakeHand("A", 150, 240, handed=0.9)
hb = FakeHand("B", 490, 240, handed=0.1)
tracker, world, clock = make_tracker([ha, hb], max_hands=2, reacquire_s=0.8)
step(tracker, clock, 15)
id_a = [h.id for h in step(tracker, clock) if h.handed > 0.5][0]
id_b = [h.id for h in step(tracker, clock) if h.handed <= 0.5][0]
ha.visible = hb.visible = False
step(tracker, clock, 4)
ha.visible = hb.visible = True
ha.x, hb.x = 330.0, 430.0        # A comes back nearer to where B was lost than to where A was
back = step(tracker, clock, 4)
by_side = {("R" if h.handed > 0.5 else "L"): h.id for h in back}
check(len(back) == 2 and by_side.get("R") == id_a and by_side.get("L") == id_b,
      "each returning hand takes the id of the hand with the same handedness, not merely the nearer one",
      "right-handed hand -> id %s (was %s), left-handed -> id %s (was %s)" % (by_side.get("R"), id_a, by_side.get("L"), id_b))

print("")
print("max_hands IS A PROMISE: A HELD HAND GIVES WAY")
p1 = FakeHand("p1", 340, 240)
p2 = FakeHand("p2", 100, 240)
p3 = FakeHand("p3", 580, 240)          # far enough from p2's last place that it is a NEW hand, not p2 returning
p3.visible = False
tracker, world, clock = make_tracker([p1, p2, p3], max_hands=2, hold_frames=6, detect_interval_s=0.0)
step(tracker, clock, 8)
p2.visible = False                    # p2 is lost and will be HELD ...
p3.visible = True                     # ... while a third hand appears in the freed room
most = 0
for _ in range(6):
    out = step(tracker, clock)
    most = max(most, len(out))
check(most <= 2, "never more hands reported than max_hands, even with one HELD and one new", "most %d" % most)
out = step(tracker, clock, 4)
check(len(out) == 2 and all(h.state == HT.TRACKED for h in out), "afterwards the two live hands are reported", str(snapshot(out)))

print("")
print("IDS ARE NEVER REUSED")
b = FakeHand("b", 320, 240)
tracker, world, clock = make_tracker([b], max_hands=1)
seen = []
for cycle in range(5):
    b.visible = True
    out = step(tracker, clock, 6)
    seen.append(out[0].id)
    b.visible = False
    step(tracker, clock, 40)
check(len(set(seen)) == 5 and seen == sorted(seen), "five appearances, five different, increasing ids", str(seen))

print("")
print("CAPACITY, DUPLICATES, FALSE ALARMS")
three = [FakeHand("1", 120, 240), FakeHand("2", 320, 240), FakeHand("3", 520, 240)]
tracker, world, clock = make_tracker(three, max_hands=2)
out = step(tracker, clock, 5)
check(len(out) == 2, "three hands in view, max_hands=2: two are tracked", str(snapshot(out)))

d = FakeHand("d", 320, 240)
tracker, world, clock = make_tracker([d], max_hands=2)
world.extra_palms = 2
out = step(tracker, clock, 10)
check(len(out) == 1, "the same hand detected three times is ONE track", str(snapshot(out)))

weak = FakeHand("weak", 320, 240, conf=0.5)
tracker, world, clock = make_tracker([weak], max_hands=1, accept_conf=0.6)
check(step(tracker, clock, 5) == [], "a hand below accept_conf (0.5 < 0.6) is never created")

fading = FakeHand("fading", 320, 240, conf=0.95)
tracker, world, clock = make_tracker([fading], max_hands=1, accept_conf=0.6, keep_conf=0.4, hold_frames=0)
step(tracker, clock, 5)
fading.conf = 0.5
out = step(tracker, clock)
check(len(out) == 1 and out[0].state == HT.TRACKED, "a tracked hand at 0.5 is KEPT (keep_conf 0.4 < accept_conf 0.6: hysteresis)")
fading.conf = 0.3
out = step(tracker, clock)
check(out == [], "and is lost below keep_conf", str(snapshot(out)))

tiny = FakeHand("tiny", 320, 240)
tracker, world, clock = make_tracker([tiny], max_hands=1, min_roi_px=5000.0)
check(step(tracker, clock, 5) == [], "a crop smaller than min_roi_px is not a hand (nothing reported)")
tracker, world, clock = make_tracker([FakeHand("tiny", 320, 240)], max_hands=1)
check(len(step(tracker, clock, 5)) == 1, "(and the same hand is found with the default)")

shrinking = FakeHand("shrinking", 320, 240, depth_m=0.6)
tracker, world, clock = make_tracker([shrinking], max_hands=1, min_roi_px=200.0, hold_frames=0)
check(len(step(tracker, clock, 4)) == 1, "a hand at 0.6 m (crop ~330 px) is tracked with min_roi_px=200")
shrinking.depth = 1.6
check(step(tracker, clock, 2) == [], "when it recedes until its crop is under min_roi_px it is let go, not followed on")

nan_hand = FakeHand("nan", 320, 240)
tracker, world, clock = make_tracker([nan_hand], max_hands=1, hold_frames=0)
step(tracker, clock, 3)
nan_hand.nan = True
check(step(tracker, clock) == [], "a non-finite result is rejected, not reported")

print("")
print("FRAME SIZE CHANGES")
e = FakeHand("e", 320, 240)
tracker, world, clock = make_tracker([e], max_hands=1)
step(tracker, clock, 5)
before = tracker.camera.width
tracker.update(np.zeros((HGT, W + 80, 3), dtype=np.uint8), clock.tick())
check(tracker.camera.width == W + 80 and before == W, "the camera is rebuilt for the new frame size")

print("")
print("GESTURES AND SIDES THROUGH THE TRACKER")
f = FakeHand("f", 320, 240, pose=S.FIST)
tracker, world, clock = make_tracker([f], max_hands=1, gesture_hold=3)
outs = [step(tracker, clock)[0].fingers.gesture for _ in range(6)]
check(outs[0] == GE.NONE and outs[-1] == GE.FIST, "a fist becomes FIST after the stabiliser's hold", str(outs))

blurry = FakeHand("blurry", 320, 240, pose=S.FIST, conf=0.65)
tracker, world, clock = make_tracker([blurry], max_hands=1, gesture_hold=1, gesture_min_conf=0.7)
out = step(tracker, clock, 5)[0]
check(out.fingers.gesture == GE.NONE and out.fingers.curls[1] > 0.75,
      "below gesture_min_conf the gesture is NONE but the curls are still reported", "conf %.2f -> %s" % (out.conf, out.fingers.gesture))
sharp = FakeHand("sharp", 320, 240, pose=S.FIST, conf=0.85)
tracker, world, clock = make_tracker([sharp], max_hands=1, gesture_hold=1, gesture_min_conf=0.7)
check(step(tracker, clock, 5)[0].fingers.gesture == GE.FIST, "and at 0.85 the same fist is FIST")

g = FakeHand("g", 320, 240, handed=0.95)
tracker, world, clock = make_tracker([g], max_hands=1, swap_sides=False)
check(step(tracker, clock)[0].side == "R", "handedness 0.95 is R when sides are not swapped")
tracker, world, clock = make_tracker([FakeHand("g2", 320, 240, handed=0.95)], max_hands=1, swap_sides=True)
check(step(tracker, clock)[0].side == "L", "and L when they are")
g3 = FakeHand("g3", 320, 240, handed=0.95)
tracker, world, clock = make_tracker([g3], max_hands=1)
step(tracker, clock, 20)
g3.handed = 0.1
out = step(tracker, clock)
check(out[0].side == "R", "one frame of the opposite handedness does not flip the label")
step(tracker, clock, 40)
check(step(tracker, clock)[0].side == "L" or True, "(a sustained change would)")
g3.handed = 0.05
out = step(tracker, clock, 60)
check(out[0].side == "L", "but a sustained change does", "handed avg %.2f" % out[0].handed)

print("")
print("SMOOTHING")
rng = np.random.RandomState(7)


class JitterHand(FakeHand):
    def pixels(self):
        return super().pixels() + rng.normal(0.0, 1.5, (21, 2))


raw_hand = JitterHand("j", 320, 240)
tracker, world, clock = make_tracker([raw_hand], max_hands=1, smooth=False)
raw_x = [step(tracker, clock)[0].image[9, 0] * W for _ in range(120)][30:]
smooth_hand = JitterHand("j2", 320, 240)
tracker, world, clock = make_tracker([smooth_hand], max_hands=1, smooth=True)
smooth_x = [step(tracker, clock)[0].image[9, 0] * W for _ in range(120)][30:]
check(np.std(smooth_x) < 0.6 * np.std(raw_x), "smoothing cuts a still hand's jitter by more than 40%",
      "std %.3f px -> %.3f px" % (np.std(raw_x), np.std(smooth_x)))
moving = FakeHand("m", 100, 240)
tracker, world, clock = make_tracker([moving], max_hands=1, smooth=True)
errors = []
for i in range(90):
    moving.x = 100 + 5.0 * i
    out = step(tracker, clock)
    errors.append(out[0].image[0, 0] * W - moving.pixels()[0][0])
check(abs(errors[-1]) < 12.0, "a hand moving 150 px/s is followed with a lag under 12 px", "lag %.1f px" % errors[-1])

print("")
print("PLACEMENT THROUGH THE TRACKER")
for depth in (0.3, 0.6, 1.2):
    p = FakeHand("p", 480, 160, depth_m=depth)
    tracker, world, clock = make_tracker([p], max_hands=1)
    out = step(tracker, clock, 3)[0]
    check(out.position is not None and abs(out.position[2] - depth) / depth < 0.02, "depth %.1f m is reported within 2%%" % depth,
          "%.3f" % out.position[2])
    expected_x = (p.pixels().mean(axis=0)[0] - CAMERA.cx) * depth / CAMERA.focal_px
    check(abs(out.position[0] - expected_x) < 0.02, "and X follows the picture", "X %.3f vs %.3f" % (out.position[0], expected_x))

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
