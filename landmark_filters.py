#!/usr/bin/env python3
"""H-01 / H-02 - ONE-EURO FILTERING OF A WHOLE VECTOR, shared by the hand and the face trackers.

smoothing.OneEuro is a scalar filter with a fixed assumed sample rate. A landmark tracker filters hundreds of numbers per person
per frame and its frame rate varies with how many people it is following, so this filters a whole vector at once with numpy and
takes the sample rate on every call. The arithmetic is smoothing.OneEuro's, line for line (tests/test_landmark_filters.py checks
the two agree to 1e-12 on random streams with a changing rate); only the loop over the numbers is gone, which took the face
tracker's 1,486 filters from ~3 ms a frame to a fraction of one. Nothing here knows what the numbers mean.
"""

import math

import numpy as np


class VectorFilter(object):
    """A bank of independent One-Euro filters over one flat vector."""

    def __init__(self, size, min_cutoff, beta, d_cutoff=1.0):
        self.size = int(size)
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x = None      # the filtered value
        self._dx = None     # the filtered derivative
        self._last = None   # the previous raw value

    @staticmethod
    def _alpha(cutoff, freq):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / freq
        return 1.0 / (1.0 + tau / te)

    def filter(self, values, freq):
        """The filtered vector for `values` sampled at `freq` Hz."""
        x = np.asarray(values, dtype=np.float64).reshape(-1)
        freq = max(1e-3, float(freq))
        if self._last is None:
            dx = np.zeros_like(x)
        else:
            dx = (x - self._last) * freq
        if self._dx is None:
            self._dx = dx
        else:
            a = self._alpha(self.d_cutoff, freq)
            self._dx = a * dx + (1.0 - a) * self._dx
        cutoff = self.min_cutoff + self.beta * np.abs(self._dx)
        a = 1.0 / (1.0 + (1.0 / (2.0 * math.pi * cutoff)) * freq)
        if self._x is None:
            self._x = x.copy()
        else:
            self._x = a * x + (1.0 - a) * self._x
        self._last = x.copy()
        return self._x.copy()

    def reset(self):
        """Forget all history; the next call returns its input unchanged."""
        self._x = None
        self._dx = None
        self._last = None
