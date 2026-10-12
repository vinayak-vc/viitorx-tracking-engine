# viitorx-vrm-model-python

Python **tracking sidecar** for the Virtual Mirror VRM avatar app. It runs pose/hand models on the
host + OAK-D depth camera and streams landmarks to the Unity client over a local UDP socket. Running
the model code in a separate process (not a native plugin inside Unity) keeps any DepthAI/GPU
instability out of the editor — that was the whole point of the sidecar design (ADR-016, "Option B2").

The Unity consumer lives in the `viitorx-vrm-avtar-unity` project
(`Runtime/Tracking/OakD/OakDUdpPoseProvider.cs`).

## Repository role (submodule)

This repo (`vinayak-vc/viitorx-vrm-model-python`) is consumed as a **git submodule** of the Unity
project repo `vinayak-vc/viitorx-vrm-avtar-unity`, checked out at:

```
Assets/Games/viitorx-vrm-avtar-unity/python-sidecar~/
```

The trailing `~` makes Unity's asset pipeline **ignore the whole folder** (same convention as
`Samples~`), so the model blobs and the `.venv` are never imported by the editor. The virtualenv is
named `.venv` (dot-hidden) as a second safeguard. **This supersedes the old `oak_sidecar/` folder at
the Unity-project root, which is deprecated — use this submodule from now on.**

---

## Pipeline stages owned by this repo

The sidecar owns everything from the sensor up to the UDP datagram. Unity owns everything after it.

**Docs for this repo** — read these before changing anything:

| File | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | rules for AI agents working here + the **repo responsibility boundary** |
| [`docs/project-overview.md`](docs/project-overview.md) | what this repo is and is not |
| [`docs/architecture.md`](docs/architecture.md) | stages, module map, coordinate spaces, failure behaviour |
| [`docs/decisions.md`](docs/decisions.md) | **ADR-P001…P007** — why every tuned constant is that value |
| [`docs/roadmap.md`](docs/roadmap.md) | delivered vs next candidates |
| [`docs/tasks.md`](docs/tasks.md) | checklist + gotchas |
| [`docs/ai_handoff.md`](docs/ai_handoff.md) | **start here to resume work** |

The Unity counterpart decisions are **ADR-028…031** in the Unity project's `docs/decisions.md`, with
measured evidence in `docs/P0_ACCEPTANCE_2026-09-07.md`, `P1_1_TRACKER_*.md`, `P1_2_FRESHNESS_*.md`,
`P1_3_POSE_BUFFER_*.md`.

```
OAK-D RGB + RGB-aligned stereo depth
  → latest-frame queue policy        P1-2  (newest RGB; depth matched by TIMESTAMP)
  → RTMW3D-x inference (~21 ms, ONNX Runtime / DirectML)
  → per-keypoint depth sample (5x5 window, 30th percentile) + back-projection
  → P0 smoother     One-Euro + 0.35 m distal caps + bounded hold-on-dropout
  → P1-1 JointTracker   TRACKED / WEAK / PREDICTED / LOST + plausibility
  → UDP JSON  { lm[33], lh?, rh?, xyz, src, seq, t }
─────────────────────────────────────────────────────── process boundary → Unity
```

**Layering rule:** P1-1 improves the *signal*; the Unity-side **P0 LimbGate is the final safety
mechanism**. A joint P1-1 marks `LOST` has its **emit confidence zeroed**, which is byte-identical to a
real occlusion, so Unity's gate still makes the final call. Never move safety out of P0.

---

## Layout

The root holds the **production path and nothing else**: the two entry points plus the nine modules
`wholebody_udp_sender.py` imports. Everything else is a harness, a self-test or a superseded
one-off, and lives under `tools/` or `tests/` (ADR-065).

That split is what lets the Unity build copy the root directory verbatim and get exactly what a
player needs — see `Editor/SidecarBuildPostprocessor.cs`.

```
.
├── sidecar_supervisor.py       # ENTRY: watchdog; restarts a dead sidecar. Unity launches this.
├── wholebody_udp_sender.py     # ENTRY: RTMW3D + measured depth + P0/P1 stages -> UDP 8899
│
├── rtmw3d_pose.py              # RTMW3D ONNX wrapper (SimCC decode, person-box tracking)
├── oak_depth.py                # DepthAI pipeline, intrinsics, depth sampling, back-projection
├── f18_portrait.py             # F-18/F-19 portrait transform (verified, imported - not re-derived)
├── smoothing.py                # P0: One-Euro + per-index displacement caps + bounded hold
├── joint_tracker.py            # P1-1: temporal state + plausibility
├── kinematic_recovery.py       # P1-4: skeleton constraints + long-horizon recovery
├── target_ownership.py         # F-21: single-person target ownership
├── pose_validation.py          # F-22: human / biomechanical pose validation
├── f21_cue_display.py          # F-21: on-screen subject cues
├── _sidecar_path.py            # import shim so harnesses in subfolders resolve the modules above
│
├── tests/                      # 8 self-tests. No pytest needed; each prints "N/N assertions passed"
│                               #   test_target_ownership 75, test_kinematic_recovery 39,
│                               #   test_joint_tracker 37, test_surface_depth 32, test_pose_validation 22
│
├── tools/
│   ├── capture/      (23)      # F-16 stereo config sweeps, F-18 portrait, F-19 production capture
│   ├── deployment/    (7)      # F-20A/F-20B supervisor, USB and failure-injection harnesses
│   ├── ownership/    (13)      # F-21 two-person ownership: protocols, replays, adversarial
│   ├── validation/    (6)      # F-22 pose validation: thresholds, rejections, replay soak
│   ├── video/         (4)      # F-23 video-driven pipeline and demo composition
│   ├── diagnostics/  (11)      # F-24..F-27 jitter, fidelity, humanized-skeleton analysis
│   └── armaim/        (9)      # ARM V2/V3 aiming analysis (was loose inside arm_v*_evidence/)
│
├── scripts/                    # double-clickable launchers. Each re-bases to the repo root with
│   ├── run_supervisor.bat      #   `cd /d "%~dp0.."`, so they work from this subfolder.
│   ├── run_capture.bat         # capture + compare_logs report
│   ├── oak_guided_v4.ps1       # guided LIVE capture, instructions SPOKEN aloud (the subject is
│   │                           #   2 m away and cannot read a console)
│   ├── run_p0_acceptance.bat   # DOES NOT RUN - needs analyze_capture.py, deleted in 29ec57e.
│   └── run_p12_ab.bat          # DOES NOT RUN - needs compare_p12.py, deleted in 29ec57e.
│                               #   Both kept because P0_ACCEPTANCE / P1_2_FRESHNESS cite them by
│                               #   name as the command that produced their numbers. They exit 1.
│
├── setup_sidecar.ps1           # one-time target setup; VERIFIES the DirectML provider
├── requirements.lock.txt       # exact pins - this is what setup installs
├── requirements.txt            # human-readable intent
│
├── docs/                       # sidecar-local notes + p0_human_report.txt
│
├── evidence/                   # ALL capture output, under one root. Resolve it with
│   ├── oak_v4/                 #   evidence_paths, never by hand - see below.
│   ├── arm_v1/                 # Tracked: the distilled .txt/.json analyses the reports cite.
│   ├── arm_v2/                 # Git-ignored: the per-frame captures, which are regenerable.
│   └── arm_v3/
│
├── evidence_paths.py           # the ONLY place that knows where evidence lives
│
└── depthai_blazepose/          # vendored geaxgx/depthai_blazepose (MIT), Phase-1 fallback.
                                # Superseded and NOT imported by the production path.
```

### Evidence paths

Every capture path resolves through `evidence_paths`, never through a literal:

```python
import evidence_paths as EV
EV.oak_v4("f21")            # <root>/evidence/oak_v4/f21
EV.evidence("oak_v4/f21")   # same thing; embedded separators are fine
EV.ensure_dir(EV.oak_v4("f21"))
```

It resolves from the module's own location, so the answer does not depend on the caller's working
directory or on how deep the calling script sits. Both had already caused silent bugs: the ADR-065
move broke thirteen harnesses that built paths from a per-file `HERE`, and seven others hardcoded an
absolute `C:\Unity\...` path to a checkout that exists on no machine - including this one, since the
project now lives on `D:`.

Set `VIRTUAL_MIRROR_EVIDENCE_DIR` to redirect the whole tree, e.g. to keep captures off the repo
volume.

The root holds 18 files: the 12 production `.py` above plus `.gitignore`, `README.md`, `AGENTS.md`,
`setup_sidecar.ps1` and the two requirements files — each of which belongs at a repository root by
convention. It was 96 before ADR-065.

**Running a harness.** They are still plain scripts, so only the path changed:

```powershell
.venv\Scripts\python.exe tests\test_target_ownership.py
.venv\Scripts\python.exe tools\diagnostics\f26_fidelity_analyze.py --selftest
.venv\Scripts\python.exe tools\ownership\f21_walkin_protocol.py --help
```

Anything under `tools/` or `tests/` that imports a project module carries a two-line prelude that
walks up to `_sidecar_path.py` and puts the root — and every `tools/` group — on `sys.path`. Python
only puts the *script's own* directory on the path, so without it a harness one level down cannot
`import rtmw3d_pose`.

Capture output (`pipeline_logs*/`, `evidence/oak_v4/`, `probe*/`, …) is **git-ignored** — regenerate
on demand; the commands are in the F-report that used them.

---
## Setup (Windows, Python 3.10)

```bash
"C:\Program Files\Python310\python.exe" -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

> Use Python **3.10** to match the OAK-D `depthai` wheels (cp310). ONNX Runtime uses the DirectML
> execution provider on the RTX 3060 — no CUDA toolkit required. The RTMW3D ONNX (369 MB) lives at the
> Unity project's `Assets/SentisModel/rtmw3d-x.onnx` (git-ignored; download URL in ADR-015).

---

## Run

### Primary — whole-body RTMW3D + measured depth

```bash
.venv\Scripts\python wholebody_udp_sender.py --model ..\..\..\SentisModel\rtmw3d-x.onnx
.venv\Scripts\python wholebody_udp_sender.py --model <onnx> --show          # + cv2 preview
.venv\Scripts\python wholebody_udp_sender.py --model <onnx> --seconds 20    # auto-stop (testing)
.venv\Scripts\python wholebody_udp_sender.py --model <onnx> --log-dir pipeline_logs
```

Stand ~2–2.5 m out, full body in frame. Beyond ~2.5 m depth coverage and facing both degrade.

### Supervised — restart on a device crash (F-20B)

`sidecar_supervisor.py` owns the sidecar's lifecycle: readiness detection, backoff, crash-loop
protection and a single-instance guard. Use it for unattended running and for live test sessions,
where a DepthAI fault would otherwise end the experiment.

```bash
.venv\Scripts\python sidecar_supervisor.py --model ..\..\..\SentisModel\rtmw3d-x.onnx
.venv\Scripts\python sidecar_supervisor.py --model <onnx> --show --cue-file <path\to\cue.json>
```

`--show` and `--cue-file` are forwarded verbatim to the sidecar (F-21 §31). They are named
explicitly rather than accepted as a generic passthrough: a supervisor that forwards arbitrary
strings could also forward `--no-ownership`, and then the thing under test is not the thing that was
configured. With neither flag set the launched command line is byte-identical to before.

Run every command above with `.venv\Scripts\python.exe`, not the `python` on PATH — they are
different environments and only the venv has DirectML. See `docs/ai_handoff.md` §6.

#### Stability flags (defaults are the shipping configuration — change only with evidence)

| Flag | Default | Stage | What it does |
|---|---|---|---|
| `--arm-max-jump` | `0.35` | P0-2 | per-frame displacement cap (m) for elbows+wrists; slewed, not dropped |
| `--leg-max-jump` | `0.35` | P0-2 | same for knees+ankles |
| `--max-jump` | `1.5` | P0 | global cap for trunk + hands (kept generous) |
| `--max-hold-frames` | `8` | P0 | bounded hold through depth dropouts |
| `--min-cutoff` / `--beta` | `0.5` / `0.4` | P0 | One-Euro stillness / reaction |
| `--depth-min-cutoff` / `--depth-beta` | `0.3` / `0.1` | P0 | heavier filtering on the noisy Z axis |
| `--tracker` / `--no-tracker` | ON | P1-1 | per-joint temporal tracking + plausibility |
| `--tracker-predict-frames` | `6` | P1-1 | max PREDICTED frames before a joint goes LOST |
| `--tracker-reacquire-frames` | `5` | P1-1 | blend frames on reacquisition (never teleport) |
| `--latest-frame` / `--no-latest-frame` | ON | P1-2 | drain queues to the NEWEST frame each iteration |
| `--conf` | `0.3` | P0 | emit-gate threshold (matches Unity's `limbConfidenceThreshold`) |
| `--inject-load-ms` | `0` | — | **TEST ONLY.** Adds artificial work to reproduce the consumer-slower-than-sensor condition without a human. Never set in production. |

> **Why `0.35 m`:** peak *legitimate* fast-arm displacement measured **0.0746 m** — 4.7× headroom —
> while pre-P0 spikes reached 0.5–0.9 m. **Why latest-frame:** `DataOutputQueue.get()` returns the
> **oldest** packet, so a full `maxSize=4` queue at 30 fps meant every pose was ~133 ms stale
> (measured 131 ms). Draining to newest took frame age to **31 ms** with no fps or compute change.

### Device-free — drive Unity without a camera

`mock_udp_sender.py`, `inject_occlusion.py` and `stream_motion.py` were removed in `29ec57e`
("Remove legacy verification and video streaming scripts"). Their surviving replacements:

```bash
# Replay a video file through the real pipeline and stream the result to Unity (F-23).
.venv\Scripts\python.exe toolsideo23_video_to_unity.py --help

# A stand-in sidecar for exercising the supervisor's restart paths without hardware (F-20B).
.venv\Scripts\python.exe tools\deployment20b_fake_sidecar.py
```

### Video as the source — the same wire, from a file or a link (F-49)

`tools/video/video_udp_sender.py` runs the production single-person pipeline (RTMW3D, the production
landmark builders, P1-1 states) on a video and sends the **same datagram** the OAK-D senders send, so
Unity — every scene, the skeleton show, the VRM avatar — runs unchanged. Unity launches it through the
supervisor when `AppBootstrap > Video Test Source > Track From Video` is ticked; by hand:

```bash
.venv/Scripts/python.exe tools/video/video_udp_sender.py --video ../../video/SR7.mp4
.venv/Scripts/python.exe tools/video/video_udp_sender.py --video https://example.com/clip.mp4
```

`--video` is a local path, a **direct** http(s) link to a video file (downloaded once into
`--cache-dir`, default the OS temp folder), or a web page, which needs `yt-dlp` — **not installed in the
venv**; snapshot `pip freeze` before installing it. Only `http`, `https` and existing files are accepted.

A **phone or webcam** works too: `--video camera:0` (a phone shows up here when DroidCam / Iriun / Camo expose it as a
Windows webcam; `--list-cameras` shows which indices open), or a phone "IP camera" app over Wi-Fi as
`--video http://192.168.1.5:8080/video`, or `--video rtsp://host/stream`. Add `--rotate 90|180|270` for a sideways portrait
phone and `--flip` for a front camera; both act on the image before the model. A live source is processed newest-frame-only
(stale frames are dropped, never queued) and reconnects on loss. Credentials in a link are never printed.

#### A phone over WebRTC — the LAN Camera Streaming viewer link (F-50)

`--video http://192.168.1.5:3000/viewer?room=room-abc123` (or `lan:<url>`) is a **LAN Camera Streaming** viewer link:
the phone's browser sender page streams over WebRTC and this process joins the room as the viewer
(`tools/video/lan_camera.py`, a utility with the same surface as `LatestFrameReader`). Nothing to install on the phone.

```bash
set CAMERA_PIN=123456
.venv/Scripts/python.exe tools/video/video_udp_sender.py --video "http://192.168.1.5:3000/viewer?room=room-abc123" --rotate 90
```

* **The PIN is never in the link or on the command line.** It comes from the `CAMERA_PIN` environment variable
  (Unity sets it for the sidecar it launches, from `AppBootstrap > Lan Camera Pin`). A link that carries `pin=` is refused.
* **One viewer per room.** A browser viewer tab open in the same room blocks this reader; it keeps retrying, so closing
  the tab is the fix. A wrong PIN, an invalid room or a media-mode mismatch ends the sender with exit code 2 and one
  clear line (never the PIN); an unreachable server is retried forever.
* Needs `aiortc`, `python-socketio[asyncio_client]` and `aiohttp` (in `requirements.txt` / `requirements.lock.txt`);
  imported only when a LAN camera is used. Snapshot `pip freeze` before touching the venv: installing them was checked
  not to move numpy, OpenCV, onnxruntime-directml or protobuf.
* Only the newest frame is processed; a phone that changes resolution mid-stream is noticed and the crop/intrinsics are
  re-derived. If the avatar lies sideways use `--rotate 90|270` (aiortc ignores a phone's orientation header) or let
  `--auto-orient` find it (F-57, below).
* When WebRTC cannot connect (router client isolation, firewall, a phone on another network with no TURN entry on the
  server) the log says so with the likely causes. There is no MJPEG/tunnel fallback in this utility.
* The server's side of the contract (events, errors, pitfalls, a checklist for a receiver in any language) is documented in the LAN
  camera project: `lan-camera-streaming/docs/receiver-guide.md`. `lan_camera.py` is written to it and is a Python reference.

#### Portrait or landscape, upright or sideways — `--auto-orient` and `--hfov-axis long` (F-57)

A phone on the LAN camera and a webcam are turned between portrait and landscape by hand, and an app may deliver the picture
upright, sideways or upside down. `--rotate` is a fixed number; `--auto-orient` finds it:

```bash
.venv/Scripts/python.exe tools/video/video_udp_sender.py --video <clip|camera:0|viewer link> --auto-orient --hfov-axis long
```

* **How it decides.** It runs the person detector and the pose model on the four rotations of a few frames and keeps the rotation
  where a person is detected AND the pose reads as a body standing up (head above shoulders above hips above ankles). It needs a
  person in view; until it has 4 agreeing frames the frames are not used (a log line says `orientation: rotate N (...)`). With nobody
  in view for 4 s it keeps `--rotate` and tries again later.
* **When it looks again.** At the start; whenever the delivered frame changes size (a phone turned between portrait and
  landscape does exactly that); and, slowly, while nothing is being emitted (3 s), which covers a rotation that keeps the frame
  size. It switches only after 3 agreeing checks, and never while poses are being sent.
* **`--hfov-axis long`.** `--hfov` is then the field of view across the LONGER side of the picture, so one phone has one focal
  length in pixels in portrait and in landscape. The default `width` is the old rule (a portrait frame gets a shorter focal
  length than the same phone in landscape).
* **A person off to the side.** With `--auto-orient` the single-person path starts its crop from the person the detector finds, not
  from the centred 3:4 region (only ~42% of a landscape frame's width).
* **Needs** `models/yolox_s.onnx` (`tools/model/fetch_yolox_s.py`). Without it the sender prints a WARNING and keeps `--rotate`.
  `--max-poses` already loads it; the detector is shared, not loaded twice. Off (the default) imports nothing new.
* Not for the OAK-D senders: they have their own `--portrait` / `--portrait-dir` (F-19) because the camera's mount is known.

#### A body the frame cuts off — `--partial-body` (F-60)

A laptop webcam shows the head and shoulders. The hips are not in the picture, and RTMW3D keeps answering for them anyway (hips at
confidence 0.6 on the image border, wrists and knees below the edge at >= 0.3), so a confidence gate cannot tell a hip that is seen
from one that is imagined. `--partial-body` (on by default; `--no-partial-body` is the old gate) decides it from the picture:

* **The anchor, every frame, per person.** Both hips in the picture -> tier 0 (the root is the measured mid-hip, exactly as before).
  Else both shoulders -> tier 1 (the root is ESTIMATED from the shoulder line and a hip offset learned from this person while their
  hips were visible). Else the nose and a face width -> tier 2. Nothing -> the frame is dropped. It works in the middle of a run: a
  person who leans over a desk falls to tier 1 and back with no restart. Hips need to be 5% clear of the picture's edge to start
  counting (a hip on the border is the model's biased guess and flaps the tier), and leave only when they are outside it.
* **On the wire:** `fr` = 0 / 1 / 2 (per person and at the root; absent = hips). Below tier 0 the hip joints and everything under them
  are sent as absent (`[0,0,0,0]`), never invented; so is any joint outside the image, in every tier.
* **Depth** is the first rule the frame supports: height (nose to ankle, with the hips in view and each ankle >= 0.8 torso below them) ->
  shoulder width -> face width -> 2.0 m. The widths are learned in metres from the last measured depth, so the depth continues when the
  hips go; a depth from a width may rise at most 40% a second (turning only ever makes a person look farther). `--depth-range` bounds
  only the height rule and the default, so a webcam at 0.6 m is not pinned to Unity's 1.6 m.
* **Measure it:** `tools/video/f60_partial_body_eval.py capture|eval|midrun` (needs a clip with a full body). Numbers: Unity repo
  `docs/decisions.md` ADR-096. Not run on a physical webcam.

#### Several people from a phone, webcam or video — `--max-poses N` (F-52)

A phone, a webcam and a video file have no OAK-D, so the production crowd detector (on the OAK's VPU) is not available. `--max-poses 2`
or more adds a **host-side person detector** (YOLOX-s, Apache-2.0, on the same GPU) and the F-32 `PersonTracker` in front of the same
pose model, and sends the F-32 `persons[]` datagram (persons[0] is also published at the root in the single-person shape, so every
scene consumes it unchanged).

```bash
.venv/Scripts/python.exe tools/model/fetch_yolox_s.py          # once: 35.9 MB, size and SHA-256 verified, not in version control
.venv/Scripts/python.exe tools/video/video_udp_sender.py --video <clip|camera:0|viewer link> --max-poses 3
```

* `--max-poses 1` (default) is the old single-person path, unchanged; the detector is not even imported.
* `--detector <onnx>` / `VIRTUAL_MIRROR_DETECTOR` choose the weights; `--det-conf` (0.45, the OAK path's value, not tuned for this
  detector) the bar for "a person".
* Each person costs one sequential pose solve. Measured on an RTX 3060 (decode excluded): 2 people 35.5 fps, 3 people 26.3,
  5 people 17.5, 8 people 14.8.
* Each person has their own z filter and P1-1 joint tracker (the single-person video chain), keyed by track id and released 3 s after the
  track disappears. Ids are never reused: a person gone for longer than the tracker's 1.2 s hold is a new id on return.
* Limits: no stereo (hip depth from apparent height; a person whose ankles are hidden gets 2.0 m), no F-21 ownership, ids fragment under
  heavy occlusion.
* **Big files:** a video over 2.5 megapixels (4K) is decoded on its own thread so decoding and inference overlap (`--prefetch` / `--no-prefetch`; automatic by default). Measured on a 4K
  two-person clip: 13.4 -> 21.8 fps. 4K decode alone tops out near 24 fps, so a 1080p copy is what plays in real time. Downscaling a 4K source before the pose stage gained nothing (measured).
* **A file plays at its own speed:** when posing falls behind the clip's clock, late frames are dropped (`--drop-late`, default on; `--no-drop-late` processes every frame in slow motion) so the skeleton stays in step with the picture.
  The heartbeat shows `speed=` (1.00 is real time) and `late=`. Dropping only helps when the next frame is already decoded: if DECODING is the limit (4K) the sender plays at the decoder's speed and warns - use a 1080p copy.
* **Slow starts are not hangs:** while the pose model or the person detector loads, the sender repeats its `frames=0 sent=0 loading ...` line every 2 s, so the supervisor's 10 s silence limit does not fire.
* **From Unity:** `AppBootstrap > Video Max People`. It reaches the sender through the supervisor's `--video-max-poses N` (forwarded as `--max-poses N`). That is
  a separate flag from the supervisor's `--max-poses`, which is the OAK multi-person cap and is ignored by the video sender.

#### Camera setup and tips for better results (a phone, webcam, USB camera or video)

What follows is split into what was **measured** (with the numbers) and what is **general advice that was not measured here**; the difference is marked.

**1. Resolution does not change how fast the pose runs - 1080p or 720p is plenty.** *(measured)* The detector sees a 640 px picture and the pose model a fixed 288x384 crop around each person, cut from the original pixels.
The same two-person clip at native 4K, 1920 wide and 1280 wide took 29.6 / 29.2 / 29.4 ms per frame and gave identical joints (21.0) and jitter (8.5 / 8.4 / 8.5 mm). So a higher-resolution camera does not make
inference slower - and a lower one does not make it faster. A 720p camera gives the same frame rate as a 1080p one; an 8K one would only add bandwidth, decoding and memory (~100 MB a frame) for nothing the pose stage can use.

**2. Where resolution DOES cost something is getting the frames to the model.** *(measured for files)* Decoding a 4K VP9 **file** took 33 ms a frame (spikes to ~150 ms) against 3.3 ms for the same clip at 1080p, and capped playback near 24 fps on this
machine, so it played in slow motion (0.62x live in Unity). A live USB camera sends frames in its own format, so that figure does not carry over - **a 4K USB camera was not measured** - but it would still need more USB bandwidth and decoding than the pose stage can use.
Set the camera to **1080p (or 720p)**. A 4K FILE should be re-encoded to 1080p first (`ffmpeg -i in.webm -vf scale=1920:1080 -c:v libx264 -crf 20 -an out.mp4`); the sender says so (`WARNING the clip is advancing at 0.69x ...`) when decoding is the limit.

**3. What actually sets the frame rate is GPU time per person.** *(measured, RTX 3060, FP16 model)* About 9 ms a person standalone and 10-15 ms while Unity is rendering. Two people: ~35 fps standalone, ~25 fps live in Unity; three people: ~26 fps standalone; five: ~17.
The heartbeat's `speed=` shows whether a FILE is keeping up (1.00 = real time). To go faster: fewer people (`Video Max People` = the number really there), a lighter Unity (a built player rather than the Editor; close other GPU programs), and the FP16 model (automatic when `rtmw3d-x-fp16.onnx` is present).

**4. A 60 fps camera will not give 60 fps of skeleton.** *(reasoned from the measurements above)* A live source processes only the newest frame and discards the rest, and two people cost ~28-40 ms a frame, so the output stays at roughly 25-35 fps whatever the camera delivers. 60 fps still helps in two ways: the newest frame
is at most ~16 ms old instead of ~33 ms (less latency), and cameras usually use a shorter exposure at 60 fps, which means less motion blur on fast moves *(general knowledge, not measured here)*. It needs good light.

**5. Framing matters more than resolution.**
* **Whole body in view, hips and ankles included.** A person is only posed when both hips are confident; hip DEPTH is estimated from the nose-to-ankle span, so a person for whom fewer than two of {nose, ankles} are visible falls back to 2.0 m and sits at the wrong distance *(from the code; seen on the occluded back row of the 8-dancer clip)*.
* **Big enough in the picture:** the pose crop is 384 px tall, so a person at least ~400 px tall (about 37% of the frame height at 1080p) loses nothing. A ~170 px person (the same clip at 288x512) cost +12% jitter *(measured)*; anything much smaller is stretched and loses detail.
* **Do not stack people.** Heavy overlap is what splits identities: on the 8-dancer clip the tracker used 12 ids for 8 people; two dancers side by side kept exactly 2 ids over 1,934 frames *(measured)*. Ids are never reused, so a person gone for over ~1.2 s returns as a new id.
* **A fixed, level camera at about hip or chest height**, landscape for 2-3 people side by side. Level it if you can: a tilted mount distorts the geometry (the `Camera Tilt Degrees` setting was built for the OAK-D's stereo depth; whether it helps a monocular source was not checked).
* **Clothing that contrasts with the background** and nobody standing in front of a busy pattern *(general advice)*.

**6. Camera settings** *(general advice)*: MJPEG over USB 3; **fixed exposure and fixed focus** (auto-exposure and autofocus make the picture pump and hunt while someone dances); even lighting; a fast shutter for dance; the lens's real horizontal field of view.

**7. Unity fields that change the result.** `Video Max People` (the real count); `Video Horizontal Fov Degrees` (the camera's actual horizontal FOV - it scales the pose, default 60); `Video Subject Height Metres` (sets the estimated distance, default 1.7); `Video Rotate Degrees` / `Video Flip Horizontal` for a sideways phone or a selfie camera;
`Video Camera Size` 1920x1080 (or 1280x720).

**8. Known limits that are not a camera problem.** No stereo, so depth is estimated per person; no F-21 ownership; the crowd scenes fed this way have not been checked by me on a screen; two or three REAL people from a real camera have not been recorded yet (the 2-person evidence is `147.webm`).

#### Supervisor state and Unity's status lights (F-51)

`sidecar_supervisor.py` writes `evidence/oak_v4/f20b/supervisor_state.json` every ~2 s. It now includes the sender's latest heartbeat, so
Unity can show whether inference is running and whether anybody is being tracked without a sidecar window:

```json
{ "SupervisorState": "RUNNING", "SidecarReady": true, "RestartCount": 0, "...": "...",
  "Child": { "Frames": 296, "Sent": 210, "Skipped": 85, "Live": "Y", "Note": "", "LineAgeSeconds": 1.7 } }
```

`Frames` = source frames the sender consumed, `Sent` = datagrams emitted (frames moving while `Sent` does not = nobody found), `Live` =
`Y`/`n` for a live source, `Note` = the free text after the numbers (`loading RTMW3D ...`, `waiting for frames`), `LineAgeSeconds` = how long
ago the sender's log last grew. `Child` is `null` until a heartbeat exists. **`SidecarReady` is not "model loaded"**: READY is marked at the
first `frames=` line, which the senders print before loading the model. The Unity side (`SidecarStatusModel`, `SidecarStatusLights`) is
the only consumer that depends on these field names; `tests/test_supervisor_state.py` pins this end.

What it is not: **no stereo** (every `src` flag is 0 unless `--assume-measured`, which relabels and moves
nothing), **no crowd** (the detector runs on the OAK-D's VPU), hip depth **estimated** from
`--subject-height` and `--hfov`, F-21 **off** unless `--ownership`. `--depth-range MIN MAX` clamps the
estimate; leave it off to let a clip's framing exercise Unity's F-48 operating volume. `--loop 0` (default)
replays forever; after a finite `--loop N` it idles rather than exiting, because the supervisor would
restart it. It prints the supervisor's readiness markers and a heartbeat, including while downloading.

### Phase 1 — BlazePose on the OAK-D VPU (fallback)

```bash
cd depthai_blazepose
..\.venv\Scripts\python udp_pose_sender.py --lm full --show
```

Device note: this unit is an **OAK-D-PRO-W** (wide lens, **OV9782** 1280×800 color). Defaults
`--color_res 800p --color_scale 1/2` give the full ~120° FOV at 640×400.

---

## Tests

Self-contained: no pytest, each prints `N/N assertions passed` and exits 0 on success. Last full run
after the ADR-065 reorganisation:

```bash
.venv\Scripts\python.exe tests	est_target_ownership.py       # F-21 ownership          75/75
.venv\Scripts\python.exe tests	est_kinematic_recovery.py     # P1-4 recovery           39/39
.venv\Scripts\python.exe tests	est_joint_tracker.py          # P1-1 tracker            37/37
.venv\Scripts\python.exe tests	est_surface_depth.py          # depth sampling          32/32
.venv\Scripts\python.exe tests	est_pose_validation.py        # F-22 validation         22/22

.venv\Scripts\python.exe tools\diagnostics26_fidelity_analyze.py --selftest    # 28 passed
.venv\Scripts\python.exe tools\diagnostics27_humanized_analyze.py --selftest   # 18 passed

# F-49 video source: classification, atomic bounded download, supervisor command shape.
.venv/Scripts/python.exe tests/test_video_source.py           # 150/150
# F-51 the supervisor copies the sender's heartbeat into supervisor_state.json (`Child`), for Unity's status lights.
.venv/Scripts/python.exe tests/test_supervisor_state.py       # 32/32
# F-52 host-side person detector + multi-person layer (scripted networks; the real-network layer skips when the weights are absent).
.venv/Scripts/python.exe tests/test_host_multiperson.py       # 73/73
# F-60 a body the frame cuts off: framing.py (the tiers, the learned offsets, depth, masking) + its wiring into both senders.
.venv/Scripts/python.exe tests/test_framing.py                # 72/72
# F-50 LAN camera (WebRTC): 78 assertions run anywhere; set CAMERA_SERVER_DIR to the lan-camera-streaming
# project folder and it also drives that real Node server with a fake phone (and the model, for the full chain).
.venv/Scripts/python.exe tests/test_lan_camera.py             # 103/103 with the server, 78/78 without

# Replay + adversarial against a real capture (needs a --log-dir from a session).
.venv\Scripts\python.exe tools\diagnostics\evaluate_p1.py --dir <capture-dir>
```

`test_joint_tracker.py` is self-contained (no pytest). It covers stationary, constant velocity, fast
motion, isolated spike, **high-confidence spike**, 3/5/10-frame dropouts, prediction, prediction
expiry, reacquisition, large-error reacquisition, irregular timestamps, zero/invalid measurement,
depth inconsistency, neighbour constraint, and a per-frame cost budget check.

---

## Diagnostics

`--log-dir <dir>` writes JSONL streams, one row per frame:

| file | contents |
|---|---|
| `sender_log.jsonl` | per-frame key landmarks, `hipZ`, coverage, **stage timings** and **freshness metrics** |
| `holds_log.jsonl` | P0-2 spike/hold events + P1-1 state transitions and suspicious samples (events only — healthy frames are never logged) |

Freshness / latency fields (permanent production diagnostics, added in P1-2):

| field | meaning |
|---|---|
| `frameAgeMs` | host processing time − camera frame timestamp (OAK device clock) |
| `queueDepth` | packets waiting when the frame was sampled |
| `staleDropped` | frames discarded as stale this iteration |
| `rgbDepthSyncMs` | \|RGB timestamp − depth timestamp\| |
| `camLatMs`, `capToPoseMs`, `poseToDepthMs`, `capToSendMs` | per-stage cost |
| `trackerMs` | P1-1 cost (~0.085 ms for 12 joints) |

The console status line prints `age=NNms stale=N` every 2 s.

---

## UDP wire contract

Local UDP, default `127.0.0.1:8899`, one JSON datagram per frame:

```jsonc
{ "lm":  [[x, y, z, vis], ...33],   // body, hip-relative metres, JointId order (unmapped slots = zeros)
  "lh":  [[x, y, z], ...21],         // left hand landmarks, hip-relative metres  (omitted when untracked)
  "rh":  [[x, y, z], ...21],         // right hand landmarks                      (omitted when untracked)
  "xyz": [hipX, hipY, hipZ],         // measured mid-hip, millimetres, camera space (root position)
  "src": [0|1, ...33],               // 1 = depth-measured, 0 = hip-plane fallback (debug)
  "seq": 1234,                       // monotonic frame id
  "t":   1788846511.0234,            // send epoch seconds
  "st":  [-1..4, ...33],             // F-29 trust channel: P1-1/P1-4 state per joint
  "own": "LOCKED",                   // F-29: F-21 ownership state  (OMITTED under --no-ownership)
  "lat": 41.3,                       // F-29: measured camera->payload latency, ms
  "fr":  0 }                         // F-60: what anchored the root: 0 hips (measured), 1 shoulders, 2 nose (estimated).
                                     //       ABSENT = hips (OAK-D senders, anything older). Video senders only.
```

**Feet are inside `lm`, not a separate field.** `build_body_landmarks` emits `FOOT_TO_JOINTID`
alongside the COCO-17 body: heels at JointId **29/30**, big toes at **31/32**. A consumer that
ignores those four slots is choosing to, not being denied the data.

### The F-29 trust channel (`st` / `own` / `lat`)

Read-only: these echo decisions the pipeline has already made so a consumer can **display** them.
Nothing upstream reads them back and omitting them changes no behaviour, so a consumer written
before F-29 is unaffected.

| `st` | meaning |
|---|---|
| `-1` | **no tracker covers this joint.** Only the 12 joints in `joint_tracker.DEFAULT_TRACKED` have one. This is a claim, not padding — reporting the other 21 as healthy would overstate what the system knows about them. |
| `0`–`4` | `TrackingState`'s own ints: TRACKED, WEAK, PREDICTED, LOST, RECOVERING. Not a parallel enum, so the two cannot drift apart. |

`st` is captured **after** P1-4, so it describes the geometry actually emitted — reading it before
recovery would report `LOST` for a joint that was reconstructed and sent.

`own` **absent** means ownership is not running (`--no-ownership`); it does **not** mean "not
locked". Those mean opposite things to an operator and must not be rendered the same way.

## Multi-person (F-32)

`multiperson_udp_sender.py` is a SECOND sender that tracks several people at once. The production
`wholebody_udp_sender.py` is untouched and remains the single-person path.

```text
OAK-D VPU : stereo depth (30 fps) + person detection (11.4 fps)   <- free, the VPU was idle
host      : PersonTracker assigns persistent, never-reused ids    <- pure numpy
host GPU  : RTMW3D per tracked person, N capped by --max-poses    <- 20.7 ms each, the budget
```

### The multi-person payload

```jsonc
{ "persons": [ { "id": 3,                  // STABLE across frames, never reused once retired
                 "state": "CONFIRMED",     // or "LOST" (held through an occlusion on prediction)
                 "lm":  [[x,y,z,vis] x33],
                 "xyz": [hipX,hipY,hipZ],
                 "src": [0|1 x33],
                 "st":  [-1..4 x33],
                 "fr":  0,                 // F-60, see above (video senders only)
                 "lh":  [...], "rh": [...] },  ... ],
  "n": 3,          // people posed this frame
  "ndet": 5,       // people the DETECTOR saw - can exceed n, the GPU budget is the limit
  "ntrack": 4,     // people the tracker is holding, posed or not
  "seq": 1234, "sid": "...", "t": 1788846511.02, "lat": 41.3,

  // ...PLUS every single-person field, carrying persons[0]:
  "lm": [...], "xyz": [...], "src": [...], "st": [...], "lh": [...], "rh": [...] }
```

**The last part is the whole compatibility story.** The most-established person is republished at the
payload ROOT in the exact single-person shape, so the Unity provider, the VRM mirror app and every
existing experience scene consume this sender **unchanged and unaware** - they see one person, as
they always have. Verified: `root lm == persons[0].lm` on every packet, and across 752 Unity frames
the primary body and `Bodies[0]` were the same person.

Cost: one duplicated person, ~6 KB of a ~9.3 KB payload (max measured 10.2 KB against a 65 KB UDP
limit). `--no-legacy-primary` drops it for a purely multi-person consumer.

### Budget - `--max-poses` is real

RTMW3D is **20.7 ms p50** with a **fixed batch of 1** (no batch axis in the ONNX), so N people cost
N sequential inferences:

| people | pose ms | max fps |
|---|---|---|
| 2 | 41.4 | 24 |
| 3 | 62.1 | 16 |
| 4 | 82.8 | 12 |

Defaults to 3. The tracker emits most-established-first, so the cap drops the people least likely to
still be there next frame rather than an arbitrary subset.

### The detector blob is not committed

2.3 MB binary; fetch it once:

```powershell
.venv\Scripts\python.exe -c "import blobconverter,shutil;shutil.copy(blobconverter.from_zoo(name='person-detection-retail-0013',shaves=6,zoo_type='intel'),'depthai_blazepose/models/person-detection-retail-0013_openvino_2022.1_6shave.blob')"
```

### Per-person filters (F-33)

Each tracked person carries a complete `person_filters.PersonFilters` chain - P0 `KeypointSmoother`,
P1-1 `SkeletonTracker`, F-22 `PoseValidator`, and P1-4 available behind `--filter-recovery` and off -
pooled on their **track id**, created when the identity is born and dropped 3 s after it stops being
seen.

Keyed on the id, never on list position: the tracker re-sorts people most-established-first every
frame, so an index-keyed bank would hand one person's One-Euro history to another.

**Each person measures their own sample rate.** One-Euro derives velocity as `delta * freq`, and this
loop does not run at camera rate - three people cost three sequential 20.7 ms solves, so ~16 fps, and
a person below `--max-poses` is updated rarer still. Told 30 while sampled at 16, the filter
over-estimates speed by 1.9x and opens up exactly when it should damp. Measured against known ground
truth (`tools/diagnostics/f33_filter_bench.py`):

| 10 fps | jitter median | lag |
|---|---|---|
| no filters | 33.5 mm | 0 ms |
| told `freq=30` (the naive port) | **35.6 mm** | 400 ms |
| self-measured rate | 29.9 mm | 200 ms |

The naive port is worse than not filtering at all on the median frame, while costing 400 ms of lag.

**ADR-071: the feet and the head are in the filter group**, which the single-person sender never put
them in. Implausible single-frame steps (over 300 mm in 33 ms - 9 m/s, not a person) across 2060
person-frames of identical input:

| | steps > 300 mm | worst step |
|---|---|---|
| `--no-filters` (raw F-32) | 2158 | 2091 mm |
| `--no-filter-feet --no-filter-head` (single-person grouping) | 630 | 2018 mm |
| default | **77** | **960 mm** |

Cost: **0.88 ms per person-frame** against 20.7 ms for the pose solve. Off-switches for every arm of
that table: `--no-filters`, `--no-adaptive-rate`, `--no-filter-feet`, `--no-filter-head`.

### Known limits

* **Lag is 233 ms at 30 fps.** Not introduced by F-33 - it is the accepted single-person tuning
  (`--min-cutoff 0.5`, `--beta 0.4`). Quote it alongside any jitter figure.
* **A slow confident drift is still followed** (847 mm of an injected 850 mm). P1-4 catches it
  (264 mm) and P1-4 is rejected for production. Inherited from the single-person path on purpose.
* **P1-1's horizons are counted in FRAMES**, so a 6-frame prediction spans ~375 ms at 16 fps against
  ~200 ms in the single-person loop. Only the P0 smoother adapts to the real rate.
* **Never run with two real people in front of the camera.** All evidence is recorded video.
* Long occlusions still cost an identity. Metric depth should improve that; video cannot test it.
  (The earlier "11 ids for 7 people" figure was machine-dependent - the video harness read the wall
  clock, so its constant-velocity prediction changed with host speed. On the video clock it is
  **9 ids across two passes**.)

---

`lat` is the sidecar's leg only. A consumer adds its own receive-to-present time for an end-to-end
figure — and must compare `t` against its own **epoch** clock to do so, never against a
monotonic/stopwatch clock.

**A joint that fails the confidence gate — or that P1-1 marks `LOST` — is emitted as `[0,0,0,0]`.**
That zero is the "invalid" signal, **not** a position. Unity must never interpolate a position toward
it (doing so drags the limb toward the origin — the F-01 collapse bug); Unity's `PoseBuffer` and
`LimbGate` both special-case it.

`seq` and `t` are **load-bearing since P1-3**: Unity's timestamped pose buffer uses them for ordering,
duplicate/out-of-order rejection and interpolation. They are no longer log-only fields.

Axes are camera space (X right, Y down, Z forward), so the Unity `PoseSpaceConverter` +
`poseFlipX/Y/Z` tuning applies. Face blendshapes stay on MediaPipe (not in this stream).
Design detail: Unity project's `docs/26_OakDDepthPhase2.md` + ADR-015/016/018.

---

## Status

- **Phase 1 (done):** OAK-D BlazePose edge → 33 body landmarks, GHUM-estimated depth.
- **Phase 2 (done, hardware-verified):** host-side RTMW3D whole-body + **measured** per-keypoint OAK-D
  depth; body + both hands with metric depth over UDP.
- **Stability program (done, live-verified with a human subject):**
  - **P0** — 0.35 m distal caps, legs into the hold set (ADR-028). Limb spikes **−67…−76%**.
  - **P1-1** — per-joint temporal tracking + plausibility (ADR-029). Catches *confident-but-wrong*
    landmarks that no confidence gate can see; **6/6** adversarial cases detected; **0.085 ms** cost.
  - **P1-2** — latest-frame queue policy (ADR-030). Frame age **131 → 31 ms**, camera→UDP
    **162 → 62 ms**, RGB/depth pairing *improved*; 180 s soak with no accumulation.

### Known limits (do not re-discover these)

- **RTMW3D does not lower confidence for an occluded limb.** A hand hidden behind the torso keeps
  ~0.63 confidence and a *wrong* position (measured: 0 frames below 0.3 across a 45 s block). This is
  why P1-1's plausibility layer exists — confidence alone is not validity.
- **A sustained high-confidence teleport** longer than the 6-frame prediction window ends in `LOST`
  with slow recovery during fast motion. Needs short-gap prediction + blended recovery (not built).
- **Compute spikes >80 ms occur only at frame index 0–2** — ONNX/DirectML warm-up, per *process*.
  A 3378-frame soak spiked only at frames 0 and 1. Not a steady-state problem.
- **Confidence is an un-normalised SimCC peak** (audit F-08); the `0.3` threshold is tuned to that
  scale and is brittle across distance/lighting. P1-1 works around it kinematically.
- Facing/depth degrade past ~2.5 m — a single-front-camera limit (ADR-019/023/024/027).

## Licenses

- This project: MIT - see [`LICENSE`](LICENSE).
- `depthai_blazepose/`: MIT © geaxgx (`depthai_blazepose/LICENSE.txt`).
