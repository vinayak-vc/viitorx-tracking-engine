#!/usr/bin/env python3
"""H-01 - THE HANDS DEMO WINDOW: the camera picture with every tracked hand drawn on it.

Used by hands_udp_sender.py --show. It draws what the tracker REPORTS (the smoothed landmarks, the id, the side, the
gesture, the estimated distance), so the window is a check on the numbers that go on the wire and not on a separate
code path. Display only: nothing here feeds back into tracking.
"""

import cv2
import numpy as np

import hand_landmarker as H
import hand_tracking as HT

#: BGR. Left and right are told apart by colour, and a HELD hand is drawn dimmer.
COLOR_LEFT = (255, 160, 40)
COLOR_RIGHT = (60, 200, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_HELD_SCALE = 0.45

TIP_INDICES = (H.THUMB_TIP, H.INDEX_TIP, H.MIDDLE_TIP, H.RING_TIP, H.PINKY_TIP)


def _color(hand):
    base = COLOR_LEFT if hand.side == HT.SIDE_LEFT else COLOR_RIGHT
    if hand.state == HT.HELD:
        return tuple(int(c * COLOR_HELD_SCALE) for c in base)
    return base


def _label_lines(hand):
    f = hand.fingers
    lines = ["#%d %s  %s" % (hand.id, hand.side, f.gesture)]
    if hand.position is not None:
        lines.append("Z %.2f m  (est.)" % hand.position[2])
    else:
        lines.append("Z n/a (%s)" % (hand.reason or "?"))
    lines.append("pinch %.2f  conf %.2f" % (f.pinch, hand.conf))
    return lines


def draw_hand(canvas, hand):
    """Draw one TrackedHand on `canvas` (BGR, the frame the model saw)."""
    h, w = canvas.shape[:2]
    color = _color(hand)
    pts = np.stack([hand.image[:, 0] * w, hand.image[:, 1] * h], axis=1).astype(np.int32)
    for a, b in H.HAND_BONES:
        cv2.line(canvas, tuple(pts[a]), tuple(pts[b]), color, 2, cv2.LINE_AA)
    for i in range(H.NUM_LANDMARKS):
        radius = 4 if i in TIP_INDICES else 2
        cv2.circle(canvas, tuple(pts[i]), radius, (255, 255, 255), -1, cv2.LINE_AA)
    # A pinch is drawn between the two tips so the continuous value is visible, not just the number.
    if hand.fingers.pinch > 0.05:
        cv2.line(canvas, tuple(pts[H.THUMB_TIP]), tuple(pts[H.INDEX_TIP]),
                 (0, int(255 * hand.fingers.pinch), 255 - int(255 * hand.fingers.pinch)), 2, cv2.LINE_AA)
    x0 = int(pts[:, 0].min())
    y0 = int(pts[:, 1].min())
    for i, text in enumerate(_label_lines(hand)):
        y = max(14, y0 - 8 - 16 * (len(_label_lines(hand)) - 1 - i))
        cv2.putText(canvas, text, (max(2, x0), y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, (max(2, x0), y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def draw_finger_bars(canvas, hand, x, y):
    """Five small curl bars (thumb..pinky) for one hand, anchored at (x, y)."""
    color = _color(hand)
    cv2.putText(canvas, "%s #%d" % (hand.side, hand.id), (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1,
                cv2.LINE_AA)
    for i, curl in enumerate(hand.fingers.curls):
        bx = x + i * 14
        cv2.rectangle(canvas, (bx, y), (bx + 10, y + 40), (60, 60, 60), 1)
        filled = int(40 * min(1.0, max(0.0, curl)))
        cv2.rectangle(canvas, (bx, y + 40 - filled), (bx + 10, y + 40), color, -1)
        if hand.fingers.extended[i]:
            cv2.rectangle(canvas, (bx, y + 44), (bx + 10, y + 47), (80, 220, 80), -1)


def draw_status(canvas, fps, infer_ms, hands, source, mirrored, sent):
    """The top-left HUD: rate, cost, hand count and what the sender is doing."""
    lines = ["%s%s   %.1f fps   %.1f ms/frame   hands %d   sent %d"
             % (source, " (mirrored)" if mirrored else "", fps, infer_ms, len(hands), sent),
             "ESC / q quit   |   raise your RIGHT hand to check the L/R letter"]
    for i, text in enumerate(lines):
        cv2.putText(canvas, text, (8, 18 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, (8, 18 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1, cv2.LINE_AA)


def render(frame, hands, fps, infer_ms, source, mirrored, sent):
    """A new image: `frame` with every hand, the finger bars and the HUD drawn on it."""
    canvas = frame.copy()
    for hand in hands:
        draw_hand(canvas, hand)
    h = canvas.shape[0]
    for i, hand in enumerate(hands):
        draw_finger_bars(canvas, hand, 10 + i * 100, h - 60)
    draw_status(canvas, fps, infer_ms, hands, source, mirrored, sent)
    return canvas
