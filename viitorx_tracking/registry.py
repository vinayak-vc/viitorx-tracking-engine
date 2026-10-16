"""The models the engine runs, in one place: where each comes from, and exactly which file it was measured with.

This is the engine's own copy of what `tools/model/fetch_*.py` know (those scripts stay: they are how the repository's developers fetch). A test
(tests/test_engine.py) compares every entry with those scripts, so the two cannot drift. Files are looked for in `models/` beside the program, or in
the folder named by `--models-dir` / the VIITORX_MODELS_DIR environment variable.

A file whose size or SHA-256 differs from its entry is a CUSTOM model: allowed (the user may replace them), reported, never refused here.
"""
import hashlib
import os

#: Fetched by `fetch-models` from a URL, verified by size and SHA-256. `url` None = not fetchable: provide the file yourself.
#: (group, file name, url, bytes, sha256, required by the group's streams)
_TABLE = (
    ("hands", "palm_detection_mediapipe_2023feb.onnx",
     "https://huggingface.co/opencv/palm_detection_mediapipe/resolve/main/palm_detection_mediapipe_2023feb.onnx",
     3905734, "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c", True),
    ("hands", "handpose_estimation_mediapipe_2023feb.onnx",
     "https://huggingface.co/opencv/handpose_estimation_mediapipe/resolve/main/handpose_estimation_mediapipe_2023feb.onnx",
     4099621, "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c", True),
    ("face", "face_detector.onnx",
     "https://huggingface.co/fernandotonon/QtMeshEditor-models/resolve/main/mocap/face/face_detector.onnx",
     418437, "02a04d5d37c3558dc4d5274f7f8f0f0f01ac94e46c5ffb2cee82395d47e23181", True),
    ("face", "face_landmarks.onnx",
     "https://huggingface.co/fernandotonon/QtMeshEditor-models/resolve/main/mocap/face/face_landmarks.onnx",
     4920979, "d16e5a55e6a480284d468ee32469692464049518c311662ab3956681de31e3e9", True),
    ("face", "face_blendshapes.onnx",
     "https://huggingface.co/fernandotonon/QtMeshEditor-models/resolve/main/mocap/face/face_blendshapes.onnx",
     1094706, "37cf54d3ccf671f8936427437a1a0b627799e16f20439de3cc35aa189ad40c66", True),
    ("cutout", "selfie_segmenter_general.onnx",
     "https://huggingface.co/onnx-community/mediapipe_selfie_segmentation/resolve/main/onnx/model.onnx",
     462352, "3241ac4ad8aa35bdaf33946776db29f7c283a413aa0b0dacb9483594b4531aad", True),
    ("cutout", "selfie_segmenter_landscape.onnx",
     "https://huggingface.co/onnx-community/mediapipe_selfie_segmentation_landscape/resolve/main/onnx/model.onnx",
     462338, "7a0adcfdb1715d3b0ff0f61486d8a181c33f4a208343a5abfccbc6540e872d24", False),
    ("person", "yolox_s.onnx",
     "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
     35858002, "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", False),
    # The body model has no download URL here: it is a 369 MB export the project's setup places by hand (python-sidecar~/README.md). Either file
    # serves; the fp16 build is half the size and about three times faster on the GPU stage (ADR-082).
    ("body", "rtmw3d-x-fp16.onnx", None, 184789912, "3d2a613264d85c859ef58bfaa93595bf46d4daf85804781e6a1063b3e9880e04", False),
    ("body", "rtmw3d-x.onnx", None, 369330857, "4a289c0e99d47eb595e99679d9d4a2d1def1b4241f9adcbafba44b9ff585ebcd", False),
)

GROUPS = ("hands", "face", "cutout", "person", "body")


class ModelSpec(object):
    __slots__ = ("group", "name", "url", "size", "sha256", "required")

    def __init__(self, group, name, url, size, sha256, required):
        self.group = group
        self.name = name
        self.url = url
        self.size = size
        self.sha256 = sha256
        self.required = required


MODELS = tuple(ModelSpec(*row) for row in _TABLE)


def specs_for(group):
    return [m for m in MODELS if m.group == group]


def spec_named(name):
    for m in MODELS:
        if m.name == name:
            return m
    return None


def models_dir(root, override=None):
    """The folder models are looked for in: the explicit override, else VIITORX_MODELS_DIR, else `models/` beside the program."""
    if override:
        return os.path.abspath(override)
    env = os.environ.get("VIITORX_MODELS_DIR")
    if env:
        return os.path.abspath(env)
    return os.path.join(root, "models")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Results of inspecting one file against its entry.
MISSING = "missing"
OK = "ok"
CUSTOM = "custom"


def inspect(path, spec, deep=True):
    """MISSING when there is no file; OK when it is exactly the registry's file; CUSTOM when it is some other file. `deep` False compares the size
    only (a 369 MB hash is seconds of work: the startup path does not need it)."""
    if not os.path.isfile(path):
        return MISSING
    if os.path.getsize(path) != spec.size:
        return CUSTOM
    if not deep:
        return OK
    return OK if sha256_of(path) == spec.sha256 else CUSTOM


def status(directory, group=None, deep=True):
    """[(spec, state, path)] for every registry entry (or one group) against a models folder."""
    out = []
    for spec in MODELS:
        if group is not None and spec.group != group:
            continue
        path = os.path.join(directory, spec.name)
        out.append((spec, inspect(path, spec, deep), path))
    return out
