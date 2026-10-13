#!/usr/bin/env python3
"""H-02 - THE FACE DEMO WINDOW: the camera picture with every tracked face drawn on it.

Used by tools/face/face_udp_sender.py --show. It draws what the tracker REPORTS (the smoothed landmarks, the head axes, the
strongest blendshapes, the estimated distance), so the window is a check on the numbers that go on the wire and not on a
separate code path. Display only: nothing here feeds back into tracking.
"""

import cv2
import numpy as np

import face_landmarker as F
import face_tracking as FT

COLOR_POINT = (90, 230, 90)
COLOR_IRIS = (255, 220, 60)
COLOR_TEXT = (255, 255, 255)
COLOR_HELD_SCALE = 0.45

#: How many of the strongest blendshapes the side panel lists.
PANEL_ROWS = 10
PANEL_WIDTH = 190
BAR_WIDTH = 90
#: The `_neutral` channel is the opposite of expression, so it is left out of the strongest-first list.
SKIP_CHANNEL = 0

AXIS_COLORS = ((60, 60, 255), (60, 220, 60), (255, 120, 60))   # head x, y, z -> red, green, blue (BGR)
AXIS_LENGTH_FRACTION = 0.45


def _dim(color, face):
    if face.state == FT.HELD:
        return tuple(int(c * COLOR_HELD_SCALE) for c in color)
    return color


def draw_text(canvas, text, org, color=COLOR_TEXT, scale=0.5):
    cv2.putText(canvas, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(canvas, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_face(canvas, face):
    """Draw one TrackedFace's landmarks and head axes on `canvas` (BGR, the frame the model saw)."""
    h, w = canvas.shape[:2]
    pts = np.stack([face.image[:, 0] * w, face.image[:, 1] * h], axis=1)
    for i, p in enumerate(pts):
        color = COLOR_IRIS if i >= F.NUM_MESH_LANDMARKS else COLOR_POINT
        cv2.circle(canvas, (int(p[0]), int(p[1])), 1, _dim(color, face), -1, cv2.LINE_AA)
    if face.pose_ok:
        origin = pts[F.NOSE_TIP]
        length = AXIS_LENGTH_FRACTION * float(np.linalg.norm(pts[F.LEFT_EYE_OUTER] - pts[F.RIGHT_EYE_OUTER]))
        for axis in range(3):
            tip = origin + length * face.rotation[0:2, axis] * (1.0 if axis != 2 else -1.0)
            cv2.line(canvas, (int(origin[0]), int(origin[1])), (int(tip[0]), int(tip[1])),
                     _dim(AXIS_COLORS[axis], face), 2, cv2.LINE_AA)
    x0 = int(pts[:F.NUM_MESH_LANDMARKS, 0].min())
    y0 = int(pts[:F.NUM_MESH_LANDMARKS, 1].min())
    lines = ["#%d  yaw %.0f  pitch %.0f  roll %.0f" % (face.id, face.yaw, face.pitch, face.roll)]
    if face.position is not None:
        lines.append("Z %.2f m  (est.)" % face.position[2])
    else:
        lines.append("Z n/a (%s)" % (face.reason or "?"))
    for i, text in enumerate(lines):
        draw_text(canvas, text, (max(2, x0), max(14, y0 - 8 - 16 * (len(lines) - 1 - i))))


def draw_panel(canvas, face, x, y):
    """The strongest blendshapes of one face as labelled bars, top-left anchored at (x, y)."""
    if face.blendshapes is None:
        draw_text(canvas, "blendshapes off", (x, y + 14))
        return
    order = [i for i in np.argsort(-face.blendshapes) if i != SKIP_CHANNEL][:PANEL_ROWS]
    for row, idx in enumerate(order):
        yy = y + row * 18
        value = float(face.blendshapes[idx])
        cv2.rectangle(canvas, (x, yy), (x + BAR_WIDTH, yy + 12), (60, 60, 60), 1)
        cv2.rectangle(canvas, (x, yy), (x + int(BAR_WIDTH * min(1.0, max(0.0, value))), yy + 12), _dim(COLOR_POINT, face), -1)
        draw_text(canvas, "%s %.2f" % (F.BLENDSHAPE_NAMES[idx], value), (x + BAR_WIDTH + 6, yy + 11), scale=0.4)


def draw_status(canvas, fps, infer_ms, faces, source, mirrored, sent):
    """The top-left HUD: rate, cost, face count and what the sender is doing."""
    lines = ["%s%s   %.1f fps   %.1f ms/frame   faces %d   sent %d"
             % (source, " (mirrored)" if mirrored else "", fps, infer_ms, len(faces), sent),
             "ESC / q quit   |   a short-range detector: sit within ~1.5 m, face filling a good part of the picture"]
    for i, text in enumerate(lines):
        draw_text(canvas, text, (8, 18 + 18 * i))


def render(frame, faces, fps, infer_ms, source, mirrored, sent):
    """A new image: `frame` with every face and the HUD drawn on it, and the blendshape panel in a strip to its right
    (appended, so it never covers the face however narrow the picture is)."""
    canvas = frame.copy()
    for face in faces:
        draw_face(canvas, face)
    draw_status(canvas, fps, infer_ms, faces, source, mirrored, sent)
    strip = np.full((canvas.shape[0], PANEL_WIDTH + BAR_WIDTH, 3), 24, dtype=np.uint8)
    if faces:
        draw_panel(strip, faces[0], 8, 50)
    return np.hstack([canvas, strip])
