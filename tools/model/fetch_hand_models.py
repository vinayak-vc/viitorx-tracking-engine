#!/usr/bin/env python3
"""H-01 - fetch the two ONNX models that hand_landmarker.py runs: the MediaPipe palm detector and the
MediaPipe hand-landmark network (21 landmarks, 21 metric world landmarks, handedness, confidence).

WHY A SCRIPT AND NOT COMMITTED FILES. *.onnx is ignored by version control (the RTMW3D and YOLOX weights are
handled the same way). This puts the files where hand_landmarker expects them and refuses to leave behind a
file that is not byte-for-byte the one H-01 was measured with.

SOURCE. The OpenCV Zoo conversions of Google's MediaPipe models (Apache-2.0), published by the `opencv`
organisation on Hugging Face. The same files live in github.com/opencv/opencv_zoo under Git LFS, but GitHub's
LFS download host was reset by the network this was developed on, so the Hugging Face mirror is used. Only
these two https URLs are ever fetched; each result is rejected unless its size and SHA-256 match the values
below, which were computed from the files H-01 downloaded and measured with. A different export, a corrupted
download or a replaced asset all fail loudly rather than load.

    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_hand_models.py
    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_hand_models.py --verify-only
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

#: (file name, url, exact size in bytes, sha256). Order is the order they are fetched.
MODELS = (
    ("palm_detection_mediapipe_2023feb.onnx",
     "https://huggingface.co/opencv/palm_detection_mediapipe/resolve/main/palm_detection_mediapipe_2023feb.onnx",
     3905734,
     "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c"),
    ("handpose_estimation_mediapipe_2023feb.onnx",
     "https://huggingface.co/opencv/handpose_estimation_mediapipe/resolve/main/handpose_estimation_mediapipe_2023feb.onnx",
     4099621,
     "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c"),
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
    """True when `path` is exactly the file H-01 was measured with; prints why not otherwise."""
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
    ap = argparse.ArgumentParser(description="H-01 fetch the MediaPipe hand ONNX models")
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
