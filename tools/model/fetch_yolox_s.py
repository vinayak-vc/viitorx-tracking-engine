#!/usr/bin/env python3
"""F-52 - fetch the YOLOX-s person detector that tools/video/host_person_detector.py runs.

WHY A SCRIPT AND NOT A COMMITTED FILE. The weights are 35.9 MB and *.onnx is ignored by version control
(the RTMW3D weights are handled the same way). This puts them where host_person_detector expects, and
refuses to leave a file behind that is not byte-for-byte the one F-52 was measured with.

SOURCE. The official Megvii-BaseDetection/YOLOX release 0.1.1rc0 (Apache-2.0), asset yolox_s.onnx. Only
that one https URL is ever fetched; nothing here follows a redirect to an arbitrary host beyond what
GitHub's own release CDN does, and the result is rejected unless its size and SHA-256 match. The hash
below was computed from the file F-52 downloaded and measured with, so a different export, a corrupted
download or a replaced asset all fail loudly rather than load.

    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_yolox_s.py
    python-sidecar~/.venv/Scripts/python.exe tools/model/fetch_yolox_s.py --verify-only
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

URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx"
EXPECTED_BYTES = 35858002
EXPECTED_SHA256 = "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"

#: Seconds without a byte before the download is abandoned.
TIMEOUT_SECONDS = 30

DEST = os.path.join(EV.PROJECT_ROOT, "models", "yolox_s.onnx")


def sha256_of(path):
    """Hex SHA-256 of a file, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path):
    """True when `path` is exactly the file F-52 was measured with; prints why not otherwise."""
    if not os.path.isfile(path):
        print("missing: %s" % path)
        return False
    size = os.path.getsize(path)
    if size != EXPECTED_BYTES:
        print("wrong size: %d bytes, expected %d" % (size, EXPECTED_BYTES))
        return False
    digest = sha256_of(path)
    if digest != EXPECTED_SHA256:
        print("wrong SHA-256: %s, expected %s" % (digest, EXPECTED_SHA256))
        return False
    return True


def download(dest):
    """Fetch URL to `dest` through a temp file, so a failed or partial download never looks complete."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    got = 0
    with urllib.request.urlopen(URL, timeout=TIMEOUT_SECONDS) as r, open(part, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            if got > EXPECTED_BYTES:
                # The file is a fixed size; a longer stream is not this file, and must not fill a disk.
                raise RuntimeError("download exceeds the expected %d bytes" % EXPECTED_BYTES)
            out.write(chunk)
    if not verify(part):
        os.remove(part)
        raise RuntimeError("downloaded file failed verification; removed")
    os.replace(part, dest)


def main():
    ap = argparse.ArgumentParser(description="F-52 fetch the YOLOX-s person detector")
    ap.add_argument("--verify-only", action="store_true", help="check the existing file, download nothing")
    ap.add_argument("--dest", default=DEST, help="where to put it (default %(default)s)")
    a = ap.parse_args()
    if verify(a.dest):
        print("OK  %s  (%d bytes, sha256 matches)" % (a.dest, EXPECTED_BYTES))
        return 0
    if a.verify_only:
        return 1
    print("downloading %s" % URL)
    download(a.dest)
    print("OK  %s  (%d bytes, sha256 matches)" % (a.dest, EXPECTED_BYTES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
