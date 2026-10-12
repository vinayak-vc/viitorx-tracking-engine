#!/usr/bin/env python3
"""F-60 tests: framing.py (which body part anchors the skeleton) and its wiring into the senders.

No model, no camera, no GPU: the poses are synthetic, built from the body proportions the tracker's priors
are measured against (ADR-096), so every case is deterministic.

  1. THE TIERS. Hips in the picture -> tier 0 and EXACTLY the pre-F-60 root and depth (pinned against
     video_udp_sender.estimate_hip_depth). Hips not in the picture - low confidence, OR located outside the
     image at HIGH confidence (RTMW3D keeps regressing keypoints for a body that is not there, measured on
     waist-up crops) -> tier 1. Shoulders gone as well -> tier 2. Nothing -> None.
  2. THE MID-RUN FALL-BACK. A tracker that has watched a full body, then loses the hips, keeps the root and the
     depth continuous (the learned shoulder-frame offset and shoulder width), and takes the hips back with
     hysteresis, not on every frame the confidence crosses the gate.
  3. HONESTY. A joint outside the image is sent as absent for an estimated-root frame; a full-body frame is
     left alone; NaN input never produces a NaN root; a person who left is forgotten.
  4. THE SENDERS. HostMultiPerson with partial_body ON sends a person whose hips are hidden (with `fr`), with it
     OFF drops them (the F-52 rule), and a full-body person is byte-identical either way except for `fr`.

Run: .venv\\Scripts\\python.exe tests/test_framing.py   (exit 0 on pass)
"""
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import framing as FR
import host_multiperson as HMP
import sidecar_supervisor as SUP
import video_udp_sender as V

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-78s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-78s %s" % (label, detail))


FW, FH = 1280, 720
FX = 0.5 * FW / math.tan(math.radians(35.0))     # a 70 degree lens
INTR = (FX, FX, FW / 2.0, FH / 2.0)
HEIGHT = 1.70


def person(cx=640.0, top=120.0, span=520.0, hips=True, ankles=True, hip_conf=0.9, torso=1.45, sh_scale=1.0,
           ears=True, shoulders=True, hip_pos=None):
    """A frontal person. span is nose-to-ankle in px; the shoulder line is 0.24 of it (the measured prior),
    the torso is `torso` shoulder widths, the ear line 0.48 of the shoulders. Returns (uv, zrel, conf)."""
    uv = np.zeros((133, 2), dtype=np.float32)
    zrel = np.zeros(133, dtype=np.float32)
    conf = np.zeros(133, dtype=np.float32)
    sw = 0.24 * span * sh_scale
    sy = top + 0.14 * span
    hy = sy + torso * sw
    uv[0] = (cx, top)
    conf[0] = 0.9
    ew = 0.48 * sw
    if ears:
        uv[3] = (cx - ew / 2, top - 0.01 * span)
        uv[4] = (cx + ew / 2, top - 0.01 * span)
        uv[1] = (cx - 0.41 * ew / 2, top - 0.02 * span)
        uv[2] = (cx + 0.41 * ew / 2, top - 0.02 * span)
        conf[1:5] = 0.9
    if shoulders:
        uv[5] = (cx - sw / 2, sy)
        uv[6] = (cx + sw / 2, sy)
        conf[5] = conf[6] = 0.9
    uv[11] = (cx - 0.39 * sw, hy)
    uv[12] = (cx + 0.39 * sw, hy)
    if hip_pos is not None:
        uv[11] = (cx - 0.39 * sw, hip_pos)
        uv[12] = (cx + 0.39 * sw, hip_pos)
    conf[11] = conf[12] = hip_conf if hips else 0.05
    uv[15] = (cx - 0.2 * sw, top + span)
    uv[16] = (cx + 0.2 * sw, top + span)
    conf[15] = conf[16] = 0.9 if ankles else 0.1
    zrel[5] = zrel[6] = -0.05
    zrel[0] = -0.02
    return uv, zrel, conf


def feed(tr, n, t0=0.0, fps=30.0, **kw):
    r = None
    for i in range(n):
        uv, zrel, conf = person(**kw)
        r = tr.update(uv, conf, zrel, INTR, (FW, FH), None, t0 + i / fps)
    return r


# ---------------------------------------------------------------- 1. the tiers
print("== 1. the tiers")
tr = FR.FramingTracker(0.3, HEIGHT)
uv, zrel, conf = person()
r = tr.update(uv, conf, zrel, INTR, (FW, FH))
mid = 0.5 * (uv[11] + uv[12])
check(r.tier == FR.TIER_HIPS, "both hips in the picture -> tier 0")
check(abs(r.hip_u - mid[0]) < 1e-3 and abs(r.hip_v - mid[1]) < 1e-3, "the root IS the measured mid-hip", "(%.1f, %.1f)" % (r.hip_u, r.hip_v))
old = V.estimate_hip_depth(uv, conf, 0.3, HEIGHT, INTR[1], None)
check(abs(r.hip_z - old) < 1e-6 and r.depth_source == FR.DEPTH_HEIGHT, "tier 0 + ankles: the depth is exactly estimate_hip_depth's", "%.4f vs %.4f" % (r.hip_z, old))
for span in (200.0, 340.0, 520.0, 650.0):
    u2, z2, c2 = person(span=span, top=40.0)
    r2 = FR.FramingTracker(0.3, HEIGHT).update(u2, c2, z2, INTR, (FW, FH))
    check(abs(r2.hip_z - V.estimate_hip_depth(u2, c2, 0.3, HEIGHT, INTR[1], None)) < 1e-6, "pre-F-60 depth reproduced at span %d px" % span)

# hips present but no ankles: the old rule fell back to 2.0 m; the shoulders now measure it
u3, z3, c3 = person(ankles=False)
r3 = FR.FramingTracker(0.3, HEIGHT).update(u3, c3, z3, INTR, (FW, FH))
expect = 0.24 * HEIGHT * FX / (0.24 * 520.0) - (-0.05)       # prior width / shoulder px, then to the hip plane
check(r3.tier == FR.TIER_HIPS and r3.depth_source == FR.DEPTH_SHOULDERS, "hips but no ankles: depth from the shoulders, not the 2.0 m default")
check(abs(r3.hip_z - (1.70 * FX / 520.0 + 0.0)) / (1.70 * FX / 520.0) < 0.12, "...and it lands within 12% of the height rule's value", "%.2f vs %.2f" % (r3.hip_z, 1.70 * FX / 520.0))
check(V.estimate_hip_depth(u3, c3, 0.3, HEIGHT, INTR[1], None) == 2.0, "(the F-49 rule really did say 2.0 m for that frame)")

# a cut-off person: the model squeezes its legs into the picture and answers with an "ankle" ABOVE the hips
ua, za, ca = person()
torso_px = float(ua[11][1] - ua[5][1])
for label, ankle_y in (("above the hips", float(ua[11][1]) - 100.0), ("only half a torso below them", float(ua[11][1]) + 0.5 * torso_px)):
    us = ua.copy()
    us[15] = (us[15][0], ankle_y)
    us[16] = (us[16][0], ankle_y)
    rs = FR.FramingTracker(0.3, HEIGHT).update(us, ca, za, INTR, (FW, FH))
    check(rs.tier == FR.TIER_HIPS and rs.depth_source == FR.DEPTH_SHOULDERS, "an ankle %s is not a height measurement: the shoulders give the depth" % label, "%.2f m" % rs.hip_z)
    check(abs(rs.hip_z - 1.70 * FX / 520.0) / (1.70 * FX / 520.0) < 0.2, "...and the depth stays near the true %.2f m, not the 6 m the squeezed span reads" % (1.70 * FX / 520.0), "%.2f m" % rs.hip_z)
rl = FR.FramingTracker(0.3, HEIGHT).update(ua, ca, za, INTR, (FW, FH))
check(rl.depth_source == FR.DEPTH_HEIGHT, "real legs (1.47 torsos from hip to ankle) still use the height rule")

# hips low confidence
u, z, c = person(hips=False)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH))
check(r.tier == FR.TIER_SHOULDERS, "hips below --conf -> tier 1")
check(c[15] > 0.89 and r.depth_source == FR.DEPTH_SHOULDERS, "...and a confident ankle does NOT bring the height rule back: the hips are not in view, so it is a guess")
# hips HIGH confidence but below the image edge: the measured failure
u, z, c = person(hip_pos=FH + 90.0)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH))
check(c[11] > 0.89 and r.tier == FR.TIER_SHOULDERS, "hips at confidence 0.9 but OUTSIDE the image -> tier 1, not a measurement")
u, z, c = person(hip_pos=FH * 0.97)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH))
check(r.tier == FR.TIER_SHOULDERS, "hips on the border are not counted at first: they must be 5% clear of the edge to start")
tr = FR.FramingTracker(0.3, HEIGHT)
tiers = []
for pos in (0.80, 0.90, 0.96, 1.00, 1.01, 1.03, 0.99, 0.96):
    u, z, c = person(hip_pos=FH * pos)
    tiers.append(tr.update(u, c, z, INTR, (FW, FH), None, len(tiers) / 30.0).tier)
check(tiers == [0, 0, 0, 0, 0, 1, 1, 1], "once counted, hips stay counted until they pass the 2% tolerance; then they must clear the 5% margin again", str(tiers))
tr = FR.FramingTracker(0.3, HEIGHT)
tiers = []
for pos in (0.99, 1.0, 0.97, 1.01, 0.96, 1.0, 0.98, 1.02):
    u, z, c = person(hip_pos=FH * pos)
    tiers.append(tr.update(u, c, z, INTR, (FW, FH), None, len(tiers) / 30.0).tier)
check(set(tiers) == {1}, "hips hovering on the border never flip the anchor: one stable estimated root", str(tiers))
u, z, c = person(hip_pos=FH * 0.90)
check(FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH)).tier == FR.TIER_HIPS, "hips 10% inside the frame are a measurement from the first frame")

# shoulders gone, face left
u, z, c = person(hips=False, shoulders=False)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH))
check(r is not None and r.tier == FR.TIER_HEAD and r.depth_source == FR.DEPTH_FACE, "no hips, no shoulders, a face -> tier 2, depth from the face")
check(r.hip_v > u[0][1], "the head-tier root hangs BELOW the nose", "root y %.0f, nose y %.0f" % (r.hip_v, u[0][1]))
u, z, c = person(hips=False, shoulders=False, ears=False)
check(FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH)) is None, "a nose with no face width has nothing to place or scale the person with -> None")
check(FR.FramingTracker(0.3, HEIGHT).update(np.zeros((133, 2), np.float32), np.zeros(133, np.float32),
                                            np.zeros(133, np.float32), INTR, (FW, FH)) is None, "nothing at all -> None")

# ---------------------------------------------------------------- the estimated root is right
print("== 2. the mid-run fall-back")
tr = FR.FramingTracker(0.3, HEIGHT)
feed(tr, 60)
check(tr.learned_hip and abs(tr.hip_b - 1.45) < 0.05, "60 full-body frames teach the hip offset (1.45 shoulder widths down)", "b=%.3f" % tr.hip_b)
check(tr.learned_shoulder and abs(tr.shoulder_m - 0.24 * HEIGHT) < 0.02, "...and this person's shoulder width in metres", "%.3f m" % tr.shoulder_m)
uv, zrel, conf = person()
truth = 0.5 * (uv[11] + uv[12])
truth_z = V.estimate_hip_depth(uv, conf, 0.3, HEIGHT, INTR[1], None)
uh, zh, ch = person(hips=False, ankles=False)
r = tr.update(uh, ch, zh, INTR, (FW, FH), None, 2.0)
torso_px = float(np.linalg.norm(truth - 0.5 * (uv[5] + uv[6])))
check(r.tier == FR.TIER_SHOULDERS, "the hips vanish in the middle of a run -> tier 1 on the very next frame")
check(math.hypot(r.hip_u - truth[0], r.hip_v - truth[1]) < 0.03 * torso_px, "the estimated root is within 3% of a torso of where the hips were", "%.2f px" % math.hypot(r.hip_u - truth[0], r.hip_v - truth[1]))
check(abs(r.hip_z - truth_z) / truth_z < 0.03, "and the depth continues the last measured one (within 3%)", "%.3f vs %.3f m" % (r.hip_z, truth_z))
# the same frame with a cold tracker (priors only) is worse but sane
cold = FR.FramingTracker(0.3, HEIGHT).update(uh, ch, zh, INTR, (FW, FH))
check(cold.tier == FR.TIER_SHOULDERS and abs(cold.hip_z - truth_z) / truth_z < 0.15, "a tracker that never saw the hips falls back on the measured priors (within 15%)", "%.2f vs %.2f" % (cold.hip_z, truth_z))

# a person who leans: the offset follows the shoulders, not the image
tr = FR.FramingTracker(0.3, HEIGHT)
feed(tr, 40)
for k in range(30):
    uh, zh, ch = person(cx=640.0 + 4.0 * k, hips=False, ankles=False)
    r = tr.update(uh, ch, zh, INTR, (FW, FH), None, 3.0 + k / 30.0)
check(abs(r.hip_u - (640.0 + 4.0 * 29)) < 1.5, "the estimated root follows a person who moves sideways", "%.1f" % r.hip_u)

# hysteresis: hips hovering at the gate
tr = FR.FramingTracker(0.3, HEIGHT)
seq = []
for c in (0.5, 0.5, 0.29, 0.35, 0.38, 0.29, 0.45, 0.45):
    uv, zrel, conf = person(hip_conf=c)
    seq.append(tr.update(uv, conf, zrel, INTR, (FW, FH), None, len(seq) / 30.0).tier)
check(seq == [0, 0, 1, 1, 1, 1, 0, 0], "hip confidence hovering at --conf: lost at once, regained only above conf + 0.10", str(seq))
tr = FR.FramingTracker(0.3, HEIGHT)
flips = 0
last = None
for i in range(200):
    c = 0.3 + 0.08 * math.sin(i * 0.9)         # wobbles across 0.22 .. 0.38
    uv, zrel, conf = person(hip_conf=c)
    t = tr.update(uv, conf, zrel, INTR, (FW, FH), None, i / 30.0).tier
    flips += 1 if (last is not None and t != last) else 0
    last = t
check(flips <= 8, "a wobbling confidence does not flap the tier every frame", "%d changes in 200 frames" % flips)

# depth may fall at once, may only rise slowly
tr = FR.FramingTracker(0.3, HEIGHT)
feed(tr, 60)
uh, zh, ch = person(hips=False, ankles=False)
z0 = float(tr.update(uh, ch, zh, INTR, (FW, FH), None, 2.0).hip_z)      # the result object is reused: copy
turned_u, turned_z, turned_c = person(hips=False, ankles=False, sh_scale=0.4)      # the shoulders turn away
z1 = float(tr.update(turned_u, turned_c, turned_z, INTR, (FW, FH), None, 2.0 + 1.0 / 30.0).hip_z)
check(z1 <= z0 * (1.0 + FR.MAX_DEPTH_RISE_PER_S / 30.0) + 1e-6, "a sudden narrow shoulder line (a turn) cannot throw the depth away", "%.2f -> %.2f m" % (z0, z1))
nearer_u, nearer_z, nearer_c = person(hips=False, ankles=False, sh_scale=1.6)
z2 = float(tr.update(nearer_u, nearer_c, nearer_z, INTR, (FW, FH), None, 2.0 + 2.0 / 30.0).hip_z)
check(z2 < z0 * 0.75, "...while stepping TOWARD the camera shows at once", "%.2f -> %.2f m" % (z0, z2))

# a webcam person, never seen full body: the face and shoulders place them at laptop distance
tr = FR.FramingTracker(0.3, HEIGHT)
u, z, c = person(top=90.0, span=1500.0, hips=False, ankles=False)      # the shoulders 360 px wide: a laptop distance
r = tr.update(u, c, z, INTR, (FW, FH))
check(0.3 < r.hip_z < 1.2, "a webcam-sized shoulder line is read as a laptop distance, not clamped to 0.6 m or the 2.0 m default", "%.2f m" % r.hip_z)

# --depth-range (Unity's default: 1.6-4.0 m) bounds the height rule, NOT the measured shoulder width
u, z, c = person(top=90.0, span=1500.0, hips=False, ankles=False)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH), (1.6, 4.0))
check(r.depth_source == FR.DEPTH_SHOULDERS and r.hip_z < 1.6, "--depth-range does not pin a webcam at 0.6 m to 1.6 m", "%.2f m" % r.hip_z)
u, z, c = person(span=520.0)
r = FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH), (3.0, 4.0))
check(r.depth_source == FR.DEPTH_HEIGHT and 3.0 <= r.hip_z <= 4.0, "...and still bounds the height rule, as before F-60", "%.2f m" % r.hip_z)
u, z, c = person(hips=False, shoulders=False, ears=False)
u[:, :] = 0.0
c[:] = 0.0
check(FR.FramingTracker(0.3, HEIGHT).update(u, c, z, INTR, (FW, FH), (1.6, 4.0)) is None, "(nothing seen is still nothing, whatever the band)")

# ---------------------------------------------------------------- zrel
print("== zrel reference")
tr = FR.FramingTracker(0.3, HEIGHT)
uv, zrel, conf = person()
zrel[11] = zrel[12] = 0.1
tr.update(uv, conf, zrel, INTR, (FW, FH))
check(abs(tr.zrel_hip(zrel) - 0.1) < 1e-6, "tier 0: the reference is the hips' own zrel (the F-49 value)")
uh, zh, ch = person(hips=False, ankles=False)
zh[11] = zh[12] = 0.9       # the model's hip depth for a body that is not there: ignored
tr.update(uh, ch, zh, INTR, (FW, FH))
check(abs(tr.zrel_hip(zh) - (-0.05 - tr.dz_shoulder)) < 1e-6, "tier 1: the reference ignores the hallucinated hip depth", "%.3f" % tr.zrel_hip(zh))

# ---------------------------------------------------------------- 3. honesty
print("== 3. honesty")
tr = FR.FramingTracker(0.3, HEIGHT)
uv = np.zeros((133, 2), np.float32)
conf = np.full(133, 0.9, np.float32)
uv[:, 0] = 600.0
uv[:, 1] = 300.0
uv[9] = (600.0, FH + 80.0)          # wrist below the image
uv[10] = (-80.0, 300.0)             # wrist left of it
uv[11] = (600.0, FH + 1.0)          # a hair past the edge, inside tolerance
out = np.zeros(133, np.float32)
res = tr.mask_outside(uv, conf, (FW, FH), out)
check(res is out, "mask_outside fills and returns the caller's buffer (no allocation per frame)")
check(out[9] == 0.0 and out[10] == 0.0, "joints outside the image are sent as absent")
check(abs(out[11] - 0.9) < 1e-6 and abs(out[0] - 0.9) < 1e-6, "joints inside it, and within 2% of its edge, keep their confidence")
check(abs(conf[9] - 0.9) < 1e-6, "the model's own array is not modified")

tr = FR.FramingTracker(0.3, HEIGHT)
uv, zrel, conf = person()
uv[:, :] = np.nan
check(tr.update(uv, conf, zrel, INTR, (FW, FH)) is None, "NaN keypoints are not a person (None), and nothing raises")
uv, zrel, conf = person(hips=False, ankles=False)
uv[5] = (np.nan, np.nan)
r = tr.update(uv, conf, zrel, INTR, (FW, FH))
check(r is None or all(math.isfinite(v) for v in (r.hip_u, r.hip_v, r.hip_z)), "one NaN shoulder never yields a NaN root")
uv, zrel, conf = person(ankles=False)
zrel[:] = np.nan
r = FR.FramingTracker(0.3, HEIGHT).update(uv, conf, zrel, INTR, (FW, FH))
check(r is not None and math.isfinite(r.hip_z) and r.depth_source == FR.DEPTH_DEFAULT, "a NaN depth offset falls back to the default depth, not a NaN root", str(r.hip_z))

tr = FR.FramingTracker(0.3, HEIGHT)
feed(tr, 60, span=520.0)
tr.hip_b = 2.2
uh, zh, ch = person(hips=False, ankles=False)
tr.update(uh, ch, zh, INTR, (FW, FH), None, 100.0)          # 98 s later: a different person
check(abs(tr.hip_b - FR.PRIOR_HIP_B) < 1e-9 and not tr.learned_shoulder, "a person absent for more than 5 s is forgotten: the next one is learned afresh", "b=%.2f" % tr.hip_b)

# events
tr = FR.FramingTracker(0.3, HEIGHT)
feed(tr, 5)
check(tr.take_event() is None, "healthy frames produce no event")
uh, zh, ch = person(hips=False, ankles=False)
tr.update(uh, ch, zh, INTR, (FW, FH), None, 1.0)
ev = tr.take_event()
check(ev is not None and "hips -> shoulders" in ev, "a tier change is reported once", str(ev))
check(tr.take_event() is None, "...and only once")
uf, zf, cf = person()
tr.update(uf, cf, zf, INTR, (FW, FH), None, 1.2)
tr.update(uh, ch, zh, INTR, (FW, FH), None, 1.4)
check(tr.take_event() is None, "changes within a second of the last report are counted but not printed", "changes=%d" % tr.tier_changes)
check(sum(tr.tier_frames) >= 8 and tr.summary().count("/") == 2, "per-tier frame counts for the heartbeat", tr.summary())

# ---------------------------------------------------------------- 4. the senders
print("== 4. the senders")


class ScriptedPose(object):
    def __init__(self, hidden_hips=False, cut=False):
        self.hidden = hidden_hips
        self.cut = cut

    def infer(self, frame, bbox):
        return person(hips=not self.hidden, ankles=not self.hidden, hip_pos=(FH + 90.0) if self.cut else None)


FRAME = np.zeros((FH, FW, 3), dtype=np.uint8)


def run(m, n=14):
    out = []
    for i in range(n):
        out.append(m.process(FRAME, INTR, i / 30.0, 100.0 + i / 30.0, 100.0 + i / 30.0))
    return [x for x in out if x is not None]


import person_tracker as PT  # noqa: E402


class Detector(object):
    """The same detection shape the real detector returns, one person every frame."""
    last_ms = 1.0

    def detect(self, frame):
        return [PT.Detection((0.35, 0.10, 0.65, 0.95), 0.9, None)]

    def describe(self):
        return "scripted"


def build2(pose, partial):
    return HMP.HostMultiPerson(pose, Detector(), V.estimate_hip_depth, "t", max_poses=2, partial_body=partial)


full_on = run(build2(ScriptedPose(), True))
full_off = run(build2(ScriptedPose(), False))
check(len(full_on) > 0 and len(full_on) == len(full_off), "a full-body person is sent on the same frames with partial_body on and off", "%d vs %d" % (len(full_on), len(full_off)))
a_, b_ = full_on[-1], full_off[-1]
check(a_["persons"][0]["lm"] == b_["persons"][0]["lm"] and a_["persons"][0]["xyz"] == b_["persons"][0]["xyz"],
      "...with byte-identical landmarks and root (the F-52 output, unchanged where it was right)", str(a_["persons"][0]["xyz"]))
check(a_["persons"][0]["fr"] == 0 and a_["fr"] == 0 and "fr" not in b_["persons"][0], "partial_body on adds `fr` 0; off adds nothing")

hidden_on = run(build2(ScriptedPose(hidden_hips=True), True))
hidden_off = run(build2(ScriptedPose(hidden_hips=True), False))
check(len(hidden_on) > 0, "partial_body ON: a person with no hips and no ankles is sent")
check(len(hidden_off) == 0, "partial_body OFF: the same person is dropped (the F-52 rule)")
p = hidden_on[-1]["persons"][0]
check(p["fr"] == 1 and hidden_on[-1]["fr"] == 1, "the datagram says tier 1, per person and at the root")
check(p["lm"][23][3] == 0.0 and p["lm"][24][3] == 0.0, "the hips are sent as ABSENT ([.., 0] confidence), not invented")
check(p["lm"][11][3] > 0.0 and p["lm"][12][3] > 0.0 and p["lm"][0][3] > 0.0, "the shoulders and the nose are sent")
check(p["lm"][27][3] == 0.0 and p["lm"][28][3] == 0.0, "the ankles are absent too")
check(0.3 < p["xyz"][2] / 1000.0 < 6.0 and math.isfinite(p["xyz"][0]), "the root is a finite, metric position", str(p["xyz"]))
cut_on = run(build2(ScriptedPose(cut=True), True))
check(len(cut_on) > 0 and cut_on[-1]["persons"][0]["fr"] == 1 and cut_on[-1]["persons"][0]["lm"][23][3] == 0.0,
      "hips at high confidence BELOW the image: tier 1, and masked out of the datagram")

# ---------------------------------------------------------------- the switch
print("== the off-switch and the supervisor")
src = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check('"--partial-body"' in src and "BooleanOptionalAction" in src, "the sender defines --partial-body (and its --no- form)")
check("partial_body=a.partial_body" in src, "the multi-person path is given the switch")


def cmd_with(*extra):
    a = SUP.build_arg_parser().parse_args(
        ["--script", os.path.join(os.path.dirname(V.__file__), "video_udp_sender.py"),
         "--video", "https://example.com/clip.mp4", "--evidence-dir", os.path.join(os.environ.get("TEMP", "."), "f60ev")] + list(extra))
    return SUP.Supervisor(a).build_command()


plain = cmd_with()
check("--partial-body" not in plain and "--no-partial-body" not in plain, "by default nothing is forwarded (the sender's own default is ON)")
off = cmd_with("--no-video-partial-body")
check("--no-partial-body" in off, "--no-video-partial-body is forwarded as --no-partial-body")
check([t for t in off if t != "--no-partial-body"] == plain, "nothing else in the command changes")

print("")
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
