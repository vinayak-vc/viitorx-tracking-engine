"""A synthetic face for the face tests: 478 landmarks in metres with the anatomy the code reads, and rotation helpers whose signs
match face_geometry's conventions.

Face-centred metres in CAMERA axes (x right, y DOWN, z away from the camera); frontal and upright: the nose points toward the camera
(negative z). Only the landmarks the code reads are placed meaningfully (the outer eye corners 33 and 263, the forehead 10, the chin
152, the nose tip 1, the inner eye corners, the mouth corners); the rest are fixed pseudo-random points inside a face-shaped ellipsoid,
so bounding boxes, centroids and the 146-point blendshape subset all have something to work on. NOT a model of a real face.
"""
import math

import numpy as np

import face_landmarker as F

#: Metres between the outer eye corners of the template: the average adult's, which face_geometry assumes.
EYE_WIDTH_M = 0.09


def make_face():
    """(478, 3) frontal, upright, centred. The forehead and chin sit at the same depth so the template's own head frame is the identity."""
    rng = np.random.RandomState(5)
    pts = np.zeros((F.NUM_LANDMARKS, 3))
    for i in range(F.NUM_LANDMARKS):
        while True:
            p = rng.uniform(-1.0, 1.0, 3)
            if p[0] ** 2 + p[1] ** 2 <= 1.0:
                break
        pts[i] = (p[0] * 0.075, p[1] * 0.095, -0.03 * (1.0 - p[0] ** 2 - p[1] ** 2))
    half = EYE_WIDTH_M / 2.0
    pts[F.RIGHT_EYE_OUTER] = (-half, -0.02, -0.01)
    pts[F.LEFT_EYE_OUTER] = (half, -0.02, -0.01)
    pts[F.RIGHT_EYE_INNER] = (-0.018, -0.02, -0.012)
    pts[F.LEFT_EYE_INNER] = (0.018, -0.02, -0.012)
    pts[F.FOREHEAD] = (0.0, -0.085, -0.01)
    pts[F.CHIN] = (0.0, 0.08, -0.01)
    pts[F.NOSE_TIP] = (0.0, 0.012, -0.045)
    pts[F.MOUTH_LEFT] = (0.026, 0.045, -0.02)
    pts[F.MOUTH_RIGHT] = (-0.026, 0.045, -0.02)
    return pts - pts[:F.NUM_MESH_LANDMARKS].mean(axis=0)


def rotation(yaw=0.0, pitch=0.0, roll=0.0):
    """The rotation (3x3, applied to column vectors) that turns a frontal face by `yaw`, `pitch`, `roll` degrees in
    face_geometry's sense, composed as roll(pitch(yaw(face))):
      yaw   > 0  the nose moves toward the image's RIGHT
      pitch > 0  the nose moves DOWN
      roll  > 0  the eye line tilts clockwise on screen
    For a single angle these are exactly what head_pose() reads back; combined they differ slightly by definition."""
    phi = math.radians(-yaw)
    theta = math.radians(pitch)
    rho = math.radians(roll)
    ry = np.array([[math.cos(phi), 0, math.sin(phi)], [0, 1, 0], [-math.sin(phi), 0, math.cos(phi)]])
    rx = np.array([[1, 0, 0], [0, math.cos(theta), -math.sin(theta)], [0, math.sin(theta), math.cos(theta)]])
    rz = np.array([[math.cos(rho), -math.sin(rho), 0], [math.sin(rho), math.cos(rho), 0], [0, 0, 1]])
    return rz @ rx @ ry


def posed(face, yaw=0.0, pitch=0.0, roll=0.0):
    """The face rotated about its own centre."""
    return face @ rotation(yaw, pitch, roll).T


def project(face_m, position, camera):
    """What a pinhole camera sees of the face placed at `position` (camera space, metres): (478, 3) with x, y in pixels and
    z in the same pixel scale (the mesh network's convention: z scaled like x)."""
    pts = face_m + np.asarray(position)
    xy = camera.project(pts)
    z = face_m[:, 2] * (camera.focal_px / position[2])
    return np.concatenate([xy, z[:, None]], axis=1)
