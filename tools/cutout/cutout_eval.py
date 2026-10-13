#!/usr/bin/env python3
"""S-01 - MEASURE the cutout pipeline on a video, with no camera and no socket.

It runs the SAME engine the sender runs (cutout_engine.CutoutEngine over the selfie networks and the person detector) on a clip and prints what
the pipeline did: how many frames had people, what a frame costs split into detector, networks and matte (the first frame, which pays the
models' warm-up, is reported apart and left out of the means), and how STEADY the mask is. Steadiness is measured as FLICKER: the share of
foreground pixels that flip on for exactly one frame (or off for exactly one frame) between two frames that agree. Real motion changes a pixel
for good; a flicker changes it and changes it back. The same network answers are fed to four matte settings, so the stages can be compared on
identical input:

    network only      the ramp, nothing else (what the raw probabilities give)
    + guided filter   the mask's edge snapped to the picture's edges
    + temporal        per-pixel smoothing that follows real movement at once
    + cleanup         far specks dropped, pinholes filled  (the sender's default)

    .venv\\Scripts\\python.exe tools/cutout/cutout_eval.py --video ..\\..\\video\\147_1080p.mp4 --seconds 20
    .venv\\Scripts\\python.exe tools/cutout/cutout_eval.py --video clip.mp4 --sheet out.jpg --sheet-times 8,25,45

`--sheet` writes a contact sheet: per requested time one row of [picture + crops | network-only cutout | default cutout | default alpha].
Look at it: no number here says whether a head is complete.
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

import cutout_engine as E
import cutout_matte as M
import cutout_overlay as OV
import cutout_udp_sender as CS

VARIANTS = (
    ("network only", dict(guided=False, temporal=False, cleanup=False)),
    ("+ guided filter", dict(guided=True, temporal=False, cleanup=False)),
    ("+ temporal", dict(guided=True, temporal=True, cleanup=False)),
    ("+ cleanup (default)", dict(guided=True, temporal=True, cleanup=True)),
)

PROVIDERS = {"dml": ["DmlExecutionProvider", "CPUExecutionProvider"], "cpu": ["CPUExecutionProvider"]}


class Variant:
    """One matte setting: its refiner, the last three binary masks, and what has been counted so far."""

    def __init__(self, label, cfg):
        self.label = label
        self.refiner = M.MatteRefiner(cfg)
        self.history = []
        self.alpha = None
        self.flicker = []
        self.fg = []
        self.with_people = 0
        self.ms = []

    def step(self, canvas, guide, first):
        t0 = time.perf_counter()
        self.alpha = self.refiner.update(canvas, guide)
        if not first:
            self.ms.append((time.perf_counter() - t0) * 1000.0)
        binary = self.alpha > 127
        self.fg.append(float(binary.mean()))
        if self.refiner.last_people > 0:
            self.with_people += 1
        self.history.append(binary)
        if len(self.history) > 3:
            self.history.pop(0)
        if len(self.history) == 3:
            a, b, c = self.history
            # B differs from A and from C while A and C agree: a pixel that flipped for exactly this one frame.
            blips = (b != a) & (b != c) & (a == c)
            self.flicker.append(float(blips.sum()) / max(1.0, float(b.sum())))


def main(argv=None):
    ap = argparse.ArgumentParser(description="S-01 cutout pipeline measurement")
    ap.add_argument("--video", required=True)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="dml")
    ap.add_argument("--roi", choices=("auto", "on", "off"), default="auto")
    ap.add_argument("--max-people", type=int, default=4)
    ap.add_argument("--mask-long-side", type=int, default=640)
    ap.add_argument("--tta", action="store_true", help="average each network answer with the mirrored picture's")
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the clip to start at")
    ap.add_argument("--seconds", type=float, default=0.0, help="seconds to process (0 = to the end)")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--sheet", default="", help="write a contact sheet to this .jpg")
    ap.add_argument("--sheet-times", default="", help="comma-separated clip times, seconds, for the sheet rows")
    ap.add_argument("--sheet-cell", type=int, default=360, help="height of one sheet cell, pixels")
    a = ap.parse_args(argv)

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit("cannot open %s" % a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if a.start > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000.0)
    providers = PROVIDERS[a.provider]

    class Args:  # the few fields CS.load_* read, so the evaluator builds the pipeline the way the sender does
        pass
    s = Args()
    s.general_model = CS.S.GENERAL_MODEL
    s.landscape_model = CS.S.LANDSCAPE_MODEL
    s.roi = a.roi
    s.detector_model = None
    s.det_conf = 0.40
    general, landscape = CS.load_networks(s, providers)
    detector = CS.load_detector(s, providers)
    CS.warm_up(general, landscape, detector)
    cfg = E.CutoutConfig(mask_long_side=a.mask_long_side, roi=(detector is not None), max_people=a.max_people, tta=a.tta)
    engine = E.CutoutEngine(general, landscape, detector, cfg)
    variants = [Variant(label, M.MatteConfig(**kw)) for label, kw in VARIANTS]

    want = sorted(float(t) for t in a.sheet_times.split(",") if t.strip()) if a.sheet_times else []
    rows = []
    frames = 0
    engine_ms = []
    first_ms = 0.0
    modes = {}
    clock = 0.0
    while True:
        if a.max_frames and frames >= a.max_frames:
            break
        if a.seconds and frames / fps >= a.seconds:
            break
        ok, frame = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        canvas, guide, crops, mode, _model_id = engine.raw(frame, clock)
        raw_ms = (time.perf_counter() - t0) * 1000.0
        for v in variants:
            v.step(canvas, guide, frames == 0)
        total_ms = raw_ms + variants[-1].ms[-1] if variants[-1].ms else raw_ms
        if frames == 0:
            first_ms = total_ms
        else:
            engine_ms.append(total_ms)
        modes[mode] = modes.get(mode, 0) + 1
        t_clip = a.start + frames / fps
        if want and t_clip >= want[0]:
            want.pop(0)
            net = variants[0].alpha
            final = variants[-1].alpha
            res = E.CutoutResult(final, variants[-1].refiner.last_people, variants[-1].refiner.last_fg_permille, mode, 0, crops, total_ms)
            left = OV.render(frame, res, 0.0, total_ms, "t=%.1fs" % t_clip, False, frames)
            h = frame.shape[0]
            panels = [left[:, :left.shape[1] // 2],
                      OV.composite(frame, net),
                      OV.composite(frame, final),
                      cv2.cvtColor(cv2.resize(final, (frame.shape[1], h), interpolation=cv2.INTER_LINEAR), cv2.COLOR_GRAY2BGR)]
            scale = a.sheet_cell / float(h)
            rows.append(np.hstack([cv2.resize(p, (int(p.shape[1] * scale), a.sheet_cell), interpolation=cv2.INTER_AREA) for p in panels]))
        clock += 1.0 / fps
        frames += 1
    cap.release()

    print("clip %s: %d frames from %.1fs, providers %s" % (a.video, frames, a.start, general.active_providers))
    print("modes: %s   (people found in %d of %d frames by the default matte)"
          % (", ".join("%s=%d" % kv for kv in sorted(modes.items())), variants[-1].with_people, frames))
    if engine_ms:
        print("engine, default matte, after the first frame: mean %.2f ms  p95 %.2f ms  (first frame, with warm-up: %.0f ms)"
              % (float(np.mean(engine_ms)), float(np.percentile(engine_ms, 95)), first_ms))
        print("  detector %.2f ms/frame   networks %.2f ms/frame (last frame's split)" % (engine.last["detect_ms"], engine.last["seg_ms"]))
    print("")
    print("%-22s %10s %12s %12s %10s" % ("matte", "matte ms", "foreground", "flicker", "frames"))
    print("%-22s %10s %12s %12s %10s" % ("", "(mean)", "(of picture)", "(1-frame %)", "w/ people"))
    for v in variants:
        print("%-22s %10.2f %11.2f%% %11.3f%% %10d"
              % (v.label, float(np.mean(v.ms)) if v.ms else 0.0, 100.0 * float(np.mean(v.fg)) if v.fg else 0.0,
                 100.0 * float(np.mean(v.flicker)) if v.flicker else 0.0, v.with_people))
    if rows and a.sheet:
        cv2.imwrite(a.sheet, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
        print("\nwrote %s (%d rows: picture + crops | network only | default | default alpha)" % (a.sheet, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
