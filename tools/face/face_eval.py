#!/usr/bin/env python3
"""H-02 - MEASURE the face pipeline on a video, with no camera and no socket.

It runs the SAME tracker the sender runs (face_tracking.FaceTracker over face_landmarker's three networks) on a clip and prints what
the pipeline did: how many frames had a face, how many ids were issued, the cost of a frame split into detector, mesh and blendshape
network (the first frame, which pays the models' warm-up, is reported apart and left out of the means), and how still the output is
(frame-to-frame movement of the landmarks and of the blendshapes, raw against smoothed). The numbers in the H-02 report come from this
script; rerun it on your own clip.

    .venv\\Scripts\\python.exe tools/face/face_eval.py --video clip.mp4 --provider cpu
    .venv\\Scripts\\python.exe tools/face/face_eval.py --video clip.mp4 --provider dml --crop 110 60 610 560

`--crop X0 Y0 X1 Y1` cuts a region out of the frame first. The face detector is SHORT RANGE: a face a few percent of the frame's width
(a full-body clip) is not found, and cropping to it stands in for a camera close enough to see a face.
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

import face_landmarker as F
import face_tracking as FT
import hand_geometry as G

PROVIDERS = {"dml": ["DmlExecutionProvider", "CPUExecutionProvider"], "cpu": ["CPUExecutionProvider"]}


class Timed(object):
    """Wraps one network call to accumulate its wall time (the first call is kept apart: it pays the warm-up)."""

    def __init__(self, inner, method):
        self.inner = inner
        self.method = method
        self.seconds = 0.0
        self.calls = 0
        self.first = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _timed(self, *args):
        t = time.perf_counter()
        out = getattr(self.inner, self.method)(*args)
        dt = time.perf_counter() - t
        if self.first is None:
            self.first = dt
        else:
            self.seconds += dt
            self.calls += 1
        return out


class TimedDetector(Timed):
    def __init__(self, inner):
        Timed.__init__(self, inner, "detect")

    def detect(self, frame):
        return self._timed(frame)


class TimedMesh(Timed):
    def __init__(self, inner):
        Timed.__init__(self, inner, "infer")

    def infer(self, frame, roi):
        return self._timed(frame, roi)


class TimedBlend(Timed):
    def __init__(self, inner):
        Timed.__init__(self, inner, "infer")

    def infer(self, landmarks):
        return self._timed(landmarks)


def run(a, smooth):
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit("cannot open %s" % a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    providers = PROVIDERS[a.provider]
    det = TimedDetector(F.FaceDetector(providers=providers))
    mesh = TimedMesh(F.FaceMeshNet(providers=providers))
    blend = TimedBlend(F.BlendshapeNet(providers=providers))
    cfg = FT.FaceTrackerConfig(max_faces=a.max_faces, smooth=smooth)
    tracker = FT.FaceTracker(det, mesh, blend, lambda w, h: G.Camera.from_hfov(w, h, a.hfov), cfg)
    frames = with_face = 0
    ids = set()
    frame_ms = []
    first_ms = 0.0
    last = {}
    lm_moves = []
    bs_moves = []
    while True:
        if a.max_frames and frames >= a.max_frames:
            break
        ok, frame = cap.read()
        if not ok:
            break
        if a.crop:
            x0, y0, x1, y1 = a.crop
            frame = frame[y0:y1, x0:x1]
        t0 = time.perf_counter()
        faces = tracker.update(frame, frames / fps)
        ms = (time.perf_counter() - t0) * 1000.0
        if frames == 0:
            first_ms = ms
        else:
            frame_ms.append(ms)
        frames += 1
        if faces:
            with_face += 1
        for f in faces:
            ids.add(f.id)
            if f.state == FT.TRACKED and f.id in last and f.blendshapes is not None:
                lm_moves.append(float(np.linalg.norm(f.image[:, :2] - last[f.id][0], axis=1).mean()))
                bs_moves.append(float(np.abs(f.blendshapes - last[f.id][1]).mean()))
            if f.blendshapes is not None:
                last[f.id] = (f.image[:, :2].copy(), f.blendshapes.copy())
    cap.release()
    return {
        "frames": frames, "with_face": with_face, "ids": len(ids), "first_ms": first_ms,
        "frame_ms_mean": float(np.mean(frame_ms)) if frame_ms else 0.0,
        "frame_ms_p95": float(np.percentile(frame_ms, 95)) if frame_ms else 0.0,
        "det": (det.calls, 1000.0 * det.seconds / max(1, det.calls)),
        "mesh": (mesh.calls, 1000.0 * mesh.seconds / max(1, mesh.calls)),
        "blend": (blend.calls, 1000.0 * blend.seconds / max(1, blend.calls)),
        "lm_move": float(np.mean(lm_moves)) if lm_moves else 0.0,
        "bs_move": float(np.mean(bs_moves)) if bs_moves else 0.0,
        "providers": det.active_providers,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="H-02 face pipeline measurement")
    ap.add_argument("--video", required=True)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="cpu")
    ap.add_argument("--max-faces", type=int, default=1)
    ap.add_argument("--hfov", type=float, default=60.0)
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), default=None)
    ap.add_argument("--max-frames", type=int, default=0)
    a = ap.parse_args(argv)
    for smooth in (False, True):
        r = run(a, smooth)
        print("smooth=%-5s providers=%s" % (smooth, r["providers"]))
        print("  frames %d  with a face %d (%.0f%%)  ids issued %d"
              % (r["frames"], r["with_face"], 100.0 * r["with_face"] / max(1, r["frames"]), r["ids"]))
        print("  per frame after the first: mean %.2f ms  p95 %.2f ms   (first frame, with warm-up: %.0f ms)"
              % (r["frame_ms_mean"], r["frame_ms_p95"], r["first_ms"]))
        print("  detector %d calls x %.2f ms   mesh %d calls x %.2f ms   blendshapes %d calls x %.2f ms"
              % (r["det"][0], r["det"][1], r["mesh"][0], r["mesh"][1], r["blend"][0], r["blend"][1]))
        print("  frame to frame: landmarks move %.5f of the frame width, blendshapes move %.5f (mean per channel)"
              % (r["lm_move"], r["bs_move"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
