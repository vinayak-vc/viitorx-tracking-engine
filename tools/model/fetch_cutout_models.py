#!/usr/bin/env python3
"""S-01 - fetch the two ONNX models that cutout_segmenter.py runs: MediaPipe's selfie segmenter in its square (256x256) and its
landscape (256x144) form.

WHY A SCRIPT AND NOT COMMITTED FILES. *.onnx is ignored by version control (the RTMW3D, YOLOX, hand and face weights are handled the
same way). This puts the files where cutout_segmenter expects them and refuses to leave behind a file that is not byte-for-byte the one
S-01 was measured with.

SOURCE. Google's MediaPipe selfie segmentation models (Apache-2.0) as ONNX conversions published by the onnx-community organisation on
Hugging Face (repositories mediapipe_selfie_segmentation and mediapipe_selfie_segmentation_landscape, licence tag apache-2.0, 0.46 MB each).
Google publishes the TFLite originals, not ONNX; these are the conversions the project uses. Their numerical parity with Google's TFLite
models was NOT reproduced here (that needs a mediapipe install, which would pull opencv-contrib-python next to opencv-python: ADR-099).
Only these two https URLs are ever fetched; each result is rejected unless its size and SHA-256 match the values below, which were read
from the repository's own X-Linked-ETag / X-Linked-Size headers and re-computed from the files S-01 downloaded and measured with.

    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_cutout_models.py
    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_cutout_models.py --verify-only
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

BASE = "https://huggingface.co/onnx-community/"

#: (file name, url, exact size in bytes, sha256). Order is the order they are fetched.
MODELS = (
    ("selfie_segmenter_general.onnx", BASE + "mediapipe_selfie_segmentation/resolve/main/onnx/model.onnx", 462352,
     "3241ac4ad8aa35bdaf33946776db29f7c283a413aa0b0dacb9483594b4531aad"),
    ("selfie_segmenter_landscape.onnx", BASE + "mediapipe_selfie_segmentation_landscape/resolve/main/onnx/model.onnx", 462338,
     "7a0adcfdb1715d3b0ff0f61486d8a181c33f4a208343a5abfccbc6540e872d24"),
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
    """True when `path` is exactly the file S-01 was measured with; prints why not otherwise."""
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
    ap = argparse.ArgumentParser(description="S-01 fetch the MediaPipe selfie segmenter ONNX models")
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
