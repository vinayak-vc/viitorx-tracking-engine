#!/usr/bin/env python3
"""S-01 tests for the matte: each stage does its one job and does not do the next stage's.

Everything here is synthetic (rectangles and noise on a small canvas), so the expected answer is known exactly: where an edge is, which blob
is a person and which is a speck, what a hole is and what a gap between an arm and a torso is.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import numpy as np

import cutout_matte as M

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-84s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-84s %s" % (label, detail))


H, W = 180, 320


def rect(top, left, bottom, right, value=1.0, canvas=None):
    c = np.zeros((H, W), np.float32) if canvas is None else canvas
    c[top:bottom, left:right] = value
    return c


def only(**kw):
    """A matte with every stage OFF except those named."""
    base = dict(guided=False, temporal=False, cleanup=False, solid_inset_frac=0.0)
    base.update(kw)
    return M.MatteConfig(**base)


print("RAMP")
p = np.array([0.0, 0.4, 0.5, 0.675, 0.85, 1.0], np.float32)
r = M.ramp(p, 0.5, 0.85)
check(np.allclose(r, [0, 0, 0, 0.5, 1, 1], atol=1e-5), "0 at or below low, 1 at or above high, linear between", str(r.round(3)))
check(np.all(M.ramp(np.array([0.3], np.float32), 0.5, 0.5) == 0.0), "a degenerate ramp (low == high) does not divide by zero")

print("")
print("GUIDED FILTER SNAPS THE EDGE TO THE PICTURE'S EDGE")
guide = np.zeros((H, W), np.float32)
guide[:, 160:] = 1.0                      # the picture's real edge is at x = 160
src = np.zeros((H, W), np.float32)
src[:, 164:] = 1.0                        # the network put its edge 4 px too far right (inside the filter's 13 px window)
src = np.clip(__import__("cv2").GaussianBlur(src, (0, 0), 2), 0, 1)


def edge_x(row):
    return float(np.interp(0.5, row, np.arange(len(row))))


before = abs(edge_x(src[90]) - 160.0)
after_full = abs(edge_x(np.clip(M.guided_filter(guide, src, 6, 1e-3, 1)[90], 0, 1)) - 160.0)
after_fast = abs(edge_x(np.clip(M.guided_filter(guide, src, 6, 1e-3, 2)[90], 0, 1)) - 160.0)
check(after_full < before * 0.7, "the full filter moves the network's edge at least 30% closer to the picture's", "%.1f px -> %.1f px" % (before, after_full))
check(after_fast < before * 0.7, "so does the fast (subsampled) filter", "%.1f px -> %.1f px" % (before, after_fast))
rng = np.random.RandomState(4)
g2 = __import__("cv2").GaussianBlur(rng.rand(H, W).astype(np.float32), (0, 0), 3)     # a softly textured picture, as a photograph is
s2 = np.clip(rect(40, 60, 140, 250) + rng.rand(H, W).astype(np.float32) * 0.1, 0, 1)
full = M.guided_filter(g2, s2, 5, 2e-3, 1)
fast = M.guided_filter(g2, s2, 5, 2e-3, 2)
diff = np.abs(full - fast)
check(float(diff.mean()) < 0.005 and float(diff.max()) < 0.06,
      "the fast filter stays close to the full one even on a hard-edged canvas (mean < 0.005, worst edge pixel < 0.06)",
      "mean %.4f max %.4f" % (float(diff.mean()), float(diff.max())))
flat = M.guided_filter(np.full((H, W), 0.5, np.float32), s2, 5, 2e-3, 1)
check(abs(float(flat[90, 150]) - float(s2[85:96, 145:156].mean())) < 0.02, "with a flat guide the filter is a plain local average")
check(M.guided_filter(g2[:12, :12], s2[:12, :12], 5, 2e-3, 2).shape == (12, 12), "a canvas too small to subsample still works (falls back to the full filter)")

print("")
print("TEMPORAL SMOOTHING AVERAGES NOISE AND FOLLOWS MOVEMENT")
rng = np.random.RandomState(1)
m = M.MatteRefiner(only(temporal=True, edge_low=0.0, edge_high=1.0))
outs = []
ins = []
for _ in range(60):
    noisy = np.clip(0.5 + (rng.rand(H, W).astype(np.float32) - 0.5) * 0.2, 0, 1)
    ins.append(noisy[90, 150])
    outs.append(m.update(noisy)[90, 150] / 255.0)
check(np.std(outs[10:]) < 0.7 * np.std(ins[10:]), "noise of +-0.1 around 0.5 is cut by 30% or more", "%.4f -> %.4f" % (np.std(ins[10:]), np.std(outs[10:])))
m = M.MatteRefiner(only(temporal=True, edge_low=0.0, edge_high=1.0))
for _ in range(5):
    m.update(np.zeros((H, W), np.float32))
a1 = m.update(np.ones((H, W), np.float32))[90, 150] / 255.0
check(a1 >= 0.9, "a person who appears (0 -> 1) is shown at once, not faded in", "first frame %.2f" % a1)
for _ in range(5):
    m.update(np.ones((H, W), np.float32))
d1 = m.update(np.zeros((H, W), np.float32))[90, 150] / 255.0
check(d1 <= 0.1, "a person who leaves (1 -> 0) is gone at once: no ghost", "first frame %.2f" % d1)
m = M.MatteRefiner(only(temporal=True, edge_low=0.0, edge_high=1.0))
first = m.update(rect(40, 60, 140, 250, 0.8))
check(abs(int(first[90, 150]) - 204) <= 1, "the first frame passes through unchanged (nothing to blend with)", str(int(first[90, 150])))
other = m.update(np.zeros((H // 2, W // 2), np.float32))
check(other.shape == (H // 2, W // 2) and int(other.max()) == 0, "a canvas of another size starts afresh instead of failing")
m.update(rect(40, 60, 140, 250, 1.0))
m.reset()
check(int(m.update(np.zeros((H, W), np.float32)).max()) == 0, "reset() forgets the previous canvas")

print("")
print("CLEANUP KEEPS PEOPLE, DROPS SPECKS, FILLS PINHOLES, LEAVES GAPS")
body = rect(40, 100, 150, 140)            # a 40 x 110 body
m = M.MatteRefiner(only(cleanup=True))
c = body.copy()
rect(5, 280, 9, 284, 1.0, c)              # a 4x4 speck far away
out = m.update(c)
check(out[7, 282] == 0 and out[90, 120] == 255 and m.last_people == 1, "a far speck is dropped, the body stays", "people=%d" % m.last_people)
c = body.copy()
rect(22, 112, 34, 128, 1.0, c)            # a 12x16 'cap' floating 6 px above the head
out = m.update(c)
check(out[28, 120] == 255 and m.last_people == 1, "a cap detached from the head but near it is kept AND is part of that person", "people=%d" % m.last_people)
c = rect(40, 60, 150, 100)
rect(40, 220, 150, 260, 1.0, c)           # a second body
out = m.update(c)
check(m.last_people == 2 and out[90, 80] == 255 and out[90, 240] == 255, "two separate bodies are two people and both stay", "people=%d" % m.last_people)
c = body.copy()
c[80:83, 115:118] = 0.0                   # a 3x3 pinhole inside the body
out = m.update(c)
check(out[81, 116] == 255, "a pinhole inside a body is filled")
c = body.copy()
c[70:100, 110:130] = 0.0                  # a 20x30 gap, as between an arm and a torso, fully inside the foreground
c2 = rect(40, 90, 150, 150)
c2[70:100, 110:130] = 0.0
out = m.update(c2)
check(out[85, 120] == 0, "a large enclosed gap (an arm on a hip) is NOT filled", str(int(out[85, 120])))
c = body.copy()
c[80:85, 115:120] = 0.65                  # a 5x5 see-through patch (alpha well below 1) wholly inside the body: 0.6% of its area
out = m.update(c)
check(out[82, 117] == 255, "a small see-through patch wholly inside a body is made solid", str(int(out[82, 117])))
c = body.copy()
c[80:90, 115:125] = 0.65                  # a 10x10 patch is 2.3% of the body: past the pinhole limit, left as the network said
out = m.update(c)
check(out[85, 120] < 255, "a larger patch is NOT painted over (it may be real: a hole the picture really has)", str(int(out[85, 120])))
out = m.update(np.zeros((H, W), np.float32))
check(int(out.max()) == 0 and m.last_people == 0 and m.last_fg_permille == 0, "an empty canvas gives an empty mask and no people")
check(m.update(body).dtype == np.uint8, "the result is uint8")

print("")
print("SOLID INTERIOR: THE INSIDE STOPS DEPENDING ON THE EDGE RAMP")
uncertain = np.zeros((H, W), np.float32)
uncertain[40:150, 100:160] = 0.6          # a body the network is only 60% sure of (dark trousers): below the 0.85 ramp top
off = M.MatteRefiner(only(solid_inset_frac=0.0)).update(uncertain)
on = M.MatteRefiner(only(solid_inset_frac=0.012, solid_low=0.5)).update(uncertain)
check(int(off[95, 130]) < 100, "without it a 0.6-sure body is see-through (alpha %d)" % int(off[95, 130]))
check(int(on[95, 130]) == 255, "with it the deep inside of the same body is opaque")
check(int(on[41, 130]) < 255 and int(on[95, 101]) < 255, "but the pixels next to its outline are NOT forced opaque (the halo stays out)", "edge px %d %d" % (int(on[41, 130]), int(on[95, 101])))
thin = np.zeros((H, W), np.float32)
thin[40:150, 100:106] = 0.6               # an arm 6 px wide: no deep inside
t_on = M.MatteRefiner(only(solid_inset_frac=0.012, solid_low=0.5)).update(thin)
check(int(t_on[95, 103]) < 100, "a thin limb has no deep inside and is left to the ramp (nothing is thickened)", str(int(t_on[95, 103])))
check(int(on[41:150, 160:200].max()) == 0 and int(on[:, :90].max()) == 0, "nothing outside the region is touched")

print("")
print("THE WHOLE CHAIN, AND ITS OFF SWITCHES")
guide = np.full((H, W), 0.5, np.float32)
body_soft = np.clip(__import__("cv2").GaussianBlur(body, (0, 0), 2), 0, 1)
m = M.MatteRefiner(M.MatteConfig())
out = m.update(body_soft, guide)
check(out.shape == (H, W) and out[95, 120] == 255 and out[10, 10] == 0, "a soft body in gives a clean mask out")
area = int((out > 127).sum())
check(abs(m.last_fg_permille - round(1000.0 * area / out.size)) <= 1, "last_fg_permille is the share of foreground", "%d/1000, counted %d px" % (m.last_fg_permille, area))
src = body_soft.copy()
before = src.copy()
M.MatteRefiner(M.MatteConfig()).update(src, guide)
check(np.array_equal(src, before), "update() does not modify the canvas it was given")
plain = M.MatteRefiner(only()).update(body_soft)
check(np.array_equal(plain, np.round(M.ramp(body_soft, 0.5, 0.85) * 255).astype(np.uint8)), "with every stage off the output is exactly the ramp of the input")
check(int(M.MatteRefiner(only(guided=True)).update(body_soft, None).max()) > 0, "guided on but no guide image given: the stage is skipped, not an error")
m = M.MatteRefiner(M.MatteConfig(cleanup=False, guided=False))
c = body.copy()
rect(5, 280, 9, 284, 1.0, c)
check(m.update(c, guide)[7, 282] == 255, "with cleanup off the far speck survives")
m = M.MatteRefiner(M.MatteConfig(cleanup=True, guided=False))
check(m.update(c, guide)[7, 282] == 0, "and with cleanup on it does not (same canvas)")
m = M.MatteRefiner(M.MatteConfig(cleanup=False, guided=True))
check(m.update(c, guide)[7, 282] < 255, "(a flat guide makes the guided filter a box blur, which already erases a 4x4 speck)")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
