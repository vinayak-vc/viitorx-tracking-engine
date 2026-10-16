#!/usr/bin/env python3
"""Phase 4 verification of a REAL build: runs the compiled `viitorx-tracking.exe` of a release folder and checks what a host and a user rely on.

    .venv/Scripts/python.exe tests/verify_build.py FOLDER [--models DIR] [--clip FILE] [--no-tamper] [--no-parity]

FOLDER is the release folder `build/build_engine.py assemble` wrote. Models are read from `--models` (default: the repository's `models/`) through
`--models-dir`, so the folder under test is never written to. Sections:

  1. the folder: no Python source or bytecode, the Visual C++ runtime beside the exe, manifest.json present and consistent with the files;
  2. the exe's commands: version, print-spec, verify, fetch-models --check, usage errors, exit codes;
  3. the file check: a modified, a deleted and an extra program file (on a COPY of the folder), with and without --no-verify;
  4. real runs on a clip, read back off the wire: hands, face, cutout and `run` with all three;
  5. parity: the same clip through the SOURCE (`python -m viitorx_tracking`) and the compiled exe, compared message by message.

Needs the models and the clip; it uses the GPU for sections 4-5. Exit 0 when every assertion passes.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sidecar_path  # noqa: F401

import numpy as np

import cutout_wire as CW
import face_wire as FW
import hand_wire as HW
from viitorx_tracking import lifecycle, manifest

PASS = 0
FAIL = 0
EXE = "viitorx-tracking.exe"


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-78s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-78s %s" % (label, detail))


def run_exe(folder, args, timeout=300, env_extra=None, stdin=subprocess.DEVNULL):
    """The exe with `args` and an environment that cannot reach a Python: PATH is reduced to the Windows folders."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    env = {"SystemRoot": windir, "PATH": os.pathsep.join([os.path.join(windir, "System32"), windir]), "TEMP": os.environ.get("TEMP", ""),
           "USERPROFILE": os.environ.get("USERPROFILE", ""), "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""), "APPDATA": os.environ.get("APPDATA", "")}
    env.update(env_extra or {})
    return subprocess.run([os.path.join(folder, EXE)] + list(args), capture_output=True, text=True, timeout=timeout, env=env, stdin=stdin,
                          encoding="utf-8", errors="replace")


class Listener:
    """UDP sockets on free local ports, one thread each, collecting every datagram."""

    def __init__(self, names):
        self.socks, self.got, self.ports = {}, {n: [] for n in names}, {}
        for n in names:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
            s.settimeout(0.25)
            self.socks[n] = s
            self.ports[n] = s.getsockname()[1]
        self.done = threading.Event()
        self.threads = [threading.Thread(target=self._collect, args=(n,), daemon=True) for n in names]
        for t in self.threads:
            t.start()

    def _collect(self, n):
        while not self.done.is_set():
            try:
                self.got[n].append(self.socks[n].recvfrom(65536)[0])
            except socket.timeout:
                pass

    def close(self):
        time.sleep(0.6)
        self.done.set()
        for t in self.threads:
            t.join(2)
        for s in self.socks.values():
            s.close()


def stream_args(listener, names):
    args = []
    for n in names:
        args += ["--stream", n, "--port", str(listener.ports[n])]
    return args


def run_streams(runner, clip, models, names, extra=()):
    """Run `runner` (a function(args)->CompletedProcess) for `run` over the clip with the named streams; returns (process, listener)."""
    listener = Listener(names)
    args = ["run", "--video", clip, "--no-realtime", "--no-loop", "--status", "--models-dir", models] + list(extra) + stream_args(listener, names)
    proc = runner(args)
    listener.close()
    return proc, listener


def cutout_frames(datagrams):
    asm = CW.Assembler()
    frames = [f for f in (asm.add(d) for d in datagrams) if f is not None]
    return frames, asm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--models", default=os.path.join(ROOT, "models"))
    ap.add_argument("--clip", default=os.path.join(os.path.dirname(os.path.dirname(ROOT)), "video", "123.webm"))
    ap.add_argument("--no-tamper", action="store_true")
    ap.add_argument("--no-parity", action="store_true")
    a = ap.parse_args()
    folder = os.path.abspath(a.folder)
    exe = os.path.join(folder, EXE)
    if not os.path.isfile(exe):
        print("no %s in %s" % (EXE, folder))
        return 2
    models = os.path.abspath(a.models)
    clip = os.path.abspath(a.clip)

    print("1. THE FOLDER")
    files = [os.path.join(d, f) for d, _, fs in os.walk(folder) for f in fs]
    size = sum(os.path.getsize(f) for f in files)
    check(not [f for f in files if f.endswith((".py", ".pyc"))], "no Python source or bytecode in the folder", "%d files, %.0f MB" % (len(files), size / 1e6))
    names = {os.path.basename(f).lower() for f in files}
    check({"vcruntime140.dll", "msvcp140.dll"} <= names, "the Visual C++ runtime is beside the exe (a machine without it still starts)")
    check({"directml.dll", "python310.dll"} <= names, "DirectML and the Python runtime are in the folder")
    check(os.path.isfile(os.path.join(folder, "docs", "WIRE.md")), "docs/WIRE.md is there for print-spec")
    check(os.path.isfile(os.path.join(folder, "LICENSE")) and os.path.isfile(os.path.join(folder, "THIRD_PARTY_NOTICES.txt")), "LICENSE and THIRD_PARTY_NOTICES.txt are there")
    check(os.path.isdir(os.path.join(folder, "models")) and not [f for f in os.listdir(os.path.join(folder, "models")) if f.endswith(".onnx")],
          "models/ is there and holds no model (they are fetched or placed by the user)")
    m = manifest.load(folder)
    check(m is not None and manifest.errors(manifest.check(folder)) == [], "manifest.json is present and every file matches it", "%d files" % len(m["files"]) if m else "")
    check(EXE in (m or {}).get("files", {}), "the exe itself is covered by the manifest")

    print("2. THE COMMANDS")
    p = run_exe(folder, ["version", "--json"])
    info = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else {}
    check(p.returncode == 0 and info.get("engine") == (m or {}).get("engine_version"), "version --json: exit 0 and the engine version of the manifest", p.stdout.strip()[:80])
    check(info.get("ports") == {"body": 8899, "hands": 8901, "face": 8902, "cutout": 8903}, "version --json reports the four ports")
    p = run_exe(folder, ["print-spec"])
    check(p.returncode == 0 and "8903" in p.stdout and len(p.stdout) > 5000, "print-spec prints the specification", "%d characters" % len(p.stdout))
    p = run_exe(folder, ["verify", "--models-dir", models])
    check(p.returncode == 0, "verify: exit 0 on the intact folder with the models present", p.stdout.strip().splitlines()[-1][:90] if p.stdout.strip() else p.stderr[-90:])
    p = run_exe(folder, ["fetch-models", "--check", "--models-dir", models])
    check(p.returncode == 0 and "OK" in p.stdout, "fetch-models --check: every model is OK", p.stdout.strip().splitlines()[-1][:80] if p.stdout.strip() else "")
    empty = tempfile.mkdtemp(prefix="vt_models_")
    p = run_exe(folder, ["fetch-models", "--check", "--models-dir", empty])
    check(p.returncode == 1 and "MISSING" in p.stdout, "fetch-models --check on an empty folder: exit 1 and MISSING", "exit %d" % p.returncode)
    p = run_exe(folder, ["hands", "--video", clip, "--seconds", "1", "--models-dir", empty])
    check(p.returncode == 21 and "fetch-models --only hands" in p.stdout, "a missing model: exit 21 with the command that fixes it", "exit %d" % p.returncode)
    check("tools/model" not in p.stdout and "fetch_hand_models.py" not in p.stdout, "the message does not point at a repository script")
    p = run_exe(folder, ["hands", "--video", os.path.join(empty, "nope.mp4"), "--seconds", "1", "--models-dir", models])
    check(p.returncode == 2, "a missing video file: exit 2", "exit %d" % p.returncode)
    p = run_exe(folder, ["bogus"])
    check(p.returncode == 2 and "unknown command" in p.stdout, "an unknown command: exit 2")
    p = run_exe(folder, ["hands", "--help"])
    check(p.returncode == 0 and "--port" in p.stdout, "hands --help works frozen")
    p = run_exe(folder, ["run", "--video", clip, "--stream", "hands", "--port", "1", "--stream", "hands", "--port", "2", "--models-dir", models])
    check(p.returncode == 2, "run refuses the same stream twice: exit 2", "exit %d" % p.returncode)
    p = run_exe(folder, ["hands", "--video", clip, "--seconds", "1", "--models-dir", models, "--parent-pid", "999999", "--no-verify"], timeout=120)
    check(p.returncode == 0, "--parent-pid of a process that does not exist stops the engine (exit 0)", "exit %d" % p.returncode)

    if not a.no_tamper:
        print("3. THE FILE CHECK (on a copy of the folder)")
        copy = os.path.join(tempfile.mkdtemp(prefix="vt_copy_"), "viitorx-tracking")
        shutil.copytree(folder, copy)
        victim = next(f for f in sorted(m["files"]) if f.endswith(".dll") and not f.startswith("models"))
        victim_path = os.path.join(copy, victim)
        original = open(victim_path, "rb").read()

        def start_engine(extra=()):
            """A stream command that exits on its own after one second: the file check runs before anything else."""
            return run_exe(copy, ["hands", "--video", clip, "--seconds", "1", "--no-realtime", "--models-dir", models, "--port", "9"] + list(extra), timeout=180)

        check(start_engine().returncode == 0, "the copy starts and runs one second of the clip (the baseline of the next checks)")
        with open(victim_path, "ab") as f:
            f.write(bytes([1]))
        p = start_engine()
        said = p.stdout + p.stderr
        check(p.returncode == 20 and victim in said, "a modified program file: exit 20 and the file's name (%s)" % victim, "exit %d" % p.returncode)
        check("MODIFIED" in said and "--no-verify" in said, "the message says what happened and what to do")
        check("Traceback" not in said, "no stack trace: a message, not a crash")
        p = start_engine(["--no-verify"])
        check(p.returncode == 0, "--no-verify starts anyway", "exit %d" % p.returncode)
        p = run_exe(copy, ["verify", "--models-dir", models])
        check(p.returncode == 20 and "MODIFIED" in p.stdout + p.stderr, "verify reports the same file with exit 20", "exit %d" % p.returncode)
        with open(victim_path, "wb") as f:
            f.write(original)
        check(start_engine().returncode == 0, "the file restored: starts again")
        os.remove(victim_path)
        p = start_engine()
        said = p.stdout + p.stderr
        check(p.returncode == 20 and "MISSING" in said and victim in said, "a deleted program file: exit 20, MISSING and its name", "exit %d" % p.returncode)
        with open(victim_path, "wb") as f:
            f.write(original)
        with open(os.path.join(copy, "extra.txt"), "w") as f:
            f.write("a user's file")
        check(start_engine().returncode == 0, "an extra file is not an error")
        with open(os.path.join(copy, "models", "my_model.onnx"), "wb") as f:
            f.write(b"user model")
        check(start_engine().returncode == 0, "a file in models/ is not checked")
        os.remove(os.path.join(copy, "manifest.json"))
        check(start_engine().returncode == 0, "no manifest.json: not checked (like a source checkout)")
        shutil.rmtree(os.path.dirname(copy), ignore_errors=True)

    print("4. REAL RUNS ON THE CLIP, READ OFF THE WIRE (%s)" % clip)
    if not os.path.isfile(clip):
        print("  SKIP no clip")
        return 1 if FAIL else 0

    def frozen(args):
        return run_exe(folder, args, timeout=600)

    for one in ("hands", "face", "cutout"):
        listener = Listener([one])
        args = [one, "--video", clip, "--seconds", "6", "--no-realtime", "--models-dir", models, "--port", str(listener.ports[one])]
        t0 = time.time()
        p = frozen(args)
        listener.close()
        got = listener.got[one]
        check(p.returncode == 0, "%s: exit 0" % one, "%.1f s, %s" % (time.time() - t0, p.stdout.strip().splitlines()[-1][:60] if p.stdout.strip() else p.stderr[-60:]))
        check("DmlExecutionProvider" in p.stdout or "DirectML" in p.stdout or "Dml" in p.stdout, "%s: the models run on DirectML in the frozen build" % one)
        check(len(got) > 20, "%s: datagrams arrived" % one, "%d" % len(got))
        if one == "hands":
            parsed = [HW.parse_message(d) for d in got]
            check(any(m_["n"] > 0 for m_ in parsed), "hands: every datagram parses and a hand was found")
        elif one == "face":
            parsed = [FW.parse_message(d) for d in got]
            check(len(parsed) > 20, "face: every datagram parses")
        else:
            frames, asm = cutout_frames(got)
            check(len(frames) > 10 and asm.rejected == {}, "cutout: frames reassemble, nothing refused", "%d frames" % len(frames))

    names3 = ["hands", "face", "cutout"]
    t0 = time.time()
    p, listener = run_streams(frozen, clip, models, names3, ["--seconds", "8"])
    elapsed = time.time() - t0
    events = [e for e in (lifecycle.parse_status_line(l) for l in p.stdout.splitlines()) if e]
    check(p.returncode == 0, "run (hands + face + cutout): exit 0", "%.1f s" % elapsed)
    check([e["event"] for e in events if e["event"] != "heartbeat"] == ["starting", "ready", "stopping"], "VTSTATUS: starting, ready, stopping")
    check(all(e["engine"] == m["engine_version"] for e in events), "VTSTATUS lines carry the engine version")
    hands_parsed = [HW.parse_message(d) for d in listener.got["hands"]]
    face_parsed = [FW.parse_message(d) for d in listener.got["face"]]
    frames, asm = cutout_frames(listener.got["cutout"])
    check(len(hands_parsed) > 50 and len(face_parsed) > 50 and len(frames) > 20 and asm.rejected == {}, "all three streams delivered and parse",
          "%d / %d / %d frames" % (len(hands_parsed), len(face_parsed), len(frames)))
    check(abs(len(hands_parsed) - len(face_parsed)) <= 2, "hands and face handled the same number of frames (one source, one loop)")
    check(len(set(m_["sid"] for m_ in hands_parsed)) == 1, "one session id per stream")
    fps_line = [l for l in p.stdout.splitlines() if "fps" in l.lower()]
    print("      last timing line: %s" % (fps_line[-1].strip()[:120] if fps_line else "(none)"))

    body_model = [f for f in ("rtmw3d-x-fp16.onnx", "rtmw3d-x.onnx") if os.path.isfile(os.path.join(models, f))]
    if body_model:
        # `body` does not stop on its own after a file (it stays alive and silent, by design): wait for the last frame, then end the process.
        listener = Listener(["body"])
        proc = subprocess.Popen([exe, "body", "--video", clip, "--no-realtime", "--loop", "1", "--models-dir", models, "--port", str(listener.ports["body"])],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        time.sleep(45)
        proc.terminate()
        body_out = proc.communicate(timeout=60)[0]
        listener.close()
        sent = listener.got["body"]
        check(len(sent) > 100, "body: datagrams arrived from the frozen exe", "%d" % len(sent))
        first = json.loads(sent[len(sent) // 2].decode("utf-8")) if sent else {}
        check({"xyz", "seq", "sid", "t"} <= set(first), "body: a datagram has the body wire's fields", " ".join(sorted(first)))
        check("video finished" in body_out and "Dml" in body_out, "body: it reached the end of the clip and ran on DirectML")
        print("      last body line: %s" % (body_out.strip().splitlines()[-1][:140] if body_out.strip() else "(none)"))
    else:
        print("  SKIP body: no RTMW3D model in %s" % models)

    if not a.no_parity:
        print("5. PARITY: the same clip through the source (twice, for the noise floor) and through the compiled exe (no realtime pacing, whole clip)")

        def source(args):
            return subprocess.run([sys.executable, "-B", "-m", "viitorx_tracking"] + args, cwd=ROOT, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")

        p_src, l_src = run_streams(source, clip, models, names3)
        p_src2, l_src2 = run_streams(source, clip, models, names3)
        p_exe, l_exe = run_streams(frozen, clip, models, names3)
        check(p_src.returncode == 0 and p_src2.returncode == 0 and p_exe.returncode == 0, "all three runs went through the whole clip and exited 0",
              "%d / %d / %d" % (p_src.returncode, p_src2.returncode, p_exe.returncode))
        for n in ("hands", "face"):
            counts = (len(l_src.got[n]), len(l_src2.got[n]), len(l_exe.got[n]))
            check(len(set(counts)) == 1 and counts[0] > 50, "%s: the same number of datagrams in all three runs (one per frame)" % n, str(counts))
        frames = [cutout_frames(l.got["cutout"])[0] for l in (l_src, l_src2, l_exe)]
        check(len(set(len(f) for f in frames)) == 1 and len(frames[0]) > 20, "cutout: the same number of frames in all three runs", str([len(f) for f in frames]))

        def numbers(value, path=""):
            """Every number of a message with its path (time, latency and sequence fields are excluded: they differ by construction)."""
            skip = ("t", "ts", "lat", "lat_ms", "infer_ms", "sid", "seq", "ms", "fps", "dt")
            if isinstance(value, dict):
                for k, v in value.items():
                    if k not in skip:
                        yield from numbers(v, path + "/" + k)
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    yield from numbers(v, path + "/%d" % i)
            elif isinstance(value, (bool, int, float)):
                yield path, float(value)

        def message_stats(da, db, parser):
            """(fraction of messages that differ at all, fraction with the same field set, largest difference among messages with the same fields)."""
            differ, same_shape, worst = 0, 0, 0.0
            for x, y in zip(da, db):
                ma, mb = dict(numbers(parser(x))), dict(numbers(parser(y)))
                if ma.keys() != mb.keys():
                    differ += 1
                    continue
                same_shape += 1
                d = max((abs(ma[k] - mb[k]) for k in ma), default=0.0)
                worst = max(worst, d)
                if d > 1e-6:
                    differ += 1
            return differ / len(da), same_shape / len(da), worst

        def mask_stats(fa, fb):
            """(fraction of frames whose mask differs at all, the mean over frames of the mean absolute grey-level difference, the worst frame's)."""
            ds = [float(np.mean(np.abs(np.frombuffer(x.mask, np.uint8).astype(np.int16) - np.frombuffer(y.mask, np.uint8).astype(np.int16)))) if len(x.mask) == len(y.mask) else 255.0
                  for x, y in zip(fa, fb)]
            return sum(1 for d in ds if d > 0) / len(ds), sum(ds) / len(ds), max(ds)

        # Two runs of the SAME code differ (suspected cause, not isolated: each frame is stamped with the wall clock, which the filters and the re-detect
        # schedule read), so "frozen equals source" can only mean "frozen differs from the source no more than the source differs from itself".
        for n, parser in (("hands", HW.parse_message), ("face", FW.parse_message)):
            base = message_stats(l_src.got[n], l_src2.got[n], parser)
            test = message_stats(l_src.got[n], l_exe.got[n], parser)
            print("      %s noise floor (source vs source): %.1f%% of messages differ, %.1f%% same fields, largest difference %.2g" % (n, base[0] * 100, base[1] * 100, base[2]))
            print("      %s frozen vs source:               %.1f%% of messages differ, %.1f%% same fields, largest difference %.2g" % (n, test[0] * 100, test[1] * 100, test[2]))
            check(test[0] <= base[0] + 0.10, "%s: the share of differing messages is within 10 points of the source's own run-to-run share" % n)
            check(test[1] >= base[1] - 0.05, "%s: messages have the same fields as often as two source runs do (within 5 points)" % n)
            if n == "face":   # hands carry counts and 0/1 flags (a "largest difference" there is a jump of 1-3); the face's largest difference is heavy-tailed even between two source runs (0.01 to 0.09 measured), so it only gets a loose bound
                check(test[2] < 1.0, "face: no gross single difference (largest under 1.0: a blendshape spans 0..1, angles are in degrees)")
        base_mask, test_mask = mask_stats(frames[0], frames[1]), mask_stats(frames[0], frames[2])
        print("      cutout noise floor (source vs source): %.1f%% of masks differ, mean grey-level difference %.4f, worst frame %.3f" % (base_mask[0] * 100, base_mask[1], base_mask[2]))
        print("      cutout frozen vs source:               %.1f%% of masks differ, mean grey-level difference %.4f, worst frame %.3f" % (test_mask[0] * 100, test_mask[1], test_mask[2]))
        # The worst single frame is heavy-tailed even between two source runs (3.9 and 1.0 in two measured pairs), so it only gets a loose bound (a broken mask is far
        # above it); the share of differing masks and the mean difference over the whole clip are the comparison.
        check(test_mask[0] <= base_mask[0] + 0.10 and test_mask[1] <= 2 * base_mask[1] + 0.05, "cutout: the share of differing masks and the mean grey-level difference are within the source's own run-to-run noise")
        check(test_mask[2] < 10.0, "cutout: no frame's mask is far from the source's (worst frame under 10 grey levels)")
        check(all(x.color_enc == y.color_enc and (x.color_w, x.color_h, x.mask_w, x.mask_h) == (y.color_w, y.color_h, y.mask_w, y.mask_h) for x, y in zip(frames[0], frames[2])), "cutout: same encodings and sizes")

    print("")
    print("-" * 80)
    print("%d/%d assertions passed" % (PASS, PASS + FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
