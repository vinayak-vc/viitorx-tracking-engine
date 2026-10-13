#!/usr/bin/env python3
"""H-02 - REFERENCE RECEIVER for the face wire (face_wire.py), and proof the wire is enough on its own.

It binds the face port, validates every datagram with face_wire.parse_message, and prints one line per second: the datagram rate, the
sender's own latency figure, and for each face its id, head pose, estimated position and the strongest blendshapes. `--view` also draws
the face in a window FROM THE DATAGRAM ALONE (never the camera picture), which is the same job an avatar engine's receiver does:
landmarks as dots, the head pose, and the blendshape bars. It is the template for a receiver in any other language: bind UDP, parse
JSON, check `ver` and `kind`, read `faces`.

    .venv\\Scripts\\python.exe tools/face/face_udp_receiver.py
    .venv\\Scripts\\python.exe tools/face/face_udp_receiver.py --view --port 8902 --seconds 30
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

import face_wire as WIRE

CANVAS = 720
WINDOW = "face wire view (ESC / q to quit)"
PANEL_ROWS = 12


def draw_message(message):
    """A square image of the first face in `message`, drawn only from `lm`, `bs` and the pose fields."""
    canvas = np.full((CANVAS, CANVAS, 3), 24, dtype=np.uint8)
    if not message["faces"]:
        cv2.putText(canvas, "no face", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 120, 120), 2, cv2.LINE_AA)
        return canvas
    face = message["faces"][0]
    width, height = message["img"]
    if "lm" in face:
        lm = np.asarray(face["lm"], dtype=np.float64)
        pts = np.stack([lm[:, 0] * width, lm[:, 1] * height], axis=1)
        lo = pts[:468].min(axis=0)
        hi = pts[:468].max(axis=0)
        scale = 0.62 * CANVAS / max(hi[0] - lo[0], hi[1] - lo[1], 1.0)
        centre = (lo + hi) / 2.0
        for p in (pts - centre) * scale + np.array([CANVAS * 0.34, CANVAS * 0.5]):
            cv2.circle(canvas, (int(p[0]), int(p[1])), 1, (90, 230, 90), -1, cv2.LINE_AA)
    head = "#%d  yaw %.0f  pitch %.0f  roll %.0f" % (face["id"], face["yaw"], face["pitch"], face["roll"])
    cv2.putText(canvas, head, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    if "bs" in face:
        order = [i for i in np.argsort(-np.asarray(face["bs"])) if i != 0][:PANEL_ROWS]
        for row, idx in enumerate(order):
            y = 70 + row * 26
            value = face["bs"][idx]
            cv2.rectangle(canvas, (470, y), (570, y + 16), (70, 70, 70), 1)
            cv2.rectangle(canvas, (470, y), (470 + int(100 * min(1.0, max(0.0, value))), y + 16), (90, 230, 90), -1)
            cv2.putText(canvas, "%s %.2f" % (WIRE.BLENDSHAPE_NAMES[idx], value), (578, y + 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main(argv=None):
    ap = argparse.ArgumentParser(description="H-02 face wire reference receiver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=WIRE.DEFAULT_PORT)
    ap.add_argument("--view", action="store_true", help="draw the face from the datagram in a window")
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
                    for f in last["faces"]:
                        pos = "pos=(%.2f %.2f %.2f)m" % tuple(f["pos"]) if "pos" in f else "pos=n/a(%s)" % f["why"]
                        top = ""
                        if "bs" in f:
                            order = [i for i in np.argsort(-np.asarray(f["bs"])) if i != 0][:3]
                            top = "  top: " + " ".join("%s=%.2f" % (WIRE.BLENDSHAPE_NAMES[i], f["bs"][i]) for i in order)
                        print("[rx]   #%d yaw=%.0f pitch=%.0f roll=%.0f %s%s"
                              % (f["id"], f["yaw"], f["pitch"], f["roll"], pos, top), flush=True)
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
