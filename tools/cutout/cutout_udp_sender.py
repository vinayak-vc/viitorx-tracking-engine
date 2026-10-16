#!/usr/bin/env python3
"""S-01 - THE CUTOUT SENDER: the people in a webcam, a LAN camera (a phone) or a video, cut out of their background, over UDP.

    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_sender.py --video camera:0 --show
    set CAMERA_PIN=123456 && .venv\\Scripts\\python.exe tools/cutout/cutout_udp_sender.py --video "http://192.168.1.5:3000/viewer?room=room-abc123" --show
    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_sender.py --video clip.mp4 --show
    .venv\\Scripts\\python.exe tools/cutout/cutout_udp_sender.py --list-cameras

WHAT IT DOES. Finds the people in each picture (YOLOX-s, tools/video/host_person_detector.py), follows them (PersonTracker), cuts a square
around each and asks MediaPipe's selfie-segmentation network which pixels are the person (cutout_segmenter.py), then cleans the answer
into a soft mask (cutout_matte.py). It sends the PICTURE and the MASK, one frame per picture, to 127.0.0.1:8903 (cutout_wire.py documents
every byte). Unity does not segment anything: it receives the pair and draws it (a camera view, a person cut out of its background, or a
solid colour silhouette). A picture with nobody in it is still sent, with an empty mask, so the camera view keeps playing.

THE FRAME LOOP (sources, pacing, heartbeat, window, socket) is tools/video/vision_sender.py, shared with the hands and face senders. This
file supplies only the cutout pipeline. It lives in tools/ because it reuses the video sender's source handling and the detector, which
are in tools/; the segmenter, the matte, the engine and the wire are at the sidecar root and import nothing from tools/.

NO PERSON DETECTOR? The engine then segments the WHOLE picture, which is right for a face filling a webcam and poor for a person far from
the camera (cutout_segmenter.py says why). --roi on refuses to start without the detector's weights; --roi off never loads them.

--flip mirrors the picture BEFORE everything (a selfie view). Unity can mirror the display itself and instantly, so leave it off and use
Unity's own mirror switch unless something else reads this stream too.
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

import cv2
import numpy as np

import cutout_engine as E
import cutout_matte as M
import cutout_overlay as OV
import cutout_segmenter as S
import cutout_wire as WIRE
import vision_sender as VSND
import video_udp_sender as VS

SESSION_ID = VSND.new_session_id("cu")
WINDOW_NAME = "cutout (ESC / q to quit)"

#: Live sources: a file is the only one that is not.
LIVE_KINDS = (VS.KIND_CAMERA, VS.KIND_STREAM, VS.KIND_LAN)

COLOR_CHOICES = ("rgba", "jpeg", "none")
MASK_CHOICES = ("raw", "deflate")


def build_parser():
    ap = argparse.ArgumentParser(description="S-01 cutout (person mask) sender")
    VSND.add_common_arguments(ap, WIRE.DEFAULT_PORT)
    ap.add_argument("--roi", choices=("auto", "on", "off"), default="auto",
                    help="segment a square crop around each detected person (on), the whole picture only (off), or crops when the "
                         "detector's weights are present (auto, default)")
    ap.add_argument("--detector-model", default=None, help="the person detector (default: models/yolox_s.onnx)")
    ap.add_argument("--det-conf", type=float, default=0.40, help="detector score needed to count a person (default %(default)s)")
    ap.add_argument("--roi-margin", type=float, default=0.18, help="crop margin as a fraction of the person's size (default %(default)s)")
    ap.add_argument("--max-people", type=int, default=4, help="most people segmented per frame (default %(default)s)")
    ap.add_argument("--detect-every", type=int, default=1, help="run the detector every N frames (default %(default)s)")
    ap.add_argument("--fallback", action=argparse.BooleanOptionalAction, default=True,
                    help="with nobody detected, segment the whole picture (default on)")
    ap.add_argument("--isolate", action=argparse.BooleanOptionalAction, default=True,
                    help="paint the other people out of each person's crop before the network looks, so two people standing apart are not "
                         "joined by the dark background between them (default on; does nothing for one person)")
    ap.add_argument("--general-model", default=S.GENERAL_MODEL, help="the square selfie network (default %(default)s)")
    ap.add_argument("--landscape-model", default=S.LANDSCAPE_MODEL, help="the 16:9 selfie network (default %(default)s)")
    ap.add_argument("--mask-long-side", type=int, default=640,
                    help="the mask's long side in pixels, never enlarged; 0 = the picture's own size (default %(default)s)")
    ap.add_argument("--guided", action=argparse.BooleanOptionalAction, default=True,
                    help="snap the mask's edge to the picture's own edges (default on)")
    ap.add_argument("--cleanup", action=argparse.BooleanOptionalAction, default=True,
                    help="drop far specks and fill pinholes (default on)")
    ap.add_argument("--edge-low", type=float, default=0.50, help="probability at or below which a pixel is background (default %(default)s)")
    ap.add_argument("--edge-high", type=float, default=0.85, help="probability at or above which a pixel is person (default %(default)s)")
    ap.add_argument("--tta", action=argparse.BooleanOptionalAction, default=False,
                    help="average each answer with the mirrored picture's: a cleaner mask for ~2 ms more per person (default off)")
    ap.add_argument("--color", choices=COLOR_CHOICES, default="rgba",
                    help="how the picture travels: raw RGBA (default, fastest on this machine), JPEG (a network), or none (mask only)")
    ap.add_argument("--jpeg-quality", type=int, default=85, help="JPEG quality 1-100 with --color jpeg (default %(default)s)")
    ap.add_argument("--mask-enc", choices=MASK_CHOICES, default="raw", help="the mask's encoding: raw or deflate (default %(default)s)")
    ap.add_argument("--send-long-side", type=int, default=1280,
                    help="shrink the picture sent so its long side is at most this; 0 = as it is (default %(default)s)")
    ap.add_argument("--chunk-bytes", type=int, default=WIRE.DEFAULT_CHUNK_BYTES,
                    help="payload bytes per datagram; ~1200 for a receiver on a real network (default %(default)s)")
    return ap


def build_engine(a, general, landscape, detector):
    """The engine for these arguments. `a.smooth` is the temporal filter (the common --smooth switch)."""
    matte = M.MatteConfig(guided=a.guided, temporal=a.smooth, cleanup=a.cleanup, edge_low=a.edge_low, edge_high=a.edge_high)
    cfg = E.CutoutConfig(mask_long_side=a.mask_long_side, roi=(detector is not None), roi_margin=a.roi_margin,
                         max_people=a.max_people, detect_every=a.detect_every, fallback=a.fallback, matte=matte, tta=a.tta,
                         isolate=a.isolate)
    return E.CutoutEngine(general, landscape, detector, cfg)


class CutoutPipeline:
    """The cutout pipeline for vision_sender.run: picture in, chunked frame out, window."""

    def __init__(self, a, engine, general, landscape, detector):
        self.a = a
        self.engine = engine
        self.general = general
        self.landscape = landscape
        self.detector = detector
        self._frame = None
        self._session32 = int(SESSION_ID[2:], 16) & 0xFFFFFFFF

    def banner(self):
        lines = ["selfie net  : %s" % self.general.describe()]
        if self.landscape is not None:
            lines.append("landscape   : %s" % self.landscape.describe())
        lines.append("detector    : %s" % (self.detector.describe() if self.detector is not None else "none (whole-picture mode only)"))
        lines.append("wire        : ver %d, kind %d, color=%s mask=%s send<=%d chunk=%d B"
                     % (WIRE.WIRE_VERSION, WIRE.KIND_FRAME, self.a.color, self.a.mask_enc, self.a.send_long_side, self.a.chunk_bytes))
        lines.append("matte       : mask long side %d, guided=%s temporal=%s cleanup=%s edge %.2f-%.2f"
                     % (self.a.mask_long_side, self.a.guided, self.a.smooth, self.a.cleanup, self.a.edge_low, self.a.edge_high))
        lines.append("people      : up to %d, isolate=%s, tta=%s, fallback=%s" % (self.a.max_people, self.a.isolate, self.a.tta, self.a.fallback))
        return lines

    def process(self, frame):
        self._frame = frame
        return self.engine.update(frame, time.perf_counter())

    def restart(self):
        self.engine.reset()

    def encode(self, result, ctx):
        """The Frame (cutout_wire.Frame) for this picture and its mask: the picture shrunk to --send-long-side and packed, the mask
        raw or deflated."""
        a = self.a
        picture = self._frame
        h, w = picture.shape[:2]
        sw, sh = S.size_for(w, h, a.send_long_side)
        if (sw, sh) != (w, h):
            picture = cv2.resize(picture, (sw, sh), interpolation=cv2.INTER_AREA)
        if a.color == "none":
            color_enc, color, cw, ch = WIRE.COLOR_NONE, b"", 0, 0
        elif a.color == "jpeg":
            ok, buf = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, int(min(100, max(1, a.jpeg_quality)))])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            color_enc, color, cw, ch = WIRE.COLOR_JPEG, buf.tobytes(), sw, sh
        else:
            color_enc, color, cw, ch = WIRE.COLOR_RGBA, cv2.cvtColor(picture, cv2.COLOR_BGR2RGBA).tobytes(), sw, sh
        alpha = result.alpha
        mh, mw = alpha.shape[:2]
        if a.mask_enc == "deflate":
            mask_enc, mask = WIRE.MASK_DEFLATE, WIRE.deflate_raw(alpha.tobytes())
        else:
            mask_enc, mask = WIRE.MASK_RAW, alpha.tobytes()
        flags = 0
        if ctx.mirrored:
            flags |= WIRE.FLAG_MIRRORED
        if ctx.source in LIVE_KINDS:
            flags |= WIRE.FLAG_LIVE
        if result.people == 0:
            flags |= WIRE.FLAG_NOBODY
        return WIRE.Frame(self._session32, ctx.seq, ctx.now, ctx.latency_ms, result.infer_ms, flags, color_enc, mask_enc,
                          result.model_id, cw, ch, mw, mh, result.fg_permille, result.people, color, mask)

    def to_bytes(self, message):
        return WIRE.encode_datagrams(message, self.a.chunk_bytes)

    def render(self, frame, result, hud):
        return OV.render(frame, result, hud.fps, hud.infer_ms, hud.source, hud.mirrored, hud.sent)

    def heartbeat_extra(self):
        return self.engine.summary()


def load_networks(a, providers):
    """The two selfie networks. The landscape one is optional (a missing file is a warning, the square one then serves 16:9 too)."""
    general = S.SelfieSegmenter(a.general_model, providers)
    landscape = None
    if _os.path.isfile(a.landscape_model):
        landscape = S.SelfieSegmenter(a.landscape_model, providers)
    else:
        print("[cutout] WARNING the landscape network is missing (%s); the square one is used for every picture" % a.landscape_model,
              flush=True)
    return general, landscape


def load_detector(a, providers):
    """The person detector, or None. `--roi on` refuses to go on without it; `--roi auto` says so and continues."""
    if a.roi == "off":
        return None
    import host_person_detector as HPD
    try:
        return HPD.HostPersonDetector(a.detector_model, conf=a.det_conf, providers=providers)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        if a.roi == "on":
            raise
        print("[cutout] WARNING no person detector (%s): segmenting the whole picture only. Run tools/model/fetch_yolox_s.py "
              "for per-person crops." % e, flush=True)
        return None


def warm_up(general, landscape, detector):
    """Run each network once on a blank picture so DirectML compiles now and not inside the first frame (~250 ms each)."""
    general.warmup()
    if landscape is not None:
        landscape.warmup()
    if detector is not None:
        detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))


STREAM_TAG = "cutout"


def wire_label():
    """The wire's description for the startup banner."""
    return "(wire ver %d, kind %d)" % (WIRE.WIRE_VERSION, WIRE.KIND_FRAME)


def build_pipeline(a):
    """The cutout pipeline for these parsed arguments, with its networks warmed up. Raises FileNotFoundError, ValueError or RuntimeError when
    a required model cannot be loaded. Shared by main() and by the engine (viitorx_tracking), which runs several pipelines on one source."""
    providers = VSND.providers_for(a.provider)
    general, landscape = load_networks(a, providers)
    detector = load_detector(a, providers)
    warm_up(general, landscape, detector)
    return CutoutPipeline(a, build_engine(a, general, landscape, detector), general, landscape, detector)


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.list_cameras:
        return VS.list_cameras()
    print("[cutout] S-01 cutout sender", flush=True)
    print("[cutout]   producer session id = %s" % SESSION_ID, flush=True)
    try:
        pipeline = build_pipeline(a)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print("[cutout] ERROR cannot load the models: %s" % e, flush=True)
        return 2
    return VSND.run(a, "cutout", SESSION_ID, wire_label(), pipeline, WINDOW_NAME)


if __name__ == "__main__":
    sys.exit(main())
