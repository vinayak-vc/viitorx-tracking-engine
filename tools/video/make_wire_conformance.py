#!/usr/bin/env python3
"""Phase 2 - THE CONFORMANCE FILE: the expected result of every golden datagram, in one machine-readable file.

`docs/WIRE.md` section 8 says a receiver is conformant when it passes the golden datagrams (`Packages/com.viitorx.avtar.tracking.vision/Tests/Editor/VisionFixtures/` in the Unity repo,
written by `make_vision_fixtures.py`). A receiver in another language should not have to read C# tests to learn what each file must produce, so this
script reads every datagram with the PYTHON REFERENCE PARSERS (hand_wire, face_wire, cutout_wire) and writes what they found to
`conformance.json` beside the fixtures:

  * a JSON datagram: accepted, with its headline values (counts, ids, sides, states, sizes, a few numbers), or refused;
  * a cutout datagram: its header, or the reason it is refused; and, for each family of chunks, the frame they reassemble into with the SHA-256 of
    its colour and mask sections.

    .venv\\Scripts\\python.exe tools/video/make_wire_conformance.py          # (re)write conformance.json
    .venv\\Scripts\\python.exe tools/video/make_wire_conformance.py --check  # exit 1 when the file on disk differs (tests/test_wire_spec.py does this)

For the `bad_*.json` files the JSON reasons (`unknown_version`, `wrong_kind`, `count_mismatch`, `bad_shape`, `truncated`, `not_json`) are the categories the Unity
receivers count them under; the Python reference only raises one error type, so for those files the contract that is checked is "refused", and the reason
is advisory. The cutout reasons are `cutout_wire`'s own words and are exact.
"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401

import argparse
import hashlib
import json
import sys

import cutout_wire as CW
import face_wire as FW
import hand_wire as HW
import make_vision_fixtures as MF

CONFORMANCE_FILE = "conformance.json"

#: What the Unity receivers call each negative JSON fixture (advisory: see the module notes).
JSON_REFUSALS = {
    "bad_version.json": ("hands", "unknown_version"),
    "bad_kind.json": ("hands", "wrong_kind"),
    "bad_count.json": ("hands", "count_mismatch"),
    "bad_landmarks.json": ("hands", "bad_shape"),
    "bad_side.json": ("hands", "bad_shape"),
    "bad_truncated.json": ("hands", "truncated"),
    "bad_not_json.txt": ("hands", "not_json"),
}


def _fixture_names():
    return sorted(n for n in _os.listdir(MF.FIXTURE_DIR) if not n.endswith(".meta"))


def _read(name):
    with open(_os.path.join(MF.FIXTURE_DIR, name), "rb") as f:
        return f.read()


def _hand_summary(msg):
    return {
        "stream": "hands", "accepted": True, "n": msg["n"], "img": msg["img"], "mir": msg["mir"], "src": msg["src"],
        "ids": [h["id"] for h in msg["hands"]], "sides": [h["side"] for h in msg["hands"]],
        "states": [h["st"] for h in msg["hands"]], "gestures": [h["ges"] for h in msg["hands"]],
        "has_pos": [("pos" in h) for h in msg["hands"]], "why": [h.get("why", "") for h in msg["hands"]],
        "pinch": [h["pinch"] for h in msg["hands"]], "lm_count": [len(h["lm"]) for h in msg["hands"]],
    }


def _face_summary(msg):
    jaw = FW.BLENDSHAPE_NAMES.index("jawOpen")
    return {
        "stream": "face", "accepted": True, "n": msg["n"], "img": msg["img"], "mir": msg["mir"], "bsv": msg["bsv"], "src": msg["src"],
        "ids": [f["id"] for f in msg["faces"]], "states": [f["st"] for f in msg["faces"]],
        "lm_count": [len(f.get("lm", [])) for f in msg["faces"]], "has_bs": [("bs" in f) for f in msg["faces"]],
        "jaw_open": [f["bs"][jaw] if "bs" in f else None for f in msg["faces"]],
        "has_pos": [("pos" in f) for f in msg["faces"]], "why": [f.get("why", "") for f in msg["faces"]],
        "pose": [f["pose"] for f in msg["faces"]], "yaw": [f["yaw"] for f in msg["faces"]],
    }


def _json_entry(name, data):
    """One JSON fixture's expectation. A fixture is read as the stream its name says (hands_ / face_ / real_hands / real_face)."""
    if name in JSON_REFUSALS:
        stream, reason = JSON_REFUSALS[name]
        parser = HW.parse_message
        try:
            parser(data)
        except HW.WireError:
            return {"stream": stream, "accepted": False, "reason": reason, "reason_is_advisory": True}
        raise AssertionError("%s was meant to be refused but the reference parser accepted it" % name)
    is_face = name.startswith("face_") or name == "real_face.json"
    parse = FW.parse_message if is_face else HW.parse_message
    msg = parse(data)
    return _face_summary(msg) if is_face else _hand_summary(msg)


def _cutout_header_entry(name, data):
    try:
        head = CW.parse_header(data)
    except CW.WireError as e:
        return {"stream": "cutout", "accepted": False, "reason": e.reason}
    keep = ("session", "seq", "chunk_index", "chunk_count", "chunk_offset", "payload_len", "flags", "color_enc", "mask_enc", "model_id",
            "color_w", "color_h", "mask_w", "mask_h", "color_len", "mask_len", "fg_permille", "people", "header_len", "chunk_len")
    out = {"stream": "cutout", "accepted": True}
    for k in keep:
        out[k] = head[k]
    return out


def _cutout_frames(names):
    """Reassemble each family's chunks (in file order) and describe the frame, or say how the frame is refused."""
    families = {}
    for n in names:
        if n.startswith("cutout_") and n.endswith(".bin") and _cutout_header_entry(n, _read(n))["accepted"]:
            families.setdefault(n[len("cutout_"):].rsplit("_", 1)[0], []).append(n)
    out = {}
    for family in sorted(families):
        asm = CW.Assembler()
        frame = None
        for n in sorted(families[family]):
            frame = asm.add(_read(n)) or frame
        entry = {"files": sorted(families[family])}
        if frame is None:
            entry["frame"] = None
            entry["rejected"] = dict(sorted(asm.rejected.items()))
        else:
            entry["frame"] = {
                "session": frame.session, "seq": frame.seq, "flags": frame.flags, "color_enc": frame.color_enc, "mask_enc": frame.mask_enc,
                "model_id": frame.model_id, "color_w": frame.color_w, "color_h": frame.color_h, "mask_w": frame.mask_w, "mask_h": frame.mask_h,
                "fg_permille": frame.fg_permille, "people": frame.people, "color_len": len(frame.color), "mask_len": len(frame.mask),
                "color_sha256": hashlib.sha256(frame.color).hexdigest(), "mask_sha256": hashlib.sha256(frame.mask).hexdigest(),
            }
            try:
                pixels = CW.mask_pixels(frame)
                entry["mask_decodes_to"] = [int(pixels.shape[1]), int(pixels.shape[0])]
                entry["mask_pixel_sum"] = int(pixels.sum())
            except CW.WireError as e:
                entry["mask_decodes_to"] = None
                entry["mask_refused"] = e.reason
        out[family] = entry
    return out


def build():
    """The whole conformance document as a dict. Deterministic: sorted keys, no times."""
    names = _fixture_names()
    files = {}
    for n in names:
        if n.startswith("args_") or n == "blendshape_names.json" or n == CONFORMANCE_FILE:
            continue
        data = _read(n)
        if n.startswith("cutout_") and n.endswith(".bin"):
            files[n] = _cutout_header_entry(n, data)
        elif n.endswith(".json") or n == "bad_not_json.txt":
            files[n] = _json_entry(n, data)
    return {
        "about": "Expected result of every golden datagram in this folder, from the Python reference parsers. See docs/WIRE.md section 8.",
        "wire_spec_version": "1.0",
        "blendshape_names_sha256": hashlib.sha256(" ".join(FW.BLENDSHAPE_NAMES).encode("utf-8")).hexdigest(),
        "files": files,
        "cutout_frames": _cutout_frames(names),
    }


def render(doc):
    return (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the file on disk is not what the reference parsers produce today")
    a = ap.parse_args(argv)
    if not _os.path.isdir(MF.FIXTURE_DIR):
        print("the Unity repo's fixture folder is not present: %s" % MF.FIXTURE_DIR)
        return 2
    path = _os.path.join(MF.FIXTURE_DIR, CONFORMANCE_FILE)
    wanted = render(build())
    if a.check:
        same = _os.path.isfile(path) and open(path, "rb").read() == wanted
        print("%s %s" % (CONFORMANCE_FILE, "is up to date" if same else "DIFFERS: rerun tools/video/make_wire_conformance.py"))
        return 0 if same else 1
    with open(path, "wb") as f:
        f.write(wanted)
    print("wrote %s (%d bytes)" % (path, len(wanted)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
