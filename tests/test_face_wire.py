#!/usr/bin/env python3
"""H-02 tests for face_wire.py (the datagram), the face sender's pipeline, and one real UDP round trip.

The wire is a contract with receivers written elsewhere, so what is pinned is what a receiver relies on: every field present and of the
right shape, a malformed or foreign datagram rejected for a stated reason, an empty frame legal, a refused 3D fit visible as an ABSENT
`pos` with a `why`, the landmark modes honoured, and the whole thing small enough for one UDP datagram.
"""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _sidecar_path  # noqa: F401  - the sender lives in tools/face
import _synthetic_face as SF
import face_landmarker as F
import face_tracking as FT
import face_wire as WIRE
import face_udp_sender as SENDER

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-66s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-66s %s" % (label, detail))


def make_face(face_id=1, position=(0.02, -0.01, 0.6), blend=True):
    face = FT.TrackedFace()
    face.id = face_id
    face.conf = 0.987
    face.state = FT.TRACKED
    image = np.zeros((478, 3))
    image[:, 0] = 0.5 + SF.make_face()[:, 0]
    image[:, 1] = 0.5 + SF.make_face()[:, 1]
    face.image = image
    face.blendshapes = np.linspace(0.0, 1.0, 52) if blend else None
    face.position = np.array(position) if position is not None else None
    face.reason = "" if position is not None else "range"
    face.yaw, face.pitch, face.roll = 4.256, -3.0, 1.1
    face.rotation = np.eye(3)
    face.pose_ok = True
    return face


def message_for(faces, landmarks=WIRE.LANDMARKS_FULL):
    return WIRE.encode_message(faces, "fc0123456789", 42, 1791890123.4567, 38.21, "camera", 1280, 720, 1108.5, False, landmarks)


print("THE DATAGRAM")
msg = message_for([make_face()])
check(msg["ver"] == 1 and msg["kind"] == "face" and msg["n"] == 1 and len(msg["faces"]) == 1 and msg["bsv"] == 1, "version, kind, count and the blendshape ordering version")
check(msg["img"] == [1280, 720] and msg["fpx"] == 1108.5 and msg["mir"] == 0 and msg["src"] == "camera", "frame and camera fields")
check(msg["seq"] == 42 and msg["sid"] == "fc0123456789" and msg["lat"] == 38.2, "session, sequence and latency")
f = msg["faces"][0]
check(len(f["bs"]) == 52 and len(f["lm"]) == 478 and all(len(p) == 3 for p in f["lm"]), "52 blendshapes and 478 x 3 landmarks")
check(f["id"] == 1 and f["st"] == 0 and f["conf"] == 0.987 and f["pos"] == [0.02, -0.01, 0.6], "id, state, confidence and position")
check(f["yaw"] == 4.26 and f["pitch"] == -3.0 and f["roll"] == 1.1 and len(f["rot"]) == 9 and f["pose"] == 1, "head pose fields")
check(f["rot"] == [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], "rot is the row-major 3x3")
check("why" not in f, "no `why` when the position is present")
raw = WIRE.to_bytes(msg)
check(len(raw) < 65000, "one face with all 478 landmarks fits one UDP datagram", "%d bytes" % len(raw))
check(len(WIRE.to_bytes(message_for([make_face(), make_face(2)]))) < 65000, "even two faces do", "%d bytes" % len(WIRE.to_bytes(message_for([make_face(), make_face(2)]))))

print("")
print("LANDMARK MODES")
check(len(message_for([make_face()], WIRE.LANDMARKS_MESH)["faces"][0]["lm"]) == 468, "--landmarks mesh sends the 468 mesh points")
none = message_for([make_face()], WIRE.LANDMARKS_NONE)["faces"][0]
check("lm" not in none and "bs" in none, "--landmarks none sends no landmarks but keeps the blendshapes")
check(len(WIRE.to_bytes(message_for([make_face()], WIRE.LANDMARKS_NONE))) < 1500, "and is then tiny", "%d bytes" % len(WIRE.to_bytes(message_for([make_face()], WIRE.LANDMARKS_NONE))))
check("bs" not in message_for([make_face(blend=False)])["faces"][0], "a tracker without blendshapes sends no `bs`")

print("")
print("ROUND TRIP")
back = WIRE.parse_message(raw)
check(back["faces"][0]["bs"][51] == 1.0 and back["faces"][0]["yaw"] == 4.26, "bytes -> message keeps the values")
check(WIRE.parse_message(msg)["n"] == 1, "a dict is accepted as well as bytes")

print("")
print("NO FACE, AND A REFUSED FIT")
empty = message_for([])
check(empty["n"] == 0 and empty["faces"] == [] and WIRE.parse_message(WIRE.to_bytes(empty))["n"] == 0, "a frame with no face is a legal datagram with n = 0")
unplaced = message_for([make_face(position=None)])
check("pos" not in unplaced["faces"][0] and unplaced["faces"][0]["why"] == "range", "a refused 3D fit sends NO position, and says why")
check(WIRE.parse_message(WIRE.to_bytes(unplaced))["n"] == 1, "and that datagram still parses")

print("")
print("REJECTIONS")


def rejected(mutate, label):
    m = json.loads(json.dumps(message_for([make_face()])))
    mutate(m)
    try:
        WIRE.parse_message(m)
        check(False, label)
    except WIRE.WireError as e:
        check(True, label, str(e))


rejected(lambda m: m.update(ver=2), "an unknown version is refused (a receiver must not guess)")
rejected(lambda m: m.update(kind="hands"), "a hands datagram is refused by the face parser")
rejected(lambda m: m.update(n=2), "n that disagrees with the list is refused")
rejected(lambda m: m["faces"][0].update(bs=[0.0] * 51), "51 blendshapes are refused")
rejected(lambda m: m["faces"][0].update(lm=m["faces"][0]["lm"][:100]), "100 landmarks are refused")
rejected(lambda m: m["faces"][0].update(lm=[[0, 0]] * 478), "2-component landmarks are refused")
rejected(lambda m: m["faces"][0].update(pos=[1.0, 2.0]), "a 2-component position is refused")
rejected(lambda m: m["faces"][0].update(rot=[1.0] * 8), "an 8-number rotation is refused")
rejected(lambda m: m["faces"][0].pop("yaw"), "a missing yaw is refused")
for bad, label in ((b"not json", "garbage bytes"), (b"[1, 2]", "a JSON array"), (b"\xff\xfe", "non-UTF-8 bytes")):
    try:
        WIRE.parse_message(bad)
        check(False, "%s are refused" % label)
    except WIRE.WireError:
        check(True, "%s are refused" % label)
check(WIRE.DEFAULT_PORT == 8902, "the face port is 8902: not the body sender's 8899, not the hands' 8901")
check(WIRE.BLENDSHAPE_NAMES == F.BLENDSHAPE_NAMES and len(WIRE.BLENDSHAPE_NAMES) == 52, "the wire's blendshape names are the model's 52")

print("")
print("A REAL UDP ROUND TRIP")
receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver.bind(("127.0.0.1", 0))
receiver.settimeout(2.0)
port = receiver.getsockname()[1]
sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sender.sendto(raw, ("127.0.0.1", port))
data, _ = receiver.recvfrom(65535)
got = WIRE.parse_message(data)
check(got["seq"] == 42 and got["faces"][0]["id"] == 1 and len(got["faces"][0]["lm"]) == 478, "a full datagram sent over loopback arrives whole and parses")
sender.close()
receiver.close()

print("")
print("SENDER DEFAULTS")
a = SENDER.build_parser().parse_args([])
check(a.video == "camera:0" and a.port == 8902 and a.max_faces == 1 and a.blendshapes is True and a.landmarks == "full" and a.flip is False,
      "the defaults: camera 0, port 8902, one face, blendshapes on, full landmarks, not mirrored")
b = SENDER.build_parser().parse_args(["--no-blendshapes", "--landmarks", "none", "--max-faces", "2", "--crop", "1", "2", "3", "4"])
check(b.blendshapes is False and b.landmarks == "none" and b.max_faces == 2 and b.crop == [1, 2, 3, 4], "the switches parse")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
