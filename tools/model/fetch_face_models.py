#!/usr/bin/env python3
"""H-02 - fetch the three ONNX models that face_landmarker.py runs: MediaPipe's face detector (BlazeFace), face mesh (478 landmarks) and
face blendshapes (52 scores).

WHY A SCRIPT AND NOT COMMITTED FILES. *.onnx is ignored by version control (the RTMW3D, YOLOX and hand weights are handled the same way). This puts
the files where face_landmarker expects them and refuses to leave behind a file that is not byte-for-byte the one H-02 was measured with.

SOURCE. Community ONNX conversions of the TFLite models inside Google's `face_landmarker.task` (Apache-2.0), published by fernandotonon in the
QtMeshEditor-models repository on Hugging Face under mocap/face/ (with a NOTICE.txt saying so). Google publishes the TFLite originals, not ONNX; these are
the conversions the project found. The converter's own parity numbers against Python mediapipe (landmarks <= 0.6 px, blendshapes <= 0.015 on its four
test images) are the CONVERTER'S and were not reproduced here. Only these three https URLs are ever fetched; each result is rejected unless its size and
SHA-256 match the values below, which were computed from the files H-02 downloaded and measured with.

    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_face_models.py
    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_face_models.py --verify-only
"""
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.isfile(_os.path.join(_d, "_sidecar_path.py")):
    _d = _os.path.dirname(_d)
_sys.path.insert(0, _d)
import _sidecar_path  # noqa: F401
import evidence_paths as EV

import argparse
import hashlib
import os
import sys
import urllib.request

#: Seconds without a byte before a download is abandoned.
TIMEOUT_SECONDS = 30

BASE = "https://huggingface.co/fernandotonon/QtMeshEditor-models/resolve/main/mocap/face/"

#: (file name, url, exact size in bytes, sha256). Order is the order they are fetched.
MODELS = (
    ("face_detector.onnx", BASE + "face_detector.onnx", 418437,
     "02a04d5d37c3558dc4d5274f7f8f0f0f01ac94e46c5ffb2cee82395d47e23181"),
    ("face_landmarks.onnx", BASE + "face_landmarks.onnx", 4920979,
     "d16e5a55e6a480284d468ee32469692464049518c311662ab3956681de31e3e9"),
    ("face_blendshapes.onnx", BASE + "face_blendshapes.onnx", 1094706,
     "37cf54d3ccf671f8936427437a1a0b627799e16f20439de3cc35aa189ad40c66"),
)

MODELS_DIR = os.path.join(EV.PROJECT_ROOT, "models")


def sha256_of(path):
    """Hex SHA-256 of a file, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path, expected_bytes, expected_sha256):
    """True when `path` is exactly the file H-02 was measured with; prints why not otherwise."""
    if not os.path.isfile(path):
        print("missing: %s" % path)
        return False
    size = os.path.getsize(path)
    if size != expected_bytes:
        print("wrong size: %s is %d bytes, expected %d" % (path, size, expected_bytes))
        return False
    digest = sha256_of(path)
    if digest != expected_sha256:
        print("wrong SHA-256: %s is %s, expected %s" % (path, digest, expected_sha256))
        return False
    return True


def download(url, dest, expected_bytes, expected_sha256):
    """Fetch `url` to `dest` through a temp file, so a failed or partial download never looks complete."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    got = 0
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as r, open(part, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            if got > expected_bytes:
                # The file is a fixed size; a longer stream is not this file, and must not fill a disk.
                out.close()
                os.remove(part)
                raise RuntimeError("download of %s exceeds the expected %d bytes" % (url, expected_bytes))
            out.write(chunk)
    if not verify(part, expected_bytes, expected_sha256):
        os.remove(part)
        raise RuntimeError("downloaded file failed verification; removed")
    os.replace(part, dest)


def main():
    ap = argparse.ArgumentParser(description="H-02 fetch the MediaPipe face ONNX models")
    ap.add_argument("--verify-only", action="store_true", help="check the existing files, download nothing")
    ap.add_argument("--dest-dir", default=MODELS_DIR, help="where to put them (default %(default)s)")
    a = ap.parse_args()
    failed = False
    for name, url, size, digest in MODELS:
        dest = os.path.join(a.dest_dir, name)
        if verify(dest, size, digest):
            print("OK  %s  (%d bytes, sha256 matches)" % (dest, size))
            continue
        if a.verify_only:
            failed = True
            continue
        print("downloading %s" % url)
        download(url, dest, size, digest)
        print("OK  %s  (%d bytes, sha256 matches)" % (dest, size))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
