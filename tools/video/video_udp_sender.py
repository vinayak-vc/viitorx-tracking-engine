#!/usr/bin/env python3
"""F-49 - THE VIDEO SENDER: run the REAL tracking pipeline on a video instead of the OAK-D feed.

WHY THIS EXISTS. Everything downstream of the sidecar - the pose buffer, the F-48 operating volume,
the humanize layer, every experience scene, the skeleton show and the VRM avatar - only ever sees a UDP
datagram. So "test this against a video" means producing the SAME datagram from a file, and letting
Unity run completely unchanged. Unity launches this through the supervisor exactly as it launches the
OAK-D senders (AppBootstrap -> "Video Test Source" fields), so the scene, the restart watchdog and the
readiness contract are the production ones.

It is f23_video_to_unity.py grown into something Unity can own: a source that may be a URL, a loop that
never ends, the supervisor's readiness markers, and a heartbeat that keeps talking while it downloads.
f23 stays as the evidence tool it was; nothing here changes it.

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
  * NO MULTI-PERSON. The crowd detector runs on the OAK-D's VPU and there is no host-side copy of it,
    so a crowd scene fed from here sees a crowd of one. The single-person pipeline is what this is.
  * F-21 OWNERSHIP IS OFF by default (--ownership to enable). A walking subject in a clip is exactly
    the case ADR-083 says F-21 mishandles, and a video has no second person to defend against.

SOURCES (--video)
  a local path                        used as is
  a direct http(s) link to a file     downloaded once into --cache-dir (.mp4 .webm .mov .mkv .avi .m4v)
  any other http(s) page              handed to yt-dlp, if one is installed (it is NOT in the venv by
                                      default; install it deliberately - see README)
Only http, https and existing local files are accepted. A URL is never passed to a shell.

    .venv\\Scripts\\python.exe tools/video/video_udp_sender.py --video ..\\..\\video\\SR7.mp4
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
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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
#: calls the child READY once it has seen `frames=`. So a download, a model load and a long skip all
#: have to keep printing; this is how often. Well inside the 10 s limit.
HEARTBEAT_SECONDS = 2.0

USER_AGENT = "viitorx-video-sender/1.0"


class SourceError(Exception):
    """The --video argument cannot be turned into a playable local file."""


def classify_source(spec):
    """What kind of thing --video names: 'file', 'direct' (a link to a video file) or 'page'.

    Raises SourceError for anything else - an empty string, a missing path, or a scheme that is not
    http/https. Refusing `file:`, `ftp:` and friends here is deliberate: this value comes from an
    Inspector field and ends up in a request or in a subprocess argument list, so it is validated
    against an allowlist rather than trusted.
    """
    text = (spec or "").strip().strip('"')
    if not text:
        raise SourceError("no video given")
    if os.path.isfile(text):
        return "file"
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        ext = os.path.splitext(urllib.parse.unquote(parsed.path))[1].lower()
        return "direct" if ext in VIDEO_EXTENSIONS else "page"
    if parsed.scheme and len(parsed.scheme) > 1:
        # A one-letter "scheme" is a Windows drive ("C:\\..."), which fell through isfile() because the
        # file does not exist - that is a missing file, not a forbidden URL, and should say so.
        raise SourceError("only http(s) links and local files are accepted, not '%s:'" % parsed.scheme)
    raise SourceError("file not found: %s" % text)


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
        raise SourceError("download failed: %s" % e)
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
                          "Use a direct .mp4/.webm link or a local path, or install yt-dlp into "
                          "python-sidecar~/.venv (snapshot `pip freeze` first).")
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
            print("[vid] frames=0 sent=0 yt-dlp: %s" % line, flush=True)
    rc = proc.wait()
    if rc != 0 or not os.path.isfile(last):
        raise SourceError("yt-dlp failed (rc=%d)" % rc)
    return last


def resolve_source(spec, cache_dir):
    """The playable local path for --video."""
    kind = classify_source(spec)
    text = spec.strip().strip('"')
    if kind == "file":
        return text
    if kind == "direct":
        return download_direct(text, cache_dir)
    return download_page(text, cache_dir)


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


def main():
    ap = argparse.ArgumentParser(description="F-49 video-driven sidecar")
    ap.add_argument("--video", required=True,
                    help="a local path, a direct http(s) link to a video file, or a page yt-dlp can "
                         "resolve")
    ap.add_argument("--model", default=EV.DEFAULT_MODEL)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--hfov", type=float, default=60.0,
                    help="assumed horizontal FOV of the source camera, degrees. Unknown for an "
                         "arbitrary clip; only scales the pose.")
    ap.add_argument("--subject-height", type=float, default=1.70,
                    help="assumed subject height (m). Sets the estimated hip depth, and therefore "
                         "whether the F-48 operating volume calls the clip too close.")
    ap.add_argument("--depth-range", type=float, nargs=2, default=None, metavar=("MIN", "MAX"),
                    help="clamp the estimated hip depth to this band, metres. Off by default so the "
                         "operating volume can be exercised by a clip's own framing.")
    ap.add_argument("--loop", type=int, default=0,
                    help="replays of the clip; 0 (default) loops forever. After the last replay the "
                         "sender stays alive and silent instead of exiting, because the supervisor "
                         "would otherwise restart it and replay the clip forever anyway.")
    ap.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True,
                    help="pace to the clip's own frame rate (default). --no-realtime runs flat out.")
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
    a = ap.parse_args()

    print("[vid] F-49 video sender", flush=True)
    print("[vid]   producer session id = %s  (F-20A: new on every process start)" % SESSION_ID,
          flush=True)
    print("[vid]   interpreter : %s" % sys.executable, flush=True)

    # Resolve FIRST and say so: a download can take longer than the supervisor's patience, and it
    # keeps printing for exactly that reason (HEARTBEAT_SECONDS).
    try:
        path = resolve_source(a.video, a.cache_dir)
    except SourceError as e:
        print("[vid] ERROR cannot use --video %r: %s" % (a.video, e), flush=True)
        return 2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print("[vid] ERROR OpenCV cannot open %s (unsupported codec, or not a video)" % path, flush=True)
        return 2
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_fps < 1.0 or src_fps > 240.0 or math.isnan(src_fps):
        src_fps = 30.0
    fx = fy = 0.5 * w / math.tan(math.radians(a.hfov) * 0.5)
    intr = (fx, fy, w * 0.5, h * 0.5)
    print("[vid]   source      : %s" % path, flush=True)
    print("[vid]   video       : %dx%d @ %.2f fps   assumed fx=fy=%.1f (hfov %.0f deg)"
          % (w, h, src_fps, fx, a.hfov), flush=True)
    print("[vid]   depth       : estimated from subject height %.2f m%s   src flags: %s"
          % (a.subject_height,
             (" clamped to %.2f-%.2f m" % tuple(a.depth_range)) if a.depth_range else "",
             "ALL 1 (--assume-measured, a label)" if a.assume_measured else "all 0 (no stereo)"),
          flush=True)
    print("[vid]   ownership   : %s   loop: %s"
          % ("ON" if a.ownership else "OFF", "forever" if a.loop <= 0 else "%d" % a.loop), flush=True)

    # Load the model while still printing, so the supervisor's heartbeat sees the load as progress.
    print("[vid] frames=0 sent=0 loading RTMW3D ...", flush=True)
    import onnxruntime as ort
    eps = ort.get_available_providers()
    print("[vid]   providers   : %s" % ", ".join(eps), flush=True)
    if "DmlExecutionProvider" not in eps:
        print("[vid]   !! no DirectML - inference falls back to CPU (~5 fps). Use the sidecar venv.",
              flush=True)
    model = R.RTMW3D(a.model)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def fresh_state():
        """Everything that carries temporal memory, rebuilt at each replay so the clip's end does
        not smear into its start."""
        return {
            "zfilt": [smoothing.OneEuro(freq=src_fps, min_cutoff=0.5, beta=0.4) for _ in range(133)],
            "skel": JT.SkeletonTracker(),
            "own": TO.TargetOwnership(TO.OwnershipConfig()) if a.ownership else None,
            "bbox": R.center_bbox(w, h),
        }

    measured = np.zeros(133, dtype=bool)
    xyz_cam = np.zeros((133, 3), dtype=np.float32)
    xyz_cam_track = np.zeros((133, 3), dtype=np.float32)
    src_all_measured = [1] * 33

    seq = 0
    sent = skipped = frames = 0
    t_run = time.time()
    last_beat = 0.0
    replay = 0

    def heartbeat(note=""):
        el = max(1e-6, time.time() - t_run)
        print("[vid] frames=%d sent=%d skipped=%d fps~%.1f replay=%d %s"
              % (frames, sent, skipped, sent / el, replay, note), flush=True)

    heartbeat("ready")
    state = fresh_state()
    t_loop = time.time()
    loop_frame = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            if loop_frame == 0:
                # Opened but yielded nothing: a decode failure from the first frame. Looping on that
                # would spin the CPU forever printing nothing useful.
                print("[vid] ERROR %s opened but produced no frames" % path, flush=True)
                return 2
            replay += 1
            if a.loop > 0 and replay >= a.loop:
                break
            cap.release()
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                print("[vid] ERROR could not reopen %s for replay" % path, flush=True)
                return 2
            state = fresh_state()
            t_loop = time.time()
            loop_frame = 0
            continue

        frames += 1
        loop_frame += 1
        if a.realtime:
            # Pace EVERY frame to the clip's own clock, including the ones that will be skipped. A clip
            # that opens on an empty room otherwise burns through its first seconds at inference speed,
            # then arrives at the subject with the wall clock far behind the video and plays flat out
            # until it catches up - which is not a fair test of anything a camera would deliver. The
            # wait comes BEFORE inference, so a frame is never stale by the time it is posed.
            due = t_loop + (loop_frame - 1) / src_fps
            wait = due - time.time()
            if wait > 0:
                time.sleep(min(wait, 0.25))
        t_frame = time.time()
        now = t_frame
        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            heartbeat()

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
        track_res = state["skel"].update(pos_in, cnf_in, now, depth_valid=dv_in, collect_events=False)

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

        lm, src = W.build_body_landmarks(uv, xyz_cam, measured, conf, zrel, zrel_hip, mid_hip, hip_z,
                                         intr, a.conf, flatten_trunk=a.flatten_trunk, use_zrel=True)
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
