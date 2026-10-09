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

print("estimate_hip_depth")
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
     "--video-depth-range", "1.6", "4.0",
     "--evidence-dir", os.path.join(tmp, "ev"), "--show", "--portrait", "--subpixel-bits", "3"])
sup = SUP.Supervisor(args)
check(sup.sender_kind() == SUP.SENDER_VIDEO, "video_udp_sender.py selects the video command shape")
cmd = sup.build_command()
joined = " ".join(cmd)
check("--video https://example.com/clip.mp4" in joined, "forwards --video", joined)
check("--assume-measured" in cmd and "--loop 3" in joined, "forwards --assume-measured and --loop")
check("--depth-range 1.6 4.0" in joined, "forwards the depth clamp as two numbers")
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

args2 = SUP.build_arg_parser().parse_args(
    ["--script", os.path.join(os.path.dirname(V.__file__), "video_udp_sender.py"),
     "--evidence-dir", os.path.join(tmp, "ev2")])
ok, why = SUP.Supervisor(args2)._validate(False)
check(not ok and "--video" in why, "the supervisor refuses a video sender with no --video", why)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
