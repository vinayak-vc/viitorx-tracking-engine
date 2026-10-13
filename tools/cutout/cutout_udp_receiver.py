#!/usr/bin/env python3
"""S-01 - REFERENCE RECEIVER for the cutout wire (cutout_wire.py), and proof the wire is enough on its own.

It binds the cutout port, reassembles frames with cutout_wire.Assembler (every datagram checked, nothing trusted), and prints one line a
second: the frame rate, how many frames arrived whole and how many were abandoned, the sender's own latency figures, and the end-to-end latency
(the datagram's `t` against this machine's clock, which is only meaningful on the machine that sent it). `--view` draws the picture and the
mask in a window FROM THE DATAGRAMS ALONE, the same job the Unity receiver does; `--dump DIR` writes the first frame of every N as a PNG pair
(the picture, and the picture cut out on a checkerboard). It is the template for a receiver in any other language: bind UDP, read the 72-byte
header, place each chunk at its offset, draw when every chunk of a frame has arrived.

    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_receiver.py
    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_receiver.py --view --port 8903 --seconds 30
    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_receiver.py --seconds 20 --dump out --dump-every 30
"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401

import argparse
import socket
import sys
import time

import cv2
import numpy as np

import cutout_overlay as OV
import cutout_wire as WIRE

WINDOW = "cutout wire view (ESC / q to quit)"

#: Receive buffer. A frame arrives as a burst of up to ~60 datagrams of 60 KB; the default 64 KB buffer would drop most of them.
RECEIVE_BUFFER_BYTES = 16 * 1024 * 1024


def compose(frame):
    """(picture BGR or None, mask uint8, cutout BGR or None) from a reassembled Frame, using only what the wire carried."""
    picture = WIRE.color_pixels_bgr(frame)
    mask = WIRE.mask_pixels(frame)
    cut = OV.composite(picture, mask) if picture is not None else None
    return picture, mask, cut


def main(argv=None):
    ap = argparse.ArgumentParser(description="S-01 cutout wire reference receiver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=WIRE.DEFAULT_PORT)
    ap.add_argument("--view", action="store_true", help="draw the picture, the mask and the cutout in a window")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = until stopped)")
    ap.add_argument("--dump", default="", help="write PNGs of received frames into this folder")
    ap.add_argument("--dump-every", type=int, default=30, help="with --dump: every Nth completed frame (default %(default)s)")
    a = ap.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_BYTES)
    sock.bind((a.host, a.port))
    sock.settimeout(0.5)
    if a.dump:
        _os.makedirs(a.dump, exist_ok=True)
    print("[rx] listening on %s:%d (receive buffer %d MB)" % (a.host, a.port, sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) // (1 << 20)),
          flush=True)

    asm = WIRE.Assembler()
    t_start = t_beat = time.time()
    frames = total = dumped = 0
    seq_gaps = 0
    last = None
    last_seq = None
    datagrams = 0
    bytes_in = 0
    try:
        while True:
            now = time.time()
            if a.seconds > 0 and now - t_start >= a.seconds:
                break
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                data = None
            if data is not None:
                datagrams += 1
                bytes_in += len(data)
                frame = asm.add(data)
                if frame is not None:
                    frames += 1
                    total += 1
                    if last_seq is not None and WIRE.seq_is_newer(frame.seq, last_seq) and ((frame.seq - last_seq) & 0xFFFFFFFF) != 1:
                        seq_gaps += 1
                    last_seq = frame.seq
                    last = (frame, time.time())
                    if a.dump and total % max(1, a.dump_every) == 1:
                        picture, mask, cut = compose(frame)
                        if picture is not None:
                            cv2.imwrite(_os.path.join(a.dump, "frame_%06d_picture.png" % frame.seq), picture)
                            cv2.imwrite(_os.path.join(a.dump, "frame_%06d_cutout.png" % frame.seq), cut)
                        cv2.imwrite(_os.path.join(a.dump, "frame_%06d_mask.png" % frame.seq), mask)
                        dumped += 1
                    if a.view:
                        picture, mask, cut = compose(frame)
                        mask_bgr = cv2.cvtColor(cv2.resize(mask, (picture.shape[1], picture.shape[0]) if picture is not None else
                                                           (mask.shape[1], mask.shape[0])), cv2.COLOR_GRAY2BGR)
                        panels = [p for p in (picture, cut, mask_bgr) if p is not None]
                        cv2.imshow(WINDOW, np.hstack(panels) if len(panels) > 1 else panels[0])
                        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                            break
            if now - t_beat >= 1.0:
                el = now - t_beat
                line = "[rx] %.1f frames/s  %.1f MB/s  whole=%d abandoned=%d stale=%d dup=%d seq_gaps=%d rejected=%s" % (
                    frames / el, bytes_in / el / 1e6, asm.completed, asm.abandoned, asm.stale, asm.duplicate, seq_gaps,
                    dict(asm.rejected) if asm.rejected else 0)
                if last is not None:
                    f, got = last
                    line += "  lat=%.1fms infer=%.1fms e2e=%.0fms people=%d fg=%.1f%% %dx%d mask %dx%d model=%d%s%s" % (
                        f.lat_ms, f.infer_ms, (got - f.t) * 1000.0, f.people, f.fg_permille / 10.0, f.color_w, f.color_h, f.mask_w,
                        f.mask_h, f.model_id, " mirrored" if f.mirrored else "", " nobody" if f.nobody else "")
                print(line, flush=True)
                frames = datagrams = bytes_in = 0
                t_beat = now
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if a.view:
            cv2.destroyAllWindows()
    print("[rx] done: %d frames whole, %d abandoned, %d stale chunks, %d duplicate chunks, %d seq gaps, rejected=%s%s"
          % (asm.completed, asm.abandoned, asm.stale, asm.duplicate, seq_gaps, dict(asm.rejected) if asm.rejected else 0,
             (", %d dumped to %s" % (dumped, a.dump)) if a.dump else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
