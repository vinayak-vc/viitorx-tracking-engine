#!/usr/bin/env python3
"""H-01 - REFERENCE RECEIVER for the hands wire (hand_wire.py), and proof the wire is enough on its own.

It binds the hands port, validates every datagram with hand_wire.parse_message, and prints one line per second: the
datagram rate, the sender's own latency figure, and for each hand its id, side, gesture, pinch and estimated position.
`--view` also draws every hand in a window FROM THE DATAGRAM ALONE (never the camera picture), which is the same job a
game engine's receiver does. It is the template for a receiver in any other language: bind UDP, parse JSON, check
`ver` and `kind`, read `hands`.

    .venv\Scripts\python.exe tools/hands/hands_udp_receiver.py
    .venv\Scripts\python.exe tools/hands/hands_udp_receiver.py --view --port 8901 --seconds 30
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

import hand_landmarker as H
import hand_wire as WIRE

CANVAS = 720
WINDOW = "hands wire view (ESC / q to quit)"
COLORS = {"L": (255, 160, 40), "R": (60, 200, 255)}


def draw_message(message):
    """A square image of every hand in `message`, drawn only from `lm` (the picture position) and `wl` (the shape)."""
    canvas = np.full((CANVAS, CANVAS, 3), 24, dtype=np.uint8)
    width, height = message["img"]
    scale = CANVAS / float(max(width, height))
    for hand in message["hands"]:
        lm = np.asarray(hand["lm"], dtype=np.float64)
        pts = np.stack([lm[:, 0] * width * scale, lm[:, 1] * height * scale], axis=1).astype(np.int32)
        color = COLORS[hand["side"]]
        for a, b in H.HAND_BONES:
            cv2.line(canvas, tuple(pts[a]), tuple(pts[b]), color, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(canvas, tuple(p), 3, (255, 255, 255), -1, cv2.LINE_AA)
        z = ("Z %.2f m" % hand["pos"][2]) if "pos" in hand else "Z n/a"
        text = "#%d %s %s %s" % (hand["id"], hand["side"], hand["ges"], z)
        cv2.putText(canvas, text, (int(pts[:, 0].min()), max(16, int(pts[:, 1].min()) - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return canvas


def main(argv=None):
    ap = argparse.ArgumentParser(description="H-01 hands wire reference receiver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=WIRE.DEFAULT_PORT)
    ap.add_argument("--view", action="store_true", help="draw the hands from the datagram in a window")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = until stopped)")
    a = ap.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((a.host, a.port))
    sock.settimeout(0.5)
    print("[rx] listening on %s:%d" % (a.host, a.port), flush=True)

    t_start = t_beat = time.time()
    received = bad = 0
    last = None
    last_seq = None
    gaps = 0
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
                try:
                    last = WIRE.parse_message(data)
                    received += 1
                    if last_seq is not None and last["seq"] != last_seq + 1:
                        gaps += 1
                    last_seq = last["seq"]
                except WIRE.WireError as e:
                    bad += 1
                    print("[rx] rejected a datagram: %s" % e, flush=True)
            if a.view and last is not None:
                cv2.imshow(WINDOW, draw_message(last))
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
            if now - t_beat >= 1.0:
                el = now - t_beat
                head = "[rx] %.1f msg/s  bad=%d seq_gaps=%d" % (received / el, bad, gaps)
                if last is not None:
                    head += "  lat=%.1fms  n=%d" % (last["lat"], last["n"])
                print(head, flush=True)
                if last is not None:
                    for h in last["hands"]:
                        pos = "pos=(%.2f %.2f %.2f)m" % tuple(h["pos"]) if "pos" in h else "pos=n/a(%s)" % h["why"]
                        print("[rx]   #%d %s %-9s pinch=%.2f curls=%s %s"
                              % (h["id"], h["side"], h["ges"], h["pinch"], h["fing"], pos), flush=True)
                received = 0
                t_beat = now
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if a.view:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
