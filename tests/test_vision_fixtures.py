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
print("THE CUTOUT FIXTURES ARE THE REFERENCE FRAMES THE C# TESTS DESCRIBE (S-01)")
import cutout_wire as CW  # noqa: E402
import numpy as np  # noqa: E402


def family(files, name):
    return [files[k] for k in sorted(files) if k.startswith("cutout_%s_" % name)]


def assemble(datagrams):
    asm = CW.Assembler()
    out = None
    for d in datagrams:
        r = asm.add(d)
        if r is not None:
            out = r
    return out, asm


files = MF.synthetic_fixtures()
frame, asm = assemble(family(files, "rgba"))
check(len(family(files, "rgba")) == 4, "the reference frame is four datagrams (so ordering and loss can be tested)")
check(frame is not None and frame.session == 0xA1B2C3D4 and frame.seq == 1042 and frame.flags == (CW.FLAG_MIRRORED | CW.FLAG_LIVE), "rgba: session, seq and flags")
check(frame.people == 2 and frame.fg_permille == 321 and frame.model_id == CW.MODEL_GENERAL, "rgba: people, fg_permille, model_id")
check(abs(frame.lat_ms - 38.2) < 1e-4 and abs(frame.infer_ms - 12.5) < 1e-6 and abs(frame.t - 1791890123.456) < 1e-6, "rgba: lat_ms, infer_ms and t")
px = np.frombuffer(frame.color, np.uint8).reshape(12, 16, 4)
check(px[2, 3].tolist() == [48, 40, 128, 255] and px[11, 15].tolist() == [240, 220, 128, 255], "rgba: picture pixel (x,y) is R=16x G=20y B=128 A=255", str(px[2, 3].tolist()))
mk = CW.mask_pixels(frame)
check(mk.shape == (6, 8) and int(mk[2, 5]) == 105 and int(mk[0, 0]) == 0 and int(mk[5, 7]) == 235, "rgba: mask pixel (x,y) is 5(x+8y)", str(int(mk[2, 5])))
nxt, _ = assemble(family(files, "next"))
check(nxt.seq == 1043 and nxt.session == 0xA1B2C3D4 and np.frombuffer(nxt.color, np.uint8)[2] == 200, "next: the following frame of the same session (B = 200)")
oth, _ = assemble(family(files, "other"))
check(oth.session == 0x0BADF00D and oth.seq == 5 and np.frombuffer(oth.color, np.uint8)[2] == 50, "other: a different session with a LOWER seq (B = 50)")
mo, _ = assemble(family(files, "maskonly"))
rect = CW.mask_pixels(mo)
check(mo.color_enc == CW.COLOR_NONE and mo.mask_enc == CW.MASK_DEFLATE and mo.model_id == CW.MODEL_LANDSCAPE and mo.color == b"", "maskonly: no colour, a deflated mask, the landscape model")
check(rect.shape == (24, 32) and int(rect[6, 8]) == 255 and int(rect[17, 23]) == 255 and int(rect[5, 8]) == 0 and int(rect[18, 23]) == 0 and int((rect == 255).sum()) == 16 * 12,
      "maskonly: inflates to the 32x24 mask with a 16x12 rectangle at (8,6)")
jp, _ = assemble(family(files, "jpeg"))
import cv2  # noqa: E402
decoded = cv2.imdecode(np.frombuffer(jp.color, np.uint8), cv2.IMREAD_COLOR)
check(jp.color_enc == CW.COLOR_JPEG and decoded is not None and decoded.shape == (8, 8, 3) and abs(int(decoded[0, 0, 0]) - 200) < 40 and abs(int(decoded[7, 7, 2]) - 240) < 40,
      "jpeg: a baseline JPEG that decodes to the documented 8x8 gradient")
check(CW.mask_pixels(jp)[3, 3] == (3 * 8 + 3) * 4, "jpeg: its raw mask is 4 (x + 8y)")
bi, _ = assemble(family(files, "bad_inflate"))
try:
    CW.mask_pixels(bi)
    check(False, "bad_inflate: a deflate stream that inflates past its declared size is refused")
except CW.WireError as e:
    check(bi is not None and e.reason == CW.REJECT_SIZE, "bad_inflate: the header is fine, the stream inflates past 32x24 and is refused", e.reason)
for name, reason in (("magic", CW.REJECT_MAGIC), ("version", CW.REJECT_VERSION), ("kind", CW.REJECT_KIND), ("header_len", CW.REJECT_HEADER),
                     ("chunk_index", CW.REJECT_CHUNK), ("sizes", CW.REJECT_SIZE), ("dimension", CW.REJECT_DIMENSIONS),
                     ("encoding", CW.REJECT_ENCODING)):
    a = CW.Assembler()
    r = a.add(files["cutout_bad_%s.bin" % name])
    check(r is None and a.rejected.get(reason) == 1, "cutout_bad_%s.bin is refused as '%s'" % (name, reason), str(a.rejected))
a = CW.Assembler()
check(a.add(files["cutout_truncated.bin"]) is None and a.rejected.get(CW.REJECT_SHORT) == 1, "cutout_truncated.bin is refused as 'short'")

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
