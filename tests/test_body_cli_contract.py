#!/usr/bin/env python3
"""Tests for the command line the Unity body host starts the engine's `body` command with.

Unity's BodyEngineArguments builds it from BodySettings (package com.viitorx.avtar.tracking.body); the expected lines are fixtures in the Unity repo
(Packages/com.viitorx.avtar.tracking.body/Tests/Editor/Tracking/BodyFixtures/args_body_*.txt), asserted byte-for-byte by BodyEngineArgumentsTests on the
C# side. Here the SAME lines are parsed by the body sender's own argparse parser (`viitorx-tracking body` passes its options to it): every flag must be
one the sender accepts (an unknown flag makes Python exit before the camera opens, which the host would show only as a missing stream) and must land in
the field the setting is named for. Note the sender's --loop is a COUNT (0 = forever), not the on/off switch of the hands, face and cutout senders.
"""
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import _sidecar_path  # noqa: F401

PASS = 0
FAIL = 0
FIXTURE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "Packages", "com.viitorx.avtar.tracking.body", "Tests", "Editor", "Tracking", "BodyFixtures"))


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-72s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-72s %s" % (label, detail))


if not os.path.isdir(FIXTURE_DIR):
    print("SKIP the Unity repo's fixture folder is not present: %s" % FIXTURE_DIR)
    sys.exit(0)

import tools.video.video_udp_sender as BS  # noqa: E402


def parse(fixture):
    line = open(os.path.join(FIXTURE_DIR, fixture), encoding="utf-8").read().strip()
    return BS.build_parser().parse_args(shlex.split(line))


def rejects(args):
    """True when the parser refuses `args` (argparse exits with status 2)."""
    try:
        BS.build_parser().parse_args(args)
    except SystemExit as e:
        return e.code == 2
    return False


print("BODY SENDER ACCEPTS THE DEFAULT LINE AND READS EVERY SETTING")
a = parse("args_body_default.txt")
check(a.video == "camera:0" and a.port == 8899 and a.host == "127.0.0.1", "source, host and port")
check(a.loop == 0, "--loop 0 is forever (a count)")
check(a.hfov == 60.0 and abs(a.subject_height - 1.7) < 1e-9, "hfov and subject height")
check(a.depth_range == [1.6, 4.0], "depth range")
check(a.auto_orient is True and a.hfov_axis == "long", "auto-orient reads the field of view across the long side")
check(a.partial_body is True and a.flip is False and a.assume_measured is False and a.rotate == 0, "partial body on; flip, assume-measured and rotate at their defaults")
check(a.camera_size == [1280, 720], "camera size")

print("BODY SENDER ACCEPTS THE CUSTOM LINE AND READS EVERY SETTING")
a = parse("args_body_custom.txt")
check(a.video == "http://192.168.1.5:3000/viewer?room=room-abc" and a.port == 9001, "a LAN camera link survives the quoting")
check(a.loop == 1, "--loop 1 plays the file once")
check(a.hfov == 75.0 and abs(a.subject_height - 1.8) < 1e-9, "hfov and subject height")
check(a.depth_range == [2.0, 5.5], "depth range")
check(a.rotate == 90 and a.auto_orient is False, "rotation, no auto-orient")
check(a.partial_body is False and a.flip is True and a.assume_measured is True, "no partial body, flip, assume-measured")
check(a.camera_size == [640, 480], "camera size")

print("THE PARSER STILL REFUSES WHAT THE HOST MUST NEVER SEND")
check(rejects(["--video", "camera:0", "--no-loop"]), "--no-loop is not a body sender flag (its --loop is a count)")
check(rejects(["--video", "camera:0", "--rotate", "45"]), "a rotation that is not a quarter turn")

print()
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
