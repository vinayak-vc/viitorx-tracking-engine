#!/usr/bin/env python3
"""Writes the two fixtures of the COMBINED command line (`viitorx-tracking run`, one process, one camera, several streams) that the Unity host starts.

The expected lines of the single-stream senders (args_hands_*, args_face_*, args_cutout_*) already exist in the vision package's fixture folder and are pinned by
VisionSenderArgumentsTests. The combined line must configure each stream EXACTLY as its own line does, so it is composed from those: the source options once,
then `--stream NAME` and the stream's own options. VisionSenderArgumentsTests asserts that Unity's `VisionSenderArguments.Run` produces these bytes;
tests/test_vision_cli_contract.py parses them with the engine's own splitter and the senders' parsers and checks that every stream's namespace equals the
single-stream line's.

    python tools/video/make_run_fixtures.py          (rewrites args_run_default.txt and args_run_custom.txt)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _sidecar_path  # noqa: F401

import make_vision_fixtures as MF

LINE = re.compile(
    r"^--video (?P<video>\".*?\") --host (?P<host>\S+) --port (?P<port>\d+) --provider (?P<provider>\S+)(?P<hfov> --hfov \S+)?(?P<axis> --hfov-axis long)?"
    r"(?P<rotate> --rotate \d+)? (?P<flip>--flip|--no-flip) (?P<smooth>--smooth|--no-smooth)(?P<size> --camera-size \d+ \d+)? --camera-fps (?P<fps>\d+) "
    r"(?P<loop>--loop|--no-loop)(?P<show> --show)?(?P<tail>.*)$")


def split_line(path):
    text = open(os.path.join(MF.FIXTURE_DIR, path), encoding="utf-8").read().strip()
    m = LINE.match(text)
    if m is None:
        raise SystemExit("cannot split %s: %s" % (path, text))
    g = {k: (v or "") for k, v in m.groupdict().items()}
    source = '--video %s%s%s --camera-fps %s %s%s' % (g["video"], g["rotate"], g["size"], g["fps"], g["loop"], g["show"])
    return source, g


def compose(names, files):
    sources = []
    streams = []
    for name, f in zip(names, files):
        source, g = split_line(f)
        sources.append(source)
        streams.append(" --stream %s --host %s --port %s --provider %s%s%s %s %s%s" % (name, g["host"], g["port"], g["provider"], g["hfov"], g["axis"], g["flip"], g["smooth"], g["tail"]))
    if len(set(sources)) != 1:
        raise SystemExit("the single-stream lines do not share their source options: %r" % sources)
    return sources[0] + "".join(streams)


def main():
    names = ["hands", "face", "cutout"]
    default = compose(names, ["args_hands_default.txt", "args_face_default.txt", "args_cutout_default.txt"])
    custom = compose(names, ["args_hands_custom.txt", "args_face_custom.txt", "args_cutout_custom.txt"])
    for name, line in (("args_run_default.txt", default), ("args_run_custom.txt", custom)):
        with open(os.path.join(MF.FIXTURE_DIR, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        print("wrote %s (%d characters)" % (name, len(line)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
