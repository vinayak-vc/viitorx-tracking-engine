#!/usr/bin/env python3
"""H-02 - THE FACE WIRE: one JSON object per UDP datagram, default 127.0.0.1:8902.

A NEW message on a NEW port, a sibling of the hands wire (hand_wire.py, 8901). The body sender's datagram (8899) is untouched.
The contract is versioned: `ver` is 1, and a receiver must ignore a datagram whose `ver` it does not know rather than guess.

    {
      "ver": 1,
      "kind": "face",
      "sid": "fc0123456789",         producer session; a new value is a new producer, so a receiver can drop its state
      "seq": 1042,                   monotonically increasing per session
      "t": 1791890123.456,           sender wall clock (unix seconds) when the datagram was built
      "lat": 38.2,                   ms from the frame being read to the datagram being built
      "src": "camera",               camera | stream | lan | file
      "img": [1280, 720],            the frame the model saw, AFTER --rotate / --flip: width, height
      "fpx": 1108.5,                 the focal length in pixels the 3D position was computed with (ASSUMED, see below)
      "mir": 0,                      1 when the picture was mirrored before the model (a selfie view)
      "bsv": 1,                      which blendshape ordering `bs` uses (1 = BLENDSHAPE_NAMES below)
      "n": 1,                        number of entries in "faces"
      "faces": [
        {
          "id": 1,                   stable per face; never reused within a session
          "conf": 0.99,              the mesh network's face-presence for this frame, 0..1
          "st": 0,                   0 TRACKED (read this frame), 1 HELD (last values, a frame or two stale)
          "bs": [52 floats],         blendshape scores 0..1 in BLENDSHAPE_NAMES order; ABSENT with --no-blendshapes
          "lm": [[x, y, z] x 478],   normalised image: x / width, y / height, z / width (smaller z is nearer); 468 mesh
                                     points then 10 iris points; ABSENT with --landmarks none, 468 long with --landmarks mesh
          "pos": [X, Y, Z],          metres, camera space (X right, Y down, Z forward): the point between the eyes; ABSENT
                                     when the 3D fit was refused (see "why")
          "why": "",                 present only when "pos" is absent: degenerate | range
          "yaw": 4.2, "pitch": -3.0, "roll": 1.1,   degrees; see face_geometry.py for the signs
          "rot": [9 floats],         the head's rotation, row-major 3x3: columns are the head's x, y, z axes in camera axes
          "pose": 1                  0 when the landmarks were degenerate and yaw/pitch/roll/rot are not valid
        }
      ]
    }

BLENDSHAPE_NAMES (52): index 0 is `_neutral`, MediaPipe's own extra channel (how neutral the face is); indexes 1-51 are the 51
ARKit-compatible blendshapes (browDownLeft ... noseSneerRight). There is no tongueOut. "Left" and "Right" are the SUBJECT's
left and right, as in ARKit. In a mirrored picture (`mir` 1) the subject's right is on the image's right.

THE POSITION IS ESTIMATED, NOT MEASURED. One camera has no stereo. `pos` comes from the distance between the outer eye corners in
the picture against an assumed 0.09 m, through a focal length assumed from a field of view the camera does not report. `lm` and
`bs` do not depend on that assumption. `yaw/pitch/roll/rot` are a geometric estimate from four landmarks, not MediaPipe's own
facial transformation matrix. None of this has been measured against ground truth (H-02 NOT TESTED).

A face that is not in the picture is not in the list. There is no placeholder entry and nothing is predicted beyond a HELD frame or two.

NOT HERE: tracking, models, sockets. This module only turns tracker output into a dict and back, so a receiver and the tests share
one definition. Pure python + numpy.
"""

import json

import numpy as np

import face_landmarker as F

WIRE_VERSION = 1
KIND = "face"
DEFAULT_PORT = 8902
NUM_LANDMARKS = F.NUM_LANDMARKS
NUM_MESH_LANDMARKS = F.NUM_MESH_LANDMARKS
NUM_BLENDSHAPES = F.NUM_BLENDSHAPES
BLENDSHAPE_NAMES = F.BLENDSHAPE_NAMES

LANDMARKS_FULL = "full"
LANDMARKS_MESH = "mesh"
LANDMARKS_NONE = "none"
LANDMARK_MODES = (LANDMARKS_FULL, LANDMARKS_MESH, LANDMARKS_NONE)

#: Decimal places kept. lm is a fraction of the frame (1e-5 is far below a pixel); bs a score (0.001); pos metres (0.1 mm).
LM_DECIMALS = 5
BS_DECIMALS = 3
POS_DECIMALS = 4
ANGLE_DECIMALS = 2
ROT_DECIMALS = 5


def encode_face(face, landmarks=LANDMARKS_FULL):
    """One tracker face (face_tracking.TrackedFace) as its wire dict."""
    entry = {
        "id": int(face.id),
        "conf": round(float(face.conf), 3),
        "st": int(face.state),
    }
    if face.blendshapes is not None:
        entry["bs"] = [round(float(v), BS_DECIMALS) for v in face.blendshapes]
    if landmarks != LANDMARKS_NONE and face.image is not None:
        count = NUM_LANDMARKS if landmarks == LANDMARKS_FULL else NUM_MESH_LANDMARKS
        entry["lm"] = [[round(float(v), LM_DECIMALS) for v in row] for row in np.asarray(face.image)[:count]]
    if face.position is not None:
        entry["pos"] = [round(float(v), POS_DECIMALS) for v in face.position]
    else:
        entry["why"] = face.reason or "none"
    entry["yaw"] = round(float(face.yaw), ANGLE_DECIMALS)
    entry["pitch"] = round(float(face.pitch), ANGLE_DECIMALS)
    entry["roll"] = round(float(face.roll), ANGLE_DECIMALS)
    entry["rot"] = [round(float(v), ROT_DECIMALS) for v in np.asarray(face.rotation).reshape(-1)]
    entry["pose"] = 1 if face.pose_ok else 0
    return entry


def encode_message(faces, session_id, seq, now, latency_ms, source, width, height, focal_px, mirrored,
                   landmarks=LANDMARKS_FULL):
    """The datagram's dict for one frame. `faces` is the tracker's list; an empty list is legal and means no face."""
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
        "bsv": 1,
        "n": len(faces),
        "faces": [encode_face(f, landmarks) for f in faces],
    }


def to_bytes(message):
    """Compact UTF-8 JSON for the socket."""
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


class WireError(ValueError):
    """A datagram that is not a face message this version understands."""


def parse_message(data):
    """Bytes or a dict -> the validated message dict. Raises WireError with the reason for anything else.

    Checks what a receiver relies on: the version and kind, and for each face that the arrays have the right shape. It does
    not clamp or repair anything.
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
        raise WireError("not a face message (kind=%r)" % (data.get("kind"),))
    faces = data.get("faces")
    if not isinstance(faces, list) or data.get("n") != len(faces):
        raise WireError("n does not match faces")
    for i, f in enumerate(faces):
        if "bs" in f and len(f["bs"]) != NUM_BLENDSHAPES:
            raise WireError("face %d: bs is not %d long" % (i, NUM_BLENDSHAPES))
        if "lm" in f:
            lm = f["lm"]
            if len(lm) not in (NUM_LANDMARKS, NUM_MESH_LANDMARKS) or any(len(p) != 3 for p in lm):
                raise WireError("face %d: lm is not %d or %d x 3" % (i, NUM_LANDMARKS, NUM_MESH_LANDMARKS))
        if "pos" in f and len(f["pos"]) != 3:
            raise WireError("face %d: pos is not 3 long" % i)
        if len(f.get("rot", [])) != 9:
            raise WireError("face %d: rot is not 9 long" % i)
        for key in ("yaw", "pitch", "roll"):
            if not isinstance(f.get(key), (int, float)):
                raise WireError("face %d: %s is missing" % (i, key))
    return data
