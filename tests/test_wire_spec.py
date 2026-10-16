#!/usr/bin/env python3
"""Phase 2 tests: docs/WIRE.md says what the encoders do, and conformance.json says what the parsers do.

A specification that nobody checks drifts. This reads the field tables, the cutout header offsets, the 52 blendshape names and the ports out of
docs/WIRE.md and compares them with the code that defines each wire; it also checks conformance.json against the reference parsers (so it cannot go
stale) and that every member of the body wire tables really appears in a body sender's source.
"""
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sidecar_path  # noqa: F401

import cutout_wire as CW
import face_wire as FW
import hand_wire as HW
import make_vision_fixtures as MF
import make_wire_conformance as MC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "docs", "WIRE.md")

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-74s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-74s %s" % (label, detail))


text = open(SPEC, encoding="utf-8").read()


def table_after(marker):
    """The rows of the first markdown table after `<!-- spec:marker -->`, each as a list of stripped cells."""
    at = text.index("<!-- spec:%s -->" % marker)
    rows = []
    started = False
    for line in text[at:].splitlines()[1:]:
        if line.startswith("|"):
            started = True
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif started:
            break
    return rows[2:]  # the header row and the separator


def names(rows, col=0):
    return [r[col].strip("`") for r in rows]


def code_block_after(marker):
    at = text.index("<!-- spec:%s -->" % marker)
    start = text.index("```", at)
    end = text.index("```", start + 3)
    return text[start + 3:end].split()


print("THE SPEC EXISTS AND NAMES ITS MARKERS")
for m in ("hands.header", "hands.entry", "face.header", "face.entry", "face.blendshapes", "cutout.header", "body.single", "body.multi"):
    check(("<!-- spec:%s -->" % m) in text, "marker spec:%s present" % m)

print("")
print("HANDS: THE TABLES ARE THE ENCODER'S MEMBERS")
sample_hands = HW.parse_message(open(os.path.join(MF.FIXTURE_DIR, "hands_held_refused.json"), "rb").read())
header_keys = [k for k in sample_hands.keys()]
check(sorted(names(table_after("hands.header"))) == sorted(header_keys), "message members == the encoder's", str(sorted(header_keys)))
entry_keys = set()
for h in sample_hands["hands"]:
    entry_keys.update(h.keys())
check(sorted(names(table_after("hands.entry"))) == sorted(entry_keys), "hand entry members == the encoder's (pos and why both seen)", str(sorted(entry_keys)))
check(HW.DEFAULT_PORT == 8901 and "`8901` | JSON | `hand_wire.py`" in text, "hands port 8901 in code and spec")

print("")
print("FACE: THE TABLES ARE THE ENCODER'S MEMBERS")
face_full = FW.parse_message(open(os.path.join(MF.FIXTURE_DIR, "face_full.json"), "rb").read())
face_refused = FW.parse_message(open(os.path.join(MF.FIXTURE_DIR, "face_refused.json"), "rb").read())
check(sorted(names(table_after("face.header"))) == sorted(face_full.keys()), "message members == the encoder's", str(sorted(face_full.keys())))
fkeys = set()
for m in (face_full, face_refused):
    for f in m["faces"]:
        fkeys.update(f.keys())
check(sorted(names(table_after("face.entry"))) == sorted(fkeys), "face entry members == the encoder's (pos and why both seen)", str(sorted(fkeys)))
check(FW.DEFAULT_PORT == 8902 and "`8902` | JSON | `face_wire.py`" in text, "face port 8902 in code and spec")
listed = code_block_after("face.blendshapes")
check(listed == list(FW.BLENDSHAPE_NAMES), "the 52 blendshape names == face_wire.BLENDSHAPE_NAMES", "%d names" % len(listed))
check(len(listed) == 52 and listed[0] == "_neutral" and "tongueOut" not in listed, "52 names, _neutral first, no tongueOut")

print("")
print("CUTOUT: THE HEADER TABLE IS THE STRUCT")
fields = ["magic", "ver", "kind", "header_len", "session", "seq", "t", "lat_ms", "infer_ms", "chunk_index", "chunk_count", "chunk_offset",
          "payload_len", "flags", "color_enc", "mask_enc", "model_id", "color_w", "color_h", "mask_w", "mask_h", "color_len", "mask_len",
          "fg_permille", "people", "reserved1", "reserved2"]
fmt = CW._HEADER.format
if isinstance(fmt, bytes):
    fmt = fmt.decode()
codes = re.findall(r"(\d*)([A-Za-z])", fmt.lstrip("<"))
expanded = []
for count, code in codes:
    if code == "s":
        expanded.append(("s", int(count or 1)))
    else:
        for _ in range(int(count or 1)):
            expanded.append((code, 1))
sizes = {"B": 1, "H": 2, "I": 4, "d": 8, "f": 4}
offsets = []
at = 0
for code, count in expanded:
    size = count if code == "s" else sizes[code]
    offsets.append((at, size))
    at += size
check(len(expanded) == len(fields), "the struct has %d fields, as the spec lists" % len(fields))
rows = table_after("cutout.header")
spec_rows = [(int(r[0]), int(r[1]), r[3].strip("`")) for r in rows]
check([n for _, _, n in spec_rows] == fields, "field names and order in the table == the struct's", "%d rows" % len(spec_rows))
check([(o, s) for o, s, _ in spec_rows] == offsets, "every offset and size in the table == the struct's")
check(at == CW.HEADER_BYTES == 72, "the header is 72 bytes")
check(CW.DEFAULT_PORT == 8903 and "`8903` | **binary**" in text, "cutout port 8903 in code and spec")
check(CW.MAGIC == b"VXCO" and "`VXCO`" in text, "magic VXCO")
check(str(CW.MAX_DIMENSION) in text and "48 MiB" in text and CW.MAX_PAYLOAD_BYTES == 48 * 1024 * 1024, "the allocation caps in the spec == the code's")
for reason in (CW.REJECT_SHORT, CW.REJECT_MAGIC, CW.REJECT_VERSION, CW.REJECT_KIND, CW.REJECT_HEADER, CW.REJECT_CHUNK, CW.REJECT_SIZE,
               CW.REJECT_DIMENSIONS, CW.REJECT_ENCODING):
    check("(`%s`)" % reason in text, "the reject reason %r is in the spec's list" % reason)

print("")
print("BODY: EVERY MEMBER IN THE TABLES IS IN A BODY SENDER'S SOURCE")
sources = ""
for rel in ("wholebody_udp_sender.py", "multiperson_udp_sender.py", os.path.join("tools", "video", "video_udp_sender.py")):
    sources += open(os.path.join(ROOT, rel), encoding="utf-8").read()
for marker in ("body.single", "body.multi"):
    for member in names(table_after(marker)):
        check(('"%s"' % member) in sources, "body member %r appears as a key in the senders" % member)
for member in ("id", "state", "lm", "xyz", "src", "st", "fr", "lh", "rh"):
    check(('"%s"' % member) in sources, "person entry member %r appears in the senders" % member)
check("`8899` | JSON" in text, "body port 8899 in the spec")

print("")
print("CONFORMANCE.JSON IS WHAT THE REFERENCE PARSERS PRODUCE TODAY")
if os.path.isdir(MF.FIXTURE_DIR):
    path = os.path.join(MF.FIXTURE_DIR, MC.CONFORMANCE_FILE)
    wanted = MC.render(MC.build())
    check(os.path.isfile(path) and open(path, "rb").read() == wanted, "conformance.json on disk == the parsers' output", "" if os.path.isfile(path) else "missing: run make_wire_conformance.py")
    doc = json.loads(wanted)
    check(len(doc["files"]) >= 40, "it covers every datagram file", "%d files" % len(doc["files"]))
    refused = [n for n, e in doc["files"].items() if not e["accepted"]]
    check(all(n.startswith("bad_") or n.startswith("cutout_bad_") or n == "cutout_truncated.bin" for n in refused),
          "only the bad_* / cutout_bad_* / truncated files are refused", "%d refused" % len(refused))
    check(set(doc["cutout_frames"]) == {"bad_inflate", "jpeg", "maskonly", "next", "other", "rgba"}, "six cutout families reassemble")
    check(doc["cutout_frames"]["rgba"]["frame"] is not None and doc["cutout_frames"]["rgba"]["frame"]["seq"] == 1042, "the rgba family reassembles into seq 1042")
    check(doc["cutout_frames"]["bad_inflate"].get("mask_refused") == "size", "the bad_inflate frame assembles but its mask is refused (size)")
else:
    print("  SKIP the Unity repo's fixture folder is not present")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
