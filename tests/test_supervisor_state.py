#!/usr/bin/env python3
"""F-51 tests: the supervisor puts the child's latest heartbeat into supervisor_state.json.

Unity's status lights are driven by that file, so what is pinned here is what the lights can rely on:

  * THE HEARTBEAT IS READ FROM EVERY SENDER'S OWN FORMAT. The three senders print different extra fields
    after `frames= sent=`; the parser must find the numbers in all of them and leave a clean free-text note.
  * THE LATEST LINE WINS, and a truncated first line of a tail read cannot confuse it.
  * NOTHING HERE CAN HURT THE WATCHDOG. No log, an empty log, a log with no heartbeat, a log that is gone:
    all give `Child: null`, never an exception - this code runs inside the process whose job is to survive.
  * THE FILE STAYS VALID JSON with the new key, and the existing keys are untouched.

Run: .venv\\Scripts\\python.exe tests/test_supervisor_state.py   (exit 0 on pass)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import sidecar_supervisor as SUP

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-70s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-70s %s" % (label, detail))


print("parse_heartbeat: each sender's own line")
video = "[vid] frames=2031 sent=4 skipped=2026 fps~0.0 replay=0 live=Y dropped=35 reconnects=0 "
b = SUP.parse_heartbeat(video)
check(b == dict(Frames=2031, Sent=4, Skipped=2026, Live="Y", Note=""), "video sender, live source", str(b))
b = SUP.parse_heartbeat("[vid] frames=0 sent=0 skipped=0 fps~0.0 replay=0 live=n dropped=0 reconnects=0 waiting for frames")
check(b["Live"] == "n" and b["Note"] == "waiting for frames" and b["Frames"] == 0, "video sender, waiting for a phone", str(b))
b = SUP.parse_heartbeat("[vid] frames=0 sent=0 loading RTMW3D ...")
check(b["Note"] == "loading RTMW3D ..." and b["Skipped"] is None and b["Live"] is None, "video sender, loading the model", str(b))
b = SUP.parse_heartbeat("[vid] frames=300 sent=300 skipped=0 fps~29.9 replay=1 ")
check(b["Sent"] == 300 and b["Live"] is None, "video sender, a file (no live= field)", str(b))
b = SUP.parse_heartbeat("[wb] frames=1200 sent=1190 hip_z=2.31m measured_body=31/33 fps~29.8 age=41ms stale=0")
check(b["Frames"] == 1200 and b["Sent"] == 1190 and b["Note"] == "", "wholebody sender (OAK-D)", str(b))
b = SUP.parse_heartbeat("[wb] frames=10 sent=0 idle - camera loop alive, no pose emitted (no body in view)")
check(b["Sent"] == 0 and b["Note"].startswith("idle - camera loop alive"), "wholebody sender, empty room", str(b))
b = SUP.parse_heartbeat("[mp] frames=500 sent=480 tracks=2 dets=2 posed=2 fps~24.1 pose~41.0ms")
check(b["Frames"] == 500 and b["Sent"] == 480, "multiperson sender", str(b))

print("")
print("parse_heartbeat: edge cases")
text = "[vid] frames=1 sent=0 a\n[lan] connected\n[vid] frames=9 sent=2 live=Y\nstray line with no numbers\n"
check(SUP.parse_heartbeat(text)["Frames"] == 9, "the LATEST heartbeat wins, other lines are ignored")
check(SUP.parse_heartbeat("ames=3 sent=1 cut mid-line\n[vid] frames=7 sent=7\n")["Frames"] == 7, "a truncated first line is ignored")
for empty in ("", None, "no heartbeat here\n", "frames=abc sent=1"):
    check(SUP.parse_heartbeat(empty) is None, "no heartbeat -> None: %r" % (empty,))
long_note = "[vid] frames=1 sent=1 " + "x" * 500
check(len(SUP.parse_heartbeat(long_note)["Note"]) <= SUP.HEARTBEAT_NOTE_MAX, "a very long note is capped")

print("")
print("the supervisor's state file")
tmp = tempfile.mkdtemp(prefix="f51_test_")


def make_supervisor(name):
    args = SUP.build_arg_parser().parse_args(
        ["--script", os.path.join(os.path.dirname(SUP.__file__), "tools", "video", "video_udp_sender.py"),
         "--video", "http://192.168.1.5:3000/viewer?room=abc", "--evidence-dir", os.path.join(tmp, name)])
    return SUP.Supervisor(args)


def state_of(sup):
    sup.write_state()
    with open(sup._state_path, encoding="utf-8") as f:
        return json.load(f)


sup = make_supervisor("a")
st = state_of(sup)
check("Child" in st and st["Child"] is None, "no log yet -> Child is null, the file is still valid JSON")
for key in ("SupervisorState", "SidecarPid", "SidecarUptime", "RestartCount", "LastExitCode",
            "LastFailureReason", "BackoffSeconds", "SidecarReady", "CurrentSessionId"):
    check(key in st, "existing key still present: %s" % key)

log = os.path.join(tmp, "run.txt")
sup._stdout_log_path = log
check(state_of(sup)["Child"] is None, "a log path that does not exist yet -> null, no exception")
open(log, "w", encoding="utf-8").write("")
check(state_of(sup)["Child"] is None, "an empty log -> null")
open(log, "w", encoding="utf-8").write("[vid] producer session id = vdabc\n[vid] interpreter : x\n")
check(state_of(sup)["Child"] is None, "banner lines only (no heartbeat yet) -> null")
open(log, "w", encoding="utf-8").write("[vid] banner\n" + video + "\n")
st = state_of(sup)
check(st["Child"]["Frames"] == 2031 and st["Child"]["Sent"] == 4 and st["Child"]["Live"] == "Y",
      "a heartbeat in the log reaches the state file", str(st["Child"]))
check(0.0 <= st["Child"]["LineAgeSeconds"] < 5.0, "LineAgeSeconds says the log just grew", str(st["Child"]["LineAgeSeconds"]))

old = time.time() - 30
os.utime(log, (old, old))
check(25.0 <= state_of(sup)["Child"]["LineAgeSeconds"] <= 40.0, "a log that stopped growing 30 s ago reports a stale line")

# a big log: only the tail is read, and the newest heartbeat still wins
with open(log, "w", encoding="utf-8") as f:
    for i in range(5000):
        f.write("[vid] frames=%d sent=%d skipped=0 fps~29.0 replay=0 live=Y\n" % (i, i))
check(state_of(sup)["Child"]["Frames"] == 4999, "a large log: the NEWEST heartbeat is found from the tail alone")

os.remove(log)
check(state_of(sup)["Child"] is None, "the log disappearing mid-run -> null, not a crash")

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
