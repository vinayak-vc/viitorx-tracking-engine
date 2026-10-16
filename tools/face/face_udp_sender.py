#!/usr/bin/env python3
"""H-02 - THE FACE SENDER: a face only, no body, from a webcam, a LAN camera (a phone) or a video, over UDP.

    .venv\Scripts\python.exe tools/face/face_udp_sender.py --video camera:0 --flip --show
    set CAMERA_PIN=123456 && .venv\Scripts\python.exe tools/face/face_udp_sender.py --video "http://192.168.1.5:3000/viewer?room=room-abc123" --show
    .venv\Scripts\python.exe tools/face/face_udp_sender.py --video clip.mp4 --show
    .venv\Scripts\python.exe tools/face/face_udp_sender.py --list-cameras

WHAT IT DOES. Finds up to --max-faces faces (face_landmarker.py: MediaPipe's face detector, 478-point face mesh and 52-score
blendshape network on ONNX Runtime), tracks them with stable ids (face_tracking.py), smooths them, reads the 52 blendshapes, the head
pose and an estimated 3D position, and sends ONE JSON datagram per frame to 127.0.0.1:8902 (face_wire.py documents every field).
A frame with no face sends a datagram with n = 0, so a receiver can tell "no face" from "no sender".

THE FRAME LOOP (sources, pacing, heartbeat, window, socket) is tools/video/vision_sender.py, shared with the hands sender. This
file supplies only the face pipeline.

THE DETECTOR IS SHORT RANGE. BlazeFace sees a 128 px letterboxed picture and is built for a face that fills a good part of it: a
webcam at a desk, a phone held up. A face a few percent of the frame's width (a full-body clip) is not found; crop the picture to
it first (tools/face/face_eval.py --crop).

THE POSITION IS AN ESTIMATE (face_geometry.py): the eye-corner distance against an assumed 0.09 m (--face-scale) through a focal length
from an assumed field of view (--hfov). Head pose is a geometric estimate from four landmarks. Neither has been measured against
ground truth (H-02 NOT TESTED).

--flip mirrors the picture before the model (a selfie view). The blendshapes' "Left" and "Right" are the SUBJECT's.
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

import face_landmarker as F
import face_overlay as OV
import face_tracking as FT
import face_wire as WIRE
import hand_geometry as G
import vision_sender as VSND

SESSION_ID = VSND.new_session_id("fc")
WINDOW_NAME = "face (ESC / q to quit)"


def build_parser():
    ap = argparse.ArgumentParser(description="H-02 face-only tracking sender")
    VSND.add_common_arguments(ap, WIRE.DEFAULT_PORT)
    ap.add_argument("--max-faces", type=int, default=1, help="most faces tracked at once (default 1)")
    ap.add_argument("--detector-model", default=F.DETECTOR_MODEL)
    ap.add_argument("--mesh-model", default=F.MESH_MODEL)
    ap.add_argument("--blendshape-model", default=F.BLENDSHAPE_MODEL)
    ap.add_argument("--det-conf", type=float, default=F.DETECTOR_SCORE_THRESHOLD,
                    help="detector score needed to try a face (default %(default)s)")
    ap.add_argument("--conf", type=float, default=0.6,
                    help="mesh presence a NEW face needs (default %(default)s)")
    ap.add_argument("--keep-conf", type=float, default=0.4,
                    help="mesh presence below which a tracked face is lost (default %(default)s)")
    ap.add_argument("--face-scale", type=float, default=1.0,
                    help="the face's eye-corner width relative to an average adult's 0.09 m; 1.1 = 10%% wider (default 1.0)")
    ap.add_argument("--blendshapes", action=argparse.BooleanOptionalAction, default=True,
                    help="run the blendshape network and send `bs` (default on); --no-blendshapes is landmarks and head pose only")
    ap.add_argument("--landmarks", choices=WIRE.LANDMARK_MODES, default=WIRE.LANDMARKS_FULL,
                    help="which landmarks go in the datagram: full (478), mesh (468) or none (default full)")
    return ap


class FacePipeline(object):
    """The face pipeline for vision_sender.run: tracker in, datagram out, window."""

    def __init__(self, a, detector, mesh, blend):
        self.a = a
        self.detector = detector
        self.mesh = mesh
        self.blend = blend
        self.cfg = FT.FaceTrackerConfig(max_faces=a.max_faces, accept_presence=a.conf, keep_presence=a.keep_conf,
                                        face_scale=a.face_scale, smooth=a.smooth, blendshapes=a.blendshapes)
        self.tracker = FT.FaceTracker(detector, mesh, blend if a.blendshapes else None,
                                      lambda w, h: G.Camera.from_hfov(w, h, a.hfov, a.hfov_axis), self.cfg)

    def banner(self):
        lines = ["detector    : %s  %s" % (_os.path.basename(self.a.detector_model), self.detector.active_providers),
                 "mesh model  : %s  %s" % (_os.path.basename(self.a.mesh_model), self.mesh.active_providers)]
        if self.blend is not None and self.a.blendshapes:
            lines.append("blendshapes : %s  %s" % (_os.path.basename(self.a.blendshape_model), self.blend.active_providers))
        lines.append("wire        : ver %d, kind '%s', landmarks=%s" % (WIRE.WIRE_VERSION, WIRE.KIND, self.a.landmarks))
        lines.append("--hfov %.1f (ASSUMED)  --face-scale %.2f  max faces %d" % (self.a.hfov, self.a.face_scale, self.a.max_faces))
        return lines

    def process(self, frame):
        return self.tracker.update(frame, time.perf_counter())

    def restart(self):
        self.tracker.reset()

    def encode(self, result, ctx):
        return WIRE.encode_message(result, ctx.session_id, ctx.seq, ctx.now, ctx.latency_ms, ctx.source, ctx.width,
                                   ctx.height, self.tracker.camera.focal_px, ctx.mirrored, self.a.landmarks)

    def to_bytes(self, message):
        return WIRE.to_bytes(message)

    def render(self, frame, result, hud):
        return OV.render(frame, result, hud.fps, hud.infer_ms, hud.source, hud.mirrored, hud.sent)

    def heartbeat_extra(self):
        t = self.tracker
        return "tracked=%d det_runs=%d mesh_runs=%d bs_runs=%d" % (t.track_count, t.detector_runs, t.mesh_runs, t.blend_runs)


STREAM_TAG = "face"


def wire_label():
    """The wire's description for the startup banner."""
    return "(wire ver %d, kind '%s')" % (WIRE.WIRE_VERSION, WIRE.KIND)


def build_pipeline(a):
    """The face pipeline for these parsed arguments. Raises FileNotFoundError, ValueError or RuntimeError when a model cannot be loaded.
    Shared by main() and by the engine (viitorx_tracking), which runs several pipelines on one source."""
    providers = VSND.providers_for(a.provider)
    detector = F.FaceDetector(a.detector_model, providers, score_threshold=a.det_conf)
    mesh = F.FaceMeshNet(a.mesh_model, providers)
    blend = F.BlendshapeNet(a.blendshape_model, providers) if a.blendshapes else None
    return FacePipeline(a, detector, mesh, blend)


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.list_cameras:
        import video_udp_sender as VS
        return VS.list_cameras()
    print("[face] H-02 face-only sender", flush=True)
    print("[face]   producer session id = %s" % SESSION_ID, flush=True)
    try:
        pipeline = build_pipeline(a)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print("[face] ERROR cannot load the face models: %s" % e, flush=True)
        return 2
    return VSND.run(a, "face", SESSION_ID, wire_label(), pipeline, WINDOW_NAME)


if __name__ == "__main__":
    sys.exit(main())
