#!/usr/bin/env python3
"""Phase 3 tests: the engine package (viitorx_tracking): bootstrap, model registry, manifest, fetch-models, lifecycle, the one-source multi-stream loop,
the command line and its exit codes, and (when the models and a clip are present) a real three-stream run read back off the wire.

Everything except the last section uses fakes (a scripted source, scripted pipelines, a fake opener, temp folders): no model, no camera, no network.
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sidecar_path  # noqa: F401

import numpy as np

from viitorx_tracking import bootstrap, cli, exitcodes, fetch, lifecycle, manifest, multi, registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-76s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-76s %s" % (label, detail))


def tmpdir():
    return tempfile.mkdtemp(prefix="vt_test_")


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ------------------------------------------------------------------------------------------------------------------------------ bootstrap
print("BOOTSTRAP: depthai")
saved = sys.modules.pop("depthai", None)
try:
    sys.modules["depthai"] = None          # makes `import depthai` raise ImportError, as on a machine without the package
    installed = bootstrap.ensure_depthai()
    stand_in = sys.modules["depthai"]
    check(installed is True and getattr(stand_in, "__viitorx_stand_in__", False), "a missing depthai gets a stand-in")
    try:
        stand_in.Device
        refused = False
    except RuntimeError as e:
        refused = "OAK-D" in str(e)
    check(refused, "using the stand-in fails with a sentence that says why")
    check(bootstrap.ensure_depthai() is True, "asking again keeps the stand-in and says so")
    sys.modules["depthai"] = types.ModuleType("depthai")
    check(bootstrap.ensure_depthai() is False, "a real (or any other) depthai module is left alone")
finally:
    sys.modules.pop("depthai", None)
    if saved is not None:
        sys.modules["depthai"] = saved

# ------------------------------------------------------------------------------------------------------------------------------ registry
print("")
print("REGISTRY: the same models as the fetch scripts")
sys.path.insert(0, os.path.join(ROOT, "tools", "model"))
import fetch_cutout_models as FC  # noqa: E402
import fetch_face_models as FF  # noqa: E402
import fetch_hand_models as FH  # noqa: E402
import fetch_yolox_s as FY  # noqa: E402

for group, module in (("hands", FH), ("face", FF), ("cutout", FC)):
    ours = [(m.name, m.url, m.size, m.sha256) for m in registry.specs_for(group)]
    check(ours == [tuple(t) for t in module.MODELS], "%s: name, url, size and sha256 equal fetch_%s_models.MODELS" % (group, group), "%d files" % len(ours))
yolox = registry.spec_named("yolox_s.onnx")
check((yolox.url, yolox.size) == (FY.URL, FY.EXPECTED_BYTES), "person: url and size equal fetch_yolox_s")
check(all(m.url is None for m in registry.specs_for("body")) and len(registry.specs_for("body")) == 2, "the body models have no URL and are two files")
check(registry.GROUPS == ("hands", "face", "cutout", "person", "body"), "the groups")

d = tmpdir()
try:
    spec = registry.ModelSpec("hands", "tiny.onnx", "https://example.invalid/tiny.onnx", 5, registry.hashlib.sha256(b"hello").hexdigest(), True)
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec) == registry.MISSING, "no file is MISSING")
    write(os.path.join(d, "tiny.onnx"), b"hello")
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec) == registry.OK, "the exact file is OK")
    write(os.path.join(d, "tiny.onnx"), b"HELLO")
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec) == registry.CUSTOM, "same size, other bytes is CUSTOM (needs the hash)")
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec, deep=False) == registry.OK, "a size-only look cannot tell: OK")
    write(os.path.join(d, "tiny.onnx"), b"hello world")
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec) == registry.CUSTOM, "another size is CUSTOM")
    check(registry.inspect(os.path.join(d, "tiny.onnx"), spec, deep=False) == registry.CUSTOM, "another size is CUSTOM even in the size-only look")
    old = os.environ.pop("VIITORX_MODELS_DIR", None)
    try:
        check(registry.models_dir("R") == os.path.join("R", "models"), "the default folder is models/ beside the program")
        os.environ["VIITORX_MODELS_DIR"] = d
        check(registry.models_dir("R") == os.path.abspath(d), "VIITORX_MODELS_DIR overrides")
        check(registry.models_dir("R", override=os.path.join(d, "x")) == os.path.abspath(os.path.join(d, "x")), "an explicit folder beats the environment")
    finally:
        os.environ.pop("VIITORX_MODELS_DIR", None)
        if old is not None:
            os.environ["VIITORX_MODELS_DIR"] = old
finally:
    shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------------------------------------------------------------------------ manifest
print("")
print("MANIFEST: build, check, what is excluded")
d = tmpdir()
try:
    write(os.path.join(d, "viitorx-tracking.exe"), b"exe")
    write(os.path.join(d, "lib", "a.dll"), b"aaaa")
    write(os.path.join(d, "lib", "deep", "b.pyd"), b"bb")
    write(os.path.join(d, "models", "m.onnx"), b"model")
    write(os.path.join(d, "models", "m.onnx.part"), b"half")
    write(os.path.join(d, "lib", "download.bin.part"), b"half")
    write(os.path.join(d, "logs", "run.log"), b"log")
    write(os.path.join(d, "lib", "__pycache__", "c.pyc"), b"c")
    check(manifest.program_files(d) == ["lib/a.dll", "lib/deep/b.pyd", "viitorx-tracking.exe"], "models, logs, bytecode and downloads in progress are not program files")
    m = manifest.write(d, "9.9.9")
    check(m["engine_version"] == "9.9.9" and set(m["files"]) == {"lib/a.dll", "lib/deep/b.pyd", "viitorx-tracking.exe"}, "write lists the program files")
    check("manifest.json" not in m["files"], "the manifest does not list itself")
    check(manifest.check(d) == [], "a folder as built has no problems")
    write(os.path.join(d, "lib", "a.dll"), b"aaab")
    p = manifest.check(d)
    check([(x.kind, x.path) for x in p] == [("modified", "lib/a.dll")], "a changed file of the same size is MODIFIED (the hash)")
    check([(x.kind, x.path) for x in manifest.check(d, deep=False)] == [], "a size-only check cannot see it")
    write(os.path.join(d, "lib", "a.dll"), b"aaaa")
    write(os.path.join(d, "lib", "deep", "b.pyd"), b"bbbbbb")
    check([(x.kind, x.path) for x in manifest.check(d, deep=False)] == [("modified", "lib/deep/b.pyd")], "a size change is MODIFIED even in the size-only check")
    write(os.path.join(d, "lib", "deep", "b.pyd"), b"bb")
    os.remove(os.path.join(d, "viitorx-tracking.exe"))
    check([(x.kind, x.path) for x in manifest.check(d)] == [("missing", "viitorx-tracking.exe")], "a deleted file is MISSING")
    write(os.path.join(d, "viitorx-tracking.exe"), b"exe")
    write(os.path.join(d, "extra.txt"), b"hi")
    p = manifest.check(d)
    check([(x.kind, x.path) for x in p] == [("unexpected", "extra.txt")] and manifest.errors(p) == [], "an extra file is reported but is not an error")
    write(os.path.join(d, "models", "m.onnx"), b"a different model")
    check(all(x.path != "models/m.onnx" for x in manifest.check(d)), "replacing a model is not a problem")
    os.remove(os.path.join(d, "extra.txt"))
    os.remove(os.path.join(d, "lib", "a.dll"))
    text = manifest.message(manifest.check(d), d)
    check("MISSING  lib/a.dll" in text and "--no-verify" in text and "Models are not checked" in text, "the message names the file and says what to do")
    check(manifest.check(tmpdir()) == [], "a folder with no manifest.json (a source checkout) is not checked")
finally:
    shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------------------------------------------------------------------------ fetch
print("")
print("FETCH-MODELS: verification, no download when right, nothing left behind")
payload = b"0123456789" * 10
good = registry.ModelSpec("hands", "g.onnx", "https://example.invalid/g", len(payload), registry.hashlib.sha256(payload).hexdigest(), True)


class FakeResponse:
    def __init__(self, data):
        self._io = io.BytesIO(data)

    def read(self, n):
        return self._io.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(data):
    return lambda url, timeout=None: FakeResponse(data)


d = tmpdir()
try:
    dest = os.path.join(d, "sub", "g.onnx")
    fetch.download(good, dest, opener=opener_for(payload))
    check(open(dest, "rb").read() == payload and not os.path.exists(dest + ".part"), "a correct download lands and leaves no .part")
    for label, data in (("a longer stream", payload + b"x"), ("a shorter stream", payload[:-1]), ("the right size, other bytes", b"x" * len(payload))):
        target = os.path.join(d, "bad.onnx")
        try:
            fetch.download(good, target, opener=opener_for(data))
            raised = False
        except fetch.FetchError:
            raised = True
        check(raised and not os.path.exists(target) and not os.path.exists(target + ".part"), "%s is refused and leaves nothing" % label)

    class Runaway:
        """A response that never ends (one byte per read, ten thousand reads): a cap that is missing shows as reads far past the file's size."""
        def __init__(self):
            self.reads = 0

        def read(self, n):
            self.reads += 1
            return b"x" if self.reads <= 10000 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    runaway = Runaway()
    try:
        fetch.download(good, os.path.join(d, "r.onnx"), opener=lambda url, timeout=None: runaway)
        raised = False
    except fetch.FetchError:
        raised = True
    check(raised and runaway.reads <= good.size + 2 and not os.path.exists(os.path.join(d, "r.onnx.part")),
          "a stream that never ends is cut off at the file's size, not read to the end", "%d reads for a %d-byte file" % (runaway.reads, good.size))

    def boom(url, timeout=None):
        raise OSError("network down")
    try:
        fetch.download(good, os.path.join(d, "n.onnx"), opener=boom)
        raised = False
    except fetch.FetchError as e:
        raised = "network down" in str(e)
    check(raised, "a network error is a FetchError carrying the reason")

    # fetch.run against the REAL registry, but with a fake fetcher and temp folders
    lines = []
    calls = []

    def fake_fetcher(spec, path):
        calls.append(spec.name)
        raise fetch.FetchError("pretend it failed verification")   # a real download removes its .part and leaves nothing: so does this

    folder = os.path.join(d, "models")
    code = fetch.run(folder, ["hands"], False, lines.append, fetcher=fake_fetcher)
    check(code == 1 and calls == ["palm_detection_mediapipe_2023feb.onnx", "handpose_estimation_mediapipe_2023feb.onnx"], "a failing fetch is reported and the exit code is 1")
    calls[:] = []
    lines[:] = []
    code = fetch.run(folder, ["hands"], True, lines.append, fetcher=fake_fetcher)
    check(code == 1 and calls == [] and any("MISSING" in l for l in lines), "--check downloads nothing and reports missing required files")
    # a CUSTOM file is left alone
    write(os.path.join(folder, "palm_detection_mediapipe_2023feb.onnx"), b"my own palm model")
    lines[:] = []
    calls[:] = []
    fetch.run(folder, ["hands"], False, lines.append, fetcher=fake_fetcher)
    check("palm_detection_mediapipe_2023feb.onnx" not in calls and any(l.startswith("CUSTOM") for l in lines), "a file the user replaced is reported CUSTOM and not overwritten")
    lines[:] = []
    code = fetch.run(folder, ["body"], True, lines.append)
    check(code == 1 and sum(1 for l in lines if "no download" in l) == 2, "the body model is never downloaded: the engine says to place it, and fails when it was asked for")
finally:
    shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------------------------------------------------------------------------ lifecycle
print("")
print("LIFECYCLE: parent watch, stdin watch, status lines")
stop = lifecycle.StopSignal()
now = [100.0]
alive = [True]
calls = []


def fake_alive(pid):
    calls.append(pid)
    return alive[0]


watch = lifecycle.ParentWatch(4242, stop, alive=fake_alive, clock=lambda: now[0], interval=1.0)
watch.poll()
watch.poll()
check(len(calls) == 1 and not stop.is_set(), "the parent is asked about once per interval, not on every frame")
now[0] += 1.5
alive[0] = False
watch.poll()
check(stop.is_set() and "4242" in stop.reason, "a parent that is gone sets the stop signal and says which")
stop2 = lifecycle.StopSignal()
lifecycle.ParentWatch(None, stop2, alive=fake_alive).poll()
check(not stop2.is_set(), "no --parent-pid, no watching")
check(lifecycle.parent_alive(os.getpid()) is True, "this process is alive")
check(lifecycle.parent_alive(2 ** 22 - 7) is False, "a pid that does not exist is gone")
check(lifecycle.parent_alive(None) is True and lifecycle.parent_alive(0) is True, "no pid given counts as alive")
stop3 = lifecycle.StopSignal()
t = lifecycle.watch_stdin(stop3, io.StringIO("line one\nline two\n"))
t.join(2.0)
check(stop3.is_set() and "standard input" in stop3.reason, "end of file on stdin sets the stop signal")
stop4 = lifecycle.StopSignal()
stop4.set("first")
stop4.set("second")
check(stop4.reason == "first", "the first reason is kept")
written = []
em = lifecycle.StatusEmitter(enabled=True, write=written.append, clock=lambda: 12.3456, pid=77)
line = em.emit("ready", streams=["hands"], live=True)
rec = lifecycle.parse_status_line(line)
check(line.startswith("VTSTATUS {") and rec["event"] == "ready" and rec["pid"] == 77 and rec["t"] == 12.346 and rec["streams"] == ["hands"], "a status line is VTSTATUS plus one JSON object")
check("\n" not in line and rec["engine"] == "0.1.0", "it is one line and carries the engine version")
check(lifecycle.parse_status_line("[hands] frames=3") is None and lifecycle.parse_status_line("VTSTATUS {broken") is None, "other lines and broken ones are not status")
check(lifecycle.parse_status_line('{"event":"ready"}') is None, "a JSON line without the VTSTATUS prefix is not a status line")
check(lifecycle.parse_status_line("x" * len(lifecycle.STATUS_PREFIX) + '{"event":"ready"}') is None, "nor is a line that only has the prefix's length in front of valid JSON")
check(lifecycle.StatusEmitter(enabled=False, write=written.append).emit("x") is None and len(written) == 1, "a disabled emitter writes nothing")


# ------------------------------------------------------------------------------------------------------------------------------ multi loop
print("")
print("RUN_MANY: one source, several pipelines")


class FakeCap:
    def __init__(self, frames, fps=30.0):
        self.frames = frames
        self.i = 0
        self.fps = fps
        self.sets = []
        self.released = False

    def read(self):
        if self.i >= self.frames:
            return False, None
        self.i += 1
        return True, np.zeros((24, 32, 3), dtype=np.uint8) + self.i

    def get(self, prop):
        return self.fps

    def set(self, prop, value):
        self.sets.append((prop, value))
        if prop == 1:
            self.i = value

    def grab(self):
        return True

    def release(self):
        self.released = True


class FakePipeline:
    def __init__(self, label, fail=False, fail_first=0, fail_every=0):
        self.label = label
        self.fail = fail
        self.fail_first = fail_first
        self.fail_every = fail_every
        self.processed = []
        self.restarts = 0

    def banner(self):
        return ["fake pipeline %s" % self.label]

    def process(self, frame):
        n = len(self.processed) + 1
        self.processed.append(int(frame[0, 0, 0]))
        if self.fail or n <= self.fail_first or (self.fail_every and n % self.fail_every == 0):
            raise ValueError("boom %s" % self.label)
        return [1]

    def encode(self, result, ctx):
        return {"stream": self.label, "seq": ctx.seq, "sid": ctx.session_id, "mirrored": ctx.mirrored, "w": ctx.width, "h": ctx.height}

    def to_bytes(self, message):
        return json.dumps(message).encode("utf-8")

    def render(self, frame, result, hud):
        return frame

    def heartbeat_extra(self):
        return "fake=%s" % self.label

    def restart(self):
        self.restarts += 1


def listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.2)
    return s, s.getsockname()[1]


def drain(sock):
    got = []
    while True:
        try:
            got.append(json.loads(sock.recvfrom(65536)[0].decode("utf-8")))
        except socket.timeout:
            return got


def common_args(**kw):
    ns = types.SimpleNamespace(video="fake.mp4", rotate=0, flip=False, crop=None, loop=False, realtime=False, seconds=0.0, show=False, cache_dir=None,
                               camera_size=(1280, 720), camera_fps=30)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def stream_for(tag, pipeline, port, **kw):
    a = common_args(host="127.0.0.1", port=port, **kw)
    return multi.Stream(tag, a, pipeline, "sess-" + tag, "(wire ver 1)", "win-" + tag)


def run(streams, common, cap, orient=None, stop=None, watch=None, open_error=None):
    lines = []
    status_lines = []
    st = lifecycle.StatusEmitter(enabled=True, write=status_lines.append, clock=lambda: 1.0, pid=1)

    def open_source(video, args):
        if open_error:
            raise RuntimeError(open_error)
        return "file", False, None, cap, "fake file"

    code = multi.run_many(common, streams, stop or lifecycle.StopSignal(), st, watch, open_source=open_source,
                          orient=orient or (lambda f, r, fl: f), print_fn=lines.append)
    return code, lines, [lifecycle.parse_status_line(l) for l in status_lines]


s1, p1 = listener()
s2, p2 = listener()
try:
    pa, pb = FakePipeline("a"), FakePipeline("b")
    orients = []
    cap = FakeCap(12)
    code, lines, events = run([stream_for("a", pa, p1), stream_for("b", pb, p2)], common_args(), cap,
                              orient=lambda f, r, fl: (orients.append(1), f)[1])
    ga, gb = drain(s1), drain(s2)
    check(code == 0, "the loop ends with 0 when the file ends")
    check(len(ga) == 12 and len(gb) == 12, "every frame reaches every stream's own port", "%d and %d datagrams" % (len(ga), len(gb)))
    check(all(m["stream"] == "a" for m in ga) and all(m["stream"] == "b" for m in gb), "each port gets only its own stream's messages")
    check(pa.processed == pb.processed == list(range(1, 13)), "both pipelines see the same frames, in order")
    check(len(orients) == 12, "each frame is oriented ONCE, not once per stream")
    check([m["seq"] for m in ga] == sorted(set(m["seq"] for m in ga)), "sequence numbers of a stream increase")
    check(ga[0]["sid"] == "sess-a" and gb[0]["sid"] == "sess-b" and ga[0]["w"] == 32 and ga[0]["h"] == 24, "the session id and the frame size reach the encoder")
    check(cap.released, "the source is released at the end")
    check([e["event"] for e in events if e["event"] != "heartbeat"] == ["starting", "ready", "stopping"] and events[0]["streams"] == ["a", "b"], "status events: starting, ready, stopping")
    check(sum(1 for e in events if e["event"] == "heartbeat" and e.get("stream") == "a") >= 2, "each stream reports heartbeats (ready and stopping at least)")
    check(any("[a] frames=12 sent=12" in l and "stopping" in l for l in lines), "the human log carries each stream's final counts")

    # a pipeline that fails alone
    pa, pb = FakePipeline("a", fail_first=3), FakePipeline("b")
    code, lines, events = run([stream_for("a", pa, p1), stream_for("b", pb, p2)], common_args(), FakeCap(10))
    ga, gb = drain(s1), drain(s2)
    check(code == 0 and len(gb) == 10 and len(ga) == 7, "a stream that fails 3 frames loses those frames; the other stream loses none", "%d / %d" % (len(ga), len(gb)))
    check(sum(1 for e in events if e["event"] == "error" and e.get("stream") == "a") == 1, "the failure is reported once, not on every frame")

    pa, pb = FakePipeline("a", fail=True), FakePipeline("b")
    code, lines, events = run([stream_for("a", pa, p1), stream_for("b", pb, p2)], common_args(), FakeCap(60))
    ga, gb = drain(s1), drain(s2)
    check(code == 0 and len(ga) == 0 and len(gb) == 60, "a stream that always fails is dropped after %d errors and the other runs to the end" % multi.MAX_CONSECUTIVE_ERRORS)
    check(len(pa.processed) == multi.MAX_CONSECUTIVE_ERRORS and any(e.get("reason") == "stream_dropped" for e in events), "the dropped stream stops being called and a status event says so")

    pa, pb = FakePipeline("a", fail_every=2), FakePipeline("b")
    code, lines, events = run([stream_for("a", pa, p1), stream_for("b", pb, p2)], common_args(), FakeCap(100))
    ga, gb = drain(s1), drain(s2)
    check(len(pa.processed) == 100 and len(ga) == 50, "a stream that fails every second frame is never dropped (failures are counted IN A ROW)", "%d frames offered, %d sent" % (len(pa.processed), len(ga)))

    pa = FakePipeline("a", fail=True)
    code, lines, events = run([stream_for("a", pa, p1)], common_args(), FakeCap(100))
    drain(s1)
    check(code == exitcodes.USAGE_OR_SOURCE and any("every stream has failed" in l for l in lines), "when every stream has been dropped the exit code is 2")

    # looping a file restarts every pipeline
    pa, pb = FakePipeline("a"), FakePipeline("b")
    cap = FakeCap(5)
    code, lines, events = run([stream_for("a", pa, p1), stream_for("b", pb, p2)], common_args(loop=True, seconds=0.4), cap)
    drain(s1)
    drain(s2)
    check(pa.restarts >= 1 and pb.restarts == pa.restarts and (1, 0) in cap.sets, "looping a file rewinds it and restarts every pipeline")

    # per-stream flip: the camera is read and rotated once, each stream mirrors its own copy
    def asymmetric(frame, rotate, flip):
        out_frame = np.zeros((24, 32, 3), dtype=np.uint8)
        out_frame[:, 0, 0] = 1       # the left edge
        out_frame[:, -1, 0] = 2      # the right edge
        flips_seen.append(flip)
        return out_frame

    flips_seen = []
    pa, pb = FakePipeline("a"), FakePipeline("b")
    code, lines, events = run([stream_for("a", pa, p1, flip=True), stream_for("b", pb, p2, flip=False)], common_args(), FakeCap(6), orient=asymmetric)
    ga, gb = drain(s1), drain(s2)
    check(flips_seen == [False] * 6, "streams that disagree about the mirror: the shared frame is oriented WITHOUT one", str(flips_seen[:3]))
    check(pa.processed == [2] * 6 and pb.processed == [1] * 6, "the stream that wants the mirror gets a mirrored copy; the other gets the camera as it is")
    check(all(m["mirrored"] is True for m in ga) and all(m["mirrored"] is False for m in gb), "each datagram is encoded with its OWN stream's mirror flag")

    flips_seen = []
    pa, pb = FakePipeline("a"), FakePipeline("b")
    code, lines, events = run([stream_for("a", pa, p1, flip=True), stream_for("b", pb, p2, flip=True)], common_args(), FakeCap(5), orient=asymmetric)
    ga, gb = drain(s1), drain(s2)
    check(flips_seen == [True] * 5 and pa.processed == [1] * 5 and pb.processed == [1] * 5, "streams that agree: the flip is applied once while orienting (no extra copy)")
    check(all(m["mirrored"] is True for m in ga + gb), "and every datagram says it was mirrored")

    flips_seen = []
    pa = FakePipeline("a")
    code, lines, events = run([stream_for("a", pa, p1, flip=True)], common_args(), FakeCap(4), orient=asymmetric)
    ga = drain(s1)
    check(flips_seen == [True] * 4 and all(m["mirrored"] is True for m in ga), "one stream alone behaves as the single-stream sender always did")

    base = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
    copy = multi.mirror_copy(base)
    check(np.array_equal(copy, base[:, ::-1]) and copy.flags["C_CONTIGUOUS"], "mirror_copy is the frame mirrored left to right and contiguous")
    copy[0, 0, 0] = 255 - copy[0, 0, 0]
    check(not np.array_equal(copy, base[:, ::-1]) and base[0, 0, 0] == 0, "mirror_copy is a copy: writing to it never touches the shared frame")
    try:
        import cv2
        check(np.array_equal(multi.mirror_copy(base), cv2.flip(base, 1)), "mirror_copy equals cv2.flip(frame, 1), what the single-stream sender uses")
    except ImportError:
        pass

    # stop signal and parent
    stop = lifecycle.StopSignal()
    stop.set("the host asked")
    pa = FakePipeline("a")
    code, lines, events = run([stream_for("a", pa, p1)], common_args(), FakeCap(50), stop=stop)
    check(code == 0 and pa.processed == [] and any("stopping: the host asked" in l for l in lines), "a stop signal ends the loop before the next frame, exit 0")
    stop = lifecycle.StopSignal()
    gone = lifecycle.ParentWatch(5, stop, alive=lambda pid: False, interval=0.0)
    pa = FakePipeline("a")
    code, lines, events = run([stream_for("a", pa, p1)], common_args(), FakeCap(50), stop=stop, watch=gone)
    check(code == 0 and pa.processed == [] and any("parent process 5 is gone" in l for l in lines), "a parent that is gone ends the loop, exit 0")

    # source failure
    code, lines, events = run([stream_for("a", FakePipeline("a"), p1)], common_args(), None, open_error="no such camera")
    check(code == exitcodes.USAGE_OR_SOURCE and any(e["event"] == "error" and e["reason"] == "source" for e in events), "a source that will not open is exit 2 with a status error")
    check(not any(e["event"] == "ready" for e in events), "and the engine never says ready")

    # --seconds
    t0 = time.time()
    code, lines, events = run([stream_for("a", FakePipeline("a"), p1)], common_args(loop=True, seconds=0.5), FakeCap(3))
    drain(s1)
    check(code == 0 and 0.4 < time.time() - t0 < 3.0, "--seconds ends a looping run", "%.2f s" % (time.time() - t0))
finally:
    s1.close()
    s2.close()

# ------------------------------------------------------------------------------------------------------------------------------ cli
print("")
print("CLI: parsing, exit codes")
head, segs = cli._split_streams(["--video", "camera:0", "--flip", "--stream", "hands", "--max-hands", "3", "--stream", "face", "--port", "9"])
check(head == ["--video", "camera:0", "--flip"] and segs == [("hands", ["--max-hands", "3"]), ("face", ["--port", "9"])], "run: everything before the first --stream is common")
try:
    cli._split_streams(["--stream"])
    raised = False
except ValueError:
    raised = True
check(raised, "--stream with no name is an error")
eng, rest = cli._split_engine_options(["--models-dir", "M", "--video", "x", "--model", "m.onnx", "--no-verify", "--status", "--parent-pid", "12"])
check(eng.models_dir == "M" and eng.no_verify and eng.status and eng.parent_pid == 12, "engine options are read from anywhere")
check(rest == ["--video", "x", "--model", "m.onnx"], "--model (the body sender's) is not mistaken for --models-dir", str(rest))

import hands_udp_sender as HS  # noqa: E402
import cutout_udp_sender as CS  # noqa: E402
import face_udp_sender as FS  # noqa: E402

pr = HS.build_parser()
a = pr.parse_args(["--video", "x"])
cli.rebase_models("hands", pr, a, "/m")
check(a.palm_model == os.path.join("/m", "palm_detection_mediapipe_2023feb.onnx") and a.hand_model == os.path.join("/m", "handpose_estimation_mediapipe_2023feb.onnx"),
      "an unset model option moves to the models folder")
a = pr.parse_args(["--video", "x", "--palm-model", "mine.onnx"])
cli.rebase_models("hands", pr, a, "/m")
check(a.palm_model == "mine.onnx", "a model option the user set is kept")
pc = CS.build_parser()
a = pc.parse_args(["--video", "x"])
cli.rebase_models("cutout", pc, a, "/m")
check(a.detector_model == os.path.join("/m", "yolox_s.onnx") and a.general_model.endswith("selfie_segmenter_general.onnx"), "cutout: the person detector (unset by default) moves too")
pf = FS.build_parser()
a = pf.parse_args(["--video", "x", "--no-blendshapes"])
cli.rebase_models("face", pf, a, "/nowhere")
need = [os.path.basename(p) for p, g in cli.missing_models("face", a)]
check(need == ["face_detector.onnx", "face_landmarks.onnx"], "face --no-blendshapes does not require the blendshape network", str(need))

out = []
check(cli.main(["version"], out.append) == 0 and out[0].startswith("viitorx-tracking 0.1.0"), "version")
out[:] = []
cli.main(["version", "--json"], out.append)
check(json.loads(out[0])["ports"] == {"body": 8899, "cutout": 8903, "face": 8902, "hands": 8901}, "version --json carries the ports")
out[:] = []
check(cli.main([], out.append) == 0 and "viitorx-tracking hands" in out[0], "no command prints the usage")
out[:] = []
check(cli.main(["nonsense"], out.append) == exitcodes.USAGE_OR_SOURCE, "an unknown command is exit 2")
out[:] = []
check(cli.main(["hands", "--video", "x.mp4", "--models-dir", os.path.join(tempfile.gettempdir(), "vt_no_such_dir")], out.append) == exitcodes.MODELS_MISSING
      and any("fetch-models --only hands" in l for l in out), "missing models: exit 21 and the command that fixes it")
out[:] = []
check(cli.main(["run"], out.append) == exitcodes.USAGE_OR_SOURCE and any("at least one stream" in l for l in out), "run with no stream is exit 2")
out[:] = []
check(cli.main(["run", "--port", "5", "--stream", "hands"], out.append) == exitcodes.USAGE_OR_SOURCE and any("--port is a stream's own" in l for l in out), "run: --port before the first --stream is refused")
out[:] = []
check(cli.main(["run", "--stream", "hands", "--stream", "hands"], out.append) == exitcodes.USAGE_OR_SOURCE and any("once" in l for l in out), "run: the same stream twice is refused")
out[:] = []
check(cli.main(["run", "--stream", "banana"], out.append) == exitcodes.USAGE_OR_SOURCE and any("unknown stream" in l for l in out), "run: an unknown stream is refused")
out[:] = []
empty_models = tmpdir()      # a mutated check must end at "models missing" quickly, never reach a camera
check(cli.main(["run", "--models-dir", empty_models, "--stream", "hands", "--rotate", "90", "--stream", "face"], out.append) == exitcodes.USAGE_OR_SOURCE
      and any("must be the same for every stream" in l for l in out), "run: a source option that differs between streams is refused (before any model is looked at)")
out[:] = []
code_flip = cli.main(["run", "--models-dir", empty_models, "--stream", "hands", "--flip", "--stream", "cutout", "--no-flip"], out.append)
check(code_flip == exitcodes.MODELS_MISSING and not any("must be the same" in l for l in out),
      "run: --flip may differ between streams (it gets as far as the models), the camera is shared and each stream mirrors its own copy")
out[:] = []
check(cli.main(["run", "--stream", "hands", "--port", "7001", "--stream", "face", "--port", "7001"], out.append) == exitcodes.USAGE_OR_SOURCE
      and any("same UDP port (7001)" in l for l in out), "run: two streams on one port are refused before any model loads")
# file check
d = tmpdir()
saved_root = bootstrap.ENGINE_ROOT
try:
    write(os.path.join(d, "a.dll"), b"abc")
    manifest.write(d, "t")
    bootstrap.ENGINE_ROOT = d
    out[:] = []
    check(cli.main(["verify"], out.append) in (exitcodes.OK, exitcodes.MODELS_MISSING) and any("all 1 are as built" in l for l in out), "verify: a folder as built passes")
    write(os.path.join(d, "a.dll"), b"abd")
    out[:] = []
    check(cli.main(["verify"], out.append) == exitcodes.FILES_MODIFIED and any("MODIFIED  a.dll" in l for l in out), "verify: a changed program file is exit 20 and named")
    out[:] = []
    check(cli.main(["hands", "--video", "x.mp4"], out.append) == exitcodes.FILES_MODIFIED, "a stream command refuses to start on a changed program file: exit 20")
    out[:] = []
    code = cli.main(["hands", "--video", "x.mp4", "--no-verify", "--models-dir", os.path.join(d, "none")], out.append)
    check(code == exitcodes.MODELS_MISSING, "--no-verify skips the file check (and the next problem is reported instead)")
    write(os.path.join(d, "a.dll"), b"abc")
    manifest.write(d, "t")
    write(os.path.join(d, "a.dll"), b"abd")
    check(cli._check_files(types.SimpleNamespace(no_verify=False, verify="size"), out.append, d) is None, "--verify size passes a same-size edit (it only looks at sizes)")
    check(cli._check_files(types.SimpleNamespace(no_verify=False, verify="full"), out.append, d) == exitcodes.FILES_MODIFIED, "--verify full catches it")
    check(cli._check_files(types.SimpleNamespace(no_verify=False, verify="off"), out.append, d) is None, "--verify off skips the check")
finally:
    bootstrap.ENGINE_ROOT = saved_root
    shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------------------------------------------------------------------------ real run
print("")
print("A REAL THREE-STREAM RUN, READ BACK OFF THE WIRE (needs the models and a clip)")
clip = os.path.join(os.path.dirname(os.path.dirname(ROOT)), "video", "123.webm")
models_ok = all(s != registry.MISSING for _sp, s, _p in registry.status(os.path.join(ROOT, "models"), deep=False) if _sp.required)
if os.environ.get("VT_SKIP_REAL") == "1":
    print("  SKIP VT_SKIP_REAL=1")
elif not (os.path.isfile(clip) and models_ok):
    print("  SKIP no clip (%s) or the required models are missing" % clip)
else:
    import cutout_wire as CW
    import face_wire as FW
    import hand_wire as HW
    socks = {}
    got = {"hands": [], "face": [], "cutout": []}
    ports = {}
    for name in got:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        s.settimeout(0.25)
        socks[name] = s
        ports[name] = s.getsockname()[1]
    done = threading.Event()

    def collect(name):
        while not done.is_set():
            try:
                got[name].append(socks[name].recvfrom(65536)[0])
            except socket.timeout:
                pass

    threads = [threading.Thread(target=collect, args=(n,), daemon=True) for n in got]
    for t in threads:
        t.start()
    py = sys.executable
    cmd = [py, "-B", "-m", "viitorx_tracking", "run", "--video", clip, "--seconds", "8", "--no-realtime", "--status",
           "--stream", "hands", "--port", str(ports["hands"]), "--stream", "face", "--port", str(ports["face"]),
           "--stream", "cutout", "--port", str(ports["cutout"])]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    time.sleep(0.5)
    done.set()
    for t in threads:
        t.join(2)
    for s in socks.values():
        s.close()
    events = [lifecycle.parse_status_line(l) for l in proc.stdout.splitlines()]
    events = [e for e in events if e]
    check(proc.returncode == 0, "the engine exits 0", proc.stderr.strip().splitlines()[-1][:100] if proc.stderr.strip() else "")
    check([e["event"] for e in events if e["event"] != "heartbeat"] == ["starting", "ready", "stopping"], "starting, ready, stopping on stdout")
    check(len(got["hands"]) > 50 and len(got["face"]) > 50, "hands and face datagrams arrived", "%d and %d" % (len(got["hands"]), len(got["face"])))
    parsed_hands = [HW.parse_message(d) for d in got["hands"]]
    parsed_face = [FW.parse_message(d) for d in got["face"]]
    check(len(set(m["sid"] for m in parsed_hands)) == 1 and len(set(m["sid"] for m in parsed_face)) == 1, "one session id per stream")
    check([m["seq"] for m in parsed_hands] == sorted(m["seq"] for m in parsed_hands), "hands sequence numbers increase")
    check(any(m["n"] > 0 for m in parsed_hands), "hands were found in the clip and the wire parses")
    asm = CW.Assembler()
    frames = [f for f in (asm.add(d) for d in got["cutout"]) if f is not None]
    check(len(frames) > 20 and asm.rejected == {}, "cutout frames reassemble with nothing refused", "%d frames" % len(frames))
    check(len(frames) > 0 and frames[0].mask_w > 0 and frames[0].color_w > 0, "a cutout frame carries its picture and mask", "%dx%d picture, %dx%d mask" % (frames[0].color_w, frames[0].color_h, frames[0].mask_w, frames[0].mask_h) if frames else "")
    check(abs(len(parsed_hands) - len(parsed_face)) <= 2, "hands and face handle the same number of frames (one source, one loop)", "%d / %d" % (len(parsed_hands), len(parsed_face)))

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
