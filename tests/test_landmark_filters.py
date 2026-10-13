#!/usr/bin/env python3
"""H-01 / H-02 tests for landmark_filters.VectorFilter against smoothing.OneEuro, the scalar filter it replaces.

The point of the vectorised filter is speed with IDENTICAL arithmetic, so the test is agreement with the reference on random
streams, including a sample rate that changes from call to call (as it does when the tracker's load changes).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import landmark_filters as LF
import smoothing

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


rng = np.random.RandomState(11)

print("AGREES WITH smoothing.OneEuro")
for min_cutoff, beta in ((1.5, 30.0), (0.5, 0.4), (3.0, 0.0), (1.0, 5.0)):
    size = 7
    vec = LF.VectorFilter(size, min_cutoff, beta)
    refs = [smoothing.OneEuro(freq=30.0, min_cutoff=min_cutoff, beta=beta) for _ in range(size)]
    worst = 0.0
    value = rng.normal(0, 1, size)
    for step in range(300):
        value = value + rng.normal(0, 0.05, size) + (rng.rand(size) < 0.05) * rng.normal(0, 2.0, size)
        freq = float(rng.choice([12.0, 24.0, 30.0, 60.0, 5.0]))
        got = vec.filter(value, freq)
        want = np.empty(size)
        for i, f in enumerate(refs):
            f.set_freq(freq)
            want[i] = f.filter(float(value[i]))
        worst = max(worst, float(np.abs(got - want).max()))
    check(worst < 1e-12, "min_cutoff %.1f beta %.1f: 300 steps at a changing rate" % (min_cutoff, beta), "worst %.2e" % worst)

print("")
print("BEHAVIOUR")
f = LF.VectorFilter(3, 1.0, 0.0)
first = f.filter([1.0, 2.0, 3.0], 30.0)
check(np.allclose(first, [1.0, 2.0, 3.0]), "the first call returns its input unchanged")
second = f.filter([1.0, 2.0, 3.0], 30.0)
check(np.allclose(second, [1.0, 2.0, 3.0]), "a constant input stays constant")
moved = f.filter([2.0, 2.0, 3.0], 30.0)
check(1.0 < moved[0] < 2.0 and moved[1] == 2.0, "a step is followed partway, only in the channel that moved", str(np.round(moved, 3)))
f.reset()
check(np.allclose(f.filter([9.0, 9.0, 9.0], 30.0), [9.0, 9.0, 9.0]), "after reset() the next call is a first call")
out = f.filter([1.0, 2.0, 3.0], 30.0)
out[0] = 999.0
check(f.filter([1.0, 2.0, 3.0], 30.0)[0] < 100.0, "the returned array is a copy: changing it does not corrupt the filter")
check(LF.VectorFilter(1, 1.0, 0.0).filter([5.0], 0.0)[0] == 5.0, "a zero or negative rate does not divide by zero")

print("")
print("FAST ENOUGH TO BE WORTH IT")
big = LF.VectorFilter(1434, 1.5, 30.0)
data = rng.normal(0, 1, 1434)
t = time.perf_counter()
for _ in range(200):
    big.filter(data + rng.normal(0, 0.01, 1434), 30.0)
per_call_ms = (time.perf_counter() - t) / 200.0 * 1000.0
check(per_call_ms < 1.0, "1434 values (a face's landmarks) filter in under 1 ms", "%.3f ms (the scalar loop took ~3 ms)" % per_call_ms)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
