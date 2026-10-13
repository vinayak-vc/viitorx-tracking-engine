#!/usr/bin/env python3
"""S-01 tests for the cutout wire: header layout, chunking, reassembly and every way a datagram can be wrong.

The Unity receiver (VirtualMirror.Vision CutoutAssembler) implements the SAME rules and is pinned to the SAME bytes: make_vision_fixtures.py
writes golden datagrams with this module's encoder, the C# tests assert their values, and test_vision_fixtures.py fails if the checked-in
files drift from the encoder.
"""
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import numpy as np

import cutout_wire as W

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-80s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-80s %s" % (label, detail))


def raises(reason, fn, *args):
    try:
        fn(*args)
    except W.WireError as e:
        return e.reason == reason
    return False


def make_frame(seq=7, session=0xCAFEF00D, cw=6, ch=4, mw=3, mh=2, color_enc=W.COLOR_RGBA, mask_enc=W.MASK_RAW, flags=0):
    rng = np.random.RandomState(seq)
    if color_enc == W.COLOR_NONE:
        color = b""
        cw = ch = 0
    elif color_enc == W.COLOR_RGBA:
        color = rng.randint(0, 256, size=cw * ch * 4, dtype=np.uint8).tobytes()
    else:
        color = bytes(rng.randint(0, 256, size=200, dtype=np.uint8))
    mask_px = rng.randint(0, 256, size=mw * mh, dtype=np.uint8).tobytes()
    mask = mask_px if mask_enc == W.MASK_RAW else W.deflate_raw(mask_px)
    return W.Frame(session, seq, 1791890123.456, 31.5, 12.25, flags, color_enc, mask_enc, W.MODEL_GENERAL, cw, ch, mw, mh, 321, 2,
                   color, mask)


def feed(asm, datagrams):
    out = None
    for d in datagrams:
        r = asm.add(d)
        if r is not None:
            out = r
    return out


print("THE HEADER LAYOUT IS THE DOCUMENTED 72 BYTES")
check(W.HEADER_BYTES == 72, "HEADER_BYTES is 72")
f = make_frame(cw=40, ch=30, mw=20, mh=15)
dg = W.encode_datagrams(f, 256)
check(len(dg[0]) == 72 + 256 and len(dg) == W.chunk_count_for(f.payload_len, 256), "a full chunk is header + chunk_bytes", str(len(dg[0])))
check(len(W.encode_datagrams(f, 10)[0]) == 72 + W.MIN_CHUNK_BYTES, "a chunk size below the minimum is raised to it")
h = struct.unpack_from("<4sBBH", dg[0], 0)
check(h == (b"VXCO", 1, 1, 72), "magic, version, kind and header_len sit at offsets 0-7", str(h))
check(struct.unpack_from("<I", dg[0], 8)[0] == 0xCAFEF00D and struct.unpack_from("<I", dg[0], 12)[0] == 7, "session at 8, seq at 12")
check(abs(struct.unpack_from("<d", dg[0], 16)[0] - 1791890123.456) < 1e-6, "t (f64) at 16")
check(abs(struct.unpack_from("<f", dg[0], 24)[0] - 31.5) < 1e-6 and abs(struct.unpack_from("<f", dg[0], 28)[0] - 12.25) < 1e-6,
      "lat_ms at 24 and infer_ms at 28")
check(struct.unpack_from("<HH", dg[0], 32) == (0, len(dg)), "chunk_index at 32, chunk_count at 34")
check(struct.unpack_from("<II", dg[0], 36) == (0, len(f.color) + len(f.mask)), "chunk_offset at 36, payload_len at 40")
check(struct.unpack_from("<BBBB", dg[0], 44) == (0, W.COLOR_RGBA, W.MASK_RAW, W.MODEL_GENERAL), "flags, color_enc, mask_enc, model_id at 44-47")
check(struct.unpack_from("<HHHH", dg[0], 48) == (40, 30, 20, 15), "color_w, color_h, mask_w, mask_h at 48-55")
check(struct.unpack_from("<II", dg[0], 56) == (len(f.color), len(f.mask)), "color_len at 56 and mask_len at 60")
check(struct.unpack_from("<HB", dg[0], 64) == (321, 2), "fg_permille at 64, people at 66")
check(struct.unpack_from("<BI", dg[0], 67) == (0, 0), "the reserved bytes are zero")

print("")
print("A FRAME SURVIVES THE ROUND TRIP, AT ANY CHUNK SIZE")
for chunk in (W.MIN_CHUNK_BYTES, 300, 1200, 60000):
    f = make_frame(color_enc=W.COLOR_RGBA, cw=40, ch=30, mw=20, mh=15)
    asm = W.Assembler()
    out = feed(asm, W.encode_datagrams(f, chunk))
    ok = out is not None and out.color == f.color and out.mask == f.mask and out.seq == f.seq and out.session == f.session
    check(ok, "chunk size %d: colour and mask bytes identical" % chunk, "chunks=%d" % W.chunk_count_for(f.payload_len, chunk))
f = make_frame(cw=40, ch=30, mw=20, mh=15, flags=W.FLAG_MIRRORED | W.FLAG_LIVE)
out = feed(W.Assembler(), W.encode_datagrams(f, 300))
check(out.mirrored and out.live and not out.nobody, "the flag bits come back (mirrored, live, not nobody)")
check(out.people == 2 and out.fg_permille == 321 and out.model_id == W.MODEL_GENERAL, "people, fg_permille and model_id come back")
check(abs(out.t - 1791890123.456) < 1e-6 and abs(out.lat_ms - 31.5) < 1e-6 and abs(out.infer_ms - 12.25) < 1e-6, "t, lat_ms and infer_ms come back")
check((out.color_w, out.color_h, out.mask_w, out.mask_h) == (40, 30, 20, 15), "the four dimensions come back")

print("")
print("THE OTHER ENCODINGS")
f = make_frame(color_enc=W.COLOR_NONE, mw=8, mh=8)
out = feed(W.Assembler(), W.encode_datagrams(f, 64))
check(out is not None and out.color_enc == W.COLOR_NONE and out.color == b"" and len(out.mask) == 64, "no colour: a mask-only frame")
check(W.color_pixels_bgr(out) is None, "color_pixels_bgr says None for a mask-only frame")
f = make_frame(mask_enc=W.MASK_DEFLATE, mw=32, mh=24)
out = feed(W.Assembler(), W.encode_datagrams(f, 100))
expected = W.inflate_raw(f.mask, 32 * 24)
check(out is not None and bytes(W.mask_pixels(out).tobytes()) == expected, "a deflated mask inflates back to its pixels")
solid = np.zeros((90, 160), np.uint8)
solid[20:70, 40:120] = 255
c = W.deflate_raw(solid.tobytes())
check(len(c) < 400 and W.inflate_raw(c, solid.size) == solid.tobytes(), "a mask of two flat areas deflates to a few hundred bytes", "%d bytes" % len(c))
check(raises(W.REJECT_SIZE, W.inflate_raw, c, solid.size - 1), "a stream that inflates past the expected size is refused (a bomb)")
check(raises(W.REJECT_SIZE, W.inflate_raw, c, solid.size + 1), "a stream that inflates to less is refused")
f = make_frame(color_enc=W.COLOR_JPEG, mw=8, mh=8)
f.color_w, f.color_h = 16, 16
out = feed(W.Assembler(), W.encode_datagrams(f, 80))
check(out is not None and out.color == f.color and out.color_enc == W.COLOR_JPEG, "a JPEG section travels as opaque bytes")

print("")
print("REASSEMBLY IS NOT FOOLED BY ORDER, REPEATS OR LOSS")
f = make_frame(cw=40, ch=30, mw=20, mh=15)
dg = W.encode_datagrams(f, 256)
check(len(dg) > 6, "the test frame has several chunks", str(len(dg)))
order = list(range(len(dg)))
random.Random(5).shuffle(order)
out = feed(W.Assembler(), [dg[i] for i in order])
check(out is not None and out.color == f.color and out.mask == f.mask, "chunks in a shuffled order assemble the same frame")
asm = W.Assembler()
out = feed(asm, [dg[0], dg[0], dg[1], dg[1]] + dg[2:])
check(out is not None and asm.duplicate == 2 and asm.completed == 1, "repeated chunks are counted and ignored", "duplicate=%d" % asm.duplicate)
asm = W.Assembler()
out = feed(asm, dg)
asm.add(dg[3])
check(asm.duplicate == 1 and asm.completed == 1, "a chunk of an already-complete frame is a duplicate, not a new assembly")
asm = W.Assembler()
out = feed(asm, dg[:-1])
check(out is None and asm.completed == 0, "a frame with a chunk missing never completes")
f2 = make_frame(seq=8, cw=40, ch=30, mw=20, mh=15)
out = feed(asm, W.encode_datagrams(f2, 256))
check(out is not None and out.seq == 8 and asm.abandoned == 1, "the next frame completes and the unfinished one is abandoned", "abandoned=%d" % asm.abandoned)
check(asm.add(dg[0]) is None and asm.stale == 1, "a chunk of an older frame is stale and ignored", "stale=%d" % asm.stale)
asm = W.Assembler()
feed(asm, W.encode_datagrams(make_frame(seq=0xFFFFFFFE, cw=40, ch=30, mw=20, mh=15), 256))
out = feed(asm, W.encode_datagrams(make_frame(seq=1, cw=40, ch=30, mw=20, mh=15), 256))
check(out is not None and out.seq == 1, "frame numbers wrap at 2**32: 1 is newer than 0xFFFFFFFE")
check(W.seq_is_newer(5, 4) and not W.seq_is_newer(4, 5) and not W.seq_is_newer(5, 5) and W.seq_is_newer(0, 0xFFFFFFFF),
      "seq_is_newer is a signed 32-bit difference")
asm = W.Assembler()
feed(asm, W.encode_datagrams(make_frame(seq=50, session=1, cw=40, ch=30, mw=20, mh=15), 256)[:2])
out = feed(asm, W.encode_datagrams(make_frame(seq=3, session=2, cw=40, ch=30, mw=20, mh=15), 256))
check(out is not None and out.session == 2 and asm.sessions == 1 and asm.abandoned == 1,
      "a new session is a new producer: its low seq is accepted, the old partial frame dropped and counted", "abandoned=%d" % asm.abandoned)

print("")
print("EVERY WAY A DATAGRAM CAN BE WRONG IS REFUSED, COUNTED AND NEVER RAISED")
good = W.encode_datagrams(make_frame(cw=40, ch=30, mw=20, mh=15), 100000)[0]


def with_field(data, offset, fmt, value):
    b = bytearray(data)
    struct.pack_into(fmt, b, offset, value)
    return bytes(b)


cases = (
    ("too short for a header", good[:71], W.REJECT_SHORT),
    ("wrong magic", b"XXXX" + good[4:], W.REJECT_MAGIC),
    ("an unknown version", with_field(good, 4, "<B", 2), W.REJECT_VERSION),
    ("an unknown kind", with_field(good, 5, "<B", 9), W.REJECT_KIND),
    ("header_len smaller than 72", with_field(good, 6, "<H", 40), W.REJECT_HEADER),
    ("header_len past the datagram", with_field(good, 6, "<H", 60000), W.REJECT_HEADER),
    ("chunk_count 0", with_field(good, 34, "<H", 0), W.REJECT_CHUNK),
    ("chunk_index beyond chunk_count", with_field(good, 32, "<H", 5), W.REJECT_CHUNK),
    ("a chunk that overruns the payload", with_field(good, 36, "<I", 10), W.REJECT_CHUNK),
    ("sections that do not add up to the payload", with_field(good, 60, "<I", 7), W.REJECT_SIZE),
    ("an unknown colour encoding", with_field(good, 45, "<B", 7), W.REJECT_ENCODING),
    ("an unknown mask encoding", with_field(good, 46, "<B", 0), W.REJECT_ENCODING),
    ("a dimension over the cap", with_field(good, 48, "<H", W.MAX_DIMENSION + 1), W.REJECT_DIMENSIONS),
    ("a zero-sized mask", with_field(good, 52, "<H", 0), W.REJECT_DIMENSIONS),
    ("raw RGBA whose length is not w*h*4", with_field(with_field(good, 56, "<I", 4799), 40, "<I", 4799 + 300)[:-1], W.REJECT_SIZE),
)
for label, datagram, reason in cases:
    asm = W.Assembler()
    r = asm.add(datagram)
    check(r is None and asm.rejected.get(reason, 0) == 1 and asm.completed == 0, "%s -> %s" % (label, reason), str(asm.rejected))
# The cap is tested at the header, where it is enforced, with a JPEG picture (any length is fine) whose sections add up to the payload: the
# cap is the only thing wrong. (Through the Assembler a frame that size would also fail the byte-sum rule, which would mask a missing cap.)
over_cap = with_field(with_field(with_field(good, 45, "<B", 2), 56, "<I", W.MAX_PAYLOAD_BYTES - 299), 40, "<I", W.MAX_PAYLOAD_BYTES + 1)
check(raises(W.REJECT_SIZE, W.parse_header, over_cap), "a payload one byte over the cap is refused at the header, before anything is allocated for it")
at_cap = with_field(with_field(with_field(good, 45, "<B", 2), 56, "<I", W.MAX_PAYLOAD_BYTES - 300), 40, "<I", W.MAX_PAYLOAD_BYTES)
check(W.parse_header(at_cap)["payload_len"] == W.MAX_PAYLOAD_BYTES, "and a payload of exactly the cap is accepted (the header is read; nothing is allocated here)")
wrong_length = W.Frame(1, 1, 0.0, 0.0, 0.0, 0, W.COLOR_RGBA, W.MASK_RAW, 0, 4, 4, 2, 2, 0, 0, b"xx", b"1234")
check(raises(W.REJECT_SIZE, W.encode_datagrams, wrong_length, 100),
      "the encoder refuses a frame the receiver would refuse (raw RGBA of the wrong length)")
asm = W.Assembler()
# Two chunks of a payload of 4 800 + 300 bytes that together carry only 90: every chunk index arrived, the bytes did not add up.
frame_for_holes = make_frame(cw=40, ch=30, mw=20, mh=15)
hole_a = W.encode_datagrams(frame_for_holes, 100000)[0]
header = hole_a[:72]
short_a = with_field(with_field(header, 32, "<H", 0), 34, "<H", 2) + b"a" * 50
short_b = with_field(with_field(with_field(header, 32, "<H", 1), 34, "<H", 2), 36, "<I", 50) + b"b" * 40
check(asm.add(short_a) is None and asm.add(short_b) is None and asm.rejected.get(W.REJECT_SIZE, 0) == 1 and asm.completed == 0,
      "chunks that arrive in full but do not add up to the payload are refused, not shown with a hole", str(asm.rejected))
check(asm.add(short_a) is None and asm.duplicate == 1, "and a straggler of the refused frame is a duplicate")
asm = W.Assembler()
a = W.encode_datagrams(make_frame(cw=40, ch=30, mw=20, mh=15), 256)
b = bytearray(a[1])
struct.pack_into("<H", b, 34, 9)
b = bytes(b)
asm.add(a[0])
check(asm.add(b) is None and asm.rejected.get(W.REJECT_HEADER, 0) + asm.rejected.get(W.REJECT_CHUNK, 0) >= 1,
      "a chunk whose header disagrees with its frame's first chunk is refused", str(asm.rejected))
rng = np.random.RandomState(9)
asm = W.Assembler()
for _ in range(300):
    blob = rng.randint(0, 256, size=int(rng.randint(0, 400)), dtype=np.uint8).tobytes()
    asm.add(blob)
check(asm.completed == 0, "300 random datagrams never complete a frame and never raise", str(sum(asm.rejected.values())))
mutated = bytearray(good)
for i in range(72):
    m = bytearray(good)
    m[i] ^= 0xFF
    a2 = W.Assembler()
    try:
        a2.add(bytes(m))
        ok = True
    except Exception:
        ok = False
    if not ok:
        mutated = None
        break
check(mutated is not None, "flipping every header byte in turn never raises")

print("")
print("THE ENCODER'S LIMITS")
big = W.Frame(1, 1, 0.0, 0.0, 0.0, 0, W.COLOR_NONE, W.MASK_RAW, 0, 0, 0, 2, 2, 0, 0, b"", b"1234")
check(len(W.encode_datagrams(big, 10)) == 1 and len(W.encode_datagrams(big, 10)[0]) == 72 + 4, "a small frame is one datagram")
check(W.chunk_count_for(0, 100) == 1 and W.chunk_count_for(100, 100) == 1 and W.chunk_count_for(101, 100) == 2, "chunk_count_for rounds up")
check(raises(W.REJECT_CHUNK, W.encode_datagrams, W.Frame(1, 1, 0.0, 0.0, 0.0, 0, W.COLOR_NONE, W.MASK_RAW, 0, 0, 0, 4096, 4096, 0, 0, b"",
                                                      b"\x00" * (4096 * 4096)), 256),
      "a frame that would need more than 65535 chunks is refused by the encoder")
f = make_frame()
f.people = 999
f.fg_permille = 5000
dg = W.encode_datagrams(f, 100)
check(struct.unpack_from("<HB", dg[0], 64) == (1000, 255), "people and fg_permille are clamped into their fields")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
