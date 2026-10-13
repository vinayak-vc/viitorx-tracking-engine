#!/usr/bin/env python3
"""H-01 tests for hand_gestures.py, against synthetic hands of KNOWN joint angles (tests/_synthetic_hand.py).

What this proves: the rules classify the poses they are written for, are unmoved by where the hand is turned or how
big it is, do not flicker at a threshold, and behave at the edges. What it does NOT prove: that the thresholds hold for
real hands on a real camera (NOT TESTED; the network's landmarks are noisier than these).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _synthetic_hand as S
import hand_gestures as GE

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


def read(world, previous=None):
    return GE.read_fingers(world, previous)


print("CURLS AND EXTENSION")
open_hand = S.make_hand(**S.OPEN)
st = read(open_hand)
check(all(c < 0.2 for c in st.curls), "an open hand has every curl below 0.2", str([round(c, 2) for c in st.curls]))
check(st.extended == [True] * 5, "an open hand has five extended fingers")
fist = S.make_hand(**S.FIST)
st = read(fist)
check(all(c > 0.75 for c in st.curls), "a fist has every curl above 0.75", str([round(c, 2) for c in st.curls]))
check(st.extended == [False] * 5, "a fist has no extended finger")
half = S.make_hand(finger_flex_deg=(120, 120, 120, 120), thumb_flex_deg=60)
check(all(0.35 < c < 0.65 for c in GE.finger_curls(half)[1:]), "a half-closed finger reads about 0.5",
      str([round(c, 2) for c in GE.finger_curls(half)]))

print("")
print("THE GESTURES")
cases = (
    ("OPEN", S.make_hand(**S.OPEN), GE.OPEN),
    ("FIST", S.make_hand(**S.FIST), GE.FIST),
    ("POINT", S.make_hand(**S.POINT), GE.POINT),
    ("VICTORY", S.make_hand(**S.VICTORY), GE.VICTORY),
    ("ROCK", S.make_hand(**S.ROCK), GE.ROCK),
    ("THUMBS_UP", S.make_hand(finger_flex_deg=(240,) * 4, thumb_flex_deg=0, thumb_dir=(0.35, -0.94, 0)),
     GE.THUMBS_UP),
)
for name, world, expected in cases:
    got = read(world).gesture
    check(got == expected, "%s is recognised" % name, "got %s" % got)

# THUMBS_DOWN is the same hand turned over: thumb straight, fingers closed, pointing down. It must not read as UP.
down = S.make_hand(finger_flex_deg=(240,) * 4, thumb_flex_deg=0, thumb_dir=(0.35, 0.94, 0))
check(read(down).gesture != GE.THUMBS_UP, "a thumb pointing DOWN is not THUMBS_UP", read(down).gesture)

ok_hand = S.make_hand(finger_flex_deg=(100, 0, 0, 0), thumb_flex_deg=30)
ok_hand[GE.H.THUMB_TIP] = ok_hand[GE.H.INDEX_TIP] + np.array([0.004, 0.0, 0.0])
st = read(ok_hand)
check(st.gesture == GE.OK, "thumb on index tip with three fingers out is OK", "pinch %.2f gesture %s" % (st.pinch, st.gesture))
check(st.pinch > 0.9, "and its pinch is near 1", "%.2f" % st.pinch)

# A relaxed hand with the tips well apart is not a pinch.
check(read(open_hand).pinch == 0.0, "an open hand pinches 0")

print("")
print("PINCH IS NOT A FIST")
fist_pinch = GE.pinch_amount(fist)
raw = GE.pinch_amount(fist, index_curl=0.0)
check(fist_pinch == 0.0, "a fist reads pinch 0 even if its tips are close", "gated %.2f (ungated %.2f)" % (fist_pinch, raw))
check(raw > 0.3, "(the tips ARE close in a fist, so the gate is what prevents a false click)", "ungated %.2f" % raw)

print("")
print("INVARIANCE")
for name, world, expected in cases[:5]:
    ok = True
    for rx, ry, rz in ((0, 0, 0), (30, 0, 0), (0, 70, 0), (0, 0, 90), (45, 45, 45), (0, 0, 180)):
        ok = ok and read(S.rotate(world, rx, ry, rz)).gesture == expected
    check(ok, "%s survives any rotation of the hand" % name)
    ok = all(read(world * k).gesture == expected for k in (0.5, 1.0, 1.6))
    check(ok, "%s survives a bigger or smaller hand" % name)
# THUMBS_UP says 'up', so turning the hand upside down must change it.
tu = cases[5][1]
check(read(S.rotate(tu, 0, 0, 180)).gesture != GE.THUMBS_UP, "THUMBS_UP is NOT rotation invariant: upside down is not up")

print("")
print("HYSTERESIS")
# Sweep one finger from open to closed and back; count how often the 'extended' flag flips.
flips = 0
prev_state = None
prev_ext = None
for deg in list(range(0, 241, 6)) + list(range(240, -1, -6)):
    world = S.make_hand(finger_flex_deg=(deg, 0, 0, 0), thumb_flex_deg=0)
    st = read(world, prev_ext)
    if prev_state is not None and st.extended[1] != prev_state:
        flips += 1
    prev_state = st.extended[1]
    prev_ext = st.extended
check(flips == 2, "a finger swept closed and open flips its state exactly twice", "flips %d" % flips)
# At the boundary, wobbling inside the hysteresis band must not flip it.
band_lo = int(GE.EXTEND_BELOW * GE.FULL_FIST_FLEXION_DEG) + 6
band_hi = int(GE.CURL_ABOVE * GE.FULL_FIST_FLEXION_DEG) - 6
ext = read(S.make_hand(finger_flex_deg=(0, 0, 0, 0)))
state = ext.extended
changed = False
for deg in (band_lo, band_hi, band_lo, band_hi, (band_lo + band_hi) // 2):
    st = read(S.make_hand(finger_flex_deg=(deg, 0, 0, 0)), state)
    changed = changed or st.extended[1] != True
    state = st.extended
check(not changed, "wobbling inside the band keeps an extended finger extended", "band %d..%d deg" % (band_lo, band_hi))

print("")
print("STABILISER")
stab = GE.GestureStabilizer(hold=3)
seq = [GE.FIST, GE.FIST, GE.OPEN, GE.FIST, GE.OPEN, GE.OPEN, GE.OPEN]
out = [stab.update(g) for g in seq]
check(out[:3] == [GE.NONE] * 3, "a new gesture is not adopted on its first frames", str(out[:3]))
check(out[-1] == GE.OPEN, "and is adopted after 3 in a row", str(out))
stab2 = GE.GestureStabilizer(hold=3)
for g in [GE.OPEN] * 3:
    stab2.update(g)
check(stab2.update(GE.FIST) == GE.OPEN and stab2.update(GE.OPEN) == GE.OPEN, "one stray frame does not change it")
stab3 = GE.GestureStabilizer(hold=1)
check(stab3.update(GE.POINT) == GE.POINT, "hold=1 adopts at once")
try:
    GE.GestureStabilizer(hold=0)
    check(False, "hold=0 is refused")
except ValueError:
    check(True, "hold=0 is refused")

print("")
print("DEGENERATE INPUT")
flat = np.zeros((21, 3))
st = read(flat)
check(st.gesture == GE.NONE and st.extended == [False] * 5, "all-zero landmarks are NONE, not an open hand", st.gesture)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
