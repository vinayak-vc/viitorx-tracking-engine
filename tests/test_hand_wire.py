#!/usr/bin/env python3
"""H-01 tests for hand_wire.py (the datagram), the sender's pure helpers, and one real UDP round trip.

The wire is a contract with receivers written elsewhere, so what is pinned here is what a receiver relies on: every
field present and of the right shape, a malformed or foreign datagram rejected for a stated reason, an empty frame legal,
a refused 3D fit visible as an ABSENT `pos` with a `why`, and the whole thing small enough for one UDP datagram.
"""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import _sidecar_path  # noqa: F401  - hands_udp_sender lives in tools/hands
import _synthetic_hand as S
import hand_gestures as GE
import hand_tracking as HT
import hand_wire as WIRE
import hands_udp_sender as SENDER

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


def make_hand(hand_id=3, side="R", position=(0.1, -0.02, 0.6), pose=None):
    hand = HT.TrackedHand()
    hand.id = hand_id
    hand.side = side
    hand.handed = 0.93
    hand.conf = 0.97
    hand.state = HT.TRACKED
    world = S.center(S.make_hand(**(pose or S.OPEN)))
    hand.world = world
    image = np.zeros((21, 3))
    image[:, 0] = 0.5 + world[:, 0]
    image[:, 1] = 0.5 + world[:, 1]
    hand.image = image
    hand.position = np.array(position) if position is not None else None
    hand.reason = "" if position is not None else "residual"
    hand.fingers = GE.read_fingers(world)
    return hand


def message_for(hands):
    return WIRE.encode_message(hands, "hd0123456789", 42, 1791890123.4567, 38.21, "camera", 1280, 720, 1108.5, False)


print("THE DATAGRAM")
msg = message_for([make_hand(), make_hand(4, "L")])
check(msg["ver"] == 1 and msg["kind"] == "hands" and msg["n"] == 2 and len(msg["hands"]) == 2, "version, kind and count")
check(msg["img"] == [1280, 720] and msg["fpx"] == 1108.5 and msg["mir"] == 0 and msg["src"] == "camera", "frame and camera fields")
check(msg["seq"] == 42 and msg["sid"] == "hd0123456789" and msg["lat"] == 38.2, "session, sequence and latency")
h = msg["hands"][0]
check(len(h["lm"]) == 21 and all(len(p) == 3 for p in h["lm"]) and len(h["wl"]) == 21, "21 x 3 landmarks and world landmarks")
check(h["id"] == 3 and h["side"] == "R" and h["st"] == 0 and h["pos"] == [0.1, -0.02, 0.6], "id, side, state and position")
check(len(h["fing"]) == 5 and len(h["ext"]) == 5 and h["ges"] == "OPEN" and 0.0 <= h["pinch"] <= 1.0, "fingers and gesture")
check("why" not in h, "no `why` when the position is present")
raw = WIRE.to_bytes(msg)
check(len(raw) < 8000, "two hands fit one datagram with room to spare", "%d bytes" % len(raw))
check(len(raw) < 65000, "and far below the UDP limit")

print("")
print("ROUND TRIP")
back = WIRE.parse_message(raw)
check(back["hands"][1]["id"] == 4 and back["hands"][1]["side"] == "L", "bytes -> message keeps every hand")
check(abs(back["hands"][0]["lm"][5][0] - round(float(make_hand().image[5, 0]), 5)) < 1e-9, "landmark precision survives the round trip")
check(WIRE.parse_message(msg)["n"] == 2, "a dict is accepted as well as bytes")

print("")
print("NO HAND, AND A REFUSED FIT")
empty = message_for([])
check(empty["n"] == 0 and empty["hands"] == [] and WIRE.parse_message(WIRE.to_bytes(empty))["n"] == 0,
      "a frame with no hand is a legal datagram with n = 0 (a receiver can tell 'no hand' from 'no sender')")
unplaced = message_for([make_hand(position=None)])
check("pos" not in unplaced["hands"][0] and unplaced["hands"][0]["why"] == "residual",
      "a refused 3D fit sends NO position, and says why", str(unplaced["hands"][0].get("why")))
check(WIRE.parse_message(WIRE.to_bytes(unplaced))["n"] == 1, "and that datagram still parses")

print("")
print("REJECTIONS")


def rejected(mutate, label):
    m = json.loads(json.dumps(message_for([make_hand()])))
    mutate(m)
    try:
        WIRE.parse_message(m)
        check(False, label)
    except WIRE.WireError as e:
        check(True, label, str(e))


rejected(lambda m: m.update(ver=2), "an unknown version is refused (a receiver must not guess)")
rejected(lambda m: m.update(kind="body"), "a different kind is refused")
rejected(lambda m: m.update(n=3), "n that disagrees with the list is refused")
rejected(lambda m: m["hands"][0].update(lm=m["hands"][0]["lm"][:20]), "20 landmarks are refused")
rejected(lambda m: m["hands"][0].update(wl=[[0, 0]] * 21), "2-component world landmarks are refused")
rejected(lambda m: m["hands"][0].update(side="X"), "a bad side is refused")
rejected(lambda m: m["hands"][0].update(fing=[0.0] * 4), "4 finger curls are refused")
rejected(lambda m: m["hands"][0].update(pos=[1.0, 2.0]), "a 2-component position is refused")
for bad, label in ((b"not json", "garbage bytes"), (b"[1, 2]", "a JSON array"), (b"\xff\xfe", "non-UTF-8 bytes")):
    try:
        WIRE.parse_message(bad)
        check(False, "%s are refused" % label)
    except WIRE.WireError:
        check(True, "%s are refused" % label)
check(WIRE.DEFAULT_PORT == 8901, "the hands port is 8901, not the body sender's 8899")

print("")
print("A REAL UDP ROUND TRIP")
receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver.bind(("127.0.0.1", 0))
receiver.settimeout(2.0)
port = receiver.getsockname()[1]
sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sender.sendto(WIRE.to_bytes(msg), ("127.0.0.1", port))
data, _ = receiver.recvfrom(65535)
got = WIRE.parse_message(data)
check(got["seq"] == 42 and got["hands"][0]["id"] == 3, "a datagram sent over loopback arrives whole and parses")
sender.close()
receiver.close()

print("")
print("SENDER HELPERS")
check(SENDER.resolve_swap_sides("auto", flipped=True) is False, "auto + mirrored picture: the model's label is the physical hand")
check(SENDER.resolve_swap_sides("auto", flipped=False) is True, "auto + unmirrored picture: the label is the opposite hand")
check(SENDER.resolve_swap_sides("model", False) is False and SENDER.resolve_swap_sides("swapped", True) is True, "explicit modes win")

clock = {"t": 0.0}
slept = []


def fake_clock():
    return clock["t"]


def fake_sleep(s):
    slept.append(s)
    clock["t"] += s


pacer = SENDER.FilePacer(30.0, True, clock=fake_clock, sleep=fake_sleep)
behind = [pacer.wait() for _ in range(5)]
check(behind == [0] * 5 and abs(sum(slept) - 4.0 / 30.0) < 1e-9, "a file is held to its frame rate (5 frames take 4 periods)", "slept %.4f s" % sum(slept))
clock["t"] += 0.5
late = pacer.wait()
check(late >= 10, "a pacer that falls behind reports how many frames", "%d frames" % late)
off = SENDER.FilePacer(30.0, False, clock=fake_clock, sleep=fake_sleep)
check(off.wait() == 0, "--no-realtime never waits")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
