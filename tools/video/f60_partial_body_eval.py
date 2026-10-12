#!/usr/bin/env python3
"""F-60 - HOW WELL DOES THE PARTIAL-BODY TRACKER DO ON A BODY THE FRAME CUTS OFF? (measurement, no Unity)

The numbers in ADR-096 come from this tool. It takes a clip with a FULL body in it, cuts a webcam-like crop around one
person at three heights, runs the SAME detector + RTMW3D on the crop, and compares what framing.FramingTracker makes of it
against the full-frame pose of the same person (the truth, in the same units).

    capture   VIDEO OUT.npz [--start N --count N --step N --pick-x 0.35]
              pose the target person in the full frame and in three crops ("waist": the hips just under the bottom edge,
              "chest", "shoulders"), each resized to 720 px tall, and save every array. Needs the GPU, the model and the
              detector weights. ~70 s for 240 frames.
    eval      OUT.npz [OUT2.npz ...]
              no model: replay the saved arrays through the tracker, per crop, and print tier coverage, root error (in
              torso lengths), depth error against the height rule on the full frame, and the old rule's numbers.
    midrun    OUT.npz [OUT2.npz ...] --cut chest
              the hips are lost in the middle of a run: full frame, then the crop, then the full frame again. Prints the
              step in the root and the depth at the loss and at the regain.

Not a test: it needs a recording and the model, which are not in version control. tests/test_framing.py is the test.
"""
import os as _os
import sys as _sys

_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401  - puts the sidecar root and every tools/ group on sys.path
import evidence_paths as EV

import argparse
import math
import sys

import numpy as np

import framing as FR

CUTS = ("waist", "chest", "shoulders")
HFOV_DEGREES = 70.0
SUBJECT_HEIGHT_M = 1.70
CROP_HEIGHT_PX = 720
WARMUP_FRAMES = 40
LOSS_AT, REGAIN_AT = 80, 160


def intrinsics(width, height):
    fx = 0.5 * width / math.tan(math.radians(HFOV_DEGREES) * 0.5)
    return (fx, fx, width / 2.0, height / 2.0)


def truth_depth(uv, conf, fy):
    """The F-49 height rule on the FULL frame: the reference everything is compared with."""
    ys = [uv[i][1] for i in (0, 15, 16) if conf[i] >= 0.3]
    span = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
    return SUBJECT_HEIGHT_M * fy / span if span > 40 else None


class Box(object):
    def __init__(self, box):
        self.box = box


def capture(args):
    import time

    import cv2
    import host_person_detector as HPD
    import multiperson_udp_sender as MP
    import rtmw3d_pose as R

    detector = HPD.HostPersonDetector(conf=0.25)
    model = R.RTMW3D(EV.DEFAULT_MODEL)

    def pose_all(frame, limit=4):
        h, w = frame.shape[:2]
        out = []
        for d in detector.detect(frame)[:limit]:
            uv, z, c = model.infer(frame, MP.track_to_bbox(Box(d.box), w, h))
            out.append((uv, z, c))
        return out

    def shoulder_mid(uv):
        return 0.5 * (uv[5] + uv[6])

    def nearest(people, ref):
        best, best_d = None, 1e9
        for p in people:
            d = float(np.linalg.norm(shoulder_mid(p[0]) - ref))
            if d < best_d:
                best, best_d = p, d
        return best, best_d

    cap = cv2.VideoCapture(args.video)
    t0 = time.perf_counter()
    data = {"frames": [], "uv": [], "z": [], "c": []}
    crops = dict((name, []) for name in CUTS)
    ref = None
    size = None
    for k in range(args.count):
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start + k * args.step)
        ok, frame = cap.read()
        if not ok:
            break
        fh, fw = frame.shape[:2]
        if fw > 1920:
            frame = cv2.resize(frame, (1920, int(fh * 1920 / fw)))
            fh, fw = frame.shape[:2]
        size = (fw, fh)
        people = pose_all(frame)
        if not people:
            continue
        if ref is None:
            ref = min((shoulder_mid(p[0]) for p in people), key=lambda m: abs(m[0] - args.pick_x * fw))
        target, dist = nearest(people, ref)
        if target is None or dist > 0.25 * fw or min(target[2][5], target[2][6], target[2][11], target[2][12]) < 0.5:
            continue
        uv, z, c = target
        ref = shoulder_mid(uv)
        shm, hipm = shoulder_mid(uv), 0.5 * (uv[11] + uv[12])
        torso = float(np.linalg.norm(hipm - shm))
        top = float(max(0.0, uv[0][1] - 0.9 * np.linalg.norm(uv[0] - shm)))
        cut_y = {"waist": float(hipm[1]) - 0.05 * torso, "chest": float(shm[1]) + 0.55 * torso,
                 "shoulders": float(shm[1]) + 0.18 * torso}
        rec = {}
        for name, y1 in cut_y.items():
            ch = y1 - top
            if ch < 120:
                rec[name] = None
                continue
            cw = ch * 16.0 / 9.0
            x0 = int(max(0, min(fw - cw, shm[0] - cw / 2)))
            crop = frame[int(top):int(y1), x0:int(min(fw, x0 + cw))]
            s = float(CROP_HEIGHT_PX) / crop.shape[0]
            crop = cv2.resize(crop, (int(crop.shape[1] * s), CROP_HEIGHT_PX))
            found = pose_all(crop, 3)
            if not found:
                rec[name] = None
                continue
            best, best_d = nearest(found, (shm - np.array([x0, int(top)], dtype=np.float32)) * s)
            if best is None or best_d > 0.2 * crop.shape[1]:
                rec[name] = None
                continue
            rec[name] = (best[0], best[1], best[2],
                         np.array([x0, int(top), s, crop.shape[1], crop.shape[0]], dtype=np.float32))
        data["frames"].append(args.start + k * args.step)
        data["uv"].append(uv)
        data["z"].append(z)
        data["c"].append(c)
        for name in CUTS:
            crops[name].append(rec[name])
    out = {"frames": np.array(data["frames"]), "fsz": np.array(size), "uv": np.array(data["uv"]),
           "z": np.array(data["z"]), "c": np.array(data["c"])}
    for name in CUTS:
        out[name + "_valid"] = np.array([r is not None for r in crops[name]])
        out[name + "_uv"] = np.array([r[0] if r else np.zeros((133, 2), np.float32) for r in crops[name]])
        out[name + "_z"] = np.array([r[1] if r else np.zeros(133, np.float32) for r in crops[name]])
        out[name + "_c"] = np.array([r[2] if r else np.zeros(133, np.float32) for r in crops[name]])
        out[name + "_geom"] = np.array([r[3] if r else np.zeros(5, np.float32) for r in crops[name]])
    np.savez_compressed(args.out, **out)
    print("saved %s: %d frames in %.1f s" % (args.out, len(data["frames"]), time.perf_counter() - t0), flush=True)
    return 0


def crop_view(d, cut, k, intr):
    """(uv, zrel, conf, crop intrinsics, crop size, crop->full transform) of crop frame k."""
    uv, z, c, g = d[cut + "_uv"][k], d[cut + "_z"][k], d[cut + "_c"][k], d[cut + "_geom"][k]
    x0, y0, s, cw, ch = g
    k_crop = (intr[0] * s, intr[1] * s, (intr[2] - x0) * s, (intr[3] - y0) * s)
    return uv, z, c, k_crop, (cw, ch), (x0, y0, s)


def quant(values):
    v = np.abs(np.array(values, dtype=float))
    return "n=%3d p50 %.3f p90 %.3f" % (len(v), np.percentile(v, 50), np.percentile(v, 90)) if len(v) else "n=0"


def evaluate(args):
    for path in args.npz:
        d = np.load(path)
        fw, fh = d["fsz"]
        intr = intrinsics(fw, fh)
        n = len(d["frames"])
        for cut in CUTS:
            valid = d[cut + "_valid"]
            warm = FR.FramingTracker(0.3, SUBJECT_HEIGHT_M)
            tiers, cold_tiers = [], []
            root_err, cold_root_err, z_err, cold_z_err, old_root_err = [], [], [], [], []
            old_pass = 0
            old_depth_err = []
            for k in range(n):
                uv_t, z_t, c_t = d["uv"][k], d["z"][k], d["c"][k]
                hip_t = 0.5 * (uv_t[11] + uv_t[12])
                torso = float(np.linalg.norm(hip_t - 0.5 * (uv_t[5] + uv_t[6])))
                zt = truth_depth(uv_t, c_t, intr[1])
                if k < WARMUP_FRAMES:
                    warm.update(uv_t, c_t, z_t, intr, (fw, fh), None, d["frames"][k] / 30.0)
                    continue
                if not valid[k]:
                    continue
                uv, z, c, k_crop, size, (x0, y0, s) = crop_view(d, cut, k, intr)
                for tracker, tier_list, root_list, z_list in ((warm, tiers, root_err, z_err),
                                                             (FR.FramingTracker(0.3, SUBJECT_HEIGHT_M), cold_tiers,
                                                              cold_root_err, cold_z_err)):
                    r = tracker.update(uv, c, z, k_crop, size, None, d["frames"][k] / 30.0)
                    if r is None:
                        tier_list.append(-1)
                        continue
                    tier_list.append(r.tier)
                    hip_full = np.array([r.hip_u, r.hip_v]) / s + np.array([x0, y0])
                    root_list.append(float(np.linalg.norm(hip_full - hip_t)) / torso)
                    if zt:
                        z_list.append((r.hip_z - zt) / zt)
                if c[11] >= 0.3 and c[12] >= 0.3:
                    old_pass += 1
                    hm = 0.5 * (uv[11] + uv[12]) / s + np.array([x0, y0])
                    old_root_err.append(float(np.linalg.norm(hm - hip_t)) / torso)
                if zt:
                    old_depth_err.append(abs(2.0 - zt) / zt)
            t, ct = np.array(tiers), np.array(cold_tiers)
            print("%s cut=%-9s frames=%3d | tiers hips/shoulders/head/none  warm %d/%d/%d/%d  cold %d/%d/%d/%d | the old gate passes %d"
                  % (path, cut, len(t), (t == 0).sum(), (t == 1).sum(), (t == 2).sum(), (t == -1).sum(),
                     (ct == 0).sum(), (ct == 1).sum(), (ct == 2).sum(), (ct == -1).sum(), old_pass))
            print("    root error / torso   warm %s | cold %s | old (the model's own hips) %s"
                  % (quant(root_err), quant(cold_root_err), quant(old_root_err)))
            print("    depth error          warm %s | cold %s | old rule (2.0 m with no ankles) median %.0f%%"
                  % (quant(z_err), quant(cold_z_err), 100.0 * (np.median(old_depth_err) if old_depth_err else 0.0)))
    return 0


def midrun(args):
    for path in args.npz:
        d = np.load(path)
        fw, fh = d["fsz"]
        intr = intrinsics(fw, fh)
        tracker = FR.FramingTracker(0.3, SUBJECT_HEIGHT_M)
        out = []
        for k in range(len(d["frames"])):
            uv_t, z_t, c_t = d["uv"][k], d["z"][k], d["c"][k]
            hip_t = 0.5 * (uv_t[11] + uv_t[12])
            torso = float(np.linalg.norm(hip_t - 0.5 * (uv_t[5] + uv_t[6])))
            now = d["frames"][k] / 30.0
            if LOSS_AT <= k < REGAIN_AT and d[args.cut + "_valid"][k]:
                uv, z, c, k_crop, size, (x0, y0, s) = crop_view(d, args.cut, k, intr)
                r = tracker.update(uv, c, z, k_crop, size, None, now)
                offset, scale = np.array([x0, y0]), s
            else:
                r = tracker.update(uv_t, c_t, z_t, intr, (fw, fh), None, now)
                offset, scale = np.zeros(2), 1.0
            if r is None:
                out.append((k, -1, float("nan"), float("nan")))
                continue
            hip_full = np.array([r.hip_u, r.hip_v]) / scale + offset
            out.append((k, r.tier, float(np.linalg.norm(hip_full - hip_t)) / torso, r.hip_z))
        out = np.array(out)
        for name, at in (("loss", LOSS_AT), ("regain", REGAIN_AT)):
            seg = out[at - 2:at + 6]
            zs = seg[:, 3]
            print("%s %s at %d: tiers %s  root err/torso %s  depth %s  (max step %.1f%%)"
                  % (path, name, at, seg[:, 1].astype(int).tolist(), np.round(seg[:, 2], 2).tolist(), np.round(zs, 2).tolist(),
                     100.0 * float(np.nanmax(np.abs(np.diff(zs)))) / float(zs[0])))
    return 0


def main():
    ap = argparse.ArgumentParser(description="F-60 partial-body measurement")
    sub = ap.add_subparsers(dest="command")
    cp = sub.add_parser("capture")
    cp.add_argument("video")
    cp.add_argument("out")
    cp.add_argument("--start", type=int, default=300)
    cp.add_argument("--count", type=int, default=240)
    cp.add_argument("--step", type=int, default=2)
    cp.add_argument("--pick-x", type=float, default=0.5, help="which person: their shoulders' x as a fraction of the width")
    ep = sub.add_parser("eval")
    ep.add_argument("npz", nargs="+")
    mp = sub.add_parser("midrun")
    mp.add_argument("npz", nargs="+")
    mp.add_argument("--cut", choices=CUTS, default="chest")
    args = ap.parse_args()
    if args.command == "capture":
        return capture(args)
    if args.command == "eval":
        return evaluate(args)
    if args.command == "midrun":
        return midrun(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
