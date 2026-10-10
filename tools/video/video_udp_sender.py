#!/usr/bin/env python3
"""F-49 - THE VIDEO SENDER: run the REAL tracking pipeline on a video, a phone or a webcam instead of
the OAK-D feed.

WHY THIS EXISTS. Everything downstream of the sidecar - the pose buffer, the F-48 operating volume,
the humanize layer, every experience scene, the skeleton show and the VRM avatar - only ever sees a UDP
datagram. So "test this against a video" means producing the SAME datagram from another source, and
letting Unity run completely unchanged. Unity launches this through the supervisor exactly as it launches
the OAK-D senders (AppBootstrap -> "Video Test Source" fields), so the scene, the restart watchdog and
the readiness contract are the production ones.

It is f23_video_to_unity.py grown into something Unity can own: a source that may be a URL, a webcam or a
phone, a loop that never ends, the supervisor's readiness markers, and a heartbeat that keeps talking
while it downloads or waits for a camera. f23 stays as the evidence tool it was; nothing here changes it.

WHAT IS REAL
  * RTMW3D, the production model, at the production confidence gate, with the production crop loop
  * build_body_landmarks / build_hand / build_joint_states - the PRODUCTION builders, imported
  * P1-1's SkeletonTracker for the per-joint trust states
  * the wire shape and the UDP port - the production contract

WHAT IS NOT, and the whole caveat
  * NO STEREO DEPTH. One camera. Every joint's depth is the model's own monocular z against an
    ASSUMED hip plane, so `src` is 0 for every joint and Unity's TRUST HUD reads 0/33 MEASURED. That is
    the honest answer, and it has a consequence worth knowing: the F-48 calibration learns only from
    stereo-measured joints, so on this source it never becomes ready and the humanize layer passes the
    tracker's own bone lengths through. --assume-measured relabels `src` to 1 so the calibration path
    can be exercised; it changes the LABEL only, never a coordinate, and the HUD will then read
    33/33 MEASURED about a video. Do not quote a recording made with it as evidence about the sensor.
  * THE HIP DEPTH IS ESTIMATED, from how tall the subject looks in the frame against --subject-height
    and --hfov. It scales the scene; it does not invent articulation. It also decides whether the
    F-48 operating volume says TOO CLOSE: a close-up clip legitimately reads near. Change
    --subject-height or --depth-range to move a clip in or out of the volume.
  * MULTI-PERSON IS OPT-IN (F-52, --max-poses 2 or more). By default this is the single-person
    pipeline: ONE crop, no detector, and with several people in frame it follows whoever the crop starts
    on. --max-poses N runs a host-side YOLOX-s person detector and the F-32 tracker in front of the
    same pose model (host_person_detector.py, host_multiperson.py); see that module for what each
    person gets and what it does not. The OAK-D's own VPU detector is still the production path for a
    real camera; this is for a phone, a webcam or a video.
  * F-21 OWNERSHIP IS OFF by default (--ownership to enable). A walking subject in a clip is exactly
    the case ADR-083 says F-21 mishandles, and a video has no second person to defend against.

SOURCES (--video)
  a local path                        used as is
  a direct http(s) link to a file     downloaded once into --cache-dir (.mp4 .webm .mov .mkv .avi .m4v)
  any other http(s) page              handed to yt-dlp, if one is installed (it is NOT in the venv by
                                      default; install it deliberately - see README)
  camera:N   (or a bare 0-9)          a webcam by index. A phone shows up here when an app such as
                                      DroidCam, Iriun or Camo exposes it as a Windows webcam. Run
                                      --list-cameras to see which indices open.
  rtsp://...  stream:<url>            a LIVE stream: an RTSP camera, or a phone "IP camera" app serving
  http://192.168.x.x:8080/video       MJPEG. An http link to a private/LAN address is read as a stream,
                                      not a page. Credentials in a link are never printed.
  http://host/viewer?room=<id>        F-50: a LAN Camera Streaming viewer link (or lan:<url>). The phone's
                                      browser sender page streams over WebRTC and this process joins the
                                      room as the viewer (lan_camera.py). The access PIN is NOT part of
                                      the link: it comes from the CAMERA_PIN environment variable.
Only http, https, rtsp and existing local files are accepted. A URL is never passed to a shell.

LIVE SOURCES (camera, stream and LAN camera) differ from a file in three ways, all deliberate: the
newest frame is always the one processed (a reader thread drops what inference cannot keep up with, so
latency cannot pile up behind a slow GPU), there is no pacing because the camera is the clock, and a lost
source is reopened with backoff instead of treated as "the clip ended".

    .venv\\Scripts\\python.exe tools/video/video_udp_sender.py --video ..\\..\\video\\SR7.mp4
    .venv\\Scripts\\python.exe tools/video/video_udp_sender.py --video camera:0 --rotate 90
    set CAMERA_PIN=123456 && .venv\\Scripts\\python.exe tools/video/video_udp_sender.py --video http://192.168.1.5:3000/viewer?room=room-abc123
    .venv\\Scripts\\python.exe tools/video/video_udp_sender.py --list-cameras
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
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import cv2
import numpy as np

import rtmw3d_pose as R
import smoothing
import wholebody_udp_sender as W
import joint_tracker as JT
import target_ownership as TO
import lan_camera as LAN   # F-50: imports nothing heavy; aiortc & co load only when a reader starts

# F-20A: a new sid is a new producer session. Stamped once per process, like the real senders.
SESSION_ID = "vd%09x" % (uuid.uuid4().int & 0xFFFFFFFFF)

#: File extensions a direct link may carry. Anything else is treated as a PAGE and goes to yt-dlp,
#: because fetching an HTML page and handing it to a video decoder fails in a confusing way.
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")

#: Hard ceiling on one download, bytes. A link that streams forever must not fill the disk of a machine
#: that is also running a GPU model. 2 GiB is far above any test clip this was built for.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

#: Seconds without a byte before a download is abandoned. The supervisor's heartbeat is separate.
DOWNLOAD_TIMEOUT_SECONDS = 20

#: The supervisor kills a child whose stdout stops growing for 10 s (--heartbeat-timeout), and it only
#: calls the child READY once it has seen `frames=`. So a download, a model load, a long skip and a camera
#: that has not delivered yet all have to keep printing; this is how often. Well inside the 10 s limit.
HEARTBEAT_SECONDS = 2.0

#: Highest camera index accepted or probed. Windows rarely exposes more than a handful.
MAX_CAMERA_INDEX = 15

#: Schemes a live stream may use. rtsp is what IP cameras speak; http(s) is what phone "IP camera" apps
#: serve (MJPEG). Nothing else is handed to OpenCV's FFmpeg backend, which will happily open file: and
#: others if asked.
STREAM_SCHEMES = ("rtsp", "rtsps", "http", "https")

#: Reconnect backoff for a live source that stops delivering, seconds. Same shape as the supervisor's.
RECONNECT_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0)

USER_AGENT = "viitorx-video-sender/1.0"

KIND_FILE = "file"
KIND_DIRECT = "direct"
KIND_PAGE = "page"
KIND_CAMERA = "camera"
KIND_STREAM = "stream"
KIND_LAN = "lan"   # F-50: a LAN Camera Streaming viewer link, received over WebRTC
LIVE_KINDS = (KIND_CAMERA, KIND_STREAM, KIND_LAN)


class SourceError(Exception):
    """The --video argument cannot be turned into something playable."""


def redact(text):
    """The same text with any user:password@ removed from every URL in it. Used for EVERYTHING printed,
    because an IP camera link commonly carries its credentials and these logs get shared."""
    return re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1***@", text or "")


def _is_lan_host(host):
    """True for a loopback, private or link-local address, or a .local name. A phone app serves its
    stream from one of these, and no public web page does - which is the whole basis for reading
    `http://192.168.1.5:8080/video` as a camera rather than as a page for yt-dlp."""
    if not host:
        return False
    h = host.strip("[]").lower()
    if h == "localhost" or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def parse_camera_index(text):
    """The camera index named by `camera:N` or a bare integer, or None when `text` is neither."""
    t = text.strip()
    m = re.match(r"(?i)^(?:camera|cam):\s*(\d+)$", t)
    if m is None and re.match(r"^\d{1,2}$", t):
        m = re.match(r"^(\d{1,2})$", t)
    if m is None:
        return None
    idx = int(m.group(1))
    if idx > MAX_CAMERA_INDEX:
        raise SourceError("camera index %d is out of range (0-%d)" % (idx, MAX_CAMERA_INDEX))
    return idx


def classify_source(spec):
    """What --video names: 'file', 'direct' (a link to a video file), 'page', 'camera', 'stream' or
    'lan' (a LAN Camera Streaming viewer link, F-50).

    Raises SourceError for anything else - an empty string, a missing path, or a scheme that is not on
    the allowlist. Refusing `file:`, `ftp:` and friends here is deliberate: this value comes from an
    Inspector field and ends up in a request, a subprocess argument list or OpenCV's FFmpeg backend, so
    it is validated against an allowlist rather than trusted.
    """
    text = (spec or "").strip().strip('"')
    if not text:
        raise SourceError("no video given")
    # An existing file always wins, so a file that happens to be called "0" is still a file.
    if os.path.isfile(text):
        return KIND_FILE
    if parse_camera_index(text) is not None:
        return KIND_CAMERA
    if text.lower().startswith("stream:"):
        inner = text[len("stream:"):].strip()
        parsed = urllib.parse.urlparse(inner)
        if parsed.scheme.lower() not in STREAM_SCHEMES or not parsed.netloc:
            raise SourceError("stream: needs an rtsp:// or http(s):// link, got '%s'" % redact(inner))
        return KIND_STREAM
    # F-50: asked BEFORE the generic http branch below. A viewer link is an http link to a LAN address
    # or a public tunnel, which that branch would read as an MJPEG stream or as a page for yt-dlp -
    # both wrong, and both fail in a way that does not mention the real cause.
    if LAN.is_lan_camera_link(text):
        try:
            LAN.parse_lan_url(text)
        except LAN.LanCameraError as e:
            raise SourceError(str(e))
        return KIND_LAN
    parsed = urllib.parse.urlparse(text)
    scheme = parsed.scheme.lower()
    if scheme in ("rtsp", "rtsps") and parsed.netloc:
        return KIND_STREAM
    if scheme in ("http", "https") and parsed.netloc:
        ext = os.path.splitext(urllib.parse.unquote(parsed.path))[1].lower()
        if ext in VIDEO_EXTENSIONS:
            return KIND_DIRECT
        if _is_lan_host(parsed.hostname):
            return KIND_STREAM
        return KIND_PAGE
    if scheme and len(scheme) > 1:
        # A one-letter "scheme" is a Windows drive ("C:\\..."), which fell through isfile() because the
        # file does not exist - that is a missing file, not a forbidden URL, and should say so.
        raise SourceError("only http(s), rtsp links, camera:N and existing local files are accepted, "
                          "not '%s:'" % scheme)
    raise SourceError("file not found: %s" % text)


def stream_url(spec):
    """The URL OpenCV should open for a KIND_STREAM spec."""
    text = spec.strip().strip('"')
    if text.lower().startswith("stream:"):
        return text[len("stream:"):].strip()
    return text


def cache_path_for(url, cache_dir):
    """Where a direct link is cached: a hash of the URL plus its own extension, so the same link is
    fetched once and two links never collide on a shared file name."""
    parsed = urllib.parse.urlparse(url)
    ext = os.path.splitext(urllib.parse.unquote(parsed.path))[1].lower() or ".mp4"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, "dl_%s%s" % (digest, ext))


def download_direct(url, cache_dir):
    """Fetch a direct link into the cache and return the local path. Reuses a finished download.
    Streams to a .part file and renames on success, so an interrupted run never leaves a truncated
    file that the next run would trust."""
    os.makedirs(cache_dir, exist_ok=True)
    target = cache_path_for(url, cache_dir)
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        print("[vid] cached: %s" % target, flush=True)
        return target
    part = target + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise SourceError("refusing a %.1f GiB download (limit %.1f GiB)"
                                  % (total / 2.0 ** 30, MAX_DOWNLOAD_BYTES / 2.0 ** 30))
            got = 0
            last_print = 0.0
            with open(part, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_DOWNLOAD_BYTES:
                        raise SourceError("download exceeded %.1f GiB; abandoned"
                                          % (MAX_DOWNLOAD_BYTES / 2.0 ** 30))
                    out.write(chunk)
                    now = time.time()
                    if now - last_print >= HEARTBEAT_SECONDS:
                        last_print = now
                        print("[vid] frames=0 sent=0 downloading %.1f%s MB"
                              % (got / 1048576.0,
                                 (" of %.1f" % (total / 1048576.0)) if total else ""), flush=True)
    except (urllib.error.URLError, OSError) as e:
        if os.path.isfile(part):
            os.remove(part)
        raise SourceError("download failed: %s" % redact(str(e)))
    except SourceError:
        if os.path.isfile(part):
            os.remove(part)
        raise
    os.replace(part, target)
    print("[vid] downloaded %.1f MB -> %s" % (os.path.getsize(target) / 1048576.0, target), flush=True)
    return target


def find_ytdlp():
    """argv prefix for yt-dlp, or None. Prefers the module in THIS interpreter (the venv), then an
    executable on PATH. It is optional on purpose: the venv does not ship it, and installing packages
    into the sidecar venv is a deliberate act (pip freeze first), not something a sender does."""
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    return [exe] if exe else None


def download_page(url, cache_dir):
    """Resolve a page URL (YouTube and the like) to a local file with yt-dlp."""
    base = find_ytdlp()
    if base is None:
        raise SourceError("this link is a web page, not a video file, and yt-dlp is not installed. "
                          "Use a direct .mp4/.webm link or a local path, install yt-dlp into "
                          "python-sidecar~/.venv (snapshot `pip freeze` first), or - if this is a phone "
                          "or IP camera - use camera:N or stream:<url>.")
    os.makedirs(cache_dir, exist_ok=True)
    cmd = base + ["--no-playlist", "--max-filesize", "2G",
                  "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
                  "-o", os.path.join(cache_dir, "yt_%(id)s.%(ext)s"),
                  "--print", "after_move:filepath", "--no-simulate", "--newline", url]
    print("[vid] frames=0 sent=0 resolving page with yt-dlp", flush=True)
    # argv list, no shell: the URL cannot become a command.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, encoding="utf-8", errors="replace")
    last = ""
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            last = line
            # Prefixed with the marker so the supervisor sees growth AND counts it as a frames= line.
            print("[vid] frames=0 sent=0 yt-dlp: %s" % redact(line), flush=True)
    rc = proc.wait()
    if rc != 0 or not os.path.isfile(last):
        raise SourceError("yt-dlp failed (rc=%d)" % rc)
    return last


# ---- live sources -----------------------------------------------------------------------------------

def open_camera(index, width, height, fps):
    """Open a webcam by index, trying the Windows backends in the order that works for phones-as-webcams.

    DirectShow first: it opens quickly and honours a requested size and MJPG, which is what lets a
    webcam app deliver 30 fps at 720p instead of a slow raw stream. Media Foundation and then OpenCV's
    own default follow. A backend that cannot open a device by index can raise, not just return False
    (a virtual camera driver does), so each attempt is guarded. Returns an opened capture or None.
    """
    for backend in (getattr(cv2, "CAP_DSHOW", 0), getattr(cv2, "CAP_MSMF", 0), 0):
        try:
            cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
        except cv2.error:
            continue
        if cap is not None and cap.isOpened():
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                cap.set(cv2.CAP_PROP_FPS, fps)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except cv2.error:
                pass
            return cap
        if cap is not None:
            cap.release()
    return None


def open_stream(url):
    """Open a live stream URL, or None. OpenCV's FFmpeg backend does the work; the scheme allowlist has
    already been applied by classify_source."""
    try:
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    except cv2.error:
        return None
    if cap is not None and cap.isOpened():
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:
            pass
        return cap
    if cap is not None:
        cap.release()
    return None


class LatestFrameReader:
    """Pulls frames from a live source on its own thread and hands the consumer only the NEWEST.

    WHY. A camera or a stream produces frames at its own rate and does not wait. If the consumer
    (RTMW3D at ~30 ms a frame) reads them in order from OpenCV's queue, any moment it runs slower than
    the source adds a frame of lag that never drains: a phone that is 100 ms behind at the start is 2 s
    behind five minutes later. Reading on a thread and keeping only the latest bounds the lag to one
    frame, and counts what was dropped so that is visible rather than silent.

    It also owns RECONNECTION: a source that stops delivering is released and reopened with backoff,
    forever, because a phone that drops off Wi-Fi comes back and nothing should need restarting.

    `opener` is a zero-argument callable returning an opened capture (anything with read() and
    release()) or None. Taking a callable rather than a URL is what lets this be tested without a
    camera.
    """

    def __init__(self, opener, name="source", clock=time.time, sleep=time.sleep):
        self._opener = opener
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._cond = threading.Condition()
        self._frame = None
        self._stamp = 0.0
        self._counter = 0
        self._consumed = 0
        self._stop = False
        self._thread = None
        self.frames_read = 0
        self.dropped = 0
        self.reconnects = 0
        self.connected = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name="reader-" + self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _run(self):
        cap = None
        failures = 0
        while True:
            with self._cond:
                if self._stop:
                    break
            if cap is None:
                cap = self._opener()
                if cap is None:
                    self.connected = False
                    wait = RECONNECT_BACKOFF_SECONDS[min(failures, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                    failures += 1
                    self._sleep(wait)
                    continue
                if failures > 0:
                    self.reconnects += 1
                self.connected = True
                failures = 0
            try:
                ok, frame = cap.read()
            except cv2.error:
                ok, frame = False, None
            if not ok or frame is None:
                try:
                    cap.release()
                except cv2.error:
                    pass
                cap = None
                self.connected = False
                failures += 1
                self._sleep(RECONNECT_BACKOFF_SECONDS[min(failures - 1, len(RECONNECT_BACKOFF_SECONDS) - 1)])
                continue
            with self._cond:
                self._frame = frame
                self._stamp = self._clock()
                self._counter += 1
                self.frames_read += 1
                self._cond.notify_all()
        if cap is not None:
            try:
                cap.release()
            except cv2.error:
                pass

    def read(self, timeout):
        """(frame, grab_time) for the newest frame not yet returned, or (None, 0.0) after `timeout`
        seconds with nothing new. Frames that arrived and were overwritten before this call are counted
        in `dropped`."""
        deadline = self._clock() + timeout
        with self._cond:
            while self._counter == self._consumed and not self._stop:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None, 0.0
                self._cond.wait(remaining)
            if self._counter == self._consumed:
                return None, 0.0
            self.dropped += self._counter - self._consumed - 1
            self._consumed = self._counter
            return self._frame, self._stamp


def estimate_hip_depth(uv, conf, conf_gate, subject_height_m, fy, depth_range):
    """Hip depth in metres from how tall the subject looks. Same rule as f23, plus an optional clamp.

    The span is nose-to-ankle in pixels; depth = height * fy / span. Below 40 px the span is noise and
    2.0 m is used. The clamp (min, max), when given, keeps a clip inside a chosen band - for example the
    F-48 operating volume - at the cost of the avatar's apparent size being less faithful.
    """
    ys = [uv[i, 1] for i in (0, 15, 16) if conf[i] >= conf_gate]
    span_px = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
    hip_z = (subject_height_m * fy / span_px) if span_px > 40 else 2.0
    lo, hi = (0.6, 6.0) if depth_range is None else depth_range
    return min(max(hip_z, lo), hi)


def orient_frame(frame, rotate_degrees, flip_horizontal):
    """Rotate (0/90/180/270, clockwise) and optionally mirror a frame BEFORE inference.

    Phone apps deliver a portrait phone as a sideways frame, and a front camera as a mirror image that
    swaps the subject's left and right. Both have to be undone in the image, because the model reads
    the image: correcting afterwards would leave it looking at a person lying down.
    """
    if rotate_degrees == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate_degrees == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotate_degrees == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if flip_horizontal:
        frame = cv2.flip(frame, 1)
    return frame


#: A FILE frame this many frame periods behind its due time is dropped instead of processed. 1.5 keeps the
#: skeleton within ~50 ms of the picture at 30 fps while still posing most frames; a larger value lets the
#: lag (and so the visible offset against the video) grow before anything is done about it.
LATE_DROP_PERIODS = 1.5


def pace_decision(due, now, src_fps, drop_late):
    """What to do with a FILE frame that was due at `due` (seconds) when it is `now`: (sleep_seconds, drop).

    Ahead of the clip's clock: sleep (at most 0.25 s at a time, so a stop request is noticed).
    Behind it by more than LATE_DROP_PERIODS frame periods and `drop_late` is on: drop the frame, so the
    clip's CLOCK is held and the skeleton stays in step with the picture, at a lower frame rate.
    Otherwise process it.

    WHY. Without this a file is processed frame by frame no matter how long that takes, so a clip that
    needs 50 ms a frame against a 33 ms period plays at 0.65x: measured live in Unity on a 4K two-person
    clip, 18.6 fps against 29.97, so after 10 s of real time the skeleton was ~4 s behind the video it
    was meant to mirror. Dropping late frames makes the clip run at its own speed whenever the machine
    can decode fast enough, and costs nothing when it already keeps up.
    """
    wait = due - now
    if wait > 0:
        return min(wait, 0.25), False
    if drop_late and -wait > LATE_DROP_PERIODS / src_fps:
        return 0.0, True
    return 0.0, False


def can_catch_up_by_dropping(prefetching, pending, read_s, reads, src_fps):
    """Is dropping a late FILE frame worth doing right now?

    Dropping saves the time spent POSING a frame, never the time spent DECODING it. So it can only help when
    the next frame is already available: with a prefetching reader that is `pending > 0`; with a plain
    reader (decode on the loop's own thread) it is when decoding is clearly cheaper than a frame period.
    When decoding is the bottleneck (a 4K file) every frame arrives late, a rule that drops late frames
    drops them ALL, and nothing is posed - measured: 13 poses out of 1,934 - so the answer is no and the
    clip plays at the decoder's speed instead.
    """
    if prefetching:
        return pending > 0
    return reads > 0 and (read_s / reads) < 0.9 / src_fps


class LoadTicker:
    """Keeps the supervisor's heartbeat fed while a BLOCKING model load runs.

    The supervisor kills a child whose stdout has not grown for 10 s. The loads here are one blocking call
    each and print nothing while they run - and they are not short: ADR-082 measured the FP32 pose model
    at 13.8 s to become ready (FP16 6.8 s), and the multi-person path adds the detector. Observed live
    from Unity: `HeartbeatTimeout` at 11.2 s into the first launch, a kill, a backoff, and a second launch
    that worked only because the files were now in the OS cache. So the same `frames=0 sent=0 <note>` line
    the sender already prints once is repeated every HEARTBEAT_SECONDS for as long as the load lasts.
    """

    def __init__(self, note, interval=HEARTBEAT_SECONDS):
        self._note = note
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="load-ticker", daemon=True)

    def _run(self):
        while not self._stop.wait(self._interval):
            print("[vid] frames=0 sent=0 %s" % self._note, flush=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=1.0)
        return False


#: Frames a prefetching file reader may decode ahead. A 4K frame is ~25 MB, so 6 is ~150 MB at the worst
#: and enough to ride out a slow frame: the 4K VP9 clip measured a median 33 ms to decode but spikes to
#: ~150 ms, which is several frame periods.
PREFETCH_FRAMES = 6

#: Above this many pixels a FILE is decoded on its own thread unless --no-prefetch. 2.5 MP sits between
#: 1080p-class video (2.07 MP, 13 ms a frame measured) and 4K (8.29 MP, 33 ms), where decoding stops
#: being a rounding error next to inference and starts to serialise with it.
PREFETCH_PIXELS = 2500000


class PrefetchCapture:
    """A cv2.VideoCapture for a FILE that decodes ahead on its own thread.

    WHY. The file loop decodes, then infers, then decodes: the two add. A 4K clip decodes in ~33 ms
    (mean 41 ms with the key-frame spikes) and a multi-person solve takes ~30 ms, so serial they gave
    ~14 fps and a clip that played at under half speed. Overlapped, the pace is whichever is slower.
    That does not make a 4K file real-time - decode alone tops out near 24 fps on the machine this was
    measured on, and more FFmpeg threads did not change it - but it removes the wasted half.

    It keeps every frame and its order (no dropping: a file is not a live source), so nothing downstream
    changes; it offers only the calls the sender uses. The properties the sender reads are captured at
    construction, because a VideoCapture is not safe to query while another thread is reading it.
    """

    def __init__(self, cap, depth=PREFETCH_FRAMES):
        self._cap = cap
        self._props = {
            cv2.CAP_PROP_FPS: cap.get(cv2.CAP_PROP_FPS),
            cv2.CAP_PROP_FRAME_WIDTH: cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            cv2.CAP_PROP_FRAME_HEIGHT: cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
        }
        self._queue = queue.Queue(maxsize=max(1, int(depth)))
        self._stop = threading.Event()
        self._ended = False
        self._thread = threading.Thread(target=self._run, name="file-prefetch", daemon=True)
        self._thread.start()

    def _put(self, item):
        """Queue `item`, giving up quietly once release() has been called."""
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _run(self):
        try:
            while not self._stop.is_set():
                ok, frame = self._cap.read()
                if not ok:
                    self._put((False, None))
                    return
                if not self._put((True, frame)):
                    return
        except cv2.error:
            # A decode error is the end of the clip as far as the sender can tell; the sender's own
            # "opened but produced no frames" / replay logic takes it from here.
            self._put((False, None))

    def isOpened(self):
        return self._cap.isOpened()

    def pending(self):
        """How many decoded frames are waiting right now. The pacing code uses it: a late frame is only worth
        dropping when the next one is already here."""
        return self._queue.qsize()

    def get(self, prop):
        return self._props.get(prop, 0.0)

    def read(self):
        """(ok, frame) in decode order; (False, None) at the end of the clip, and on every call after."""
        if self._ended:
            return False, None
        while True:
            try:
                ok, frame = self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self._thread.is_alive():
                    self._ended = True
                    return False, None
                continue
            if not ok:
                self._ended = True
            return ok, frame

    def release(self):
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self._cap.release()
        except cv2.error:
            pass


def open_file_capture(path, prefetch):
    """A capture for a local video file, decoding ahead when `prefetch` is True; None if it cannot open.

    `prefetch` None means automatic: on for anything over PREFETCH_PIXELS.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        return None
    if prefetch is None:
        prefetch = (cap.get(cv2.CAP_PROP_FRAME_WIDTH) * cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) > PREFETCH_PIXELS
    return PrefetchCapture(cap) if prefetch else cap


def list_cameras():
    """Print which camera indices open and at what size. For picking the index of a phone webcam."""
    found = 0
    for idx in range(MAX_CAMERA_INDEX + 1):
        cap = open_camera(idx, 1280, 720, 30)
        if cap is None:
            continue
        ok, frame = cap.read()
        h, w = (frame.shape[0], frame.shape[1]) if ok and frame is not None else (0, 0)
        cap.release()
        print("camera:%d  opens, %s" % (idx, ("%dx%d" % (w, h)) if ok else "but delivered no frame"))
        found += 1
    if found == 0:
        print("no camera opened. Is the phone app running and exposing a webcam?")
    return 0 if found else 1


def main():
    ap = argparse.ArgumentParser(description="F-49 video-driven sidecar")
    ap.add_argument("--video", default=None,
                    help="a local path, a direct http(s) video link, a page yt-dlp can resolve, "
                         "camera:N, or a live rtsp:// / LAN http stream")
    ap.add_argument("--list-cameras", action="store_true",
                    help="print which camera indices open, then exit")
    ap.add_argument("--model", default=EV.DEFAULT_MODEL)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--hfov", type=float, default=60.0,
                    help="assumed horizontal FOV of the source camera, degrees (measured AFTER any "
                         "rotation). A phone's main camera is roughly 70-80; unknown for a clip.")
    ap.add_argument("--subject-height", type=float, default=1.70,
                    help="assumed subject height (m). Sets the estimated hip depth, and therefore "
                         "whether the F-48 operating volume calls the clip too close.")
    ap.add_argument("--depth-range", type=float, nargs=2, default=None, metavar=("MIN", "MAX"),
                    help="clamp the estimated hip depth to this band, metres. Off by default so the "
                         "operating volume can be exercised by a clip's own framing.")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate each frame clockwise before inference (a portrait phone)")
    ap.add_argument("--flip", action=argparse.BooleanOptionalAction, default=False,
                    help="mirror each frame horizontally before inference (a front camera)")
    ap.add_argument("--camera-size", type=int, nargs=2, default=(1280, 720), metavar=("W", "H"),
                    help="requested webcam resolution. Ignored by files and streams.")
    ap.add_argument("--camera-fps", type=int, default=30, help="requested webcam frame rate")
    ap.add_argument("--loop", type=int, default=0,
                    help="FILES: replays of the clip; 0 (default) loops forever. After the last replay "
                         "the sender stays alive and silent instead of exiting, because the supervisor "
                         "would otherwise restart it and replay the clip forever anyway. Ignored by "
                         "cameras and streams.")
    ap.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True,
                    help="FILES: pace to the clip's own frame rate (default). --no-realtime runs flat out.")
    ap.add_argument("--drop-late", action=argparse.BooleanOptionalAction, default=True,
                    help="FILES with --realtime: when processing falls behind the clip's own clock, drop frames "
                         "to hold it, so the skeleton stays in step with the picture (at a lower frame rate) "
                         "instead of drifting behind. --no-drop-late processes every frame, however long that "
                         "takes (the clip then plays in slow motion).")
    ap.add_argument("--ownership", action=argparse.BooleanOptionalAction, default=False,
                    help="run F-21 target ownership. OFF: a video has one subject, and ADR-083 "
                         "documents F-21 withholding frames from a subject who walks.")
    ap.add_argument("--assume-measured", action=argparse.BooleanOptionalAction, default=False,
                    help="label every joint's depth as MEASURED on the wire. LABEL ONLY; see the module "
                         "header. Lets the F-48 calibration path run on a source with no stereo.")
    ap.add_argument("--cache-dir",
                    default=os.path.join(tempfile.gettempdir(), "viitorx_video_cache"),
                    help="where downloaded videos are kept")
    ap.add_argument("--smooth-zrel", action=argparse.BooleanOptionalAction, default=True,
                    help="One-Euro filter the model's per-joint depth, with production's trunk "
                         "parameters (see f23_video_to_unity.py for the reasoning).")
    ap.add_argument("--sway-gain", type=float, default=0.45,
                    help="scale applied to the root's lateral/vertical translation. Z is never scaled.")
    ap.add_argument("--flatten-trunk", action=argparse.BooleanOptionalAction, default=False,
                    help="production's own default is OFF; see f23_video_to_unity.py")
    # F-52. 1 (the default) is the F-49 single-person path, byte-for-byte: one crop, no detector.
    ap.add_argument("--max-poses", type=int, default=1,
                    help="F-52: how many people get a pose solve per frame. 1 = the single-person path "
                         "(default, unchanged). 2 or more runs a host-side person detector and the "
                         "multi-person tracker, so a phone, webcam or video can drive several skeletons. "
                         "Each person costs one sequential pose solve.")
    ap.add_argument("--detector", default=None,
                    help="F-52: person-detector ONNX for --max-poses > 1 (default "
                         "python-sidecar~/models/yolox_s.onnx, or VIRTUAL_MIRROR_DETECTOR). Fetch it "
                         "with tools/model/fetch_yolox_s.py.")
    ap.add_argument("--prefetch", action=argparse.BooleanOptionalAction, default=None,
                    help="FILES: decode ahead on a separate thread so decoding and inference overlap instead "
                         "of adding. Default: automatic - on for files over 2.5 megapixels (4K), where decoding "
                         "(~33 ms a frame measured) is as slow as the pose stage. Ignored by cameras and streams.")
    ap.add_argument("--det-conf", type=float, default=0.45,
                    help="F-52: person-detector confidence. 0.45 is the OAK-D path's value, not yet "
                         "tuned for this detector.")
    a = ap.parse_args()

    if a.list_cameras:
        return list_cameras()
    if not a.video:
        ap.error("--video is required (or --list-cameras)")

    print("[vid] F-49 video sender", flush=True)
    print("[vid]   producer session id = %s  (F-20A: new on every process start)" % SESSION_ID,
          flush=True)
    print("[vid]   interpreter : %s" % sys.executable, flush=True)

    # Classify and, for a file, resolve FIRST and say so: a download can take longer than the
    # supervisor's patience, and it keeps printing for exactly that reason (HEARTBEAT_SECONDS).
    try:
        kind = classify_source(a.video)
        path = None
        if kind in (KIND_FILE, KIND_DIRECT, KIND_PAGE):
            text = a.video.strip().strip('"')
            path = text if kind == KIND_FILE else (
                download_direct(text, a.cache_dir) if kind == KIND_DIRECT
                else download_page(text, a.cache_dir))
    except SourceError as e:
        print("[vid] ERROR cannot use --video %r: %s" % (redact(a.video), e), flush=True)
        return 2
    live = kind in LIVE_KINDS

    reader = None
    cap = None
    if live and kind == KIND_LAN:
        # F-50. The reader owns its own reconnection (socket, room and peer connection), so it does not
        # go through LatestFrameReader's open/read/release cycle - but it has the same surface, and
        # everything below treats the two alike. The PIN comes from CAMERA_PIN and is never printed.
        try:
            reader = LAN.LanCameraReader(a.video)
            print("[vid]   source      : LAN camera, room '%s' on %s  (LIVE, WebRTC: newest frame only, "
                  "rejoins on loss)" % (reader.room_id, reader.base_url), flush=True)
            reader.start()
        except LAN.LanCameraError as e:
            print("[vid] ERROR cannot use the LAN camera: %s" % e, flush=True)
            return 2
    elif live:
        if kind == KIND_CAMERA:
            cam_index = parse_camera_index(a.video)
            cam_w, cam_h = a.camera_size
            describe = "camera %d (requested %dx%d @ %d)" % (cam_index, cam_w, cam_h, a.camera_fps)
            opener = lambda: open_camera(cam_index, cam_w, cam_h, a.camera_fps)  # noqa: E731
        else:
            url = stream_url(a.video)
            describe = "stream %s" % redact(url)
            opener = lambda: open_stream(url)  # noqa: E731
        print("[vid]   source      : %s  (LIVE: newest frame only, reconnects on loss)" % describe,
              flush=True)
        reader = LatestFrameReader(opener, name=kind).start()
    else:
        cap = open_file_capture(path, a.prefetch)
        if cap is None:
            print("[vid] ERROR OpenCV cannot open %s (unsupported codec, or not a video)" % path,
                  flush=True)
            return 2
        print("[vid]   source      : %s%s" % (path, "  (decoding ahead on its own thread)"
                                              if isinstance(cap, PrefetchCapture) else ""), flush=True)
    print("[vid]   orientation : rotate %d deg, %s" % (a.rotate, "mirrored" if a.flip else "not mirrored"),
          flush=True)
    print("[vid]   depth       : estimated from subject height %.2f m%s   src flags: %s"
          % (a.subject_height,
             (" clamped to %.2f-%.2f m" % tuple(a.depth_range)) if a.depth_range else "",
             "ALL 1 (--assume-measured, a label)" if a.assume_measured else "all 0 (no stereo)"),
          flush=True)
    print("[vid]   ownership   : %s   %s"
          % ("ON" if a.ownership else "OFF",
             "live" if live else ("loop: forever" if a.loop <= 0 else "loop: %d" % a.loop)), flush=True)

    # Load the model while still printing, so the supervisor's heartbeat sees the load as progress.
    print("[vid] frames=0 sent=0 loading RTMW3D ...", flush=True)
    import onnxruntime as ort
    eps = ort.get_available_providers()
    print("[vid]   providers   : %s" % ", ".join(eps), flush=True)
    if "DmlExecutionProvider" not in eps:
        print("[vid]   !! no DirectML - inference falls back to CPU (~5 fps). Use the sidecar venv.",
              flush=True)
    with LoadTicker("loading RTMW3D ..."):
        model = R.RTMW3D(a.model)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    multi = None
    if a.max_poses > 1:
        # F-52. Imported only here: the single-person path must not pay for, or break on, the detector.
        print("[vid] frames=0 sent=0 loading the person detector ...", flush=True)
        import host_person_detector as HPD
        import host_multiperson as HMP
        try:
            with LoadTicker("loading the person detector ..."):
                detector = HPD.HostPersonDetector(a.detector, conf=a.det_conf)
        except RuntimeError as e:
            print("[vid] ERROR %s" % e, flush=True)
            return 2
        multi = HMP.HostMultiPerson(model, detector, estimate_hip_depth, SESSION_ID, conf=a.conf,
                                    max_poses=a.max_poses, subject_height=a.subject_height,
                                    depth_range=a.depth_range, smooth_zrel=a.smooth_zrel,
                                    sway_gain=a.sway_gain, flatten_trunk=a.flatten_trunk,
                                    assume_measured=a.assume_measured)
        print("[vid]   multi-person: up to %d people, detector %s, conf %.2f"
              % (a.max_poses, detector.describe(), a.det_conf), flush=True)

    measured = np.zeros(133, dtype=bool)
    xyz_cam = np.zeros((133, 3), dtype=np.float32)
    xyz_cam_track = np.zeros((133, 3), dtype=np.float32)
    src_all_measured = [1] * 33

    seq = 0
    sent = skipped = frames = 0
    t_run = time.time()
    last_beat = 0.0
    replay = 0
    # Set from the FIRST frame, after orientation: a rotated phone swaps width and height, and the
    # focal length derives from the width, so none of this can be known before a frame exists.
    ctx = {"w": 0, "h": 0, "fx": 0.0, "fy": 0.0, "intr": None, "src_fps": 30.0, "state": None}

    # FILE pacing numbers, for the heartbeat and the one-time "too slow to keep up" warning.
    pace = {"speed": None, "late": 0, "read_s": 0.0, "reads": 0, "warned": False}

    def heartbeat(note=""):
        el = max(1e-6, time.time() - t_run)
        extra = ""
        if reader is not None:
            extra = " live=%s dropped=%d reconnects=%d" % ("Y" if reader.connected else "n",
                                                           reader.dropped, reader.reconnects)
        if pace["speed"] is not None:
            # How fast the CLIP is advancing against the wall clock: 1.00 is real time, 0.62 is slow motion.
            extra += " speed=%.2f late=%d" % (pace["speed"], pace["late"])
        if multi is not None:
            extra += " " + multi.summary()
        print("[vid] frames=%d sent=%d skipped=%d fps~%.1f replay=%d%s %s"
              % (frames, sent, skipped, sent / el, replay, extra, note), flush=True)

    def fresh_state():
        """Everything that carries temporal memory, rebuilt at each replay so the clip's end does
        not smear into its start."""
        return {
            "zfilt": [smoothing.OneEuro(freq=ctx["src_fps"], min_cutoff=0.5, beta=0.4) for _ in range(133)],
            "skel": JT.SkeletonTracker(),
            "own": TO.TargetOwnership(TO.OwnershipConfig()) if a.ownership else None,
            "bbox": R.center_bbox(ctx["w"], ctx["h"]),
        }

    heartbeat("ready")
    t_loop = time.time()
    loop_frame = 0

    try:
        while True:
            t_grab = None
            if live:
                frame, t_grab = reader.read(HEARTBEAT_SECONDS)
                if frame is None and getattr(reader, "fatal", None):
                    # F-50: the LAN camera's session ended for good (the server refused a rejoin).
                    # Exit non-zero so the supervisor reports it, instead of idling on 'no new frame'.
                    print("[vid] ERROR the LAN camera cannot continue: %s" % reader.fatal, flush=True)
                    return 2
                if frame is None:
                    # Nothing new within the heartbeat interval: say so rather than go quiet, or the
                    # supervisor reads a camera that has not delivered as a hung process.
                    heartbeat("waiting for frames" if reader.frames_read == 0 else "no new frame")
                    last_beat = time.time()
                    continue
            else:
                t_read = time.perf_counter()
                ok, frame = cap.read()
                pace["read_s"] += time.perf_counter() - t_read
                pace["reads"] += 1
                if not ok:
                    if loop_frame == 0:
                        # Opened but yielded nothing: a decode failure from the first frame. Looping on
                        # that would spin the CPU forever printing nothing useful.
                        print("[vid] ERROR %s opened but produced no frames" % path, flush=True)
                        return 2
                    replay += 1
                    if a.loop > 0 and replay >= a.loop:
                        break
                    cap.release()
                    cap = open_file_capture(path, a.prefetch)
                    if cap is None:
                        print("[vid] ERROR could not reopen %s for replay" % path, flush=True)
                        return 2
                    ctx["state"] = fresh_state() if ctx["w"] else None
                    if multi is not None:
                        multi.reset()
                    t_loop = time.time()
                    loop_frame = 0
                    continue

            frame = orient_frame(frame, a.rotate, a.flip)
            if ctx["w"] and (frame.shape[0], frame.shape[1]) != (ctx["h"], ctx["w"]):
                # F-50. A WebRTC sender adapts its resolution to the bandwidth, and any live source may
                # be reopened at another size. The crop box, the focal length and the per-person state
                # were all derived from the FIRST frame's size, so carrying on would feed the model a
                # crop and an intrinsic matrix for a picture that no longer exists. Re-derive them
                # exactly as for a first frame (which also rebuilds the filters, so one frame of reset).
                print("[vid]   frame size changed %dx%d -> %dx%d: re-deriving crop and intrinsics"
                      % (ctx["w"], ctx["h"], frame.shape[1], frame.shape[0]), flush=True)
                ctx["w"] = 0
            if ctx["w"] == 0:
                ctx["h"], ctx["w"] = frame.shape[0], frame.shape[1]
                fps = (cap.get(cv2.CAP_PROP_FPS) if cap is not None else 0.0) or 30.0
                ctx["src_fps"] = 30.0 if (fps < 1.0 or fps > 240.0 or math.isnan(fps)) else fps
                ctx["fx"] = ctx["fy"] = 0.5 * ctx["w"] / math.tan(math.radians(a.hfov) * 0.5)
                ctx["intr"] = (ctx["fx"], ctx["fy"], ctx["w"] * 0.5, ctx["h"] * 0.5)
                ctx["state"] = fresh_state()
                print("[vid]   frame       : %dx%d @ %.2f fps   assumed fx=fy=%.1f (hfov %.0f deg)"
                      % (ctx["w"], ctx["h"], ctx["src_fps"], ctx["fx"], a.hfov), flush=True)
            w, h, fy, fx, intr = ctx["w"], ctx["h"], ctx["fy"], ctx["fx"], ctx["intr"]
            src_fps = ctx["src_fps"]
            state = ctx["state"]

            frames += 1
            loop_frame += 1
            if not live and a.realtime:
                # Pace EVERY file frame to the clip's own clock, including the ones that will be
                # skipped. A clip that opens on an empty room otherwise burns through its first seconds
                # at inference speed, then arrives at the subject with the wall clock far behind the
                # video and plays flat out until it catches up. The wait comes BEFORE inference, so a
                # frame is never stale by the time it is posed. A live source is its own clock.
                if loop_frame == 2:
                    # Re-anchor the clock once the FIRST frame is done: its cost is the one-off model warm-up
                    # (the GPU compiles its graphs on the first run), which is not lag and must not be
                    # "caught up" by dropping the next second of the clip.
                    t_loop = time.time() - (loop_frame - 1) / src_fps
                due = t_loop + (loop_frame - 1) / src_fps
                # Dropping a frame only helps if the NEXT one is already decoded and waiting: it saves the
                # time spent posing this one, never the time spent decoding it. When decoding itself is the
                # bottleneck (a 4K file) every frame arrives late, a rule that drops late frames drops them
                # ALL - measured: 13 poses out of 1,934 - so in that case nothing is dropped and the clip
                # simply plays at the decoder's speed, with a warning.
                prefetching = isinstance(cap, PrefetchCapture)
                can_catch_up = can_catch_up_by_dropping(prefetching, cap.pending() if prefetching else 0,
                                                        pace["read_s"], pace["reads"], src_fps)
                nap, drop = pace_decision(due, time.time(), src_fps, a.drop_late and can_catch_up)
                if nap > 0:
                    time.sleep(nap)
                if loop_frame > 1:
                    pace["speed"] = ((loop_frame - 1) / src_fps) / max(1e-6, time.time() - t_loop)
                if (not pace["warned"] and loop_frame >= 120 and pace["speed"] is not None
                        and pace["speed"] < 0.9 and not can_catch_up and pace["reads"] > 0):
                    pace["warned"] = True
                    print("[vid] WARNING the clip is advancing at %.2fx real time and dropping frames cannot fix it: "
                          "frames reach the loop slower than the %.0f ms frame period (a %dx%d file decodes slowly). "
                          "Use a lower-resolution copy of the clip (e.g. 1080p) to play it in real time."
                          % (pace["speed"], 1000.0 / src_fps, ctx["w"], ctx["h"]), flush=True)
                if drop:
                    pace["late"] += 1
                    # A dropped frame skips the heartbeat below, and the supervisor kills a child that goes
                    # quiet for 10 s: keep talking even while every frame in a stretch is being dropped.
                    if time.time() - last_beat >= HEARTBEAT_SECONDS:
                        last_beat = time.time()
                        heartbeat()
                    continue
            # `lat` for a live source starts when the frame was GRABBED, so the time it waited for the
            # model is counted; for a file there is no capture time and it starts at decode.
            t_frame = t_grab if t_grab is not None else time.time()
            now = time.time()
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                heartbeat()

            if multi is not None:
                # F-52. The tracker's clock is the VIDEO's own timeline for a file (reproducible, and
                # independent of how fast this machine chews through it) and the wall clock for a live
                # source, where the wall clock is the frame clock.
                multi.src_fps = src_fps
                message = multi.process(frame, intr, now if live else loop_frame / src_fps, now, t_frame)
                if message is None:
                    skipped += 1
                    continue
                sock.sendto(json.dumps(message).encode("utf-8"), (a.host, a.port))
                sent += 1
                continue

            uv, zrel, conf = model.infer(frame, state["bbox"])
            # production M15 crop refinement, same blend as the live sender
            refined = R.bbox_from_keypoints(uv, conf, w, h, thr=a.conf)
            body_conf = float(np.mean([conf[i] for i in R.COCO17_TO_JOINTID]))
            if refined is not None and body_conf >= a.conf:
                state["bbox"] = tuple(0.7 * np.array(state["bbox"]) + 0.3 * np.array(refined))
            else:
                state["bbox"] = R.center_bbox(w, h)

            if conf[11] < a.conf or conf[12] < a.conf:
                skipped += 1
                continue
            hip_z = estimate_hip_depth(uv, conf, a.conf, a.subject_height, fy, a.depth_range)
            if a.smooth_zrel:
                zrel = np.array([state["zfilt"][i].filter(float(zrel[i])) for i in range(len(zrel))],
                                dtype=np.float32)
            zrel_hip = 0.5 * (float(zrel[11]) + float(zrel[12]))
            hip_uv = 0.5 * (uv[11] + uv[12])
            mid_hip = np.array([(hip_uv[0] - intr[2]) * hip_z / fx,
                                (hip_uv[1] - intr[3]) * hip_z / fy, hip_z], dtype=np.float32)

            # P1-1 per-joint states, for the TRUST HUD. States only; geometry is never written back, so
            # this cannot turn an unmeasured joint into a "measured" one (see f23's long note).
            pos_in, cnf_in, dv_in = {}, {}, {}
            for j in state["skel"].indices:
                p = W._fallback_point(j, uv, zrel, zrel_hip, mid_hip, hip_z, intr, True) + mid_hip
                xyz_cam_track[j] = p
                pos_in[j] = (float(p[0]), float(p[1]), float(p[2]))
                cnf_in[j] = float(conf[j])
                dv_in[j] = True
            track_res = state["skel"].update(pos_in, cnf_in, now, depth_valid=dv_in,
                                             collect_events=False)

            own_state = None
            if state["own"] is not None:
                raw_scale = None
                if conf[5] >= a.conf and conf[6] >= a.conf:
                    sh = 0.5 * (xyz_cam_track[5] + xyz_cam_track[6])
                    raw_scale = float(np.linalg.norm(sh - mid_hip))
                own_state, should_emit = state["own"].update(
                    TO.Observation(valid=True, pos=tuple(float(v) for v in mid_hip), conf=body_conf,
                                   scale=raw_scale), now)
                for e in state["own"].drain_events():
                    print("[vid] TARGET %s state=%s target_id=%s%s" % (
                        e["event"], e["state"], e.get("target_id"),
                        (" reason=%s" % e["reason"]) if "reason" in e else ""), flush=True)
                if not should_emit:
                    skipped += 1
                    continue

            lm, src = W.build_body_landmarks(uv, xyz_cam, measured, conf, zrel, zrel_hip, mid_hip,
                                             hip_z, intr, a.conf, flatten_trunk=a.flatten_trunk,
                                             use_zrel=True)
            lh = W.build_hand(uv, xyz_cam, measured, conf, zrel, zrel_hip, mid_hip, hip_z, intr, 91,
                              a.conf, use_zrel=True)
            rh = W.build_hand(uv, xyz_cam, measured, conf, zrel, zrel_hip, mid_hip, hip_z, intr, 112,
                              a.conf, use_zrel=True)
            seq += 1
            msg = {"lm": lm,
                   "xyz": [round(float(mid_hip[0] * 1000.0 * a.sway_gain), 1),
                           round(float(mid_hip[1] * 1000.0 * a.sway_gain), 1),
                           round(float(mid_hip[2] * 1000.0), 1)],
                   "src": src_all_measured if a.assume_measured else src,
                   "seq": seq, "sid": SESSION_ID, "t": round(time.time(), 4),
                   "st": W.build_joint_states(track_res),
                   "lat": round((time.time() - t_frame) * 1000.0, 1)}
            if own_state is not None:
                msg["own"] = own_state
            if lh is not None:
                msg["lh"] = lh
            if rh is not None:
                msg["rh"] = rh
            sock.sendto(json.dumps(msg).encode("utf-8"), (a.host, a.port))
            sent += 1
    finally:
        if reader is not None:
            reader.stop()

    if cap is not None:
        cap.release()
    heartbeat("video finished (%d replay%s); idle" % (replay, "" if replay == 1 else "s"))
    # Stay alive and quiet. Exiting would make the supervisor restart this and replay the clip, which
    # is the opposite of "play it N times". The heartbeat keeps the supervisor's watchdog satisfied.
    try:
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            heartbeat("video finished; idle")
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    _sys.exit(main())
