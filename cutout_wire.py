#!/usr/bin/env python3
"""S-01 - THE CUTOUT WIRE: one camera picture and its person mask per FRAME, a frame split into UDP datagrams, default 127.0.0.1:8903.

A NEW message on a NEW port, a sibling of the hands wire (hand_wire.py, 8901) and the face wire (face_wire.py, 8902). The body sender's
datagram (8899) is untouched. Unlike those, this one carries IMAGE DATA, so it is BINARY, not JSON: a picture does not survive being
text, and a JSON parse per 4 MB frame is exactly the garbage the Unity side was built to avoid (ADR-101).

THE CONTRACT IS VERSIONED. `ver` is 1. A receiver must ignore a datagram whose `ver` it does not know rather than guess, and may ignore a
`kind` it does not know. All integers are LITTLE-ENDIAN. A picture is far bigger than a datagram (a 1280x720 RGBA frame is 3.7 MB, a UDP
datagram at most 64 KB), so a frame is cut into CHUNKS and EVERY chunk carries the whole 72-byte header, so any one datagram says which
frame it belongs to and how to place it; the receiver keeps no state to read a header.

    HEADER (72 bytes, at the start of every datagram)
    off size type   name
      0    4  bytes  magic         b"VXCO"
      4    1  u8     ver           1
      5    1  u8     kind          1 = a frame (anything else is ignored)
      6    2  u16    header_len    72 in version 1. Bytes between the fields below and header_len are skipped, so a later version may
                                   append fields without breaking a version-1 reader that checks `ver` first
      8    4  u32    session       a new random value per sender process = a new producer: a receiver drops its partial frame
     12    4  u32    seq           frame number, +1 per frame, wraps at 2**32 (compare as a signed 32-bit difference)
     16    8  f64    t             sender wall clock, unix seconds, when the frame was packed
     24    4  f32    lat_ms        ms from the camera frame being read to the frame being packed
     28    4  f32    infer_ms      ms of segmentation work for this frame
     32    2  u16    chunk_index   0 .. chunk_count-1
     34    2  u16    chunk_count   >= 1
     36    4  u32    chunk_offset  where this chunk's bytes go inside the frame's PAYLOAD
     40    4  u32    payload_len   the whole payload: colour section + mask section
     44    1  u8     flags         bit0 mirrored (the sender flipped the picture), bit1 live source (0 = a file), bit2 nobody found
     45    1  u8     color_enc     0 none, 1 raw RGBA32, 2 JPEG
     46    1  u8     mask_enc      1 raw A8, 2 deflate (a raw RFC 1951 stream of the A8 image, no zlib header)
     47    1  u8     model_id      0 unknown, 1 selfie general (256x256), 2 selfie landscape (256x144)
     48    2  u16    color_w       0 when color_enc is 0
     50    2  u16    color_h
     52    2  u16    mask_w        the mask is often smaller than the picture: it is stretched over it
     54    2  u16    mask_h
     56    4  u32    color_len     bytes of the colour section
     60    4  u32    mask_len      bytes of the mask section; color_len + mask_len == payload_len
     64    2  u16    fg_permille   foreground area x 1000
     66    1  u8     people        people cut out: the larger of the people tracked and the foreground groups (capped at 255); 0 = nobody
     67    1  u8     reserved      0
     68    4  u32    reserved      0 (room for a payload checksum)

    PAYLOAD = colour section, then mask section, laid end to end, row-major, TOP ROW FIRST.
      raw RGBA32 : R, G, B, 255 for each pixel (the alpha byte is not the mask; the mask is its own section)
      JPEG       : a baseline JPEG of the picture
      raw A8     : one byte per mask pixel, 0 = background, 255 = person, soft in between

WHAT A RECEIVER DOES. Chunks of one frame share (session, seq). It writes each chunk's bytes at `chunk_offset` and the frame is complete when
every chunk_index has arrived once. A chunk for a NEWER seq abandons an unfinished older frame (never wait: a live picture that is late is
worse than one that is missing); a chunk for an OLDER seq is ignored; a repeated chunk is ignored. Everything is bounded: payload_len is
capped at MAX_PAYLOAD_BYTES and every dimension at MAX_DIMENSION, so a sender that lies cannot make a receiver allocate without limit.

CHUNK SIZE. DEFAULT_CHUNK_BYTES (60 000) fills a loopback datagram. Over a real network the IP layer would cut that into ~40 fragments and
lose the whole datagram if one went missing; a network receiver wants about 1 200 (the sender's --chunk-bytes).

NOT HERE: models, sockets, cameras. This module only packs and unpacks, so a receiver and the tests share one definition. Pure python + numpy.
"""

import struct
import zlib

import numpy as np

WIRE_VERSION = 1
MAGIC = b"VXCO"
KIND_FRAME = 1
DEFAULT_PORT = 8903
HEADER_BYTES = 72

#: Payload bytes per datagram: a 72-byte header plus this stays inside the 65 507-byte UDP limit with room to spare.
DEFAULT_CHUNK_BYTES = 60000
MIN_CHUNK_BYTES = 256
MAX_CHUNK_BYTES = 65000

#: Largest frame payload a receiver will assemble: 3840x2160 RGBA is 33 MB, plus a mask. Past this the header is lying or broken.
MAX_PAYLOAD_BYTES = 48 * 1024 * 1024
MAX_DIMENSION = 4096

COLOR_NONE = 0
COLOR_RGBA = 1
COLOR_JPEG = 2
MASK_RAW = 1
MASK_DEFLATE = 2

MODEL_UNKNOWN = 0
MODEL_GENERAL = 1
MODEL_LANDSCAPE = 2

FLAG_MIRRORED = 1
FLAG_LIVE = 2
FLAG_NOBODY = 4

_HEADER = struct.Struct("<4sBBHIIdffHHIIBBBBHHHHIIHBBI")
assert _HEADER.size == HEADER_BYTES, "cutout header layout is not 72 bytes"

# Reasons a datagram is refused; the same words the C# receiver counts them under.
REJECT_SHORT = "short"
REJECT_MAGIC = "magic"
REJECT_VERSION = "version"
REJECT_KIND = "kind"
REJECT_HEADER = "header"
REJECT_CHUNK = "chunk"
REJECT_SIZE = "size"
REJECT_DIMENSIONS = "dimensions"
REJECT_ENCODING = "encoding"


class WireError(ValueError):
    """A datagram or frame that is not a cutout message this version understands. `reason` is one of the REJECT_* words."""

    def __init__(self, reason, detail=""):
        ValueError.__init__(self, "%s%s" % (reason, (": " + detail) if detail else ""))
        self.reason = reason


class Frame:
    """One frame: the metadata of the header plus the two payload sections. Built by the sender, rebuilt by the Assembler."""

    __slots__ = ("session", "seq", "t", "lat_ms", "infer_ms", "flags", "color_enc", "mask_enc", "model_id", "color_w", "color_h",
                 "mask_w", "mask_h", "fg_permille", "people", "color", "mask")

    def __init__(self, session=0, seq=0, t=0.0, lat_ms=0.0, infer_ms=0.0, flags=0, color_enc=COLOR_NONE, mask_enc=MASK_RAW,
                 model_id=MODEL_UNKNOWN, color_w=0, color_h=0, mask_w=0, mask_h=0, fg_permille=0, people=0, color=b"", mask=b""):
        self.session = session
        self.seq = seq
        self.t = t
        self.lat_ms = lat_ms
        self.infer_ms = infer_ms
        self.flags = flags
        self.color_enc = color_enc
        self.mask_enc = mask_enc
        self.model_id = model_id
        self.color_w = color_w
        self.color_h = color_h
        self.mask_w = mask_w
        self.mask_h = mask_h
        self.fg_permille = fg_permille
        self.people = people
        self.color = color
        self.mask = mask

    @property
    def payload_len(self):
        return len(self.color) + len(self.mask)

    @property
    def mirrored(self):
        return bool(self.flags & FLAG_MIRRORED)

    @property
    def live(self):
        return bool(self.flags & FLAG_LIVE)

    @property
    def nobody(self):
        return bool(self.flags & FLAG_NOBODY)


def check_sections(color_enc, mask_enc, color_w, color_h, mask_w, mask_h, color_len, mask_len):
    """The rules that tie a header's sizes together. Raises WireError; returns None when they hold. Shared by the sender (it must not
    build a frame a receiver would refuse) and by the receiver."""
    for name, v in (("color_w", color_w), ("color_h", color_h), ("mask_w", mask_w), ("mask_h", mask_h)):
        if v < 0 or v > MAX_DIMENSION:
            raise WireError(REJECT_DIMENSIONS, "%s=%d" % (name, v))
    if color_enc not in (COLOR_NONE, COLOR_RGBA, COLOR_JPEG):
        raise WireError(REJECT_ENCODING, "color_enc=%d" % color_enc)
    if mask_enc not in (MASK_RAW, MASK_DEFLATE):
        raise WireError(REJECT_ENCODING, "mask_enc=%d" % mask_enc)
    if mask_w < 1 or mask_h < 1:
        raise WireError(REJECT_DIMENSIONS, "mask %dx%d" % (mask_w, mask_h))
    if color_enc == COLOR_NONE:
        if color_len != 0:
            raise WireError(REJECT_SIZE, "color_enc 0 with color_len %d" % color_len)
    else:
        if color_w < 1 or color_h < 1:
            raise WireError(REJECT_DIMENSIONS, "color %dx%d" % (color_w, color_h))
        if color_len < 1:
            raise WireError(REJECT_SIZE, "colour section is empty")
        if color_enc == COLOR_RGBA and color_len != color_w * color_h * 4:
            raise WireError(REJECT_SIZE, "raw RGBA of %dx%d is not %d bytes" % (color_w, color_h, color_len))
    if mask_len < 1:
        raise WireError(REJECT_SIZE, "mask section is empty")
    if mask_enc == MASK_RAW and mask_len != mask_w * mask_h:
        raise WireError(REJECT_SIZE, "raw A8 of %dx%d is not %d bytes" % (mask_w, mask_h, mask_len))
    if color_len + mask_len > MAX_PAYLOAD_BYTES:
        raise WireError(REJECT_SIZE, "payload %d exceeds %d" % (color_len + mask_len, MAX_PAYLOAD_BYTES))


def _payload_slice(color, mask, start, end):
    """Bytes [start, end) of colour + mask laid end to end, without joining the two (a join is a 4 MB copy per frame)."""
    lc = len(color)
    if end <= lc:
        return color[start:end]
    if start >= lc:
        return mask[start - lc:end - lc]
    return bytes(color[start:]) + bytes(mask[:end - lc])


def chunk_count_for(payload_len, chunk_bytes):
    """How many datagrams a payload of this size takes at this chunk size."""
    return max(1, (payload_len + chunk_bytes - 1) // chunk_bytes)


def encode_datagrams(frame, chunk_bytes=DEFAULT_CHUNK_BYTES):
    """One Frame as the list of datagrams that carry it, in chunk order. Raises WireError for a frame a receiver would refuse."""
    chunk_bytes = max(MIN_CHUNK_BYTES, min(MAX_CHUNK_BYTES, int(chunk_bytes)))
    check_sections(frame.color_enc, frame.mask_enc, frame.color_w, frame.color_h, frame.mask_w, frame.mask_h,
                   len(frame.color), len(frame.mask))
    total = frame.payload_len
    count = chunk_count_for(total, chunk_bytes)
    if count > 0xFFFF:
        raise WireError(REJECT_CHUNK, "%d chunks do not fit a u16; raise --chunk-bytes" % count)
    out = []
    for index in range(count):
        start = index * chunk_bytes
        end = min(total, start + chunk_bytes)
        header = _HEADER.pack(MAGIC, WIRE_VERSION, KIND_FRAME, HEADER_BYTES, frame.session & 0xFFFFFFFF, frame.seq & 0xFFFFFFFF,
                              float(frame.t), float(frame.lat_ms), float(frame.infer_ms), index, count, start, total,
                              frame.flags & 0xFF, frame.color_enc, frame.mask_enc, frame.model_id, frame.color_w, frame.color_h,
                              frame.mask_w, frame.mask_h, len(frame.color), len(frame.mask), min(1000, max(0, frame.fg_permille)),
                              min(255, max(0, frame.people)), 0, 0)
        out.append(bytes(header) + bytes(_payload_slice(frame.color, frame.mask, start, end)))
    return out


def parse_header(datagram):
    """The validated header of one datagram: a dict of its fields plus `chunk_len`. Raises WireError with the reason."""
    if len(datagram) < HEADER_BYTES:
        raise WireError(REJECT_SHORT, "%d bytes" % len(datagram))
    (magic, ver, kind, header_len, session, seq, t, lat_ms, infer_ms, chunk_index, chunk_count, chunk_offset, payload_len, flags,
     color_enc, mask_enc, model_id, color_w, color_h, mask_w, mask_h, color_len, mask_len, fg_permille, people, _r1,
     _r2) = _HEADER.unpack_from(datagram, 0)
    if magic != MAGIC:
        raise WireError(REJECT_MAGIC)
    if ver != WIRE_VERSION:
        raise WireError(REJECT_VERSION, "ver=%d" % ver)
    if kind != KIND_FRAME:
        raise WireError(REJECT_KIND, "kind=%d" % kind)
    if header_len < HEADER_BYTES or header_len > len(datagram):
        raise WireError(REJECT_HEADER, "header_len=%d of %d bytes" % (header_len, len(datagram)))
    chunk_len = len(datagram) - header_len
    if chunk_count < 1 or chunk_index >= chunk_count:
        raise WireError(REJECT_CHUNK, "chunk %d of %d" % (chunk_index, chunk_count))
    if chunk_len < 1 or chunk_offset + chunk_len > payload_len:
        raise WireError(REJECT_CHUNK, "chunk of %d bytes at %d does not fit a payload of %d" % (chunk_len, chunk_offset, payload_len))
    if color_len + mask_len != payload_len:
        raise WireError(REJECT_SIZE, "sections %d + %d are not the payload %d" % (color_len, mask_len, payload_len))
    check_sections(color_enc, mask_enc, color_w, color_h, mask_w, mask_h, color_len, mask_len)
    return {"session": session, "seq": seq, "t": t, "lat_ms": lat_ms, "infer_ms": infer_ms, "chunk_index": chunk_index,
            "chunk_count": chunk_count, "chunk_offset": chunk_offset, "payload_len": payload_len, "flags": flags,
            "color_enc": color_enc, "mask_enc": mask_enc, "model_id": model_id, "color_w": color_w, "color_h": color_h,
            "mask_w": mask_w, "mask_h": mask_h, "color_len": color_len, "mask_len": mask_len, "fg_permille": fg_permille,
            "people": people, "header_len": header_len, "chunk_len": chunk_len}


def seq_is_newer(a, b):
    """True when frame number `a` is later than `b`, with the u32 wrap-around: the signed 32-bit difference is positive."""
    delta = (a - b) & 0xFFFFFFFF
    return 0 < delta < 0x80000000


class Assembler:
    """Datagrams in, whole Frames out. One frame is assembled at a time; see the module notes for the rules.

    Counters (all monotonically increasing, for a HUD and for the tests): completed, abandoned (an unfinished frame dropped for a newer
    one), stale (a chunk of an older frame), duplicate, rejected (a dict reason -> count), sessions (changes of producer).
    """

    def __init__(self):
        self.completed = 0
        self.abandoned = 0
        self.stale = 0
        self.duplicate = 0
        self.rejected = {}
        self.sessions = 0
        self._session = None
        self._seq = None
        self._head = None
        self._buffer = None
        self._seen = None
        self._remaining = 0
        self._bytes = 0

    def reset(self):
        self._session = None
        self._seq = None
        self._head = None
        self._buffer = None
        self._seen = None
        self._remaining = 0
        self._bytes = 0

    def _reject(self, reason):
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def add(self, datagram):
        """Feed one datagram. Returns the completed Frame when this chunk finishes one, else None. Never raises on bad input."""
        try:
            head = parse_header(datagram)
        except WireError as e:
            self._reject(e.reason)
            return None
        if self._session is None or head["session"] != self._session:
            if self._session is not None:
                self.sessions += 1
                if self._buffer is not None:
                    self.abandoned += 1
            self._session = head["session"]
            self._seq = None
            self._buffer = None
        if self._seq is not None and head["seq"] != self._seq:
            if seq_is_newer(head["seq"], self._seq):
                if self._buffer is not None:
                    self.abandoned += 1
                self._buffer = None
            else:
                self.stale += 1
                return None
        elif self._seq is not None and self._buffer is None:
            # The frame with this number is already complete: a chunk of it arriving again is a duplicate, and must not start a
            # second assembly that can never finish.
            self.duplicate += 1
            return None
        if self._buffer is None:
            self._seq = head["seq"]
            self._head = head
            self._buffer = bytearray(head["payload_len"])
            self._seen = [False] * head["chunk_count"]
            self._remaining = head["chunk_count"]
            self._bytes = 0
        else:
            first = self._head
            for key in ("payload_len", "chunk_count", "color_enc", "mask_enc", "color_w", "color_h", "mask_w", "mask_h",
                        "color_len", "mask_len"):
                if head[key] != first[key]:
                    self._reject(REJECT_HEADER)
                    return None
        if self._seen[head["chunk_index"]]:
            self.duplicate += 1
            return None
        start = head["chunk_offset"]
        self._buffer[start:start + head["chunk_len"]] = datagram[head["header_len"]:head["header_len"] + head["chunk_len"]]
        self._seen[head["chunk_index"]] = True
        self._remaining -= 1
        self._bytes += head["chunk_len"]
        if self._remaining > 0:
            return None
        if self._bytes != self._head["payload_len"]:
            # Every chunk index arrived but they overlap or leave a gap: an honest sender cannot produce that, and a frame with holes in it
            # is a wrong picture. Refused, not shown. (The Unity assembler does the same.)
            self._buffer = None
            self._seen = None
            self._reject(REJECT_SIZE)
            return None
        h = self._head
        buffer = bytes(self._buffer)
        frame = Frame(h["session"], h["seq"], h["t"], h["lat_ms"], h["infer_ms"], h["flags"], h["color_enc"], h["mask_enc"],
                      h["model_id"], h["color_w"], h["color_h"], h["mask_w"], h["mask_h"], h["fg_permille"], h["people"],
                      buffer[:h["color_len"]], buffer[h["color_len"]:])
        self._buffer = None
        self._seen = None
        self.completed += 1
        return frame


def deflate_raw(data, level=1):
    """A raw RFC 1951 stream (no zlib header, no checksum), which is what .NET's DeflateStream reads. Level 1: a mask is mostly runs, and
    the next level up costs twice the time for ~10% fewer bytes (measured on the S-01 masks)."""
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(bytes(data)) + c.flush()


def inflate_raw(data, expected_len):
    """Inverse of deflate_raw. Refuses a stream that inflates to more than `expected_len` (a decompression bomb) or to less."""
    d = zlib.decompressobj(-15)
    out = d.decompress(bytes(data), expected_len + 1)
    if len(out) != expected_len or d.unconsumed_tail:
        raise WireError(REJECT_SIZE, "mask inflates to %d bytes, expected %d" % (len(out), expected_len))
    return out


def mask_pixels(frame):
    """The frame's mask as a uint8 [mask_h, mask_w] array (inflated when it is deflated)."""
    raw = frame.mask if frame.mask_enc == MASK_RAW else inflate_raw(frame.mask, frame.mask_w * frame.mask_h)
    return np.frombuffer(raw, dtype=np.uint8).reshape(frame.mask_h, frame.mask_w)


def color_pixels_bgr(frame):
    """The frame's picture as a BGR uint8 [h, w, 3] array, or None when the frame carries no colour."""
    if frame.color_enc == COLOR_NONE:
        return None
    import cv2
    if frame.color_enc == COLOR_JPEG:
        return cv2.imdecode(np.frombuffer(frame.color, dtype=np.uint8), cv2.IMREAD_COLOR)
    rgba = np.frombuffer(frame.color, dtype=np.uint8).reshape(frame.color_h, frame.color_w, 4)
    return np.ascontiguousarray(rgba[:, :, 2::-1])
