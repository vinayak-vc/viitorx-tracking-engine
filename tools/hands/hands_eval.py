#!/usr/bin/env python3
"""H-01 - MEASURE the hands pipeline on a video, with no camera and no socket.

It runs the SAME tracker the sender runs (hand_tracking.HandTracker over hand_landmarker's two networks) on a clip and
prints what the pipeline did: how many frames had a hand, how many ids were issued, the cost of a frame split into the
palm detector and the landmark network, and how still a still hand is (frame-to-frame landmark movement, raw against
smoothed). The numbers in the H-01 report come from this script; rerun it on your own clip.

    .venv\\Scripts\\python.exe tools/hands/hands_eval.py --video clip.mp4 --provider dml
    .venv\\Scripts\\python.exe tools/hands/hands_eval.py --video clip.mp4 --provider cpu --crop 0 150 720 850 --upscale 1.4

`--crop X0 Y0 X1 Y1` and `--upscale` cut a region out of the frame first. A full-body clip shows hands only a few dozen
pixels wide, which is below what the palm detector (a 192 px network) can see; cropping to the upper body stands in for
a webcam that is close enough to see a hand.
"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401

import argparse
import sys
import time

import cv2
import numpy as np

import hand_geometry as G
import hand_landmarker as H
import hand_tracking as HT

PROVIDERS = {"dml": ["DmlExecutionProvider", "CPUExecutionProvider"], "cpu": ["CPUExecutionProvider"]}


class TimedPalms(object):
    """Wraps the palm detector to accumulate its wall time."""

    def __init__(self, inner):
        self.inner = inner
        self.seconds = 0.0
        self.calls = 0

    def detect(self, frame):
        t = time.perf_counter()
        out = self.inner.detect(frame)
        self.seconds += time.perf_counter() - t
        self.calls += 1
        return out


class TimedNet(object):
    """Wraps the landmark network to accumulate its wall time."""

    def __init__(self, inner):
        self.inner = inner
        self.seconds = 0.0
        self.calls = 0

    def infer(self, frame, roi):
        t = time.perf_counter()
        out = self.inner.infer(frame, roi)
        self.seconds += time.perf_counter() - t
        self.calls += 1
        return out


def run(a, smooth):
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit("cannot open %s" % a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    providers = PROVIDERS[a.provider]
    palms = TimedPalms(H.PalmDetector(providers=providers))
    net = TimedNet(H.HandLandmarkNet(providers=providers))
    cfg = HT.HandTrackerConfig(max_hands=a.max_hands, smooth=smooth)
    tracker = HT.HandTracker(palms, net, lambda w, h: G.Camera.from_hfov(w, h, a.hfov), cfg)
    frames = with_hands = hand_count = 0
    ids = set()
    frame_ms = []
    last = {}
    moves = []
    t_wall = time.perf_counter()
    while True:
        if a.max_frames and frames >= a.max_frames:
            break
        ok, frame = cap.read()
        if not ok:
            break
        if a.crop:
            x0, y0, x1, y1 = a.crop
            frame = frame[y0:y1, x0:x1]
        if a.upscale != 1.0:
            frame = cv2.resize(frame, None, fx=a.upscale, fy=a.upscale)
        t0 = time.perf_counter()
        hands = tracker.update(frame, frames / fps)
        frame_ms.append((time.perf_counter() - t0) * 1000.0)
        frames += 1
        if hands:
            with_hands += 1
        for h in hands:
            hand_count += 1
            ids.add(h.id)
            if h.state == HT.TRACKED and h.id in last:
                moves.append(float(np.linalg.norm(h.image[:, :2] - last[h.id], axis=1).mean()))
            last[h.id] = h.image[:, :2].copy()
        for gone in [i for i in last if i not in {h.id for h in hands}]:
            del last[gone]
    wall = time.perf_counter() - t_wall
    cap.release()
    return {
        "frames": frames, "with_hands": with_hands, "hand_reports": hand_count, "ids": len(ids),
        "frame_ms_mean": float(np.mean(frame_ms)), "frame_ms_p95": float(np.percentile(frame_ms, 95)),
        "palm_ms_per_call": 1000.0 * palms.seconds / max(1, palms.calls), "palm_calls": palms.calls,
        "net_ms_per_call": 1000.0 * net.seconds / max(1, net.calls), "net_calls": net.calls,
        "move_mean": float(np.mean(moves)) if moves else 0.0,
        "move_median": float(np.median(moves)) if moves else 0.0,
        "wall_fps": frames / max(1e-9, wall), "providers": palms.inner.active_providers,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="H-01 hands pipeline measurement")
    ap.add_argument("--video", required=True)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="dml")
    ap.add_argument("--max-hands", type=int, default=2)
    ap.add_argument("--hfov", type=float, default=60.0)
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), default=None)
    ap.add_argument("--upscale", type=float, default=1.0)
    ap.add_argument("--max-frames", type=int, default=0)
    a = ap.parse_args(argv)
    for smooth in (False, True):
        r = run(a, smooth)
        print("smooth=%-5s providers=%s" % (smooth, r["providers"]))
        print("  frames %d  with a hand %d (%.0f%%)  hand reports %d  ids issued %d"
              % (r["frames"], r["with_hands"], 100.0 * r["with_hands"] / max(1, r["frames"]), r["hand_reports"], r["ids"]))
        print("  per frame: mean %.2f ms  p95 %.2f ms  (%.0f fps flat out, decode included)"
              % (r["frame_ms_mean"], r["frame_ms_p95"], r["wall_fps"]))
        print("  palm detector %d calls x %.2f ms   landmark net %d calls x %.2f ms"
              % (r["palm_calls"], r["palm_ms_per_call"], r["net_calls"], r["net_ms_per_call"]))
        print("  landmark movement frame to frame, mean per landmark (fraction of the frame width): mean %.5f  median %.5f"
              % (r["move_mean"], r["move_median"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
