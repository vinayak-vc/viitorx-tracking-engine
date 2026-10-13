#!/usr/bin/env python3
"""H-02 tests for face_geometry.py: head pose and the monocular position, against synthetic faces of KNOWN pose and distance.

What this proves: if the landmarks are a rigid face at a known pose and distance, the code reads them back, and the angle signs are
what the documentation says. What it does NOT prove: that a real face has the template's proportions, that the assumed 0.09 m eye
width and the assumed field of view are right, or that the angles agree with MediaPipe's own facial transformation matrix. A real
face's forehead-to-chin line is not exactly perpendicular to its eye line, so the pose has a neutral offset of a few degrees that
has not been calibrated (H-02 NOT TESTED).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _synthetic_face as SF
import face_geometry as FG
import hand_geometry as G

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


cam = G.Camera.from_hfov(1280, 720, 60.0)
face = SF.make_face()
facepx = face * 1000.0     # head_pose takes pixels; 1 mm = 1 px here

print("HEAD POSE: A FRONTAL FACE IS THE IDENTITY")
pose = FG.head_pose(facepx)
check(pose.ok and np.allclose(pose.rotation, np.eye(3), atol=1e-9), "an upright frontal face has the identity rotation")
check(abs(pose.yaw) < 1e-6 and abs(pose.pitch) < 1e-6 and abs(pose.roll) < 1e-6, "and yaw = pitch = roll = 0",
      "%.4f %.4f %.4f" % (pose.yaw, pose.pitch, pose.roll))

print("")
print("HEAD POSE: EACH ANGLE ALONE, AND ITS SIGN")
for name in ("yaw", "pitch", "roll"):
    worst = 0.0
    for angle in (-60, -30, -10, 10, 30, 60):
        kw = {name: angle}
        got = getattr(FG.head_pose(SF.posed(facepx, **kw)), name)
        worst = max(worst, abs(got - angle))
    check(worst < 1e-6, "%s is read back exactly over +-60 degrees" % name, "worst error %.2e deg" % worst)
check(FG.head_pose(SF.posed(facepx, yaw=30)).yaw > 0 and SF.posed(facepx, yaw=30)[1, 0] > facepx[1, 0],
      "yaw > 0 moves the nose toward the image's RIGHT (+x)")
check(SF.posed(facepx, pitch=30)[1, 1] > facepx[1, 1] and FG.head_pose(SF.posed(facepx, pitch=30)).pitch > 0,
      "pitch > 0 moves the nose DOWN (+y)")
p = SF.posed(facepx, roll=30)
check(p[263, 1] > p[33, 1] and FG.head_pose(p).roll > 0, "roll > 0 lowers the image-right eye: a clockwise tilt on screen")

print("")
print("HEAD POSE: THE ROTATION MATRIX IS THE POSE")
worst = 0.0
for yaw, pitch, roll in ((20, 10, 5), (-35, 15, -10), (50, -20, 25), (-10, -30, 40)):
    r = SF.rotation(yaw, pitch, roll)
    got = FG.head_pose(facepx @ r.T).rotation
    worst = max(worst, float(np.abs(got - r).max()))
check(worst < 1e-9, "for combined rotations the 3x3 equals the rotation that was applied", "worst %.2e" % worst)
r = FG.head_pose(SF.posed(facepx, 40, 20, 15)).rotation
check(np.allclose(r.T @ r, np.eye(3), atol=1e-9) and abs(np.linalg.det(r) - 1.0) < 1e-9,
      "and it is a proper rotation (orthonormal, determinant +1)")
combined = FG.head_pose(SF.posed(facepx, yaw=30, pitch=20))
check(abs(combined.yaw - 30) < 5 and abs(combined.pitch - 20) < 5,
      "combined yaw and pitch read approximately (the angles are defined from the look direction)",
      "yaw %.1f pitch %.1f" % (combined.yaw, combined.pitch))

print("")
print("HEAD POSE: IT SURVIVES WHERE THE FACE IS AND HOW BIG")
worst = 0.0
for scale in (0.3, 1.0, 5.0):
    for yaw in (0, 25):
        worst = max(worst, abs(FG.head_pose(SF.posed(facepx, yaw=yaw) * scale).yaw - yaw))
check(worst < 1e-6, "scaling the face does not change its pose")
degenerate = FG.head_pose(np.zeros((478, 3)))
check(not degenerate.ok and degenerate.yaw == 0.0, "coincident landmarks are refused, not turned into an angle")
flat = facepx.copy()
flat[152] = flat[10]
check(not FG.head_pose(flat).ok, "a chin on the forehead is refused")

print("")
print("POSITION: A KNOWN DISTANCE")
worst_z = worst_xy = 0.0
for z in (0.3, 0.5, 0.8, 1.2, 2.0):
    for x, y in ((0.0, 0.0), (0.12, -0.05), (-0.2, 0.08)):
        position = np.array([x * z, y * z, z])
        placed = FG.place_face(SF.project(face, position, cam), cam)
        if not placed.ok:
            check(False, "accepted at z=%.2f" % z, placed.reason)
            continue
        # the reported point is the one between the eyes, not the face's centroid
        eyes = position + (face[33] + face[263]) / 2.0
        worst_z = max(worst_z, abs(placed.position[2] - eyes[2]) / z)
        worst_xy = max(worst_xy, np.linalg.norm(placed.position[:2] - eyes[:2]) / z)
check(worst_z < 0.03, "depth recovered within 3% over 0.3-2.0 m", "worst %.2f%% (perspective across the face's own depth)" % (worst_z * 100))
check(worst_xy < 0.03, "X and Y recovered within 3% of the depth", "worst %.2f%%" % (worst_xy * 100))
turned = FG.place_face(SF.project(SF.posed(face, yaw=45), [0, 0, 0.8], cam), cam)
check(turned.ok and abs(turned.position[2] - 0.8) / 0.8 < 0.05, "a face turned 45 degrees is still at 0.8 m (the 3D eye distance, not the apparent width)",
      "%.3f m" % turned.position[2])

print("")
print("POSITION: FACE SIZE AND REFUSALS")
wide = FG.place_face(SF.project(face * 1.2, [0, 0, 0.8], cam), cam)
check(wide.ok and abs(wide.position[2] - 0.8 / 1.2) < 0.02, "a face 20% wider than assumed reads 20% too near (the documented bias)", "%.3f" % wide.position[2])
fixed = FG.place_face(SF.project(face * 1.2, [0, 0, 0.8], cam), cam, face_scale=1.2)
check(fixed.ok and abs(fixed.position[2] - 0.8) < 0.01, "face_scale=1.2 removes it", "%.3f" % fixed.position[2])
check(not FG.place_face(SF.project(face, [0, 0, 6.0], cam), cam).ok, "a face 6 m away is out of range")
check(not FG.place_face(SF.project(face, [0, 0, 0.05], cam), cam).ok, "a face 5 cm away is out of range")
check(FG.place_face(np.zeros((478, 3)), cam).reason == "degenerate", "coincident eye corners are refused as degenerate")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
