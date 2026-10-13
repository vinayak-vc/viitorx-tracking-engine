#!/usr/bin/env python3
"""H-02 - WHERE IS THE HEAD AND WHICH WAY IS IT TURNED, FROM ONE CAMERA.

POSITION. The mesh's 3D landmarks are in pixels with z in the same scale as x and y, so the 3D distance between the two outer
eye corners (landmarks 33 and 263) is the eye-corner width AS SEEN AT THE FACE'S DEPTH, turn and tilt included. An adult's outer
eye corners are about 0.09 m apart, so by the pinhole model

    Z = focal_px * 0.09 m / width_px        X, Y = (the point between the eyes - the principal point) * Z / focal_px

`face_scale` scales the 0.09 for a face known to be wider or narrower (children and some adults differ by 10 % or more, and
every metre of Z scales with it). The focal length comes from a field of view the camera does not report, as for the hands
(hand_geometry.Camera). This is an ESTIMATE; its accuracy against a ruler has not been measured (H-02 NOT TESTED).

HEAD POSE. A frame is built from four mesh landmarks: x from the right outer eye corner (33) to the left (263), y from the
forehead (10) to the chin (152), z their cross product, then y re-squared. R has those axes as columns in CAMERA axes (x right,
y down, z away), so an upright face looking straight at the camera is the identity. From the direction the face looks
(n = -z) and the eye line:

    yaw   > 0  the nose moves toward the image's RIGHT          (atan2(n.x, -n.z))
    pitch > 0  the nose moves DOWN                               (atan2(n.y, hypot(n.x, n.z)))
    roll  > 0  the eye line tilts clockwise on screen            (atan2(x.y, x.x), exact when yaw = pitch = 0)

A mirrored picture (--flip) mirrors yaw and roll. This is a geometric estimate from four noisy landmarks, NOT MediaPipe's
facial transformation matrix, and it has not been compared with it.

NOT HERE: the networks, tracking, filtering or the wire. Pure numpy.
"""

import math

import numpy as np

import face_landmarker as F

#: Metres between the outer eye corners of an average adult.
OUTER_EYE_WIDTH_M = 0.09

#: Metres. A face nearer or further than this is outside what a webcam face model can say anything about.
MIN_DEPTH_M = 0.15
MAX_DEPTH_M = 4.00

#: Pixels. Corners closer than this are a degenerate fit, not a face.
MIN_EYE_WIDTH_PX = 4.0


class HeadPose(object):
    """`rotation` (3x3, columns are the head's x, y, z axes in camera axes), `yaw`, `pitch`, `roll` in degrees, `ok`."""
    __slots__ = ("rotation", "yaw", "pitch", "roll", "ok")

    def __init__(self, rotation, yaw, pitch, roll, ok):
        self.rotation = rotation
        self.yaw = yaw
        self.pitch = pitch
        self.roll = roll
        self.ok = ok


class FacePlacement(object):
    """`position` is the point between the eyes in camera space (X right, Y down, Z forward), metres; `ok` False when it
    was refused (`reason` says why) and `position` must not be used."""
    __slots__ = ("position", "eye_width_px", "ok", "reason")

    def __init__(self, position, eye_width_px, ok, reason=""):
        self.position = position
        self.eye_width_px = eye_width_px
        self.ok = ok
        self.reason = reason


def head_pose(landmarks):
    """HeadPose from the 478 landmarks ((>= 264, 3): x, y in pixels, z in the same scale)."""
    lm = np.asarray(landmarks, dtype=np.float64)
    x = lm[F.LEFT_EYE_OUTER] - lm[F.RIGHT_EYE_OUTER]
    y_raw = lm[F.CHIN] - lm[F.FOREHEAD]
    nx = float(np.linalg.norm(x))
    if nx < MIN_EYE_WIDTH_PX or float(np.linalg.norm(y_raw)) < MIN_EYE_WIDTH_PX:
        return HeadPose(np.eye(3), 0.0, 0.0, 0.0, False)
    x = x / nx
    z = np.cross(x, y_raw)
    nz = float(np.linalg.norm(z))
    if nz < 1e-9:
        return HeadPose(np.eye(3), 0.0, 0.0, 0.0, False)
    z = z / nz
    y = np.cross(z, x)
    rotation = np.stack([x, y, z], axis=1)
    look = -z
    yaw = math.degrees(math.atan2(look[0], -look[2]))
    pitch = math.degrees(math.atan2(look[1], math.hypot(look[0], look[2])))
    roll = math.degrees(math.atan2(x[1], x[0]))
    return HeadPose(rotation, yaw, pitch, roll, True)


def place_face(landmarks, camera, face_scale=1.0):
    """FacePlacement from the 478 landmarks and a hand_geometry.Camera."""
    lm = np.asarray(landmarks, dtype=np.float64)
    right = lm[F.RIGHT_EYE_OUTER]
    left = lm[F.LEFT_EYE_OUTER]
    width = float(np.linalg.norm(left - right))
    if width < MIN_EYE_WIDTH_PX:
        return FacePlacement(None, width, False, "degenerate")
    z = camera.focal_px * OUTER_EYE_WIDTH_M * float(face_scale) / width
    if not MIN_DEPTH_M <= z <= MAX_DEPTH_M:
        return FacePlacement(None, width, False, "range")
    centre = (right[0:2] + left[0:2]) / 2.0
    x = (centre[0] - camera.cx) * z / camera.focal_px
    y = (centre[1] - camera.cy) * z / camera.focal_px
    return FacePlacement(np.array([x, y, z]), width, True)
