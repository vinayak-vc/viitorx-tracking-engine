#!/usr/bin/env python3
"""F-46 - is the rig right, BEFORE the camera rolls?

Everything this checks has silently been wrong at some point in this project's history, and each one
looks fine from the outside:

  * the sub-pixel banner once announced 1/8 while the pipeline built whole-pixel (ADR-077)
  * `mono_res` was a declared parameter that the body ignored (ADR-081)
  * the IR projector had never been switched on at all (ADR-080)
  * a harness run under the system Python falls back to CPU at ~5 fps and says nothing
  * the mount is 5.89 deg out of level against its own 2 deg tolerance, and nothing corrects a
    tilt unless someone sets CameraMount.TiltDegrees by hand (ADR-079)

None of those produce an error. They produce a take that has to be shot again, and the reason is
not visible until someone reads a log line nobody was looking at. So: one command, one table, and a
verdict, run while there is still time to fix something.

    python-sidecar~/.venv/Scripts/python.exe tools/capture/f46_preflight.py
    python-sidecar~/.venv/Scripts/python.exe tools/capture/f46_preflight.py --tilt-set 18
"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401
import evidence_paths as EV

import argparse
import os
import socket
import sys
import time

import numpy as np

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
ROWS = []


def row(name, state, detail):
    ROWS.append((name, state, detail))
    mark = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[state]
    print("[%s] %-22s %s" % (mark, name, detail))


def check_interpreter():
    """The trap that costs the most and announces itself least: the system Python has no working
    GPU path here, falls back to CPU silently, and turns ~30 fps into ~5."""
    try:
        import onnxruntime as ort
    except ImportError as e:
        row("interpreter", FAIL, "onnxruntime not importable: %s" % e)
        return
    providers = ort.get_available_providers()
    if "DmlExecutionProvider" in providers:
        row("interpreter", PASS, "DirectML available (%s)" % os.path.basename(sys.executable))
    else:
        row("interpreter", FAIL,
            "NO DirectML - this is the wrong interpreter. Use .venv\\Scripts\\python.exe. "
            "Inference would run on CPU at ~5 fps. Providers: %s" % ", ".join(providers))


def check_model():
    path = EV.DEFAULT_MODEL
    if not os.path.isfile(path):
        row("model", FAIL, "not found: %s" % path)
        return
    mb = os.path.getsize(path) / 1e6
    name = os.path.basename(path)
    if "fp16" in name:
        row("model", PASS, "%s (%.0f MB) - 2.97x faster than fp32 (ADR-082)" % (name, mb))
    else:
        row("model", WARN,
            "%s (%.0f MB) is the fp32 build. Three people will run at ~15.5 fps instead of ~37. "
            "Build the fp16 one with tools/model/f45_make_fp16.py." % (name, mb))


def check_config():
    import oak_depth as D
    c = D.STEREO_CONFIG

    # STEREO_CONFIG ships with sub-pixel OFF and every production launch turns it on by passing
    # --subpixel-bits, so reading the bare module default here would report a configuration nothing
    # actually runs. Read the SUPERVISOR's default instead - that is the value Unity will launch
    # with - so there is one source of truth and this cannot drift away from it.
    launched_bits = None
    try:
        import sidecar_supervisor as S
        for action in S.build_arg_parser()._actions:
            if action.dest == "subpixel_bits":
                launched_bits = action.default
    except Exception:
        pass
    if launched_bits is None:
        launched_bits = c["subpixelBits"] if c["subpixel"] else 0

    if launched_bits == 3:
        row("sub-pixel", PASS,
            "supervisor launches with --subpixel-bits 3 (1/8 px) - 8x finer depth (ADR-077)")
    else:
        row("sub-pixel", WARN,
            "the supervisor launches with --subpixel-bits %s; depth error is %dx coarser than it "
            "needs to be" % (launched_bits, 8 if launched_bits == 0 else 1))

    if c["monoRes"] == "800p" and tuple(c["rgbIsp"]) == (1, 1):
        row("resolution", PASS, "mono 800p + rgb ISP 1/1 - full sensor, usable to ~4.5 m (ADR-081)")
    else:
        row("resolution", WARN,
            "mono=%s rgbIsp=%s - not the full sensor. Range and depth both suffer."
            % (c["monoRes"], tuple(c["rgbIsp"])))


def check_port(port):
    """A stale producer on 8899 is the difference between 'Unity shows nothing' and 'Unity shows
    somebody else's session', and the second one is much harder to notice on a monitor."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        row("udp %d" % port, PASS, "free - no stale sidecar holding it")
    except OSError:
        row("udp %d" % port, WARN,
            "in use. Unity itself binds it in Play mode, so this is expected if the Editor is "
            "playing - and a problem if it is not.")
    finally:
        s.close()


def check_footage():
    clip = os.path.join(EV.ASSETS_ROOT, "Games", "video", "456.webm")
    if os.path.isfile(clip):
        row("crowd footage", PASS, "456.webm present (seven dancers) - act 4 source")
    else:
        row("crowd footage", WARN, "456.webm not found; the crowd act needs a live crowd instead")


def check_device(seconds, tilt_set):
    import depthai as dai
    import oak_depth as D
    try:
        from f19_level import angles  # the MEASURED tilt maths, not a re-derivation
    except Exception:
        angles = None

    pipeline = D.build_rgbd_pipeline()
    imu = pipeline.create(dai.node.IMU)
    imu.enableIMUSensor(dai.IMUSensor.ACCELEROMETER_RAW, 100)
    imu.setBatchReportThreshold(1)
    imu.setMaxBatchReports(10)
    xi = pipeline.create(dai.node.XLinkOut)
    xi.setStreamName("imu")
    imu.out.link(xi.input)

    try:
        dev = dai.Device(pipeline)
    except Exception as e:
        row("camera", FAIL, "cannot open the OAK-D: %s" % e)
        return

    with dev:
        row("camera", PASS, "connected (%s)" % dev.getMxId())
        row("IR projector", PASS if "on (" in D.enable_ir_dot_projector(dev, 0.8) else WARN,
            D.enable_ir_dot_projector(dev, 0.8))

        q_rgb = dev.getOutputQueue("rgb", 4, False)
        q_depth = dev.getOutputQueue("depth", 4, False)
        q_imu = dev.getOutputQueue("imu", 50, False)

        for _ in range(30):
            q_rgb.get()
            q_depth.get()

        acc, valid, frames = [], [], 0
        t0 = time.time()
        while time.time() - t0 < seconds:
            f = q_rgb.get().getCvFrame()
            d = q_depth.get().getFrame()
            valid.append(float(np.count_nonzero(d)) / d.size)
            frames += 1
            pk = q_imu.tryGet()
            if pk is not None:
                for p in pk.packets:
                    a = p.acceleroMeter
                    acc.append([a.x, a.y, a.z])
        fps = frames / (time.time() - t0)
        h, w = f.shape[:2]

        row("frame size", PASS, "%dx%d rgb, %dx%d depth" % (w, h, d.shape[1], d.shape[0]))
        row("throughput", PASS if fps >= 27.0 else WARN,
            "%.1f fps sustained over %.0fs" % (fps, seconds))
        row("valid depth", PASS if np.mean(valid) > 0.10 else WARN,
            "%.1f%% of depth pixels have a reading (scene-dependent; compare, do not judge)"
            % (100.0 * float(np.mean(valid))))

        if acc and angles is not None:
            mean = np.mean(np.array(acc, dtype=np.float64), axis=0)
            tilt, _ = angles(mean)
            if tilt <= 2.0:
                row("mount tilt", PASS, "%.2f deg - level, leave CameraMount.TiltDegrees at 0" % tilt)
            elif abs(tilt - tilt_set) <= 2.0 and tilt_set > 0:
                row("mount tilt", PASS,
                    "%.2f deg, and Unity is set to %.1f - compensated (ADR-079)" % (tilt, tilt_set))
            elif tilt > 45.0:
                # Not a tilt to compensate - at this angle the optical axis is nearly vertical, so
                # the camera is face-up on a desk or face-down, not aimed at a room. Telling someone
                # to set cameraTiltDegrees to 88 would be obediently wrong.
                row("mount tilt", FAIL,
                    "%.1f deg - the optical axis is nearly VERTICAL. The camera is lying flat "
                    "rather than aimed at the scene; mount it before anything else here means "
                    "much." % tilt)
            else:
                row("mount tilt", FAIL,
                    "%.2f deg out of level. Either level it, or set AppBootstrap."
                    "cameraTiltDegrees to %.0f (POSITIVE = nose down) and re-run with "
                    "--tilt-set %.0f. Uncorrected, the floor shears %.2f m per metre of depth."
                    % (tilt, tilt, tilt, np.tan(np.radians(tilt))))
        else:
            row("mount tilt", WARN, "no IMU samples - cannot confirm level")


def main():
    ap = argparse.ArgumentParser(description="F-46: check the rig before filming")
    ap.add_argument("--seconds", type=float, default=6.0, help="throughput measurement window")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--tilt-set", type=float, default=0.0,
                    help="what AppBootstrap.cameraTiltDegrees is set to, so a deliberately angled "
                         "mount reads as compensated rather than as broken")
    ap.add_argument("--no-camera", action="store_true", help="skip everything needing the device")
    args = ap.parse_args()

    print("F-46 PREFLIGHT")
    print("=" * 78)
    check_interpreter()
    check_model()
    check_config()
    check_port(args.port)
    check_footage()
    if not args.no_camera:
        check_device(args.seconds, args.tilt_set)
    print("=" * 78)

    fails = [r for r in ROWS if r[1] == FAIL]
    warns = [r for r in ROWS if r[1] == WARN]
    if fails:
        print("NOT READY - %d blocking:" % len(fails))
        for n, _, d in fails:
            print("   %s: %s" % (n, d))
        return 1
    if warns:
        print("READY, with %d thing(s) worth knowing:" % len(warns))
        for n, _, d in warns:
            print("   %s: %s" % (n, d))
        return 0
    print("READY - every check passed. Roll.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
