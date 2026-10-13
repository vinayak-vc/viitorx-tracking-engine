#!/usr/bin/env python3
"""H-02 tests for face_tracking.py: ids, detect-then-track, loss and re-adoption, capacity, smoothing, expression, pose, placement.

The detector, the mesh network and the blendshape network are replaced by a FAKE WORLD with known ground truth (a face has a position in
pixels, a distance, a head pose and an expression vector), so every claim is checked against what is really there: an id must stay on the
face it was given to, a face 0.6 m away must be reported at 0.6 m, a face that vanishes must not be invented, and the blendshape network
must be handed the RAW landmarks. The clock is injected.

What this proves: the loop's bookkeeping. What it does NOT prove: that the real networks behave like the fakes (the real-network smoke test
is in test_face_landmarker.py; a live camera run is NOT TESTED).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _synthetic_face as SF
import face_geometry as FG
import face_landmarker as F
import face_tracking as FT
import hand_geometry as G

PASS = 0
FAIL = 0
W, HGT = 640, 480
CAMERA = G.Camera.from_hfov(W, HGT, 60.0)
TEMPLATE = SF.make_face()


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-66s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-66s %s" % (label, detail))


class FakeFace(object):
    """One ground-truth face: where it is (pixels of its centre), how far, how it is turned, what expression it has."""

    def __init__(self, name, x, y, depth_m=0.6, yaw=0.0, pitch=0.0, roll=0.0, presence=0.98, expression=None):
        self.name = name
        self.x = x
        self.y = y
        self.depth = depth_m
        self.yaw = yaw
        self.pitch = pitch
        self.roll = roll
        self.presence = presence
        self.expression = np.zeros(52) if expression is None else np.asarray(expression, dtype=np.float64)
        self.visible = True
        self.nan = False

    def landmarks(self):
        z = self.depth
        position = ((self.x - CAMERA.cx) * z / CAMERA.focal_px, (self.y - CAMERA.cy) * z / CAMERA.focal_px, z)
        return SF.project(SF.posed(TEMPLATE, self.yaw, self.pitch, self.roll), position, CAMERA)


class FakeWorld(object):
    """Stands in for the detector, the mesh network and the blendshape network at once."""

    def __init__(self, faces):
        self.faces = faces
        self.detect_calls = 0
        self.mesh_calls = 0
        self.blend_calls = 0
        self.last_blend_input = None
        self.extra_detections = 0

    def detect(self, frame):
        self.detect_calls += 1
        out = []
        for f in self.faces:
            if not f.visible:
                continue
            lm = f.landmarks()
            lo = lm[:468, :2].min(axis=0)
            hi = lm[:468, :2].max(axis=0)
            kp = np.stack([lm[F.RIGHT_EYE_OUTER, :2], lm[F.LEFT_EYE_OUTER, :2], lm[F.NOSE_TIP, :2],
                           lm[F.CHIN, :2], lm[F.RIGHT_EYE_OUTER, :2], lm[F.LEFT_EYE_OUTER, :2]])
            for _ in range(1 + self.extra_detections):
                out.append(F.FaceDetection([lo[0], lo[1], hi[0], hi[1]], kp, 0.95))
        return out

    def infer(self, frame, roi=None, landmarks=None):
        if landmarks is not None:                      # the blendshape network
            self.blend_calls += 1
            self.last_blend_input = np.asarray(landmarks).copy()
            best = min((f for f in self.faces if f.visible),
                       key=lambda f: float(np.linalg.norm(f.landmarks()[1, :2] - np.asarray(landmarks)[1, :2])), default=None)
            return best.expression.copy() if best is not None else np.zeros(52)
        self.mesh_calls += 1                           # the mesh network
        best = None
        best_d = None
        for f in self.faces:
            if not f.visible:
                continue
            lm = f.landmarks()
            mid = (lm[F.RIGHT_EYE_OUTER, :2] + lm[F.LEFT_EYE_OUTER, :2]) / 2.0
            c = roi.source_to_crop(np.array([mid]))[0]
            if 0.0 <= c[0] <= 1.0 and 0.0 <= c[1] <= 1.0:
                d = float(np.linalg.norm(c - 0.5))
                if best is None or d < best_d:
                    best, best_d = f, d
        if best is None:
            return F.FaceMesh(np.zeros((478, 3)), 0.05, roi)
        lm = best.landmarks()
        if best.nan:
            lm = lm * np.nan
        return F.FaceMesh(lm, best.presence, roi)


class MeshAdapter(object):
    def __init__(self, world):
        self.world = world

    def infer(self, frame, roi):
        return self.world.infer(frame, roi=roi)


class BlendAdapter(object):
    def __init__(self, world):
        self.world = world

    def infer(self, landmarks):
        return self.world.infer(None, landmarks=landmarks)


class Clock(object):
    def __init__(self, dt=1.0 / 30.0):
        self.t = 100.0
        self.dt = dt

    def tick(self, n=1):
        self.t += self.dt * n
        return self.t


FRAME = np.zeros((HGT, W, 3), dtype=np.uint8)


def make_tracker(faces, with_blend=True, **cfg_kw):
    world = FakeWorld(faces)
    cfg_kw.setdefault("smooth", False)
    cfg = FT.FaceTrackerConfig(**cfg_kw)
    tracker = FT.FaceTracker(world, MeshAdapter(world), BlendAdapter(world) if with_blend else None,
                             lambda w, h: G.Camera.from_hfov(w, h, 60.0), cfg)
    return tracker, world, Clock()


def step(tracker, clock, n=1):
    out = None
    for _ in range(n):
        out = tracker.update(FRAME, clock.tick())
    return out


def snapshot(faces):
    return [(f.id, f.state) for f in faces]


print("ONE FACE")
a = FakeFace("a", 320, 240, depth_m=0.6)
tracker, world, clock = make_tracker([a], max_faces=1)
faces = step(tracker, clock)
check(len(faces) == 1 and faces[0].id == 1 and faces[0].state == FT.TRACKED, "a face in view is found: id 1, TRACKED", str(snapshot(faces)))
step(tracker, clock, 60)
check(world.detect_calls == 1, "with max_faces=1 the detector runs only to FIND the face", "%d detector runs" % world.detect_calls)
check(tracker.mesh_runs == 61 and tracker.blend_runs == 61, "afterwards one mesh pass and one blendshape pass per frame", "%d / %d" % (tracker.mesh_runs, tracker.blend_runs))
face = step(tracker, clock)[0]
eyes_z = 0.6 + (TEMPLATE[F.RIGHT_EYE_OUTER][2] + TEMPLATE[F.LEFT_EYE_OUTER][2]) / 2.0
check(face.position is not None and abs(face.position[2] - eyes_z) / eyes_z < 0.03, "the point between the eyes is reported at the right distance", "%.3f vs %.3f m" % (face.position[2], eyes_z))
check(abs(face.position[0]) < 0.03, "on the optical axis it has X ~ 0", "%.3f" % face.position[0])
check(face.image.shape == (478, 3) and abs(face.image[1, 0] - a.landmarks()[1, 0] / W) < 1e-6, "image landmarks are normalised by the frame size")
check(face.pose_ok and abs(face.yaw) < 0.5 and abs(face.pitch) < 0.5, "a frontal face has yaw = pitch = 0", "%.2f %.2f" % (face.yaw, face.pitch))

print("")
print("HEAD POSE THROUGH THE TRACKER")
for name, value in (("yaw", 25.0), ("pitch", -15.0), ("roll", 12.0)):
    f = FakeFace("p", 320, 240, **{name: value})
    tracker, world, clock = make_tracker([f], max_faces=1)
    out = step(tracker, clock, 3)[0]
    check(abs(getattr(out, name) - value) < 3.0, "%s %+.0f is read within 3 degrees" % (name, value), "%.2f" % getattr(out, name))

print("")
print("EXPRESSION: 52 BLENDSHAPES")
expr = np.zeros(52)
expr[F.BLENDSHAPE_INDEX["jawOpen"]] = 0.7
expr[F.BLENDSHAPE_INDEX["eyeBlinkLeft"]] = 1.4        # out of range: must be clamped
expr[F.BLENDSHAPE_INDEX["mouthSmileRight"]] = -0.2
e = FakeFace("e", 320, 240, expression=expr)
tracker, world, clock = make_tracker([e], max_faces=1)
out = step(tracker, clock, 3)[0]
check(out.blendshapes is not None and out.blendshapes.shape == (52,), "52 scores")
check(abs(out.blendshapes[25] - 0.7) < 1e-9, "jawOpen comes through", "%.3f" % out.blendshapes[25])
check(out.blendshapes[9] == 1.0 and out.blendshapes[44] == 0.0, "scores are clamped to [0, 1]")
check(np.allclose(world.last_blend_input[:, :2], e.landmarks()[:, :2]), "the blendshape network is handed the landmarks in IMAGE PIXELS")
lag = FakeFace("lag", 200, 240)
tracker, world, clock = make_tracker([lag], max_faces=1, smooth=True)
step(tracker, clock, 20)
lag.x = 440                                           # the face jumps; the smoothed landmarks lag behind it
reported = step(tracker, clock)[0]
check(np.allclose(world.last_blend_input[:, :2], lag.landmarks()[:, :2]),
      "with smoothing ON the blendshape network still gets the RAW landmarks, not the smoothed (lagging) ones")
check(abs(reported.image[1, 0] * W - lag.landmarks()[1, 0]) > 10.0, "(and the reported landmarks DO lag, so the two differ)", "lag %.0f px" % abs(reported.image[1, 0] * W - lag.landmarks()[1, 0]))
tracker, world, clock = make_tracker([FakeFace("e2", 320, 240)], with_blend=False, max_faces=1)
out = step(tracker, clock, 3)[0]
check(out.blendshapes is None and tracker.blend_runs == 0, "without a blendshape network: no scores, no cost")
tracker, world, clock = make_tracker([FakeFace("e3", 320, 240)], max_faces=1, blendshapes=False)
out = step(tracker, clock, 3)[0]
check(out.blendshapes is None and world.blend_calls == 0, "cfg.blendshapes=False skips the network even when one is supplied")

print("")
print("SMOOTHING")
rng = np.random.RandomState(7)


class JitterFace(FakeFace):
    def landmarks(self):
        return super().landmarks() + np.concatenate([rng.normal(0.0, 1.2, (478, 2)), np.zeros((478, 1))], axis=1)


raw = JitterFace("j", 320, 240)
tracker, world, clock = make_tracker([raw], max_faces=1, smooth=False)
raw_x = [step(tracker, clock)[0].image[1, 0] * W for _ in range(120)][30:]
sm = JitterFace("j2", 320, 240)
tracker, world, clock = make_tracker([sm], max_faces=1, smooth=True)
sm_x = [step(tracker, clock)[0].image[1, 0] * W for _ in range(120)][30:]
check(np.std(sm_x) < 0.6 * np.std(raw_x), "smoothing cuts a still face's landmark jitter by more than 40%", "std %.3f px -> %.3f px" % (np.std(raw_x), np.std(sm_x)))

noisy = FakeFace("n", 320, 240)
tracker, world, clock = make_tracker([noisy], max_faces=1, smooth=True)
jaws = []
for _ in range(150):
    noisy.expression = np.zeros(52)
    noisy.expression[25] = 0.3 + rng.normal(0.0, 0.03)
    jaws.append(step(tracker, clock)[0].blendshapes[25])
check(np.std(jaws[40:]) < 0.02, "a resting expression is steadied (noise std 0.03 -> under 0.02)", "std %.4f" % np.std(jaws[40:]))
blink = FakeFace("b", 320, 240)
tracker, world, clock = make_tracker([blink], max_faces=1, smooth=True)
step(tracker, clock, 30)
blink.expression = np.zeros(52)
blink.expression[9] = 1.0
after = [step(tracker, clock)[0].blendshapes[9] for _ in range(4)]
check(after[2] > 0.75, "but a blink is followed fast: a 0 -> 1 step reaches > 0.75 within 3 frames (~100 ms)", str(np.round(after, 2)))

print("")
print("LOSS, HELD, RE-ADOPTION")
a = FakeFace("a", 320, 240)
tracker, world, clock = make_tracker([a], max_faces=1, hold_frames=2, reacquire_s=0.8)
step(tracker, clock, 10)
first_id = step(tracker, clock)[0].id
a.visible = False
states = [snapshot(step(tracker, clock)) for _ in range(5)]
check(states[0] and states[0][0][1] == FT.HELD and states[1] and states[1][0][1] == FT.HELD, "a lost face is HELD for hold_frames frames", str(states[:2]))
check(states[2] == [] and states[3] == [], "and then it is not reported: nothing is invented", str(states[2:]))
a.visible = True
a.x += 12
out = step(tracker, clock)
check(len(out) == 1 and out[0].id == first_id and out[0].state == FT.TRACKED, "found again nearby within 0.8 s: the SAME id", str(snapshot(out)))
a.visible = False
step(tracker, clock, 6)
a.visible = True
a.x = 600
out = step(tracker, clock)
check(len(out) == 1 and out[0].id != first_id, "found again far away: a NEW id", str(snapshot(out)))
a2 = FakeFace("a", 320, 240)
tracker, world, clock = make_tracker([a2], max_faces=1, reacquire_s=0.8)
step(tracker, clock, 5)
old = step(tracker, clock)[0].id
a2.visible = False
step(tracker, clock, 40)
a2.visible = True
out = step(tracker, clock)
check(len(out) == 1 and out[0].id != old, "found again after the window: a NEW id", str(snapshot(out)))
b = FakeFace("b", 320, 240)
tracker, world, clock = make_tracker([b], max_faces=1)
seen = []
for _ in range(5):
    b.visible = True
    seen.append(step(tracker, clock, 6)[0].id)
    b.visible = False
    step(tracker, clock, 40)
check(len(set(seen)) == 5 and seen == sorted(seen), "five appearances, five different, increasing ids: never reused", str(seen))

print("")
print("SEVERAL FACES")
left = FakeFace("left", 160, 240, depth_m=0.9)
right = FakeFace("right", 480, 240, depth_m=0.9)
tracker, world, clock = make_tracker([left, right], max_faces=2)
faces = step(tracker, clock)
ids = {("left" if f.image[1, 0] * W < 320 else "right"): f.id for f in faces}
check(len(faces) == 2 and ids["left"] != ids["right"], "two faces, two ids", str(ids))
wrong = 0
for frame in range(1, 90):
    t = np.sin(frame / 89.0 * np.pi)
    left.x = 160 + 70 * t
    right.x = 480 - 70 * t
    out = step(tracker, clock)
    for f in out:
        nearest_left = abs(f.image[1, 0] * W - left.landmarks()[1, 0]) < abs(f.image[1, 0] * W - right.landmarks()[1, 0])
        if f.id != ids["left" if nearest_left else "right"]:
            wrong += 1
check(wrong == 0 and len(out) == 2, "faces that approach and part keep their ids and no new one appears")
check(world.detect_calls == 1, "with both found the detector stops", "%d runs" % world.detect_calls)

one_of_two = FakeFace("one", 320, 240)
tracker, world, clock = make_tracker([one_of_two], max_faces=2, detect_interval_s=0.10)
step(tracker, clock, 90)
check(10 <= world.detect_calls <= 3.0 / 0.10 + 1, "one face of two: it looks for another, but at most every 0.1 s", "%d detector runs in 3 s" % world.detect_calls)

d = FakeFace("d", 320, 240)
tracker, world, clock = make_tracker([d], max_faces=2)
world.extra_detections = 2
step(tracker, clock, 40)
check(tracker.mesh_runs == 40, "the face the detector keeps re-finding costs NO extra mesh pass (judged against the face, not the crop)", "%d mesh runs in 40 frames" % tracker.mesh_runs)
check(len(snapshot(step(tracker, clock))) == 1, "and it is one face, however often it is detected")

three = [FakeFace("1", 110, 240, depth_m=1.2), FakeFace("2", 320, 240, depth_m=1.2), FakeFace("3", 530, 240, depth_m=1.2)]
tracker, world, clock = make_tracker(three, max_faces=2)
check(len(step(tracker, clock, 5)) == 2, "three faces in view, max_faces=2: two are reported")

p1 = FakeFace("p1", 340, 240, depth_m=1.2)
p2 = FakeFace("p2", 100, 240, depth_m=1.2)
p3 = FakeFace("p3", 580, 240, depth_m=1.2)
p3.visible = False
tracker, world, clock = make_tracker([p1, p2, p3], max_faces=2, hold_frames=6, detect_interval_s=0.0)
step(tracker, clock, 8)
p2.visible = False
p3.visible = True
most = 0
for _ in range(6):
    most = max(most, len(step(tracker, clock)))
check(most <= 2, "max_faces is a promise: never more reported, even with one HELD and one new", "most %d" % most)

print("")
print("PRESENCE GATES, JUNK, FRAME SIZE, RESET")
weak = FakeFace("weak", 320, 240, presence=0.5)
tracker, world, clock = make_tracker([weak], max_faces=1, accept_presence=0.6)
check(step(tracker, clock, 5) == [], "a face below accept_presence (0.5 < 0.6) is never created")
fading = FakeFace("fading", 320, 240, presence=0.95)
tracker, world, clock = make_tracker([fading], max_faces=1, accept_presence=0.6, keep_presence=0.4, hold_frames=0)
step(tracker, clock, 5)
fading.presence = 0.5
check(len(step(tracker, clock)) == 1, "a tracked face at 0.5 is KEPT (keep 0.4 < accept 0.6: hysteresis)")
fading.presence = 0.3
check(step(tracker, clock) == [], "and is lost below keep_presence")
nan_face = FakeFace("nan", 320, 240)
tracker, world, clock = make_tracker([nan_face], max_faces=1, hold_frames=0)
step(tracker, clock, 3)
nan_face.nan = True
check(step(tracker, clock) == [], "a non-finite result is rejected, not reported")
tiny = FakeFace("tiny", 320, 240)
tracker, world, clock = make_tracker([tiny], max_faces=1, min_roi_px=5000.0)
check(step(tracker, clock, 5) == [], "a crop smaller than min_roi_px is not a face")
shrinking = FakeFace("shrinking", 320, 240, depth_m=0.6)
tracker, world, clock = make_tracker([shrinking], max_faces=1, min_roi_px=100.0, hold_frames=0)
check(len(step(tracker, clock, 4)) == 1, "a face at 0.6 m (crop ~200 px) is tracked with min_roi_px=100")
shrinking.depth = 2.5
check(step(tracker, clock, 2) == [], "when it recedes until its crop is under min_roi_px it is let go, not followed on")
far = FakeFace("far", 320, 240, depth_m=4.4)
tracker, world, clock = make_tracker([far], max_faces=1)
out = step(tracker, clock, 3)
check(len(out) == 1 and out[0].position is None and out[0].reason == "range" and out[0].blendshapes is not None,
      "a face too far for a position still reports its expression, with the 3D fit refused and why", str((out[0].reason if out else None)))
e = FakeFace("e", 320, 240)
tracker, world, clock = make_tracker([e], max_faces=1)
step(tracker, clock, 5)
before = tracker.camera.width
tracker.update(np.zeros((HGT, W + 80, 3), dtype=np.uint8), clock.tick())
check(before == W and tracker.camera.width == W + 80, "the camera is rebuilt for a new frame size")
tracker, world, clock = make_tracker([FakeFace("r", 320, 240)], max_faces=1)
first = step(tracker, clock)[0].id
tracker.reset()
check(tracker.track_count == 0, "reset() drops every track")
check(step(tracker, clock)[0].id > first, "and ids keep counting up after a reset: never reused")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
