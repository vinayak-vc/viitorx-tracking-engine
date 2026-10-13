#!/usr/bin/env python3
"""H-01 - FINGER POSES AND GESTURES from the 21 world landmarks.

WHY WORLD LANDMARKS. They are metres and hand-centred, so a bend angle or a fingertip distance read from them does
not depend on how far the hand is from the camera or which way it is turned. Everything below is rotation and scale
invariant by construction, except THUMBS_UP, which says "up" and so reads the world y axis (image down = +y).

A finger's CURL is 0 for a straight finger and 1 for a fully closed one: the sum of its flexion at the three joints
(knuckle, middle, tip), each the angle between the bone before the joint and the bone after it, over a full fist's
total. A finger is EXTENDED or CURLED with a gap between the two thresholds, and a finger already extended keeps that
state until it crosses the CLOSE threshold (hysteresis), so a hand held at the boundary does not flicker.

THE GESTURES (a closed set; anything else is NONE rather than a guess)
  OPEN       index, middle, ring and pinky extended
  FIST       none of the four fingers extended, thumb not out
  POINT      index extended, middle, ring and pinky curled
  VICTORY    index and middle extended, ring and pinky curled
  ROCK       index and pinky extended, middle and ring curled
  THUMBS_UP  thumb out and pointing up, the four fingers curled
  OK         thumb tip touching the index tip, middle, ring and pinky extended
PINCH is separate and continuous: 1 when the thumb and index tips touch, 0 when they are a palm-length apart. It is
forced to 0 while the index finger is closed (curl above PINCH_INDEX_MAX_CURL): in a fist the thumb and index tips are
close together without anyone pinching, and a consumer using pinch as a click would fire on every fist.

The thresholds are anatomical constants checked against synthetic hands of known joint angles
(tests/test_hand_gestures.py). Whether they hold for real hands on a real camera has NOT been measured here.

NOT HERE: detection, tracking, depth or the wire. Pure numpy.
"""

import math

import numpy as np

import hand_landmarker as H

# Finger order everywhere in this module and on the wire: thumb, index, middle, ring, pinky.
FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")

# (MCP, PIP, DIP, TIP) landmark indices of the four long fingers.
_LONG_FINGERS = (
    (H.INDEX_MCP, H.INDEX_PIP, H.INDEX_DIP, H.INDEX_TIP),
    (H.MIDDLE_MCP, H.MIDDLE_PIP, H.MIDDLE_DIP, H.MIDDLE_TIP),
    (H.RING_MCP, H.RING_PIP, H.RING_DIP, H.RING_TIP),
    (H.PINKY_MCP, H.PINKY_PIP, H.PINKY_DIP, H.PINKY_TIP),
)

#: Total flexion (degrees, knuckle + middle + tip) of a fully closed finger. A finger's curl is its flexion over this.
FULL_FIST_FLEXION_DEG = 240.0
#: Curl below which a finger counts as extended, and above which it counts as curled. Between them it keeps its state.
EXTEND_BELOW = 0.35
CURL_ABOVE = 0.55

#: Thumb: it is EXTENDED when it is nearly straight (its curl below THUMB_EXTEND_BELOW) and its tip is held away from
#: the index knuckle (tip-to-knuckle distance over palm length at or above THUMB_OUT_ABOVE). Hysteresis as for the fingers.
THUMB_EXTEND_BELOW = 0.40
THUMB_CURL_ABOVE = 0.55
THUMB_OUT_ABOVE = 0.35
THUMB_IN_BELOW = 0.28

#: Thumb-to-index tip distance over palm length: touching at or below PINCH_CLOSED, open at or above PINCH_OPEN.
PINCH_CLOSED = 0.25
PINCH_OPEN = 1.0
#: A closed index finger cannot pinch; above this curl the pinch is 0.
PINCH_INDEX_MAX_CURL = 0.75
#: Pinch value at or above which OK is recognised.
OK_PINCH_ABOVE = 0.8

#: How far above horizontal the thumb must point (the sine of the elevation) for THUMBS_UP. World y is DOWN.
THUMBS_UP_ELEVATION = 0.6

OPEN = "OPEN"
FIST = "FIST"
POINT = "POINT"
VICTORY = "VICTORY"
ROCK = "ROCK"
THUMBS_UP = "THUMBS_UP"
OK = "OK"
NONE = "NONE"
GESTURES = (OPEN, FIST, POINT, VICTORY, ROCK, THUMBS_UP, OK, NONE)


def _flexion_deg(a, b, c):
    """The bend at joint `b` between bones a->b and b->c, in degrees: 0 when the three are in a straight line."""
    u = b - a
    v = c - b
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    cosine = max(-1.0, min(1.0, float(u.dot(v)) / (nu * nv)))
    return math.degrees(math.acos(cosine))


def palm_length(world):
    """Wrist to middle-finger knuckle, metres: the ruler every distance below is divided by."""
    return float(np.linalg.norm(world[H.MIDDLE_MCP] - world[H.WRIST]))


def finger_curls(world):
    """Curl 0..1 for thumb, index, middle, ring, pinky. `world` is (21, 3)."""
    w = np.asarray(world, dtype=np.float64)
    curls = []
    # Thumb: flexion at its knuckle and tip, over the most a thumb bends in a fist.
    thumb = _flexion_deg(w[H.THUMB_CMC], w[H.THUMB_MCP], w[H.THUMB_IP]) + \
        _flexion_deg(w[H.THUMB_MCP], w[H.THUMB_IP], w[H.THUMB_TIP])
    curls.append(min(1.0, thumb / 120.0))
    for mcp, pip, dip, tip in _LONG_FINGERS:
        total = _flexion_deg(w[H.WRIST], w[mcp], w[pip]) + _flexion_deg(w[mcp], w[pip], w[dip]) + \
            _flexion_deg(w[pip], w[dip], w[tip])
        curls.append(min(1.0, total / FULL_FIST_FLEXION_DEG))
    return curls


def thumb_out(world):
    """Thumb-tip to index-knuckle distance over palm length: small when the thumb is tucked, large when it is out."""
    w = np.asarray(world, dtype=np.float64)
    length = palm_length(w)
    if length < 1e-9:
        return 0.0
    return float(np.linalg.norm(w[H.THUMB_TIP] - w[H.INDEX_MCP])) / length


def pinch_amount(world, index_curl=None):
    """0..1: 1 when the thumb and index tips touch, 0 when they are a palm length apart or more, and 0 while the index
    finger is closed. `index_curl` is passed when the caller already has the curls."""
    w = np.asarray(world, dtype=np.float64)
    length = palm_length(w)
    if length < 1e-9:
        return 0.0
    if index_curl is None:
        index_curl = finger_curls(w)[1]
    if index_curl > PINCH_INDEX_MAX_CURL:
        return 0.0
    d = float(np.linalg.norm(w[H.THUMB_TIP] - w[H.INDEX_TIP])) / length
    return max(0.0, min(1.0, (PINCH_OPEN - d) / (PINCH_OPEN - PINCH_CLOSED)))


def thumb_elevation(world):
    """Sine of the angle the thumb (knuckle to tip) points above horizontal: 1 straight up, -1 straight down."""
    w = np.asarray(world, dtype=np.float64)
    v = w[H.THUMB_TIP] - w[H.THUMB_MCP]
    n = float(np.linalg.norm(v))
    return float(-v[1] / n) if n > 1e-9 else 0.0


class FingerState(object):
    """What one frame of landmarks says about the fingers.

    curls      five floats 0..1, thumb to pinky
    extended   five bools, all with hysteresis; the thumb is extended when nearly straight AND held away from the palm
    pinch      0..1
    gesture    one of GESTURES (instantaneous; GestureStabilizer makes it steady)
    """
    __slots__ = ("curls", "extended", "pinch", "gesture")

    def __init__(self, curls, extended, pinch, gesture):
        self.curls = curls
        self.extended = extended
        self.pinch = pinch
        self.gesture = gesture


def classify(extended, pinch, thumb_up):
    """The gesture for a finger configuration. `extended` is five bools (thumb first)."""
    thumb, index, middle, ring, pinky = extended
    if index and middle and ring and pinky:
        return OPEN
    if pinch >= OK_PINCH_ABOVE and middle and ring and pinky:
        return OK
    if index and pinky and not middle and not ring:
        return ROCK
    if index and middle and not ring and not pinky:
        return VICTORY
    if index and not middle and not ring and not pinky:
        return POINT
    if not (index or middle or ring or pinky):
        if thumb and thumb_up:
            return THUMBS_UP
        if not thumb:
            return FIST
    return NONE


def read_fingers(world, previous=None):
    """FingerState for one frame. `previous` is the last frame's `extended` list, which supplies the hysteresis;
    pass None for the first frame of a hand."""
    w = np.asarray(world, dtype=np.float64)
    if palm_length(w) < 1e-6:
        # Landmarks that all coincide have no bones to bend: say nothing rather than call it an open hand.
        return FingerState([0.0] * 5, [False] * 5, 0.0, NONE)
    curls = finger_curls(w)
    extended = [False] * 5
    for i in range(1, 5):
        was = bool(previous[i]) if previous is not None else False
        if was:
            extended[i] = curls[i] < CURL_ABOVE
        else:
            extended[i] = curls[i] < EXTEND_BELOW
    out = thumb_out(w)
    if previous is not None and previous[0]:
        extended[0] = curls[0] < THUMB_CURL_ABOVE and out > THUMB_IN_BELOW
    else:
        extended[0] = curls[0] < THUMB_EXTEND_BELOW and out >= THUMB_OUT_ABOVE
    pinch = pinch_amount(w, curls[1])
    gesture = classify(extended, pinch, thumb_elevation(w) >= THUMBS_UP_ELEVATION)
    return FingerState(curls, extended, pinch, gesture)


class GestureStabilizer(object):
    """Turns the per-frame gesture into a steady one: a new gesture is adopted only after `hold` frames in a row."""

    def __init__(self, hold=3):
        if hold < 1:
            raise ValueError("hold must be at least 1")
        self.hold = int(hold)
        self.current = NONE
        self._candidate = NONE
        self._count = 0

    def update(self, gesture):
        if gesture == self.current:
            self._candidate = gesture
            self._count = 0
        elif gesture == self._candidate:
            self._count += 1
            if self._count >= self.hold:
                self.current = gesture
                self._count = 0
        else:
            self._candidate = gesture
            self._count = 1
            if self.hold == 1:
                self.current = gesture
                self._count = 0
        return self.current

    def reset(self):
        self.current = NONE
        self._candidate = NONE
        self._count = 0
