"""A synthetic hand for the hand tests: 21 world landmarks built by forward kinematics, with joint angles you choose.

Hand-centred metres, the network's axes: x right, y DOWN, z away from the camera, palm facing the camera, fingers up.
Bone lengths are an adult hand's (wrist to middle fingertip ~0.18 m; the palm 0.09 m, the network's own median).
Not a model of a real hand's every degree of freedom - just enough that finger flexion, thumb position and pinch are
physically consistent, so a rule can be tested against known inputs instead of against a network's output.
"""
import math

import numpy as np

WRIST = np.array([0.0, 0.05, 0.0])

#: (knuckle position, [proximal, middle, distal] bone lengths) for index, middle, ring, pinky.
_FINGERS = (
    (np.array([0.030, -0.036, 0.0]), (0.040, 0.023, 0.019)),
    (np.array([0.010, -0.040, 0.0]), (0.045, 0.027, 0.020)),
    (np.array([-0.010, -0.037, 0.0]), (0.042, 0.026, 0.020)),
    (np.array([-0.028, -0.030, 0.0]), (0.032, 0.018, 0.017)),
)

#: How a finger's total flexion is shared between its knuckle, middle and tip joints (a real fist is ~90/100/60).
_FLEX_SHARE = (0.36, 0.40, 0.24)

_THUMB_CMC = np.array([0.018, 0.035, 0.0])
_THUMB_LENGTHS = (0.040, 0.035, 0.030)
#: Thumb direction held out to the side ("L" shape), and the direction it folds toward (across the palm, toward the camera).
_THUMB_OUT = np.array([0.87, -0.50, 0.0])
_THUMB_FOLD = np.array([-0.55, -0.30, -0.78])


def _unit(v):
    return v / np.linalg.norm(v)


def make_hand(finger_flex_deg=(0.0, 0.0, 0.0, 0.0), thumb_flex_deg=0.0, thumb_dir=None):
    """21 landmarks (MediaPipe order). `finger_flex_deg` is the TOTAL flexion of index, middle, ring, pinky (240 = a
    closed fist's finger); `thumb_flex_deg` is the thumb's (0 straight out, 120 folded across the palm). `thumb_dir`
    overrides the direction the straight thumb points (default out to the side)."""
    pts = np.zeros((21, 3))
    pts[0] = WRIST
    # Thumb: 1 CMC, 2 MCP, 3 IP, 4 TIP. The chain bends toward the fold direction as flexion grows.
    base = _unit(np.asarray(thumb_dir if thumb_dir is not None else _THUMB_OUT, dtype=np.float64))
    fold = _unit(_THUMB_FOLD - _THUMB_FOLD.dot(base) * base)
    pts[1] = _THUMB_CMC
    total = math.radians(thumb_flex_deg)
    angle = 0.0
    position = pts[1]
    for k, (length, share) in enumerate(zip(_THUMB_LENGTHS, (0.2, 0.4, 0.4))):
        angle += total * share
        direction = math.cos(angle) * base + math.sin(angle) * fold
        position = position + length * direction
        pts[2 + k] = position
    # Long fingers: 5-8 index, 9-12 middle, 13-16 ring, 17-20 pinky. Flexion rotates the bone toward -z (the palm).
    for f, (knuckle, lengths) in enumerate(_FINGERS):
        total = math.radians(finger_flex_deg[f])
        base_index = 5 + 4 * f
        pts[base_index] = knuckle
        angle = 0.0
        position = knuckle
        for k, (length, share) in enumerate(zip(lengths, _FLEX_SHARE)):
            angle += total * share
            direction = np.array([0.0, -math.cos(angle), -math.sin(angle)])
            position = position + length * direction
            pts[base_index + 1 + k] = position
    return pts


def rotate(points, rx=0.0, ry=0.0, rz=0.0):
    """Rotate about the hand's centre by Euler angles in degrees (x, then y, then z)."""
    ax, ay, az = (math.radians(a) for a in (rx, ry, rz))
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return points @ (mz @ my @ mx).T


def center(points):
    """The same hand with its centroid at the origin, as the network's world landmarks are."""
    return points - points.mean(axis=0)


OPEN = dict(finger_flex_deg=(0, 0, 0, 0), thumb_flex_deg=0)
FIST = dict(finger_flex_deg=(240, 240, 240, 240), thumb_flex_deg=120)
POINT = dict(finger_flex_deg=(0, 240, 240, 240), thumb_flex_deg=120)
VICTORY = dict(finger_flex_deg=(0, 0, 240, 240), thumb_flex_deg=120)
ROCK = dict(finger_flex_deg=(0, 240, 240, 0), thumb_flex_deg=120)
