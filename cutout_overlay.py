#!/usr/bin/env python3
"""S-01 - THE CUTOUT DEMO WINDOW: the picture with its crops drawn on it, next to the cutout on a checkerboard, plus a status line.

Pure drawing (OpenCV + numpy): it reads a CutoutResult and the frame the engine saw and returns an image. It is what `--show` displays and
what cutout_eval.py writes to its contact sheets, so what you look at while tuning is exactly what the engine produced.
"""

import cv2
import numpy as np

#: Checkerboard square side and the two greys, so transparency reads as transparency and a white shirt is not mistaken for background.
CHECK_SIDE = 24
CHECK_DARK = 100
CHECK_LIGHT = 170

#: Widest picture the window shows, pixels (both panels side by side).
MAX_WIDTH = 1500


def checkerboard(h, w):
    """A uint8 BGR checkerboard [h, w, 3]."""
    yy, xx = np.mgrid[0:h, 0:w]
    c = np.where(((yy // CHECK_SIDE) + (xx // CHECK_SIDE)) % 2 == 0, CHECK_DARK, CHECK_LIGHT).astype(np.uint8)
    return np.dstack((c, c, c))


def composite(frame, alpha_u8, background=None):
    """`frame` (BGR uint8) over `background` (a BGR image or None for the checkerboard) through `alpha_u8` (any size; stretched)."""
    h, w = frame.shape[:2]
    a = cv2.resize(alpha_u8, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32) * (1.0 / 255.0)
    if background is None:
        background = checkerboard(h, w)
    out = frame.astype(np.float32) * a[:, :, None] + background.astype(np.float32) * (1.0 - a[:, :, None])
    return out.astype(np.uint8)


def render(frame, result, fps, infer_ms, source, mirrored, sent):
    """One window image: [frame with the crops drawn] [cutout on a checkerboard], and a line of numbers on top."""
    h, w = frame.shape[:2]
    left = frame.copy()
    for (x0, y0, s) in result.boxes:
        cv2.rectangle(left, (x0, y0), (x0 + s, y0 + s), (0, 255, 0), max(2, w // 500))
    right = composite(frame, result.alpha)
    both = np.hstack((left, right))
    scale = min(1.0, MAX_WIDTH / float(both.shape[1]))
    if scale < 1.0:
        both = cv2.resize(both, (int(both.shape[1] * scale), int(both.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    text = ("%s  %.1f fps  %.1f ms  people=%d  mode=%s  fg=%.1f%%  %s  sent=%d"
            % (source, fps, infer_ms, result.people, result.mode, result.fg_permille / 10.0, "mirrored" if mirrored else "not mirrored",
               sent))
    cv2.rectangle(both, (0, 0), (both.shape[1], 26), (0, 0, 0), -1)
    cv2.putText(both, text, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return both
