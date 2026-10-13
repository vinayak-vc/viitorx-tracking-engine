#!/usr/bin/env python3
"""H-03 tests for the golden datagrams the Unity receivers are tested against (tools/video/make_vision_fixtures.py).

Two things are pinned. (1) The fixtures on disk in the Unity repo are exactly what the production encoders produce TODAY, so a change to a wire
field (hand_wire.py, face_wire.py) fails here before it silently breaks the C# parser's tests. (2) Every positive fixture passes the Python
parser (hand_wire.parse_message / face_wire.parse_message) and every negative one is refused, so the fixtures mean what the C# tests say they mean.
The real_* fixtures are snapshots of the real networks and are only checked for validity, not regenerated.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sidecar_path  # noqa: F401

import face_wire as FW
import hand_wire as HW
import make_vision_fixtures as MF

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-68s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-68s %s" % (label, detail))


print("THE FIXTURES ON DISK ARE WHAT THE ENCODERS PRODUCE")
if not os.path.isdir(MF.FIXTURE_DIR):
    print("  SKIP the Unity repo's fixture folder is not present: %s" % MF.FIXTURE_DIR)
else:
    expected = MF.synthetic_fixtures()
    check(len(expected) >= 20, "the generator produces the full set", "%d fixtures" % len(expected))
    for name in sorted(expected):
        path = os.path.join(MF.FIXTURE_DIR, name)
        exists = os.path.isfile(path)
        same = exists and open(path, "rb").read() == expected[name]
        check(same, "%s is byte-identical to the encoder's output" % name, "" if same else ("missing" if not exists else "DRIFTED: rerun make_vision_fixtures.py"))

    print("")
    print("POSITIVE FIXTURES PASS THE PYTHON PARSER, NEGATIVE ONES ARE REFUSED")
    for name in sorted(expected):
        data = expected[name]
        if name.startswith("hands_"):
            ok = HW.parse_message(data)["kind"] == "hands"
            check(ok, "%s parses as hands" % name)
        elif name.startswith("face_"):
            ok = FW.parse_message(data)["kind"] == "face"
            check(ok, "%s parses as face" % name)
        elif name.startswith("bad_"):
            try:
                HW.parse_message(data)
                check(False, "%s is refused" % name)
            except HW.WireError:
                check(True, "%s is refused" % name)
    for name in ("real_hands.json", "real_face.json"):
        path = os.path.join(MF.FIXTURE_DIR, name)
        if os.path.isfile(path):
            data = open(path, "rb").read()
            parse = HW.parse_message if "hands" in name else FW.parse_message
            check(parse(data)["n"] >= 1, "%s (a snapshot of the real networks) is a valid message with a subject" % name)
        else:
            print("  SKIP %s is not present (make_vision_fixtures.py --real)" % name)

print("")
print("GENERATION IS DETERMINISTIC")
with tempfile.TemporaryDirectory() as tmp:
    MF.write_all(tmp, MF.synthetic_fixtures())
    again = MF.synthetic_fixtures()
    same = all(open(os.path.join(tmp, n), "rb").read() == d for n, d in again.items())
    check(same, "writing then regenerating gives the same bytes")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
