#!/usr/bin/env python3
"""H-03 tests for the command lines the Unity host starts the senders with.

Unity's VisionSenderArguments builds them from the "Hands and Face" settings of AppBootstrap; the expected lines are fixtures in the Unity repo
(Tests/EditMode/VisionFixtures/args_*.txt), asserted byte-for-byte by VisionSenderArgumentsTests on the C# side. Here the SAME lines are parsed by
the senders' own argparse parsers: every flag must be one the sender accepts (an unknown flag makes Python exit before the camera opens, which the
host would show only as a dead sender) and must land in the field the setting is named for.
"""
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sidecar_path  # noqa: F401

import make_vision_fixtures as MF

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-72s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-72s %s" % (label, detail))


def parse(builder, fixture):
    path = os.path.join(MF.FIXTURE_DIR, fixture)
    line = open(path, encoding="utf-8").read().strip()
    return builder().parse_args(shlex.split(line))


if not os.path.isdir(MF.FIXTURE_DIR):
    print("SKIP the Unity repo's fixture folder is not present: %s" % MF.FIXTURE_DIR)
    sys.exit(0)

import tools.hands.hands_udp_sender as HS  # noqa: E402
import tools.face.face_udp_sender as FS  # noqa: E402

print("HANDS SENDER ACCEPTS THE DEFAULT LINE AND READS EVERY SETTING")
a = parse(HS.build_parser, "args_hands_default.txt")
check(a.video == "camera:0" and a.port == 8901 and a.host == "127.0.0.1", "source, host and port")
check(a.flip is True and a.smooth is True and a.loop is True, "flip, smooth and loop are on")
check(a.max_hands == 2 and a.sides == "auto" and a.provider == "auto", "max hands, sides and provider")
check(a.camera_size == [1280, 720] and a.camera_fps == 30 and a.hfov == 60.0, "camera size, fps and hfov")
check(a.hfov_axis == "width" and a.rotate == 0 and a.show is False, "axis, rotation and preview are at their defaults")

a = parse(HS.build_parser, "args_hands_custom.txt")
check(a.video == "http://192.168.1.5:3000/viewer?room=room-abc" and a.port == 9001, "a LAN camera link survives the quoting")
check(a.flip is False and a.smooth is False and a.loop is False and a.show is True, "the off switches and --show")
check(a.rotate == 90 and a.hfov == 75.0 and a.hfov_axis == "long", "rotation, hfov and the long axis")
check(a.camera_size == [640, 480] and a.camera_fps == 15 and a.provider == "cpu", "camera size, fps, provider")
check(a.max_hands == 3 and abs(a.palm_conf - 0.3) < 1e-9 and abs(a.conf - 0.7) < 1e-9 and abs(a.keep_conf - 0.35) < 1e-9, "max hands and the three confidences")
check(abs(a.hand_scale - 1.1) < 1e-9 and a.sides == "swapped", "hand scale and sides")

print("")
print("FACE SENDER ACCEPTS THE DEFAULT LINE AND READS EVERY SETTING")
a = parse(FS.build_parser, "args_face_default.txt")
check(a.video == "camera:0" and a.port == 8902 and a.host == "127.0.0.1", "source, host and port")
check(a.max_faces == 1 and a.blendshapes is True and a.landmarks == "full", "max faces, blendshapes, landmarks")
check(a.flip is True and a.smooth is True and a.loop is True, "flip, smooth and loop are on")
check(abs(a.det_conf - 0.5) < 1e-9 and abs(a.face_scale - 1.0) < 1e-9, "detector score and face scale")

a = parse(FS.build_parser, "args_face_custom.txt")
check(a.port == 9002 and a.max_faces == 2 and a.blendshapes is False and a.landmarks == "mesh", "port, max faces, no blendshapes, mesh landmarks")
check(abs(a.det_conf - 0.3) < 1e-9 and abs(a.conf - 0.7) < 1e-9 and abs(a.keep_conf - 0.35) < 1e-9 and abs(a.face_scale - 1.1) < 1e-9, "the confidences and face scale")
check(a.rotate == 90 and a.hfov_axis == "long" and a.flip is False and a.show is True, "rotation, long axis, no flip, --show")

print("")
print("CUTOUT SENDER ACCEPTS THE DEFAULT LINE AND READS EVERY SETTING (S-01)")
import tools.cutout.cutout_udp_sender as CS  # noqa: E402
a = parse(CS.build_parser, "args_cutout_default.txt")
check(a.video == "camera:0" and a.port == 8903 and a.host == "127.0.0.1", "source, host and port")
check(a.flip is False and a.smooth is True and a.loop is True and a.show is False, "no flip (Unity mirrors), temporal filter on, loop on")
check(a.roi == "auto" and abs(a.det_conf - 0.4) < 1e-9 and a.max_people == 4 and a.fallback is True and a.isolate is True,
      "detector: roi, score, people, fallback, people kept apart")
check(a.mask_long_side == 640 and abs(a.edge_low - 0.5) < 1e-9 and abs(a.edge_high - 0.85) < 1e-9, "the mask: size and edge ramp")
check(a.guided is True and a.cleanup is True and a.tta is False, "guided filter and cleanup on, flip-averaging off")
check(a.color == "rgba" and a.mask_enc == "raw" and a.send_long_side == 1280 and a.chunk_bytes == 60000, "what travels: raw picture and mask, 1280 px, 60 000 B")
check(a.camera_size == [1280, 720] and a.camera_fps == 30 and a.provider == "auto" and a.rotate == 0, "camera size, fps, provider, rotation")
check(a.hfov == 60.0, "no field of view is sent: the sender's own default stands")

a = parse(CS.build_parser, "args_cutout_custom.txt")
check(a.video == "http://192.168.1.5:3000/viewer?room=room-abc" and a.port == 9003, "a LAN camera link survives the quoting")
check(a.flip is True and a.smooth is False and a.loop is False and a.show is True, "mirror in the sender, no temporal filter, no loop, --show")
check(a.roi == "on" and abs(a.det_conf - 0.3) < 1e-9 and a.max_people == 2 and a.fallback is False and a.isolate is False, "detector settings")
check(a.mask_long_side == 480 and abs(a.edge_low - 0.4) < 1e-9 and abs(a.edge_high - 0.8) < 1e-9, "mask size and edge ramp")
check(a.guided is False and a.cleanup is False and a.tta is True, "guided off, cleanup off, flip-averaging on")
check(a.color == "jpeg" and a.jpeg_quality == 70 and a.mask_enc == "deflate" and a.send_long_side == 960 and a.chunk_bytes == 1200,
      "JPEG at quality 70, a deflated mask, 960 px, 1 200 B datagrams")
check(a.rotate == 90 and a.provider == "cpu" and a.camera_size == [640, 480] and a.camera_fps == 15, "rotation, provider, camera size, fps")

print("")
print("THE DEFAULT PORTS ARE THE WIRES' OWN")
import cutout_wire as CW  # noqa: E402
import face_wire as FW  # noqa: E402
import hand_wire as HW  # noqa: E402
check(parse(HS.build_parser, "args_hands_default.txt").port == HW.DEFAULT_PORT, "hands default line uses hand_wire.DEFAULT_PORT", str(HW.DEFAULT_PORT))
check(parse(FS.build_parser, "args_face_default.txt").port == FW.DEFAULT_PORT, "face default line uses face_wire.DEFAULT_PORT", str(FW.DEFAULT_PORT))
check(parse(CS.build_parser, "args_cutout_default.txt").port == CW.DEFAULT_PORT, "cutout default line uses cutout_wire.DEFAULT_PORT", str(CW.DEFAULT_PORT))

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
