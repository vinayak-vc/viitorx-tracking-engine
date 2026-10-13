#!/usr/bin/env python3
"""S-01 - THE CUTOUT SEGMENTER: MediaPipe's selfie-segmentation network on ONNX Runtime, and the crop geometry around it.

WHAT IT IS. Google's MediaPipe selfie segmentation (Apache-2.0) as two ONNX files (tools/model/fetch_cutout_models.py): a SQUARE network
(256 x 256) and a LANDSCAPE one (256 x 144). Both read RGB scaled to 0..1 (no mean or std: the exporter's preprocessor_config says
do_normalize false) as NCHW float32 and write ONE sigmoid map, `alphas`, of the same size: the probability that a pixel belongs to a
person. 1.6-1.9 ms a call on DirectML (RTX 3060, measured).

WHY A CROP AND NOT THE WHOLE FRAME. The network sees 256 pixels across whatever it is given. Someone 2-3 m from a camera is a quarter of a
1080p frame, which is 65 pixels tall to the network. On the two-dancer clip the whole-frame mask was one merged blob with the dark curtain
and the floor lights attached; the same network on a square crop around each detected person (they are then ~256 pixels tall) returned two
separate, clean silhouettes (S-01 report). So cutout_engine.py cuts one square per person and this module owns the geometry: how big the
square is, how it is cut when it hangs over the picture's edge, and how its 256 x 256 answer is laid back into the full-frame canvas.

WHAT IS NOT HERE. Finding the people (the detector, injected into the engine), following them (person_tracker.py), and everything done to
the probabilities afterwards (cutout_matte.py). This module imports nothing from tools/.

THE LIMIT, stated here because it decides what to expect. This network was trained for selfies and video calls: a person, a few metres of
background, mostly upper body. It is reliable on a lone standing or sitting person in an ordinary room. It is NOT reliable on dark clothing
against a dark background (the legs of the dark-trousered dancer come back incomplete), and it has no notion of "this person" versus "that
person": with two people in one crop both are returned.
"""

import os
import time

import cv2
import numpy as np

import evidence_paths as EV

#: Where the weights are expected. models/ sits inside python-sidecar~, which Unity does not import. *.onnx is not in version control.
GENERAL_MODEL = (os.environ.get("VIRTUAL_MIRROR_SEGMENTER")
                 or os.path.join(EV.PROJECT_ROOT, "models", "selfie_segmenter_general.onnx"))
LANDSCAPE_MODEL = os.path.join(EV.PROJECT_ROOT, "models", "selfie_segmenter_landscape.onnx")

#: Both files are 0.46 MB. Anything far from that is not the file the S-01 measurements were made with.
EXPECTED_MODEL_BYTES = 462352

#: model_id values of the wire (cutout_wire.MODEL_*): which network produced a frame's mask.
MODEL_GENERAL = 1
MODEL_LANDSCAPE = 2

#: A whole-frame picture at least this much wider than tall goes to the landscape network (16:9 is 1.78, 4:3 is 1.33). Below it the
#: square network is used and the picture is stretched into it, which is what MediaPipe's own graph does.
LANDSCAPE_MIN_ASPECT = 1.4

#: Smallest crop side, frame pixels. A box of a few pixels is a detector glitch, not a person worth a network call.
MIN_SIDE = 64

#: Fraction of the crop side over which a pasted mask fades in from the crop's edge, so two overlapping crops never leave a straight seam.
DEFAULT_FEATHER = 0.08

#: How far past a neighbour's box, as a fraction of its width and height on each side, blank_neighbours paints: the detector's box is a few
#: percent tight on a hat, a raised hand or a trailing sleeve, and a sliver of the neighbour left in view is a sliver the network may keep.
NEIGHBOUR_GROW = 0.04

#: The ring around a neighbour's grown box, as a fraction of its size, whose MEDIAN colour is the colour the box is painted with: the
#: background just beside the person is the best available guess for the background behind them.
NEIGHBOUR_RING = 0.14

#: Pixels a ring needs for its median to mean anything; a neighbour whose ring is smaller (the box fills the crop) is left as it is.
NEIGHBOUR_MIN_RING_PIXELS = 24

#: How far outside the grown box the paint fades out, as a fraction of the picture's side: a hard rectangle is a feature the network may
#: segment itself.
NEIGHBOUR_SOFTNESS = 0.024


class SelfieSegmenter:
    """One MediaPipe selfie-segmentation network. Stateless: call infer() with any BGR picture, get its probability map back.

    Not thread-safe: one instance owns one ONNX session and one reusable input tensor.
    """

    def __init__(self, model_path, providers=None):
        import onnxruntime as ort
        self.model_path = model_path
        if not os.path.isfile(model_path):
            raise FileNotFoundError("segmenter weights not found: %s - run tools/model/fetch_cutout_models.py" % model_path)
        if providers is None:
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, sess_options=ort.SessionOptions(), providers=providers)
        self.active_providers = self.session.get_providers()
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("%s: expected one input and one output, found %d and %d" % (model_path, len(inputs), len(outputs)))
        shape = inputs[0].shape
        oshape = outputs[0].shape
        # Dimension 0 is a symbolic batch size; the rest are fixed by the exported graph. Refuse anything else rather than guess.
        if len(shape) != 4 or shape[1] != 3 or not isinstance(shape[2], int) or not isinstance(shape[3], int):
            raise ValueError("%s: input %s is not [batch, 3, height, width] with fixed sizes" % (model_path, shape))
        if len(oshape) != 4 or oshape[1] != 1 or oshape[2] != shape[2] or oshape[3] != shape[3]:
            raise ValueError("%s: output %s is not one channel of the input's %dx%d" % (model_path, oshape, shape[3], shape[2]))
        self.input_name = inputs[0].name
        self.height = int(shape[2])
        self.width = int(shape[3])
        # One reusable tensor: a 256x256 float tensor is 0.8 MB, not worth an allocation per person per frame at 30 fps.
        self._blob = np.empty((1, 3, self.height, self.width), dtype=np.float32)
        #: ms spent in the last infer(), for the heartbeat. perf_counter, never time.time().
        self.last_ms = 0.0
        self.calls = 0

    def describe(self):
        """One line for the startup banner, so the weights and the provider are never invisible."""
        return "%s (%dx%d, %.2f MB) on %s" % (os.path.basename(self.model_path), self.width, self.height,
                                              os.path.getsize(self.model_path) / 1e6, self.active_providers[0])

    def infer(self, bgr, flip_average=False, neighbours=None, keep=None):
        """Probability that each pixel is a person: float32 [height, width] in 0..1, at the NETWORK's size, not the picture's.

        `bgr` is a uint8 BGR picture of any size; it is resized to the network's input (area-averaged when shrinking, which is what
        keeps a thin limb from vanishing between sampled pixels).

        `neighbours` are the boxes of the OTHER people in this crop and `keep` is this person's own box, both (x1, y1, x2, y2) normalised to
        the crop: every neighbour is painted over with the background around it before the network looks (blank_neighbours), so the network
        is asked about one person and not about a pair. Measured on the two-dancer clip it is what removes the dark-curtain blob that two
        people standing apart otherwise get joined by.

        `flip_average` also runs the picture's mirror image and averages the two answers (test-time augmentation): the network's blocky
        low-confidence patches, which differ between a picture and its mirror, partly cancel. Measured: it completed a patch of dress
        that the single pass left see-through, and cut one-frame flicker by ~10% (1.72% to 1.55% of the foreground), for ~+3 ms a call
        (one person: 5.3 to 8.2 ms of network time per frame).
        """
        t0 = time.perf_counter()
        h, w = bgr.shape[:2]
        if w > 2 * self.width or h > 2 * self.height:
            # INTER_AREA straight from a ~1000 px crop to 256 costs 2.6 ms (measured), a linear step to twice the target costs 0.1 ms
            # and leaves the area step an exact 2:1 average, which is fast and keeps the anti-aliasing a thin limb needs.
            bgr = cv2.resize(bgr, (2 * self.width, 2 * self.height), interpolation=cv2.INTER_LINEAR)
            h, w = bgr.shape[:2]
        interp = cv2.INTER_AREA if (w > self.width or h > self.height) else cv2.INTER_LINEAR
        small = cv2.resize(bgr, (self.width, self.height), interpolation=interp)
        if neighbours:
            blank_neighbours(small, neighbours, keep)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        np.multiply(rgb.transpose(2, 0, 1), 1.0 / 255.0, out=self._blob[0], casting="unsafe")
        out = self.session.run(None, {self.input_name: self._blob})[0][0, 0]
        if flip_average:
            # Two calls, not one batch of two: on DirectML a batch-2 run measured 5x SLOWER than two batch-1 runs (25 vs ~5 ms).
            np.multiply(rgb[:, ::-1].transpose(2, 0, 1), 1.0 / 255.0, out=self._blob[0], casting="unsafe")
            out = 0.5 * (out + self.session.run(None, {self.input_name: self._blob})[0][0, 0][:, ::-1])
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        self.calls += 1
        return np.clip(out, 0.0, 1.0)

    def warmup(self):
        """One call on a blank picture, so DirectML compiles its graph now and not in the middle of the first frame (~250 ms)."""
        self.infer(np.zeros((self.height, self.width, 3), dtype=np.uint8))


def size_for(width, height, long_side):
    """The (w, h) a picture is worked at: its long side scaled to `long_side`, never enlarged. long_side <= 0 means 'as it is'."""
    if long_side <= 0 or max(width, height) <= long_side:
        return int(width), int(height)
    scale = long_side / float(max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def square_roi(box, frame_w, frame_h, margin, min_side=MIN_SIDE):
    """The square crop for one person: (x0, y0, side) in FRAME pixels. x0 and y0 may be negative: the crop may hang over the edge.

    `box` is the detector's NORMALISED (x1, y1, x2, y2). The square is centred on the box and as long as its longer side plus `margin` of
    it on each end: a standing person's box is tall and narrow, so the square is mostly background left and right, which the network
    needs as context and which an outstretched arm needs as room. 0.18 is wide enough for the arms of the dancers in the test clips.
    """
    x1, y1, x2, y2 = box
    bx1, by1, bx2, by2 = x1 * frame_w, y1 * frame_h, x2 * frame_w, y2 * frame_h
    cx = 0.5 * (bx1 + bx2)
    cy = 0.5 * (by1 + by2)
    side = max(bx2 - bx1, by2 - by1) * (1.0 + 2.0 * margin)
    s = int(round(max(float(min_side), side)))
    return int(round(cx - s / 2.0)), int(round(cy - s / 2.0)), s


def crop_square(frame, roi):
    """The square `roi` cut out of `frame`. Where it hangs over the picture the border pixels are repeated (not black: a black band reads
    to the network as part of the scene and bleeds a false edge into the mask)."""
    x0, y0, s = roi
    h, w = frame.shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(w, x0 + s), min(h, y0 + s)
    if xb <= xa or yb <= ya:
        return np.zeros((s, s, 3), dtype=np.uint8)
    piece = frame[ya:yb, xa:xb]
    top, bottom, left, right = ya - y0, (y0 + s) - yb, xa - x0, (x0 + s) - xb
    if top or bottom or left or right:
        piece = cv2.copyMakeBorder(piece, top, bottom, left, right, cv2.BORDER_REPLICATE)
    return piece


def _rect_pixels(shape, rect, grow):
    """The pixel box (xa, ya, xb, yb) of a normalised rect grown by `grow` of its own size on each side, clipped to the picture, or None
    when nothing of it is left inside."""
    h, w = shape
    x1, y1, x2, y2 = rect
    gx = grow * (x2 - x1)
    gy = grow * (y2 - y1)
    xa = max(0, int(np.floor((x1 - gx) * w)))
    ya = max(0, int(np.floor((y1 - gy) * h)))
    xb = min(w, int(np.ceil((x2 + gx) * w)))
    yb = min(h, int(np.ceil((y2 + gy) * h)))
    if xb <= xa or yb <= ya:
        return None
    return xa, ya, xb, yb


def _soft_edges(count, low, high, soft):
    """A 1-D weight over `count` pixel centres: 1 from `low` to `high`, falling to 0 over `soft` pixels outside them (a smoothstep)."""
    centres = np.arange(count, dtype=np.float32) + 0.5
    rise = np.clip((centres - (low - soft)) / soft, 0.0, 1.0)
    fall = np.clip(((high + soft) - centres) / soft, 0.0, 1.0)
    t = np.minimum(rise, fall)
    return t * t * (3.0 - 2.0 * t)


def _ring_colour(picture, outer, inner, own):
    """The median colour of the ring between the pixel boxes `outer` and `inner`, as a float vector of three, or None when too little of the
    ring is left. The ring is four slices of the picture (no per-pixel mask), every second pixel of each; a slice that this person's own box
    mostly covers is left out, so the colour is the background's and not the person's."""
    oxa, oya, oxb, oyb = outer
    ixa, iya, ixb, iyb = inner
    pieces = []
    count = 0
    for xa, ya, xb, yb in ((oxa, oya, oxb, iya), (oxa, iyb, oxb, oyb), (oxa, iya, ixa, iyb), (ixb, iya, oxb, iyb)):
        if xb <= xa or yb <= ya:
            continue
        if own is not None:
            overlap = max(0, min(xb, own[2]) - max(xa, own[0])) * max(0, min(yb, own[3]) - max(ya, own[1]))
            if overlap * 2 > (xb - xa) * (yb - ya):
                continue
        piece = picture[ya:yb, xa:xb].reshape(-1, 3)
        if piece.shape[0] > 400:
            piece = piece[::2]
        pieces.append(piece)
        count = count + piece.shape[0]
    if count < NEIGHBOUR_MIN_RING_PIXELS:
        return None
    return np.median(np.concatenate(pieces, axis=0), axis=0)


def blank_neighbours(picture, neighbours, keep=None):
    """Paint every neighbour's box with the colour of the background around it. Modifies `picture` IN PLACE and returns how many boxes
    were painted.

    `picture` is the uint8 BGR picture the network is about to read (network-sized). `neighbours` and `keep` are (x1, y1, x2, y2) normalised
    to it: the other people's boxes, and this person's own. A box is grown by NEIGHBOUR_GROW and painted with the median colour of the ring
    around it (NEIGHBOUR_RING); the paint fades out over NEIGHBOUR_SOFTNESS of the picture's side outside the box, so there is no hard
    rectangle for the network to see. This person's own box is never painted, which keeps an arm that reaches into a neighbour's box. Two
    boxes that mostly coincide leave almost nothing to paint, by design: people who overlap cannot be told apart by their boxes and are left
    to the network, as before. Only the window around each box is touched, so the cost follows the neighbours' size, not the picture's.
    """
    h, w = picture.shape[:2]
    own = _rect_pixels((h, w), keep, 0.0) if keep is not None else None
    soft = max(1.0, NEIGHBOUR_SOFTNESS * max(h, w))
    painted = 0
    for rect in neighbours:
        box = _rect_pixels((h, w), rect, NEIGHBOUR_GROW)
        outer = _rect_pixels((h, w), rect, NEIGHBOUR_RING)
        if box is None or outer is None:
            continue
        fill = _ring_colour(picture, outer, box, own)
        if fill is None:
            continue
        xa, ya, xb, yb = box
        wxa = max(0, int(xa - soft))
        wya = max(0, int(ya - soft))
        wxb = min(w, int(np.ceil(xb + soft)))
        wyb = min(h, int(np.ceil(yb + soft)))
        weight = _soft_edges(wyb - wya, ya - wya, yb - wya, soft)[:, None] * _soft_edges(wxb - wxa, xa - wxa, xb - wxa, soft)[None, :]
        if own is not None:
            oxa = max(own[0], wxa)
            oya = max(own[1], wya)
            oxb = min(own[2], wxb)
            oyb = min(own[3], wyb)
            if oxb > oxa and oyb > oya:
                weight[oya - wya:oyb - wya, oxa - wxa:oxb - wxa] = 0.0
        region = picture[wya:wyb, wxa:wxb].astype(np.float32)
        region += (fill.astype(np.float32) - region) * weight[:, :, None]
        picture[wya:wyb, wxa:wxb] = (region + 0.5).astype(np.uint8)
        painted = painted + 1
    return painted


def feather_window(side, fraction=DEFAULT_FEATHER):
    """A side x side float32 window: 1 inside, falling linearly to ~0 at each edge over `fraction` of the side."""
    ramp_px = max(1, int(side * fraction))
    ramp = np.minimum(np.arange(side) + 1, np.arange(side)[::-1] + 1).astype(np.float32) / ramp_px
    ramp = np.minimum(1.0, ramp)
    return np.minimum(ramp[:, None], ramp[None, :])


def paste_max(canvas, alpha_square, roi, scale, feather=DEFAULT_FEATHER):
    """Lay one crop's probability map into the full-frame canvas by taking the larger of the two values at each pixel.

    `canvas` is float32 [Hm, Wm] at the WORKING size; `roi` is in frame pixels; `scale` is canvas width / frame width. The crop's own
    edge fades out (feather_window) so a person whose limb leaves their crop is finished by their neighbour's crop, not cut by it. The
    larger value wins because every crop is an independent opinion that a person is here: two crops that both see the same person agree,
    and where one is blind the other still counts.
    """
    x0, y0, s = roi
    sm = max(1, int(round(s * scale)))
    mx0 = int(round(x0 * scale))
    my0 = int(round(y0 * scale))
    interp = cv2.INTER_AREA if sm < alpha_square.shape[1] else cv2.INTER_LINEAR
    a = cv2.resize(alpha_square, (sm, sm), interpolation=interp)
    a *= feather_window(sm, feather)
    ch, cw = canvas.shape[:2]
    xa, ya, xb, yb = max(0, mx0), max(0, my0), min(cw, mx0 + sm), min(ch, my0 + sm)
    if xb <= xa or yb <= ya:
        return
    np.maximum(canvas[ya:yb, xa:xb], a[ya - my0:yb - my0, xa - mx0:xb - mx0], out=canvas[ya:yb, xa:xb])
