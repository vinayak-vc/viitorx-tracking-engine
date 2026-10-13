#!/usr/bin/env python3
"""H-01 tests for hand_geometry.py: the monocular 3D fit, checked against synthetic projections of KNOWN positions.

What this proves: if the network's world landmarks and the picture agree, the fit recovers the position, whatever the
hand's tilt. What it does NOT prove: that a real hand is the size of the network's average hand, or that the assumed
field of view is the camera's, so absolute accuracy against a ruler is NOT TESTED.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _synthetic_hand as S
import hand_geometry as G

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


cam = G.Camera.from_hfov(1280, 720, 60.0)


def project_hand(world, position, camera=cam):
    """What a pinhole camera sees: the hand's landmarks, placed at `position` (camera space), projected to pixels."""
    pts = world + np.asarray(position)
    return camera.project(pts)


print("CAMERA")
check(abs(cam.focal_px - 640.0 / math.tan(math.radians(30.0))) < 1e-6, "focal length from a 60 degree horizontal FOV",
      "%.1f px" % cam.focal_px)
portrait = G.Camera.from_hfov(720, 1280, 60.0, axis="long")
landscape = G.Camera.from_hfov(1280, 720, 60.0, axis="long")
check(abs(portrait.focal_px - landscape.focal_px) < 1e-9, "axis='long' gives a phone one focal length in both orientations")
for bad in (0.0, 4.0, 176.0):
    try:
        G.Camera.from_hfov(1280, 720, bad)
        check(False, "FOV %.0f is refused" % bad)
    except ValueError:
        check(True, "FOV %.0f is refused" % bad)
try:
    G.Camera(0, 720, 500)
    check(False, "a zero-width camera is refused")
except ValueError:
    check(True, "a zero-width camera is refused")

print("")
print("RECOVERING A KNOWN POSITION")
world = S.center(S.make_hand(**S.OPEN))
worst_z = worst_xy = 0.0
for z in (0.25, 0.4, 0.6, 1.0, 1.5, 2.2):
    for x, y in ((0.0, 0.0), (0.15, -0.05), (-0.2, 0.1)):
        position = np.array([x * z, y * z, z])
        px = project_hand(world, position)
        placed = G.place_hand(px, world, cam)
        if not placed.ok:
            check(False, "fit accepted at z=%.2f" % z, placed.reason)
            continue
        worst_z = max(worst_z, abs(placed.position[2] - z) / z)
        worst_xy = max(worst_xy, np.linalg.norm(placed.position[:2] - position[:2]) / z)
check(worst_z < 0.02, "depth recovered within 2%% over 0.25-2.2 m", "worst %.3f%%" % (worst_z * 100))
check(worst_xy < 0.02, "X and Y recovered within 2%% of the depth", "worst %.3f%%" % (worst_xy * 100))

print("")
print("TILT IS NOT DISTANCE")
# The fit treats the hand as flat at one depth (weak perspective), so a tilted hand, whose near edge is bigger than its
# far edge, reads slightly off. Measured here, and bounded, rather than assumed away.
z = 0.6
worst = 0.0
for rx, ry in ((0, 60), (60, 0), (45, 45), (0, 80)):
    tilted = S.center(S.rotate(S.make_hand(**S.OPEN), rx, ry, 0))
    placed = G.place_hand(project_hand(tilted, [0, 0, z]), tilted, cam)
    err = abs(placed.position[2] - z) / z if placed.ok else 1.0
    worst = max(worst, err)
    check(placed.ok and err < 0.05, "a hand tilted %d/%d deg is still at %.2f m, within 5%%" % (rx, ry, z),
          "got %.3f (%.1f%% off)" % (placed.position[2] if placed.ok else -1, err * 100))
for near in (0.3, 0.25):
    tilted = S.center(S.rotate(S.make_hand(**S.OPEN), 60, 0, 0))
    placed = G.place_hand(project_hand(tilted, [0, 0, near]), tilted, cam)
    err = abs(placed.position[2] - near) / near if placed.ok else 1.0
    check(placed.ok and err < 0.10, "tilted 60 deg at %.2f m (a webcam's usual range) is within 10%%" % near,
          "%.1f%% off - the weak-perspective error grows as the hand nears the lens" % (err * 100))
# The naive alternative (apparent width) fails the same test: a hand turned edge-on looks half as wide.
edge = S.center(S.rotate(S.make_hand(**S.OPEN), 0, 80, 0))
px_edge = project_hand(edge, [0, 0, z])
px_flat = project_hand(world, [0, 0, z])
check(np.ptp(px_edge[:, 0]) < 0.6 * np.ptp(px_flat[:, 0]), "(an edge-on hand IS much narrower in the picture)",
      "%.0f vs %.0f px" % (np.ptp(px_edge[:, 0]), np.ptp(px_flat[:, 0])))

print("")
print("HAND SIZE")
big = world * 1.2
px_big = project_hand(big, [0, 0, 0.7])
average_assumed = G.place_hand(px_big, world, cam)
check(average_assumed.ok and abs(average_assumed.position[2] - 0.7 / 1.2) < 0.02,
      "a hand 20%% bigger than assumed reads 20%% too near (the documented bias)", "z %.3f vs true 0.700" % average_assumed.position[2])
corrected = G.place_hand(px_big, world, cam, hand_scale=1.2)
check(corrected.ok and abs(corrected.position[2] - 0.7) < 0.01, "hand_scale=1.2 removes it", "z %.3f" % corrected.position[2])

print("")
print("REFUSALS")
mirrored = world.copy()
mirrored[:, 0] *= -1.0
placed = G.place_hand(project_hand(world, [0, 0, 0.6]), mirrored, cam)
check(not placed.ok, "world landmarks that disagree with the picture are refused", placed.reason)
placed = G.place_hand(project_hand(world, [0, 0, 0.6]), world * 0.0, cam)
check(not placed.ok and placed.reason == "degenerate", "all-zero world landmarks are refused", placed.reason)
check(not G.place_hand(project_hand(world, [0, 0, 6.0]), world, cam).ok, "a hand 6 m away is out of range", G.place_hand(project_hand(world, [0, 0, 6.0]), world, cam).reason)
check(not G.place_hand(project_hand(world, [0, 0, 0.05]), world, cam).ok, "a hand 5 cm away is out of range")
noisy = project_hand(world, [0, 0, 0.6]) + np.random.RandomState(1).normal(0, 40.0, (21, 2))
check(not G.place_hand(noisy, world, cam).ok, "landmarks scattered at random are refused", G.place_hand(noisy, world, cam).reason)
check(not G.place_hand(np.zeros((2, 2)), np.zeros((2, 3)), cam).ok, "too few points are refused")

print("")
print("CAMERA-SPACE POINTS")
position = np.array([0.1, -0.05, 0.6])
pts = G.camera_space_points(world, position)
check(np.allclose(pts.mean(axis=0), position, atol=1e-9), "the landmarks' centroid is the position")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
