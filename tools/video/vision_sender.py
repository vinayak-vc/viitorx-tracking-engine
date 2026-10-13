#!/usr/bin/env python3
"""H-01 / H-02 - THE FRAME LOOP the hands sender and the face sender share: pick a source, read frames, run a pipeline, send a
datagram, show a window.

Everything that is the same between "find hands" and "find a face" lives here once: the --video sources (the video sender's,
reused: camera:N, a LAN Camera Streaming link, an rtsp/http stream, a file or a direct video link), orientation, pacing of a
file to its own frame rate, newest-frame-only reading of a live source, the supervisor-style heartbeat, the UDP socket, the
demo window and the exit codes. A sender supplies only a PIPELINE: an object with

    process(frame)                 -> result          the tracker's list for this frame
    encode(result, ctx)            -> dict            the datagram (ctx: session id, seq, time, latency, source, size, mirrored)
    to_bytes(message)              -> bytes, or a list of bytes (S-01: one datagram each, in order) for a message too big for one
    render(frame, result, hud)     -> image           the demo window (hud: fps, infer_ms, source, mirrored, sent)
    heartbeat_extra()              -> str             extra text for the heartbeat line
    banner()                       -> [str]           lines for the startup banner

This module is in tools/ because it reuses video_udp_sender, and tools/ is not shipped in a Unity build (root/*.py is). The
pipelines' own modules (hand_*.py, face_*.py) are at the sidecar root and import nothing from tools/.
"""
import os as _os
import sys as _sys

_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401  - puts the sidecar root and every tools/ group on sys.path

import argparse
import socket
import sys
import time
import uuid

import cv2

import lan_camera as LAN
import video_udp_sender as VS   # the source handling lives there; importing it loads no model

#: Seconds of heartbeat silence the supervisor tolerates is 10; print well inside it.
HEARTBEAT_SECONDS = VS.HEARTBEAT_SECONDS

PROVIDER_SETS = {
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
    "cpu": ["CPUExecutionProvider"],
}


def new_session_id(prefix):
    """A producer session id: a new value on every process start, so a receiver can drop its state."""
    return "%s%09x" % (prefix, uuid.uuid4().int & 0xFFFFFFFFF)


def providers_for(choice):
    """The ONNX Runtime provider list for --provider (None = the module's own default, DirectML then CPU)."""
    return None if choice == "auto" else PROVIDER_SETS[choice]


def add_common_arguments(ap, default_port):
    """The arguments every vision sender takes."""
    ap.add_argument("--video", default="camera:0",
                    help="camera:N | LAN camera viewer link | rtsp/http stream | a file or direct video link "
                         "(default camera:0)")
    ap.add_argument("--list-cameras", action="store_true", help="print which camera indices open and exit")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--provider", choices=("auto", "dml", "cpu"), default="auto",
                    help="ONNX Runtime provider for the networks. auto = DirectML then CPU")
    ap.add_argument("--hfov", type=float, default=60.0,
                    help="the camera's horizontal field of view in degrees. ASSUMED: it sets the scale of every "
                         "metre in `pos` (default %(default)s)")
    ap.add_argument("--hfov-axis", choices=("width", "long"), default="width",
                    help="'long': --hfov spans the longer side, so a phone keeps one focal length in portrait "
                         "and landscape")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate the picture clockwise before the model (a sideways phone)")
    ap.add_argument("--flip", action=argparse.BooleanOptionalAction, default=False,
                    help="mirror the picture before the model (a selfie view). Mirrors `pos`.X too")
    ap.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), default=None,
                    help="cut this region (pixels of the delivered frame) out before anything else. For a clip where the "
                         "subject is small, or a camera that sees more than you want tracked")
    ap.add_argument("--smooth", action=argparse.BooleanOptionalAction, default=True,
                    help="One-Euro smoothing (default on)")
    ap.add_argument("--camera-size", type=int, nargs=2, default=(1280, 720), metavar=("W", "H"))
    ap.add_argument("--camera-fps", type=int, default=30)
    ap.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True,
                    help="FILES: replay forever (default); --no-loop stops at the end")
    ap.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True,
                    help="FILES: pace to the clip's own frame rate (default); --no-realtime runs flat out")
    ap.add_argument("--cache-dir", default=None, help="where a downloaded clip is kept (default: the OS temp folder)")
    ap.add_argument("--show", action="store_true", help="the demo window: the picture with the result drawn on it")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = run until stopped)")


def open_source(spec, a):
    """(kind, live, reader, cap, label) for --video. Raises VS.SourceError / LAN.LanCameraError."""
    kind = VS.classify_source(spec)
    text = spec.strip().strip('"')
    path = None
    if kind in (VS.KIND_FILE, VS.KIND_DIRECT, VS.KIND_PAGE):
        path = text if kind == VS.KIND_FILE else (
            VS.download_direct(text, a.cache_dir) if kind == VS.KIND_DIRECT else VS.download_page(text, a.cache_dir))
    if kind == VS.KIND_LAN:
        reader = LAN.LanCameraReader(spec)
        reader.start()
        return kind, True, reader, None, "LAN camera, room '%s' on %s" % (reader.room_id, reader.base_url)
    if kind == VS.KIND_CAMERA:
        index = VS.parse_camera_index(spec)
        width, height = a.camera_size
        reader = VS.LatestFrameReader(lambda: VS.open_camera(index, width, height, a.camera_fps), name=kind).start()
        return kind, True, reader, None, "camera %d (requested %dx%d @ %d)" % (index, width, height, a.camera_fps)
    if kind == VS.KIND_STREAM:
        url = VS.stream_url(spec)
        reader = VS.LatestFrameReader(lambda: VS.open_stream(url), name=kind).start()
        return kind, True, reader, None, "stream %s" % VS.redact(url)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        raise VS.SourceError("OpenCV cannot open %s" % path)
    return kind, False, None, cap, "file %s" % path


class FilePacer(object):
    """Holds a FILE to its own frame rate. A live source needs none: the camera is the clock."""

    def __init__(self, fps, enabled, clock=time.perf_counter, sleep=time.sleep):
        self.period = 1.0 / fps if fps and fps > 1.0 else 1.0 / 30.0
        self.enabled = enabled
        self._clock = clock
        self._sleep = sleep
        self._start = None
        self._index = 0

    def wait(self):
        """Block until the next frame is due. Returns how many frames behind schedule it is (0 when on time)."""
        if not self.enabled:
            return 0
        now = self._clock()
        if self._start is None:
            self._start = now
        due = self._start + self._index * self.period
        self._index += 1
        if now < due:
            self._sleep(due - now)
            return 0
        return int((now - due) / self.period)

    def restart(self):
        self._start = None
        self._index = 0


class SendContext(object):
    """What a pipeline's encode() needs besides the result."""
    __slots__ = ("session_id", "seq", "now", "latency_ms", "source", "width", "height", "mirrored")

    def __init__(self, session_id, seq, now, latency_ms, source, width, height, mirrored):
        self.session_id = session_id
        self.seq = seq
        self.now = now
        self.latency_ms = latency_ms
        self.source = source
        self.width = width
        self.height = height
        self.mirrored = mirrored


class Hud(object):
    """What a pipeline's render() needs besides the picture and the result."""
    __slots__ = ("fps", "infer_ms", "source", "mirrored", "sent")

    def __init__(self, fps, infer_ms, source, mirrored, sent):
        self.fps = fps
        self.infer_ms = infer_ms
        self.source = source
        self.mirrored = mirrored
        self.sent = sent


def run(a, tag, session_id, port_label, pipeline, window_name):
    """The whole sender, around `pipeline`. Returns the process exit code (2 for a source or model problem)."""
    print("[%s] interpreter : %s" % (tag, sys.executable), flush=True)
    try:
        kind, live, reader, cap, label = open_source(a.video, a)
    except (VS.SourceError, LAN.LanCameraError) as e:
        print("[%s] ERROR cannot use --video %r: %s" % (tag, VS.redact(a.video), e), flush=True)
        return 2
    print("[%s]   source      : %s  (%s)" % (tag, label, "LIVE: newest frame only" if live else "file"), flush=True)
    for line in pipeline.banner():
        print("[%s]   %s" % (tag, line), flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (a.host, a.port)
    print("[%s]   sending to  : %s:%d  %s" % (tag, a.host, a.port, port_label), flush=True)

    file_fps = cap.get(cv2.CAP_PROP_FPS) if cap is not None else 0.0
    pacer = FilePacer(file_fps, enabled=(not live and a.realtime))

    seq = int(time.time() * 100) % 100000000
    frames = sent = with_result = 0
    t_run = time.time()
    last_beat = 0.0
    fps_window = []
    infer_ms = 0.0
    result_code = 0

    def heartbeat(note=""):
        el = max(1e-6, time.time() - t_run)
        extra = ""
        if reader is not None:
            extra = " live=%s dropped=%d reconnects=%d" % ("Y" if reader.connected else "n", reader.dropped,
                                                           reader.reconnects)
        print("[%s] frames=%d sent=%d with_result=%d fps~%.1f infer=%.1fms %s%s %s"
              % (tag, frames, sent, with_result, sent / el, infer_ms, pipeline.heartbeat_extra(), extra, note),
              flush=True)

    heartbeat("ready")
    try:
        while True:
            if a.seconds > 0 and time.time() - t_run >= a.seconds:
                break
            t_grab = None
            if live:
                frame, t_grab = reader.read(HEARTBEAT_SECONDS)
                if frame is None and getattr(reader, "fatal", None):
                    print("[%s] ERROR the LAN camera cannot continue: %s" % (tag, reader.fatal), flush=True)
                    result_code = 2
                    break
                if frame is None:
                    heartbeat("waiting for frames" if reader.frames_read == 0 else "no new frame")
                    last_beat = time.time()
                    continue
            else:
                behind = pacer.wait()
                ok, frame = cap.read()
                if not ok:
                    if not a.loop:
                        break
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    pacer.restart()
                    pipeline.restart()
                    ok, frame = cap.read()
                    if not ok:
                        print("[%s] ERROR %s produced no frames" % (tag, label), flush=True)
                        result_code = 2
                        break
                for _ in range(min(behind, 3)):    # a file that cannot keep up drops frames, not time
                    cap.grab()
                t_grab = time.time()

            t0 = time.perf_counter()
            if a.crop:
                frame = frame[a.crop[1]:a.crop[3], a.crop[0]:a.crop[2]]
            frame = VS.orient_frame(frame, a.rotate, a.flip)
            result = pipeline.process(frame)
            cost_ms = (time.perf_counter() - t0) * 1000.0
            infer_ms = cost_ms if frames == 0 else 0.9 * infer_ms + 0.1 * cost_ms
            if frames == 0 and not live:
                # The first frame pays the models' warm-up (DirectML compiles its graphs: ~0.3 s). Without this the pacer
                # sees a deficit it can only repay by dropping frames, and the picture jumps for the next second.
                pacer.restart()
            frames += 1
            if len(result) > 0:
                with_result += 1

            seq += 1
            now = time.time()
            ctx = SendContext(session_id, seq, now, (now - t_grab) * 1000.0, kind, frame.shape[1], frame.shape[0], a.flip)
            payload = pipeline.to_bytes(pipeline.encode(result, ctx))
            if isinstance(payload, (bytes, bytearray)):
                sock.sendto(payload, addr)
            else:
                # S-01: a message too big for one datagram (the cutout frame carries a picture) comes as a list, in chunk order.
                for datagram in payload:
                    sock.sendto(datagram, addr)
            sent += 1

            if a.show:
                fps_window.append(time.perf_counter())
                fps_window = [t for t in fps_window if fps_window[-1] - t <= 1.0]
                fps = (len(fps_window) - 1) / max(1e-6, fps_window[-1] - fps_window[0]) if len(fps_window) > 1 else 0.0
                cv2.imshow(window_name, pipeline.render(frame, result, Hud(fps, infer_ms, kind, a.flip, sent)))
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break

            if time.time() - last_beat >= HEARTBEAT_SECONDS:
                heartbeat()
                last_beat = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        heartbeat("stopping")
        if reader is not None:
            reader.stop()
        if cap is not None:
            cap.release()
        sock.close()
        if a.show:
            cv2.destroyAllWindows()
    return result_code
