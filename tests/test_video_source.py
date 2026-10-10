#!/usr/bin/env python3
"""F-49 tests for video_udp_sender.py's SOURCE handling and for the supervisor's video command.

What can go wrong with a video source is mostly about the string the operator typed, so that is what
is pinned here, without a model, a GPU or a camera:

  * WHICH THINGS ARE ACCEPTED. A local file, a direct http(s) link, a page. Everything else - file:,
    ftp:, javascript:, an empty string, a path that does not exist - is refused with a message, because
    the value comes from an Inspector field and ends up in a request or a subprocess argument list.
  * A DOWNLOAD IS ATOMIC AND BOUNDED. Driven against a real loopback HTTP server: it is cached, reused,
    never leaves a half-written file the next run would trust, and refuses one that is too big.
  * THE SUPERVISOR FORWARDS ONLY WHAT THE SENDER'S ARGPARSE DEFINES. The supervisor is how Unity
    launches this, and a flag the sender does not know makes Python exit before the clip is opened.
    The test parses the real sender's parser and checks every forwarded flag against it.

Run: .venv\\Scripts\\python.exe tests/test_video_source.py   (exit 0 on pass)
"""
import http.server
import os
import re
import sys
import tempfile
import threading

import time as _time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import sidecar_supervisor as SUP
import video_udp_sender as V

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-62s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-62s %s" % (label, detail))


def refuses(spec):
    try:
        V.classify_source(spec)
    except V.SourceError as e:
        return str(e)
    return None


tmp = tempfile.mkdtemp(prefix="f49_test_")
real_file = os.path.join(tmp, "clip.mp4")
with open(real_file, "wb") as fh:
    fh.write(b"x" * 2048)

print("classify_source")
check(V.classify_source(real_file) == "file", "an existing local path is a file")
check(V.classify_source('"%s"' % real_file) == "file", "surrounding quotes are tolerated")
check(V.classify_source("https://example.com/a/b/clip.mp4") == "direct", "https link to .mp4 is direct")
check(V.classify_source("http://example.com/clip.WEBM?token=1") == "direct",
      "extension is read from the PATH, case-insensitively, ignoring the query")
check(V.classify_source("https://www.youtube.com/watch?v=abc") == "page", "a watch page is a page")
check(V.classify_source("https://example.com/clip.mp4.html") == "page", ".mp4.html is a page, not a file")
for bad in ("", "   ", "file:///C:/Windows/win.ini", "ftp://example.com/clip.mp4",
            "javascript:alert(1)", "C:/definitely/not/here.mp4", "relative/missing.mp4"):
    msg = refuses(bad)
    check(msg is not None, "refuses %r" % bad, msg or "ACCEPTED")
check("not found" in (refuses("C:/definitely/not/here.mp4") or ""),
      "a missing Windows path says 'not found', not 'forbidden scheme'")
check(refuses("https:///no-host.mp4") is not None, "a URL with no host is refused")

print("cache_path_for")
a = V.cache_path_for("https://example.com/a.mp4", tmp)
b = V.cache_path_for("https://example.com/b.mp4", tmp)
check(a != b, "two links never share a cache file")
check(a == V.cache_path_for("https://example.com/a.mp4", tmp), "the same link maps to the same file")
check(a.endswith(".mp4"), "the cache keeps the link's own extension")

print("download_direct, against a loopback HTTP server")
PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + os.urandom(100000)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_address[1]
cache = os.path.join(tmp, "cache")

got = V.download_direct(base + "/clip.mp4", cache)
check(os.path.isfile(got) and open(got, "rb").read() == PAYLOAD, "downloads the exact bytes")
check(not os.path.exists(got + ".part"), "no .part file is left behind")
mtime = os.path.getmtime(got)
again = V.download_direct(base + "/clip.mp4", cache)
check(again == got and os.path.getmtime(got) == mtime, "a second request reuses the cache")

try:
    V.download_direct(base + "/missing.mp4", cache)
    check(False, "a 404 raises")
except V.SourceError as e:
    check(True, "a 404 raises SourceError", str(e))
leftovers = [f for f in os.listdir(cache) if f.endswith(".part")]
check(not leftovers, "a failed download leaves no .part file", str(leftovers))

saved = V.MAX_DOWNLOAD_BYTES
V.MAX_DOWNLOAD_BYTES = 1000
try:
    V.download_direct(base + "/big.mp4", cache)
    check(False, "an oversize download is refused")
except V.SourceError as e:
    check("refusing" in str(e) or "exceeded" in str(e), "an oversize download is refused", str(e))
finally:
    V.MAX_DOWNLOAD_BYTES = saved
check(not [f for f in os.listdir(cache) if f.endswith(".part")], "and leaves no .part file")
server.shutdown()

print("yt-dlp is optional")
saved_find = V.find_ytdlp
V.find_ytdlp = lambda: None
try:
    V.download_page("https://www.youtube.com/watch?v=abc", cache)
    check(False, "a page without yt-dlp raises")
except V.SourceError as e:
    check("yt-dlp" in str(e) and "direct" in str(e), "a page without yt-dlp says what to do instead")
finally:
    V.find_ytdlp = saved_find

print("live sources: classification")
check(V.classify_source("camera:0") == "camera", "camera:0 is a camera")
check(V.classify_source("CAM: 3") == "camera", "the prefix is case-insensitive and tolerates a space")
check(V.classify_source("2") == "camera", "a bare digit is a camera index")
check(V.parse_camera_index("camera:7") == 7, "the index is parsed")
check(V.parse_camera_index("clip.mp4") is None, "a file name is not a camera index")
check("out of range" in (refuses("camera:99") or ""), "camera:99 is refused as out of range")
digit_file = os.path.join(tmp, "7")
with open(digit_file, "wb") as fh:
    fh.write(b"x")
check(V.classify_source(digit_file) == "file", "an existing file wins over a camera index")
check(V.classify_source("rtsp://192.168.1.20:554/live") == "stream", "rtsp:// is a stream")
check(V.classify_source("rtsp://admin:secret@10.0.0.5/ch1") == "stream", "rtsp with credentials is a stream")
check(V.classify_source("http://192.168.1.5:8080/video") == "stream",
      "an http link to a LAN address is a phone stream, not a page")
check(V.classify_source("http://10.0.0.8:4747/video") == "stream", "10.x is LAN")
check(V.classify_source("http://172.20.1.2/video") == "stream", "172.16-31.x is LAN")
check(V.classify_source("http://127.0.0.1:9000/video") == "stream", "loopback is a stream")
check(V.classify_source("http://myphone.local:8080/video") == "stream", ".local is a stream")
check(V.classify_source("http://8.8.8.8/video") == "page", "a PUBLIC address is still a page")
check(V.classify_source("http://192.168.1.5/recordings/clip.mp4") == "direct",
      "a LAN link that names a video FILE is downloaded, not streamed")
check(V.classify_source("stream:https://cam.example.com/live") == "stream", "stream: forces a stream")
check(V.stream_url("stream:https://cam.example.com/live") == "https://cam.example.com/live",
      "and its prefix is stripped before OpenCV sees it")
check(refuses("stream:file:///etc/passwd") is not None, "stream: does not smuggle another scheme")
check(refuses("stream:ftp://x/y") is not None, "stream:ftp is refused")
check(refuses("rtmp://host/live") is not None, "rtmp is not on the allowlist")

print("redact")
check(V.redact("rtsp://admin:s3cret@10.0.0.5/ch1") == "rtsp://***@10.0.0.5/ch1", "credentials are removed")
check("s3cret" not in (refuses("stream:ftp://u:s3cret@x/y") or ""), "and never appear in an error message")
check(V.redact("http://example.com/a?b=c@d") == "http://example.com/a?b=c@d", "an @ in a query is left alone")
check(V.redact(None) == "", "None is tolerated")

print("orient_frame")
img = np.zeros((4, 6, 3), dtype=np.uint8)
img[0, 0] = (255, 0, 0)
check(V.orient_frame(img, 0, False).shape == (4, 6, 3), "no rotation keeps the shape")
check(V.orient_frame(img, 90, False).shape == (6, 4, 3), "90 degrees swaps width and height")
check(tuple(V.orient_frame(img, 90, False)[0, 3]) == (255, 0, 0), "90 degrees is CLOCKWISE (top-left goes top-right)")
check(tuple(V.orient_frame(img, 180, False)[3, 5]) == (255, 0, 0), "180 puts top-left at bottom-right")
check(tuple(V.orient_frame(img, 270, False)[5, 0]) == (255, 0, 0), "270 puts top-left at bottom-left")
check(tuple(V.orient_frame(img, 0, True)[0, 5]) == (255, 0, 0), "flip mirrors left-right")

print("LatestFrameReader (fake capture, no camera)")


class FakeCap:
    """A capture that yields numbered frames, optionally failing after N reads."""
    def __init__(self, fail_after=None):
        self.n = 0
        self.fail_after = fail_after
        self.released = False

    def read(self):
        if self.fail_after is not None and self.n >= self.fail_after:
            return False, None
        self.n += 1
        # ~1 kHz and a 16-bit counter, so the value never wraps inside a test and rises monotonically.
        _time.sleep(0.001)
        return True, np.full((2, 2, 3), self.n, dtype=np.uint16)

    def release(self):
        self.released = True




reader = V.LatestFrameReader(lambda: FakeCap(), name="fake").start()
frame, stamp = reader.read(2.0)
check(frame is not None and stamp > 0, "delivers a frame with its grab time")
_time.sleep(0.2)
newest, _ = reader.read(2.0)
check(int(newest[0, 0, 0]) > int(frame[0, 0, 0]) + 1, "after a pause it returns the NEWEST frame, not the next one",
      "first=%d now=%d" % (frame[0, 0, 0], newest[0, 0, 0]))
check(reader.dropped > 0, "and counts the frames it dropped", "dropped=%d" % reader.dropped)
reader.stop()

opens = []


def flaky_opener():
    cap = FakeCap(fail_after=5 if not opens else None)
    opens.append(cap)
    return cap


sleeps = []
reader2 = V.LatestFrameReader(flaky_opener, name="flaky", sleep=lambda s: (sleeps.append(s), _time.sleep(0.01))[1]).start()
deadline = _time.time() + 3.0
while _time.time() < deadline and len(opens) < 2:
    _time.sleep(0.02)
frame2, _ = reader2.read(2.0)
check(len(opens) >= 2, "a source that stops delivering is reopened", "opens=%d" % len(opens))
check(opens[0].released, "and the dead capture is released")
check(reader2.reconnects >= 1, "the reconnect is counted", "reconnects=%d" % reader2.reconnects)
check(frame2 is not None, "frames flow again after the reconnect")
check(sleeps and sleeps[0] == V.RECONNECT_BACKOFF_SECONDS[0], "the first retry uses the shortest backoff", str(sleeps[:3]))
reader2.stop()

none_calls = []
reader3 = V.LatestFrameReader(lambda: (none_calls.append(1), None)[1], name="absent",
                              sleep=lambda s: _time.sleep(0.005)).start()
frame3, _ = reader3.read(0.3)
check(frame3 is None, "a camera that never opens yields nothing instead of hanging")
check(len(none_calls) > 1, "but keeps trying", "attempts=%d" % len(none_calls))
check(not reader3.connected, "and reports itself disconnected")
reader3.stop()

print("open_stream against a real MJPEG server (the protocol phone IP-camera apps speak)")
import cv2 as _cv2


class MjpegHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            n = 0
            while n < 400:
                canvas = np.full((120, 160, 3), 40, dtype=np.uint8)
                _cv2.rectangle(canvas, (n % 120, 20), (n % 120 + 30, 80), (0, 255, 0), -1)
                ok, jpg = _cv2.imencode(".jpg", canvas)
                data = jpg.tobytes()
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                n += 1
                _time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):
        pass


mjpeg = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MjpegHandler)
threading.Thread(target=mjpeg.serve_forever, daemon=True).start()
mjpeg_url = "http://127.0.0.1:%d/video" % mjpeg.server_address[1]
check(V.classify_source(mjpeg_url) == "stream", "the server's address classifies as a stream", mjpeg_url)
live = V.LatestFrameReader(lambda: V.open_stream(mjpeg_url), name="mjpeg").start()
got_frames = []
t_end = _time.time() + 15.0
while _time.time() < t_end and len(got_frames) < 10:
    f, _ = live.read(2.0)
    if f is not None:
        got_frames.append(f)
check(len(got_frames) >= 10, "ten frames arrive over MJPEG", "got=%d" % len(got_frames))
check(got_frames and got_frames[0].shape == (120, 160, 3), "with the right size", str(got_frames[0].shape if got_frames else None))
live.stop()
mjpeg.shutdown()
check(V.open_stream("http://127.0.0.1:1/video") is None, "an unreachable stream returns None, not an exception")

print("estimate_hip_depth")
import time as _time

import numpy as np
uv = np.zeros((133, 2), dtype=np.float32)
conf = np.zeros(133, dtype=np.float32)
uv[0] = (100, 100)
uv[15] = (100, 500)
uv[16] = (100, 500)
conf[0] = conf[15] = conf[16] = 0.9
d = V.estimate_hip_depth(uv, conf, 0.3, 1.70, 800.0, None)
check(abs(d - 1.70 * 800.0 / 400.0) < 1e-6, "depth = height * fy / span", "%.3f m" % d)
check(V.estimate_hip_depth(uv, conf, 0.3, 1.70, 800.0, (1.6, 3.0)) == 3.0,
      "a clamp band caps a far estimate")
check(V.estimate_hip_depth(uv, conf, 0.3, 1.70, 100.0, (1.6, 3.0)) == 1.6,
      "and floors a near one")
conf[:] = 0.0
check(V.estimate_hip_depth(uv, conf, 0.3, 1.70, 800.0, None) == 2.0,
      "no confident body falls back to 2.0 m rather than dividing by zero")

print("supervisor command for the video sender")
args = SUP.build_arg_parser().parse_args(
    ["--script", os.path.join(os.path.dirname(V.__file__), "video_udp_sender.py"),
     "--video", "https://example.com/clip.mp4", "--video-assume-measured", "--video-loop", "3",
     "--video-depth-range", "1.6", "4.0", "--video-rotate", "90", "--video-flip",
     "--video-camera-size", "1280", "720",
     "--evidence-dir", os.path.join(tmp, "ev"), "--show", "--portrait", "--subpixel-bits", "3"])
sup = SUP.Supervisor(args)
check(sup.sender_kind() == SUP.SENDER_VIDEO, "video_udp_sender.py selects the video command shape")
cmd = sup.build_command()
joined = " ".join(cmd)
check("--video https://example.com/clip.mp4" in joined, "forwards --video", joined)
check("--assume-measured" in cmd and "--loop 3" in joined, "forwards --assume-measured and --loop")
check("--depth-range 1.6 4.0" in joined, "forwards the depth clamp as two numbers")
check("--rotate 90" in joined and "--flip" in cmd, "forwards rotate and flip")
check("--camera-size 1280 720" in joined, "forwards the webcam size as two numbers")
check(SUP.redact("--video rtsp://admin:s3cret@10.0.0.5/ch1") == "--video rtsp://***@10.0.0.5/ch1",
      "the supervisor log redacts stream credentials")
for camera_flag in ("--portrait", "--no-portrait", "--subpixel-bits", "--mono-res", "--rgb-isp",
                    "--ir-dot", "--show", "--seconds", "--max-poses"):
    check(camera_flag not in cmd, "does NOT forward %s" % camera_flag)

# Every forwarded flag must exist in the sender's own argparse, or Python exits before the clip opens.
src = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
defined = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))
forwarded = [t for t in cmd if t.startswith("--")]
unknown = [t for t in forwarded if t not in defined]
check(not unknown, "every forwarded flag is defined by video_udp_sender.py", str(unknown))
check(SUP.SENDER_BY_SCRIPT.get("video_udp_sender.py") == SUP.SENDER_VIDEO,
      "SENDER_BY_SCRIPT maps the sender by basename")

# F-52: --video-max-poses. Unity ALWAYS passes the OAK multi-person --max-poses (default 3), so the video
# sender must never inherit it: only --video-max-poses > 1 may switch multi-person on.
def video_cmd(*extra):
    a = SUP.build_arg_parser().parse_args(
        ["--script", os.path.join(os.path.dirname(V.__file__), "video_udp_sender.py"),
         "--video", "https://example.com/clip.mp4", "--evidence-dir", os.path.join(tmp, "ev3")] + list(extra))
    return SUP.Supervisor(a).build_command()


def max_poses_of(cmd):
    return cmd[cmd.index("--max-poses") + 1] if "--max-poses" in cmd else None


check(max_poses_of(video_cmd()) is None, "F-52: by default the video sender is NOT given --max-poses (single-person path)")
check(max_poses_of(video_cmd("--max-poses", "5")) is None,
      "F-52: the OAK multi-person --max-poses Unity always passes does NOT reach the video sender")
check(max_poses_of(video_cmd("--video-max-poses", "1")) is None, "F-52: --video-max-poses 1 forwards nothing")
check(max_poses_of(video_cmd("--video-max-poses", "0")) is None, "F-52: --video-max-poses 0 forwards nothing")
check(max_poses_of(video_cmd("--video-max-poses", "3")) == "3", "F-52: --video-max-poses 3 -> the sender's --max-poses 3")
check(max_poses_of(video_cmd("--video-max-poses", "3", "--max-poses", "5")) == "3",
      "F-52: the video value wins over the OAK value")
check(max_poses_of(video_cmd("--video-max-poses", "99")) == str(SUP.VIDEO_MAX_PEOPLE),
      "F-52: an absurd count is clamped", "to %d" % SUP.VIDEO_MAX_PEOPLE)
check(video_cmd("--video-max-poses", "3").count("--max-poses") == 1, "F-52: --max-poses appears once")
check(video_cmd("--video-max-poses", "2")[:2] == video_cmd()[:2], "F-52: the command shape is otherwise unchanged")
oak = SUP.build_arg_parser().parse_args(
    ["--script", os.path.join(os.path.dirname(V.__file__), "..", "..", "multiperson_udp_sender.py"),
     "--evidence-dir", os.path.join(tmp, "ev4"), "--video-max-poses", "4", "--max-poses", "2"])
oak_cmd = SUP.Supervisor(oak).build_command()
check(max_poses_of(oak_cmd) == "2" and "--video-max-poses" not in oak_cmd,
      "F-52: the OAK multi-person sender still gets --max-poses from --max-poses, and never the video flag")

args2 = SUP.build_arg_parser().parse_args(
    ["--script", os.path.join(os.path.dirname(V.__file__), "video_udp_sender.py"),
     "--evidence-dir", os.path.join(tmp, "ev2")])
ok, why = SUP.Supervisor(args2)._validate(False)
check(not ok and "--video" in why, "the supervisor refuses a video sender with no --video", why)

# ---- F-52: the heartbeat must survive a long, silent model load ------------------------------------------
print("")
print("load ticker (supervisor kills a child silent for 10 s; the FP32 model alone takes ~14 s to load)")
import contextlib
import io as _io

buf = _io.StringIO()
with contextlib.redirect_stdout(buf):
    with V.LoadTicker("loading RTMW3D ...", interval=0.05):
        _time.sleep(0.4)
    after_exit = buf.getvalue()
    _time.sleep(0.2)
lines = [l for l in buf.getvalue().splitlines() if l.strip()]
check(len(lines) >= 4, "a load that lasts several intervals prints several heartbeat lines", "%d lines in 0.4 s at 0.05 s" % len(lines))
check(buf.getvalue() == after_exit, "nothing is printed after the load has finished")
hb = SUP.parse_heartbeat(buf.getvalue())
check(hb is not None and hb["Frames"] == 0 and hb["Sent"] == 0 and hb["Note"] == "loading RTMW3D ...",
      "the supervisor reads the line as frames=0, sent=0, note 'loading RTMW3D ...' (what the MODEL light keys on)", str(hb))
check(V.HEARTBEAT_SECONDS <= 5.0, "the default interval is well inside the supervisor's 10 s limit", "%.1f s" % V.HEARTBEAT_SECONDS)

buf = _io.StringIO()
raised = False
with contextlib.redirect_stdout(buf):
    try:
        with V.LoadTicker("loading ...", interval=0.05):
            _time.sleep(0.15)
            raise RuntimeError("the load failed")
    except RuntimeError:
        raised = True
    stopped_at = buf.getvalue()
    _time.sleep(0.2)
check(raised, "an error inside the load propagates (the ticker never swallows it)")
check(buf.getvalue() == stopped_at, "...and the ticker has stopped by then, so a dead sender does not keep claiming to be loading")
src_text = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check('with LoadTicker("loading RTMW3D ...")' in src_text and 'with LoadTicker("loading the person detector ...")' in src_text,
      "both blocking loads (pose model, person detector) run under the ticker")

# ---- F-52: a FILE must play at its own speed, or say why it cannot -----------------------------------------
print("")
print("holding a file to its own clock")
P = 1.0 / 30.0
nap, drop = V.pace_decision(10.0, 9.9, 30.0, True)
check(abs(nap - 0.1) < 1e-9 and not drop, "ahead of the clock: sleep until the frame is due", "%.3f s" % nap)
check(V.pace_decision(10.0, 5.0, 30.0, True) == (0.25, False), "a long wait is taken 0.25 s at a time")
check(V.pace_decision(10.0, 10.0 + 0.5 * P, 30.0, True) == (0.0, False), "a little behind: process it")
check(V.pace_decision(10.0, 10.0 + 1.4 * P, 30.0, True) == (0.0, False), "under 1.5 frame periods behind: still process it")
check(V.pace_decision(10.0, 10.0 + 1.6 * P, 30.0, True) == (0.0, True), "more than 1.5 frame periods behind: drop it")
check(V.pace_decision(10.0, 11.0, 30.0, False) == (0.0, False), "--no-drop-late never drops, however far behind")
check(V.pace_decision(10.0, 10.0 + 1.6 * P, 60.0, True) == (0.0, True) and V.pace_decision(10.0, 10.0 + 1.6 * P, 24.0, True) == (0.0, False),
      "the tolerance is in FRAME PERIODS of the clip's own rate, not a fixed time")
check(V.can_catch_up_by_dropping(True, 2, 0.0, 0, 30.0) and not V.can_catch_up_by_dropping(True, 0, 0.0, 0, 30.0),
      "prefetching: drop only when the next frame is already decoded and waiting")
check(V.can_catch_up_by_dropping(False, 0, 0.010, 1, 30.0) and not V.can_catch_up_by_dropping(False, 0, 0.040, 1, 30.0)
      and not V.can_catch_up_by_dropping(False, 0, 0.0, 0, 30.0),
      "plain reader: drop only when decoding is clearly cheaper than a frame period, and never before it is measured")


def simulate_file(decode_s, pose_s, frames=900, fps=30.0, drop_late=True, prefetch=True):
    """The pacing loop on a simulated clock: frame k can be read at k*decode_s (a prefetching reader decodes
    ahead from t=0; a plain reader decodes in the loop). Returns (frames_posed, clip_speed)."""
    period = 1.0 / fps
    t = 0.0
    posed = 0
    for k in range(frames):
        if prefetch:
            available = (k + 1) * decode_s
            t = max(t, available)
            # frames already decoded beyond this one (the reader is at most 6 ahead)
            pending = min(6, max(0, int(t / decode_s) - (k + 1)))
            reads, read_s = 1, 0.0 if pending > 0 else decode_s
        else:
            t += decode_s
            pending, reads, read_s = 0, 1, decode_s
        due = k * period
        can_drop = V.can_catch_up_by_dropping(prefetch, pending, read_s, reads, fps)
        nap, drop = V.pace_decision(due, t, fps, drop_late and can_drop)
        t += nap
        if drop:
            continue
        t += pose_s
        posed += 1
    return posed, (frames * period) / t


posed, speed = simulate_file(0.005, 0.050)
check(speed > 0.95 and posed > 0.4 * 900, "posing is the bottleneck (50 ms a frame, 5 ms to decode): dropping holds real time",
      "speed %.2fx, %d of 900 posed" % (speed, posed))
posed_nd, speed_nd = simulate_file(0.005, 0.050, drop_late=False)
check(speed_nd < 0.7, "...which is what --no-drop-late gives up: slow motion", "speed %.2fx" % speed_nd)
posed, speed = simulate_file(0.041, 0.035)
check(posed > 0.9 * 900, "DECODING is the bottleneck (41 ms to decode, 35 ms to pose): frames are NOT thrown away for nothing",
      "%d of 900 posed (the earlier rule posed 13 of 1,934)" % posed)
check(0.7 < speed < 0.85, "...and the clip plays at the decoder's speed, honestly", "speed %.2fx" % speed)
posed, speed = simulate_file(0.004, 0.020)
check(posed >= 890 and speed > 0.98, "a machine that keeps up drops nothing and runs at real time", "speed %.2fx, %d posed" % (speed, posed))

src_text = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check('"--drop-late"' in src_text and "BooleanOptionalAction, default=True" in src_text, "--drop-late exists and is on by default")
check("speed=%.2f late=%d" in src_text, "the heartbeat reports the clip's speed and the dropped-frame count")
check("can_catch_up_by_dropping(" in src_text and "a.drop_late and can_catch_up" in src_text,
      "the loop only drops when dropping can actually catch up")
check("if loop_frame == 2:" in src_text and "t_loop = time.time() - (loop_frame - 1) / src_fps" in src_text,
      "the clock is re-anchored after the first frame, so the one-off model warm-up is not mistaken for lag")
hb = SUP.parse_heartbeat("[vid] frames=300 sent=250 skipped=2 fps~19.0 replay=0 speed=0.77 late=40 people=2 tracks=2 dets=2 detect~9.6ms pose~12.1ms ")
check(hb is not None and hb["Frames"] == 300 and hb["Sent"] == 250 and hb["Note"] == "",
      "the supervisor still parses the heartbeat with the new fields, and its note stays empty", str(hb))

# ---- F-52: decoding a big FILE on its own thread --------------------------------------------------------
print("")
print("prefetching file capture")
import cv2


class FakeCap:
    """A VideoCapture stand-in: scripted frames, optional per-read delay, optional decode error."""

    def __init__(self, n, delay=0.0, fail_at=None):
        self.n = n
        self.delay = delay
        self.fail_at = fail_at
        self.reads = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FPS: 30.0, cv2.CAP_PROP_FRAME_WIDTH: 3840.0, cv2.CAP_PROP_FRAME_HEIGHT: 2160.0}.get(prop, 0.0)

    def read(self):
        if self.delay:
            _time.sleep(self.delay)
        if self.fail_at is not None and self.reads == self.fail_at:
            raise cv2.error("scripted decode failure")
        if self.reads >= self.n:
            return False, None
        self.reads += 1
        return True, np.full((2, 2, 3), self.reads, dtype=np.uint8)

    def release(self):
        self.released = True


pf = V.PrefetchCapture(FakeCap(8))
got = []
while True:
    ok, frame = pf.read()
    if not ok:
        break
    got.append(int(frame[0, 0, 0]))
check(got == list(range(1, 9)), "every frame arrives, in decode order", str(got))
check(pf.read() == (False, None) and pf.read() == (False, None), "after the end it keeps answering (False, None), never blocks")
pf.release()

fast = FakeCap(100)
pf = V.PrefetchCapture(fast, depth=3)
_time.sleep(0.4)
check(fast.reads <= 3 + 1, "decoding ahead is bounded by the queue depth", "%d frames read with nobody consuming" % fast.reads)
t0 = _time.time()
pf.release()
check(_time.time() - t0 < 2.0 and fast.released, "release() with a full queue stops promptly and releases the capture",
      "%.2f s" % (_time.time() - t0))
check(not pf._thread.is_alive(), "the decode thread is gone after release()")

pf = V.PrefetchCapture(FakeCap(100), depth=4)
_time.sleep(0.3)
check(pf.pending() == 4, "PrefetchCapture.pending() reports how many decoded frames are waiting", str(pf.pending()))
pf.read()
_time.sleep(0.2)
check(pf.pending() == 4, "...and refills as frames are taken")
pf.release()

N_FRAMES, STEP = 15, 0.02
t0 = _time.time()
cap_serial = FakeCap(N_FRAMES, delay=STEP)
while True:
    ok, f = cap_serial.read()
    if not ok:
        break
    _time.sleep(STEP)             # "inference"
serial = _time.time() - t0
t0 = _time.time()
pf = V.PrefetchCapture(FakeCap(N_FRAMES, delay=STEP))
while True:
    ok, f = pf.read()
    if not ok:
        break
    _time.sleep(STEP)             # the same "inference"
overlapped = _time.time() - t0
pf.release()
check(overlapped < 0.8 * serial, "decode and inference overlap: the pace is the slower of the two, not their sum",
      "serial %.2f s -> overlapped %.2f s" % (serial, overlapped))

escaped = []
saved_hook = threading.excepthook
threading.excepthook = lambda args: escaped.append(args.exc_type.__name__)
pf = V.PrefetchCapture(FakeCap(10, fail_at=4))
got = []
while True:
    ok, frame = pf.read()
    if not ok:
        break
    got.append(int(frame[0, 0, 0]))
_time.sleep(0.2)
threading.excepthook = saved_hook
check(got == [1, 2, 3, 4], "a decode error ends the clip cleanly after the frames before it, with no hang", str(got))
check(escaped == [], "...and the error does not escape the decode thread as an unhandled exception", str(escaped))
pf.release()

fc = FakeCap(1)
pf = V.PrefetchCapture(fc)
fc.get = lambda prop: -1.0          # a VideoCapture must not be queried while another thread reads it
check(pf.get(cv2.CAP_PROP_FPS) == 30.0 and pf.get(cv2.CAP_PROP_FRAME_WIDTH) == 3840.0 and pf.get(12345) == 0.0
      and pf.isOpened(), "the properties the sender reads are captured up front (never re-queried); unknown ones are 0.0")
pf.release()

vid = os.path.join(tmp, "tiny.avi")
vw = cv2.VideoWriter(vid, cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (320, 240))
for i in range(5):
    vw.write(np.full((240, 320, 3), 40 * i, dtype=np.uint8))
vw.release()
c_small = V.open_file_capture(vid, None)
check(c_small is not None and not isinstance(c_small, V.PrefetchCapture),
      "automatic: a small file is read directly, exactly as before (no thread)")
c_small.release()
saved = V.PREFETCH_PIXELS
V.PREFETCH_PIXELS = 1000
c_auto = V.open_file_capture(vid, None)
check(isinstance(c_auto, V.PrefetchCapture), "automatic: a file over the pixel threshold is prefetched")
frames_read = 0
while c_auto.read()[0]:
    frames_read += 1
check(frames_read == 5, "...and a real file is read through it frame for frame", str(frames_read))
c_auto.release()
V.PREFETCH_PIXELS = saved
c_forced = V.open_file_capture(vid, True)
check(isinstance(c_forced, V.PrefetchCapture), "--prefetch forces it on a small file")
c_forced.release()
c_off = V.open_file_capture(vid, False)
check(c_off is not None and not isinstance(c_off, V.PrefetchCapture), "--no-prefetch forces it off")
c_off.release()
check(V.open_file_capture(os.path.join(tmp, "does_not_exist.avi"), None) is None, "an unopenable file gives None, not an exception")
check(V.PREFETCH_PIXELS == 2500000 and 1920 * 1080 < V.PREFETCH_PIXELS < 3840 * 2160,
      "the automatic threshold sits between 1080p and 4K")
src_text = open(V.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
check('"--prefetch"' in src_text and "BooleanOptionalAction, default=None" in src_text, "--prefetch exists and defaults to automatic")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
