#!/usr/bin/env python3
"""H-01 - THE HANDS SENDER: hands only, no body, from a webcam, a LAN camera (a phone) or a video, over UDP.

    .venv\\Scripts\\python.exe tools/hands/hands_udp_sender.py --video camera:0 --flip --show
    set CAMERA_PIN=123456 && .venv\\Scripts\\python.exe tools/hands/hands_udp_sender.py --video "http://192.168.1.5:3000/viewer?room=room-abc123" --show
    .venv\\Scripts\\python.exe tools/hands/hands_udp_sender.py --video clip.mp4 --show
    .venv\\Scripts\\python.exe tools/hands/hands_udp_sender.py --list-cameras

WHAT IT DOES. Finds up to --max-hands hands anywhere in the picture (hand_landmarker.py: MediaPipe's palm detector and
hand-landmark network on ONNX Runtime), tracks them with stable ids (hand_tracking.py), smooths them, estimates each
hand's 3D position and reads its finger poses and gesture, and sends the result as ONE JSON datagram per frame to
127.0.0.1:8901 (hand_wire.py documents every field). A frame with no hand sends a datagram with n = 0, so a receiver can
tell "no hand" from "no sender".

THE FRAME LOOP (sources, pacing, heartbeat, window, socket) is tools/video/vision_sender.py, shared with the face sender
(tools/face/face_udp_sender.py). This file supplies only the hands pipeline. Both live in tools/ because they reuse the video
sender's source handling and tools/ is not shipped in a Unity build (root/*.py is); the hand_*.py modules are at the root and
import nothing from tools/.

IT NEEDS NO BODY. That is the point: the body sender (video_udp_sender.py) drops any frame without a person, because its
hands come from a whole-body model. This one does not look for a person at all.

SOURCES (--video) are the video sender's, reused from it rather than copied: camera:N, a LAN Camera Streaming viewer
link (the access PIN comes from the CAMERA_PIN environment variable, never the link), a phone's IP-camera stream
(rtsp:// or http://<lan address>/video), a local file or a direct video link. A live source is processed
newest-frame-only, so latency cannot pile up behind a slow GPU.

THE DEPTH IS AN ESTIMATE (hand_geometry.py). --hfov is the camera's horizontal field of view, which a webcam or a phone
does not report: the 60 degree default is a guess, and every metre in `pos` scales with it. --hand-scale is your hand
relative to an average one.

LEFT AND RIGHT (--sides). MediaPipe's handedness label assumes a MIRRORED picture. With --flip the picture is mirrored
before the model, so the label is the physical hand (`model`); without it the label is the opposite hand (`swapped`).
`auto` (the default) picks accordingly. Unverified against a known hand (H-01 NOT TESTED): raise your right hand with
--show on and read the letter.
"""

import os as _os
import sys as _sys

_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401  - puts the sidecar root and every tools/ group on sys.path
import evidence_paths as EV  # noqa: F401

import argparse
import sys
import time

import hand_geometry as G
import hand_landmarker as H
import hand_overlay as OV
import hand_tracking as HT
import hand_wire as WIRE
import vision_sender as VSND
from vision_sender import FilePacer  # noqa: F401  (re-exported: the tests import it from here)
from video_udp_sender import SourceError  # noqa: F401

SESSION_ID = VSND.new_session_id("hd")
WINDOW_NAME = "hands (ESC / q to quit)"


def build_parser():
    ap = argparse.ArgumentParser(description="H-01 hands-only tracking sender")
    VSND.add_common_arguments(ap, WIRE.DEFAULT_PORT)
    ap.add_argument("--max-hands", type=int, default=2, help="most hands tracked at once (default 2)")
    ap.add_argument("--palm-model", default=H.PALM_MODEL)
    ap.add_argument("--hand-model", default=H.HAND_MODEL)
    ap.add_argument("--palm-conf", type=float, default=H.PALM_SCORE_THRESHOLD,
                    help="palm detector score needed to try a hand (default %(default)s)")
    ap.add_argument("--conf", type=float, default=0.6,
                    help="landmark confidence a NEW hand needs (default %(default)s)")
    ap.add_argument("--keep-conf", type=float, default=0.4,
                    help="landmark confidence below which a tracked hand is lost (default %(default)s)")
    ap.add_argument("--hand-scale", type=float, default=1.0,
                    help="your hand's size relative to an average adult hand; 1.1 = 10%% larger (default 1.0)")
    ap.add_argument("--sides", choices=("auto", "model", "swapped"), default="auto",
                    help="how the handedness label becomes L/R (see the module notes)")
    return ap


def resolve_swap_sides(mode, flipped):
    """cfg.swap_sides for --sides. `auto` follows the picture: mirrored -> the model's label is the physical hand."""
    if mode == "model":
        return False
    if mode == "swapped":
        return True
    return not flipped


class HandsPipeline(object):
    """The hands pipeline for vision_sender.run: tracker in, datagram out, window."""

    def __init__(self, a, palms, net):
        self.a = a
        self.palms = palms
        self.net = net
        self.cfg = HT.HandTrackerConfig(max_hands=a.max_hands, accept_conf=a.conf, keep_conf=a.keep_conf,
                                        hand_scale=a.hand_scale, smooth=a.smooth,
                                        swap_sides=resolve_swap_sides(a.sides, a.flip))
        self.tracker = HT.HandTracker(palms, net, lambda w, h: G.Camera.from_hfov(w, h, a.hfov, a.hfov_axis), self.cfg)

    def banner(self):
        return ["palm model  : %s  %s" % (_os.path.basename(self.a.palm_model), self.palms.active_providers),
                "hand model  : %s  %s" % (_os.path.basename(self.a.hand_model), self.net.active_providers),
                "wire        : ver %d, kind '%s'" % (WIRE.WIRE_VERSION, WIRE.KIND),
                "--hfov %.1f (ASSUMED)  --hand-scale %.2f  max hands %d  sides=%s(swap=%s)"
                % (self.a.hfov, self.a.hand_scale, self.a.max_hands, self.a.sides, self.cfg.swap_sides)]

    def process(self, frame):
        return self.tracker.update(frame, time.perf_counter())

    def restart(self):
        self.tracker.reset()

    def encode(self, result, ctx):
        return WIRE.encode_message(result, ctx.session_id, ctx.seq, ctx.now, ctx.latency_ms, ctx.source, ctx.width,
                                   ctx.height, self.tracker.camera.focal_px, ctx.mirrored)

    def to_bytes(self, message):
        return WIRE.to_bytes(message)

    def render(self, frame, result, hud):
        return OV.render(frame, result, hud.fps, hud.infer_ms, hud.source, hud.mirrored, hud.sent)

    def heartbeat_extra(self):
        t = self.tracker
        return "tracked=%d det_runs=%d lm_runs=%d" % (t.track_count, t.detector_runs, t.landmark_runs)


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.list_cameras:
        import video_udp_sender as VS
        return VS.list_cameras()
    print("[hands] H-01 hands-only sender", flush=True)
    print("[hands]   producer session id = %s" % SESSION_ID, flush=True)
    providers = VSND.providers_for(a.provider)
    try:
        palms = H.PalmDetector(a.palm_model, providers, score_threshold=a.palm_conf)
        net = H.HandLandmarkNet(a.hand_model, providers)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print("[hands] ERROR cannot load the hand models: %s" % e, flush=True)
        return 2
    pipeline = HandsPipeline(a, palms, net)
    return VSND.run(a, "hands", SESSION_ID, "(wire ver %d, kind '%s')" % (WIRE.WIRE_VERSION, WIRE.KIND), pipeline,
                    WINDOW_NAME)


if __name__ == "__main__":
    sys.exit(main())
