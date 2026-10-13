#!/usr/bin/env python3
"""H-01 - THE HANDS WIRE: one JSON object per UDP datagram, default 127.0.0.1:8901.

This is a NEW message on a NEW port. It does not touch the body sender's datagram (127.0.0.1:8899), so the VRM mirror,
every showcase scene and OakDUdpPoseProvider are unaffected by it and unaware of it. The contract is versioned: `ver`
is 1, and a receiver must ignore a datagram whose `ver` it does not know rather than guess.

    {
      "ver": 1,
      "kind": "hands",
      "sid": "hd0123456789",         producer session; a new value is a new producer, so a receiver can drop its state
      "seq": 1042,                   monotonically increasing per session
      "t": 1791890123.456,           sender wall clock (unix seconds) when the datagram was built
      "lat": 38.2,                   ms from the frame being read to the datagram being built
      "src": "camera",               camera | stream | lan | file
      "img": [1280, 720],            the frame the model saw, AFTER --rotate / --flip: width, height
      "fpx": 1108.5,                 the focal length in pixels the 3D position was computed with (ASSUMED, see below)
      "mir": 0,                      1 when the picture was mirrored before the model (a selfie view)
      "n": 2,                        number of entries in "hands"
      "hands": [
        {
          "id": 3,                   stable per hand; never reused within a session
          "side": "R",               "L" or "R"; see hand_tracking.py for what this means for a camera
          "hd": 0.93,                the averaged handedness score the label came from, 0..1
          "conf": 0.97,              the landmark network's confidence for this frame, 0..1
          "st": 0,                   0 TRACKED (read this frame), 1 HELD (last values, a frame or two stale)
          "lm": [[x, y, z] x 21],    normalised image: x / width, y / height, z / width (smaller z is nearer)
          "wl": [[x, y, z] x 21],    metres, hand-centred, x right, y DOWN, z away from the camera
          "pos": [X, Y, Z],          metres, camera space (X right, Y down, Z forward), the hand's centre; ABSENT when
                                     the 3D fit was refused (see "why")
          "why": "",                 present only when "pos" is absent: shape | degenerate | scale | residual | range
          "fing": [c x 5],           curl 0..1 of thumb, index, middle, ring, pinky
          "ext": [b x 5],            1 when that finger is extended (with hysteresis)
          "pinch": 0.12,             0..1; 1 when the thumb and index tips touch
          "ges": "OPEN"              OPEN FIST POINT VICTORY ROCK THUMBS_UP OK NONE (stabilised)
        }
      ]
    }

Landmark order is MediaPipe's: 0 wrist; 1-4 thumb; 5-8 index; 9-12 middle; 13-16 ring; 17-20 pinky (hand_landmarker.py).

THE DEPTH IS ESTIMATED, NOT MEASURED. One camera has no stereo. `pos` comes from comparing the hand's size in the
picture with the size of an average adult hand (hand_geometry.py), through a focal length that is assumed from a field
of view the camera does not report. A hand 10 % bigger than average reads 10 % too near. `wl` and `lm`, the shape and
the picture position, do not depend on that assumption. Accuracy against a ruler has not been measured (H-01 NOT TESTED).

A hand that is not in the picture is not in the list. There is no placeholder entry and nothing is predicted beyond a
HELD frame or two.

NOT HERE: tracking, models, sockets. This module only turns tracker output into a dict and back, so a receiver and the
tests share one definition. Pure python + numpy.
"""

import json

import numpy as np

WIRE_VERSION = 1
KIND = "hands"
DEFAULT_PORT = 8901
NUM_LANDMARKS = 21

#: Decimal places kept. lm is a fraction of the frame (1e-5 is far below a pixel); wl is metres (0.1 mm).
LM_DECIMALS = 5
WL_DECIMALS = 4
POS_DECIMALS = 4


def _round_matrix(a, decimals):
    return [[round(float(v), decimals) for v in row] for row in np.asarray(a)]


def encode_hand(hand):
    """One tracker hand (hand_tracking.TrackedHand) as its wire dict."""
    f = hand.fingers
    entry = {
        "id": int(hand.id),
        "side": hand.side,
        "hd": round(float(hand.handed), 3),
        "conf": round(float(hand.conf), 3),
        "st": int(hand.state),
        "lm": _round_matrix(hand.image, LM_DECIMALS),
        "wl": _round_matrix(hand.world, WL_DECIMALS),
        "fing": [round(float(c), 3) for c in f.curls],
        "ext": [1 if e else 0 for e in f.extended],
        "pinch": round(float(f.pinch), 3),
        "ges": f.gesture,
    }
    if hand.position is not None:
        entry["pos"] = [round(float(v), POS_DECIMALS) for v in hand.position]
    else:
        entry["why"] = hand.reason or "none"
    return entry


def encode_message(hands, session_id, seq, now, latency_ms, source, width, height, focal_px, mirrored):
    """The datagram's dict for one frame. `hands` is the tracker's list; an empty list is legal and means no hand."""
    return {
        "ver": WIRE_VERSION,
        "kind": KIND,
        "sid": session_id,
        "seq": int(seq),
        "t": round(float(now), 3),
        "lat": round(float(latency_ms), 1),
        "src": source,
        "img": [int(width), int(height)],
        "fpx": round(float(focal_px), 1),
        "mir": 1 if mirrored else 0,
        "n": len(hands),
        "hands": [encode_hand(h) for h in hands],
    }


def to_bytes(message):
    """Compact UTF-8 JSON for the socket."""
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


class WireError(ValueError):
    """A datagram that is not a hands message this version understands."""


def parse_message(data):
    """Bytes or a dict -> the validated message dict. Raises WireError with the reason for anything else.

    Checks what a receiver relies on: the version and kind, and for each hand that the landmark arrays have the right
    shape. It does not clamp or repair anything.
    """
    if isinstance(data, (bytes, bytearray)):
        try:
            data = json.loads(bytes(data).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise WireError("not JSON: %s" % e)
    if not isinstance(data, dict):
        raise WireError("not an object")
    if data.get("ver") != WIRE_VERSION:
        raise WireError("unknown version %r" % (data.get("ver"),))
    if data.get("kind") != KIND:
        raise WireError("not a hands message (kind=%r)" % (data.get("kind"),))
    hands = data.get("hands")
    if not isinstance(hands, list) or data.get("n") != len(hands):
        raise WireError("n does not match hands")
    for i, h in enumerate(hands):
        for key in ("lm", "wl"):
            arr = h.get(key)
            if not isinstance(arr, list) or len(arr) != NUM_LANDMARKS or any(len(p) != 3 for p in arr):
                raise WireError("hand %d: %s is not 21 x 3" % (i, key))
        if h.get("side") not in ("L", "R"):
            raise WireError("hand %d: bad side %r" % (i, h.get("side")))
        if len(h.get("fing", [])) != 5 or len(h.get("ext", [])) != 5:
            raise WireError("hand %d: fing/ext are not 5 long" % i)
        if "pos" in h and len(h["pos"]) != 3:
            raise WireError("hand %d: pos is not 3 long" % i)
    return data
