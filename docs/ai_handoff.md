# AI handoff — Python tracking sidecar

> **Read this first. Updated 2026-09-17.**
>
> **Where the detailed reports live.** This file was last substantively updated at F-08 (2026-09-08)
> and was 14 features stale. Since roughly F-16 the project's feature reports and ADRs have been
> written into the **Unity repo's** `docs/` directory, not this one:
>
> ```text
> <unity-repo>/docs/decisions.md                                  ADR-001 .. ADR-060
> <unity-repo>/docs/roadmap.md  /  tasks.md
> <unity-repo>/docs/F19_... F20A_... F20B_... F21_... F22_... F23_....md  per-feature reports
> ```
>
> That is the working convention, and `AGENTS.md` §12 has not caught up with it. **Do not** start a
> second parallel set of ADRs here — read the Unity repo's `docs/decisions.md` and append there. This
> file is kept as the pointer and the current-state summary, which is what it is for.

---

## 2026-10-13: CUTOUT (S-01) - THE PERSON IN THE PICTURE, ISOLATED

A third sender and a NEW wire. `cutout_segmenter.py` + `cutout_matte.py` + `cutout_engine.py` + `cutout_wire.py` + `cutout_overlay.py` at the root; the entry `tools/cutout/cutout_udp_sender.py` (webcam, LAN camera, stream, file; the frame loop is the shared `tools/video/vision_sender.py`); the wire is a binary chunked frame (the picture + a soft person mask) on UDP **8903**, documented byte by byte in `cutout_wire.py` and pinned by 26 golden datagrams written into the Unity repo's `Packages/com.viitorx.avtar.tracking.vision/Tests/Editor/VisionFixtures` (`make_vision_fixtures.py`; `test_vision_fixtures.py` checks drift). The networks are MediaPipe's selfie segmenter as two ONNX files (`tools/model/fetch_cutout_models.py`, exactly two URLs, size + SHA-256); the detector and tracker are the existing YOLOX-s and F-32 `PersonTracker`; **no new pip package**. Report: Unity repo `docs/S01_CUTOUT_2026-10-13.md`; ADR-103. Unity renders it (`Runtime/Vision`, `Showcase/Cutout`) and runs NO model.

**How it works in one line.** detector -> tracker -> a square crop per person with the others' boxes painted out (`--isolate`) -> selfie network -> max-merged canvas -> matte -> uint8 mask + the picture, one frame, chunked over UDP.

**Carry forward.** (a) `people` on the wire is the larger of the people tracked and the foreground groups. (b) A wire field change needs the encoder, the fixtures, both test suites and an ADR in both repos. (c) A flag rename breaks Unity's launch silently: `test_vision_cli_contract.py` fails first. (d) `vision_sender.py` accepts a LIST of datagrams from a pipeline (cutout) as well as one (hands, face). (e) Tried and dropped, with numbers: a learned-background veto, a per-person box prior, smoothing the painted boxes (report s.8). (f) The GPU clock falls under the paced duty cycle: a file source's fps decays from ~30 to ~22 over 16 s while the detector's time doubles; not a code defect. **NOT TESTED:** a person on a live webcam (the one 11 s run saw nobody); a phone / LAN camera; a network between machines; a long soak.

## 2026-10-13: PACKAGING PLAN (P-01) - PLAN ONLY

See `docs/PLAN_TRACKING_ENGINE_AND_PACKAGES_2026-10-13.md`. This repo is planned to become the Viitorx Tracking Engine (compiled `viitorx-tracking.exe`, no `.py` files shipped, models beside it, startup file check, Inno Setup installer). **Status 2026-10-16: phases 0-4 done** (compile spike GO, wire spec `docs/WIRE.md`, engine package `viitorx_tracking/`, compiled build + installer in `build/`; plan sections 10-13). Read plan section 13 for what is measured and what is NOT TESTED (installer never run, `fetch-models` frozen over the network, clean machine, LAN WebRTC). Next: phase 5, the Unity packages. Original note: Nothing here has changed yet. A GitHub token was pasted into a chat: it must be revoked, never stored.

## 2026-10-13: UNITY HOSTS THE SENDERS (H-04)

No sender behaviour changed. The Unity repo's AppBootstrap now starts the hands and face senders itself (a `VisionHost`, launched through its `SidecarProcessLauncher` in `DirectSenderOnly` mode), from a settings section and an F6 control panel; two sample scenes in `Showcase/HandsFace`. See Unity `docs/H04_VISION_BOOTSTRAP_AND_SHOWCASE_2026-10-13.md` and ADR-102.
New here: `tests/test_vision_cli_contract.py` (20 assertions): the command lines Unity builds (`Packages/com.viitorx.avtar.tracking.vision/Tests/Editor/VisionFixtures/args_*.txt`) must be accepted by `hands_udp_sender.build_parser()` and `face_udp_sender.build_parser()` and land in the fields they are named for.

**Carry forward:** renaming or removing a sender flag breaks Unity's launch silently (Python exits before the camera opens); the contract test fails first. One camera feeds one sender: the hands and face senders cannot read the same webcam or LAN viewer together. **NOT TESTED:** Unity starting these senders; a live camera.

## 2026-10-13: UNITY RECEIVERS (H-03)

No sidecar behaviour changed. The Unity repo gained `Runtime/Vision` (assembly `VirtualMirror.Vision`) that reads the hands (8901) and face (8902) wires; see Unity `docs/H03_UNITY_RECEIVERS_2026-10-13.md` and ADR-101.
New here: `tools/video/make_vision_fixtures.py` (writes 22 golden datagrams with the production encoders into the Unity repo's `Packages/com.viitorx.avtar.tracking.vision/Tests/Editor/VisionFixtures`; `--real` captures `real_hands.json` / `real_face.json` from the networks) and `tests/test_vision_fixtures.py` (45 assertions, fails on drift).

**Carry forward:** a wire change = encoder + regenerate fixtures + both suites + ADR in both repos. The C# parser reads `ver`, then `kind`, as soon as it sees them and refuses an unknown version, so bump `ver` for an incompatible change. **NOT TESTED:** the Unity Editor compile and Play mode; a live camera; a rigged character.

## 2026-10-13: FACE ONLY, 52 BLENDSHAPES (H-02)

A sibling of the hands pipeline: `face_landmarker.py` + `face_tracking.py` (+ `face_geometry.py`, `face_wire.py`, `face_overlay.py`) at the root, the entry `tools/face/face_udp_sender.py`, the wire `{ver:1, kind:"face"}` on UDP 8902. The crop maths is now `roi_geometry.py` and the One-Euro bank `landmark_filters.py` (both shared with the hands); the frame loop is `tools/video/vision_sender.py`. The networks are community ONNX conversions of the TFLite models in `face_landmarker.task` (`tools/model/fetch_face_models.py`, size + SHA-256 verified); **no new pip package**. Report: Unity repo `docs/H02_FACE_ONLY_2026-10-13.md`; ADR-100.
**Carry forward:** the 52 are `_neutral` + 51 ARKit (no tongueOut): index by name; read blendshapes from the RAW landmarks; the detector is SHORT RANGE; head pose and `pos` are estimates; the conversion's parity with Google's was not reproduced. **NOT TESTED on a live camera.** Next: the Unity receivers.

---

## 2026-10-13: HANDS ONLY, NO BODY (H-01)

A separate pipeline, not a mode of the body one: `hand_landmarker.py` + `hand_tracking.py` (+ `hand_geometry.py`, `hand_gestures.py`, `hand_wire.py`, `hand_overlay.py`) at the root, the entry `tools/hands/hands_udp_sender.py`, the wire `{ver:1, kind:"hands"}` on UDP 8901. The networks are MediaPipe's palm detector and hand-landmark network as OpenCV Zoo ONNX (`tools/model/fetch_hand_models.py`, Hugging Face mirror, size + SHA-256 verified); **no new pip package**. Report: Unity repo `docs/H01_HANDS_ONLY_2026-10-13.md`; ADR-099.
**Carry forward:** `pos.Z` is an ESTIMATE (hand size + assumed `--hfov`), absent with a `why` when refused; the L/R letter follows MediaPipe's mirrored-picture convention; crossing hands can lock both crops onto one hand; the entry point stays in `tools/` until the source handling is extracted to the root. **NOT TESTED on a live camera.** Next: the Unity receiver, then H-02 face only (52 blendshapes, port 8902).

---

## 2026-10-12: A BODY THE CAMERA CUTS OFF (F-60)

`framing.py` anchors the skeleton on the hips while they are in the picture, then the shoulders, then the nose, per person and per frame, and puts the tier in the datagram as `fr` (0/1/2, absent = hips). Below tier 0 the root and its depth are ESTIMATES (shoulder frame + a hip offset learned from this person; shoulder / face width learned in metres from the last measured depth); a joint outside the image is sent as absent; nothing below the hips is invented. `--no-partial-body` is the old hip gate. Full numbers, thresholds and the DO-NOT list: Unity repo `docs/decisions.md` **ADR-096**; the measurement: `tools/video/f60_partial_body_eval.py`.
**2026-10-12 addendum (Unity ADR-097):** the sentence above "nothing below the hips is invented" was not true for joints INSIDE the picture: below tier 0 the model's hips, knees and feet (3-100% of frames, above the hips on a seated user) were still sent. `FramingTracker.mask_outside(..., tier)` now zeroes WholeBody 11-22 whenever the tier is not 0; both senders pass `anchor.tier`. Unity additionally refuses a floor from such feet.
**What the measurement overturned:** RTMW3D does NOT go quiet when the hips leave the picture; it reports them (and knees, wrists) at >= 0.3 outside the image, and the old path sent that plus a fixed 2.0 m depth.
**Carry forward:** `--depth-range` bounds only the height rule and the default; the 5% entry margin and the 0.8-torso ankle rule are there for measured failures (flapping; a 6 m depth) - do not remove them without re-running the tool; `host_multiperson.HostMultiPerson` defaults `partial_body=False` on purpose; not run on a physical webcam.

---

## 2026-10-12: AUTO ORIENT FOR A PHONE / WEBCAM / VIDEO (F-57)

`--auto-orient` picks the rotation by scoring the four rotations (detector x pose x "reads upright") on a few frames and re-does it when the frame size changes; `--hfov-axis long` makes one phone have one focal length in portrait and landscape. Off by default. Details and numbers: Unity repo `docs/decisions.md` ADR-093; module header of `tools/video/frame_orientation.py`.
**Carry forward:** the OAK-D senders do not use it (known mount, `--portrait`); do not retune the thresholds without re-running the 96-run decision test; not yet run on a live phone.

---

## 2026-10-10 (even later): THREE FIXES FROM THE FIRST LIVE TWO-PERSON RUN (F-52 follow-up)

`LoadTicker` keeps the `frames=0 sent=0 loading ...` heartbeat going during blocking model loads (the supervisor kills a child silent for 10 s; the FP32 model needs ~14 s);
`PrefetchCapture` decodes a big FILE on its own thread (`--prefetch`, automatic over 2.5 MP); Unity now passes the FP16 model. Details: Unity repo `docs/tasks.md` "F-52 FOLLOW-UP".
**Carry forward:** do not remove the ticker "because READY already printed" (READY comes before the model loads); a 4K file cannot play in real time here (decode ~24 fps).

---

## 2026-10-10 (latest): A PHONE / WEBCAM / VIDEO CAN DRIVE SEVERAL SKELETONS (F-52)

Report and ADR are in the **Unity repo**: `docs/tasks.md` F-52, `docs/decisions.md` **ADR-088**. What is in THIS repo:
`tools/video/host_person_detector.py` (YOLOX-s on the GPU), `tools/video/host_multiperson.py` (detector -> `PersonTracker` -> one
pose per person), `video_udp_sender.py --max-poses N`, `sidecar_supervisor.py --video-max-poses N`, `tools/model/fetch_yolox_s.py`,
`tests/test_host_multiperson.py` (73/73).

**Start here to try it:** `.venv/Scripts/python.exe tools/model/fetch_yolox_s.py`, then
`.venv/Scripts/python.exe tools/video/video_udp_sender.py --video ..\..\video\456.webm --max-poses 3 --port 18899`.
Throughput (decode excluded): 2 people 35.5 fps, 3 people 26.3, 5 people 17.5, 8 people 14.8.

**Carry forward:** (1) default `--max-poses 1` is the old single-person path and must stay byte-for-byte; (2) do NOT give this path
F-33's synthetic depth — it paints every box with one distance and flattens the model's own z; (3) `PersonTracker` and `build_*` are shared
with the OAK-D sender, change them in one place; (4) Unity asks for it through the supervisor's `--video-max-poses` (NOT `--max-poses`, which Unity always sends for the OAK sender and the video sender
must never inherit — `tests/test_video_source.py` pins this); (5) 3 people is 26 fps, under the camera's 30.

---

## 2026-10-10 (later): THE SUPERVISOR NOW REPORTS THE SENDER'S HEARTBEAT (F-51)

Unity's status lights (Unity repo: `docs/tasks.md` F-51, **ADR-087**) need to tell "alive" from "model running" from "a person found".
`sidecar_supervisor.py` therefore copies the sender's latest `frames= sent= ...` line into `supervisor_state.json` as `Child` (fields in
`README.md`). `tests/test_supervisor_state.py` pins it (32/32). **Carry forward:** (1) the field names are a contract with Unity — do not
rename them without changing `SidecarStatusModel`; (2) `SidecarReady` is NOT "model loaded"; (3) the parser must never raise: it runs
inside the watchdog.

---

## 2026-10-10: A PHONE CAN BE THE CAMERA, OVER WEBRTC (F-50)

Report and ADR are in the **Unity repo** as usual: `docs/tasks.md` F-50, `docs/decisions.md` **ADR-086**,
`docs/ai_handoff.md`. What changed in THIS repo:

```text
tools/video/lan_camera.py          NEW   frame source: joins a LAN Camera Streaming room as the viewer (aiortc + Socket.IO)
tools/video/video_udp_sender.py    +     source kind `lan`; fatal-error exit; frame-size-change re-derivation
tests/test_lan_camera.py           NEW   78/78 alone, 103/103 with CAMERA_SERVER_DIR (real Node server + model)
tests/_lan_fake_sender.py          NEW   test helper: a fake phone built on aiortc
requirements.txt / .lock.txt       +     aiortc, python-socketio[asyncio_client], aiohttp (+24 transitive pins)
README.md                          +     "A phone over WebRTC" section, test line
```

**Carry forward:** (1) it is a SOURCE inside the video sender, not a second program; (2) the PIN arrives ONLY as the
`CAMERA_PIN` environment variable — never accept it in `--video` or a flag; (3) a wrong PIN is fatal (exit 2), an occupied
room or an unreachable server is retried; (4) `RTCPeerConnection.close()` is bounded at 3 s because it was measured hanging;
(5) before installing anything into the venv, `pip freeze` first — this install was diffed and moved nothing.
**Unproven:** everything involving a real phone or network (see the Unity repo's F-50 NOT DONE list).
The LAN camera project's `docs/receiver-guide.md` documents the server's side of this contract for a receiver in any language.

---

## 0. 2026-09-17: MULTI-PERSON EXISTS NOW (F-32), AND IT RUNS THE FULL FILTER CHAIN (F-33)

Two features that change what this repo is. Reports and ADRs are in the **Unity repo** as usual:
`docs/F32_MULTIPERSON_2026-09-17.md` (ADR-070), `docs/F33_PER_PERSON_FILTERS_2026-09-17.md`
(ADR-071). Design summary: `architecture.md` → *Multi-person*.

```text
F-32   multi-person detection + identity tracking        BUILT, recorded video only
F-33   per-person P0 + P1-1 + F-22 filter chains         BUILT AND MEASURED, same caveat
```

**A correction came out of it, and it matters more than either feature.** F-29 treated `456.webm` as
one subject at 2.9 m and drew range conclusions from it. **The clip contains seven dancers.** The
tracked body's shoulder width ranges 0.068–0.455 m — a chimera assembled from whichever dancers fell
inside the migrating crop. Every "degradation with distance" figure from that clip measured IDENTITY
CONTAMINATION. A genuine single subject at 1.96 m gives 93.7% plausible hands; the real limit beyond
~2 m is **untested**. The single-person pipeline does not fail loudly on multi-person input — it
emits a plausible skeleton belonging to nobody, and those numbers reached a report.

**Three things to carry forward:**

1. `multiperson_udp_sender.py` is a SECOND program, not a mode. `wholebody_udp_sender.py` is
   byte-identical and stays the single-person production path.
2. A temporal filter belongs to an IDENTITY. The tracker re-sorts most-established-first every
   frame, so `PersonFilterPool` is keyed on the track id, never on list position.
3. `freq` is not cosmetic. One-Euro derives velocity as `delta * freq`, and this loop runs at ~16 fps
   with three people. Told 30, the filter is worse on jitter AND lag at once; at 10 fps it is worse
   than no filter at all on the median frame.

```text
implausible steps (>300 mm/frame)   2158 unfiltered -> 77 with F-33, over 2060 person-frames
filter cost                         0.88 ms per person-frame vs 20.7 ms for the pose solve
lag at 30 fps                       233 ms - NOT new, it is the single-person tuning. Always say it.
budget                              RTMW3D 20.7 ms, fixed batch of 1 -> 2 people 24 fps, 3 -> 16
tests                               270/270 across the suite (46 new)
```

**NOT TRUE:** never run with real people; the video A/B's stereo depth is synthetic; a slow confident
drift is still followed (847 mm of an injected 850 mm — P1-4's job, and P1-4 is rejected).

---

## 1. Current state (2026-09-15, end of day)

```text
F-19   portrait production integration + live VRM validation     DONE
F-20A  session ids, stale-pose watchdog, neutral failsafe        PASS
F-20B  sidecar supervisor / watchdog                             PASS
F-21   single-person target ownership                            CONDITIONAL - offline fix verified,
                                                                 INSTRUMENT NOW READY, live two-person
                                                                 session STILL NOT RUN
F-22   biomechanical pose validation                             CONDITIONAL - live L2 STILL PENDING
F-23   video-driven avatar validation (NEW)                      EVIDENCE ONLY - retarget proven at
                                                                 r=0.714; nothing depth-derived proven
```

**Nothing is committed.** All of this is uncommitted working-tree state in both repos.

## 1a. 2026-09-16: THE LIVE SESSION RAN. READ THIS FIRST.

The F-21 two-person session happened and **found a second silent wrong-person route**. With two real
people on the real camera, the sidecar emitted B under A's identity for ~97 datagrams inside one
epoch with ZERO events logged - the S27 defect, live (F-21 S34, ADR-061).

S30's gate is NOT at fault and did not regress: it guards REACQUISITION, and this never left LOCKED.
The cause is that `owner_pos` is updated to every accepted frame, so a migration slower than 0.35 m
PER FRAME walks the reference from one human to another without failing a single test. Every earlier
test used `123.webm`, where the observation TELEPORTS 303-687 px between bodies; the margin was
designed against jumps and validated against jumps. This was a few millimetres per frame.

```text
F-21   CONDITIONAL - and a HARDER no than before the session.
       S30's reacquisition fix STANDS (IMPOSTOR_WALKIN 0/2 silent, 2/2 declared).
       The LOCKED-branch drift route is OPEN, measured and imaged.
       Occlusion costs EITHER 4.19 s of blackout OR a silent wrong person, depending on
       which way the detector falls. Both were observed in the same 8-rep session.
DO NOT ship single-person ownership as a safety property on this evidence.
DO NOT quote "false holds = 0/4" as evidence occlusion is handled - it is true and blind to this.
```

Instrument work done the same day: three scorer defects fixed (F-21 S33.1, S33.2, S34.4) and an
anchor-hold guard added to both the protocol and the scorer. f21_walkin_score_test.py 22/22 -> 32/32.

## 1b. What happened on 2026-09-15 (read this before planning)

The day was SUPPOSED to be the F-21 live two-person session. **No second person was available, so
that session did not happen.** Two other things were done instead:

1. **The F-21 instrument was rebuilt and verified** - ten defects found and fixed before any human
   time was spent, several of which would have destroyed a session. See the F-21 report S32.
2. **A milestone review question was answered**: does the avatar do what a person in a video does?
   New F-23 tooling drives the REAL Unity avatar from an ordinary video file. See the F-23 report.

New ADRs: **058** (cue legibility is a visual-angle measurement, and the cue is drawn by the
producer in one window), **059** (pelvic roll re-enabled - it is image-plane derived and was
switched off as collateral of a bug already fixed separately), **060** (a measured NEGATIVE result:
slerp damping is NOT what separates the avatar from the debug skeleton).

## 2. What changed here on 2026-09-15 (F-21 §30/§31 hand-off-fix pass)

```text
target_ownership.py       PATH-CONSISTENCY GATE (ADR-057). ~20 lines. A reacquire is refused if the
                          candidate was tracked continuously from OUTSIDE switch_margin_m to inside
                          it (reason="path_walked_in"). New config: path_consistency (default True),
                          chain_gap_s (0.25 s). Off-switch kept for A/B, per AGENTS.md §10.
wholebody_udp_sender.py   `import io` — a ONE-LINE FIX for a real defect: _read_cue() calls io.open
                          in a module that never imported io, and its own `except Exception:
                          return None` swallowed the NameError every frame. --cue-file had NEVER
                          worked; the live-protocol cue banner has been dead since it was written.
sidecar_supervisor.py     forwards --show and --cue-file to the sidecar. Explicit flags, NOT a
                          generic --extra-args passthrough (that could also forward --no-ownership
                          and change what is under test). Default command line is byte-identical.
f21_live_protocol.py      REV 3: polls proc.poll() during every wait and between phases and ABORTS
                          with rc/phase/clock/last-ownership-state instead of scoring phases against
                          a dead producer. New --supervised mode. Two new phases,
                          WALK_IN_IMPOSTOR / WALK_IN_OWNER.
f2x_replay_soak.py        UNIT BUG FIXED: it fed ownership METRES while setting switch_margin_m from
                          a PIXEL diagonal (~141 metre gate), so no soak has ever exercised F-21's
                          discrimination - only its plumbing. Now uses the production 0.35 m / 0.45.
                          New --reset-per-clip.
test_target_ownership.py  46 -> 61 assertions (t19-t22 cover both directions of the gate + control)
f21_adversarial.py        63 -> 101 assertions (P1-P11 path-consistency scenarios added)
NEW: f21_ground_truth.py, f21_wrongperson_replay.py, f21_relabel.py, f21_perf_ab.py,
     validate_labels.py, f21_supervisor_integration.py, f21_live_protocol_abort_test.py
```


## 2b. New tooling on 2026-09-15 (evening)

```text
f21_cue_display.py       large cue PANEL: instruction at 60 px cap-height (20.5-25.7 arcmin at
                         2.5 m) + a ~300 px A/B glyph + a top-down diagram. The old banner was
                         13 px = 4.1-6.2 arcmin, i.e. AT the acuity limit. ADR-058.
f21_walkin_protocol.py   the S30 false-hold experiment: 4 matched cases, self-anchoring reps,
                         fixed A/B labels. Adds OWNER_OCCLUDED / OWNER_TURN, which are the REAL
                         false-hold cases and which no offline clip has ever exercised.
f21_walkin_score.py      + f21_walkin_score_test.py (22/22). Wilson 95 % intervals.
f24_wire_probe.py        records the production UDP wire and forwards it to Unity BYTE-IDENTICALLY
                         (verified 40/40), so live measurement and a driven avatar share one clock.
f24_trunk_jitter.py      trunk-yaw noise floor from the wire. Self-tested against known data.
f25_unity_play.py        toggles Unity Play via Ctrl+P, refusing unless the editor is genuinely
                         foreground. Unity MCP is NOT wired into the agent session; the bridge on
                         6400 greets "WELCOME UNITY-MCP 1 FRAMING=1" and then resets every framing
                         tried, so it was not pursued.
f23_video_to_unity.py    video -> RTMW3D -> the PRODUCTION landmark builder -> UDP.
f23_dance_demo.py        records Unity while the clip drives it.
f23_compose_sbs.py       side-by-side + a GAME-VIEW GUARD (0 % refuse / 100 % pass, verified) so a
                         composite can never again be built from the wrong window.
f23_followance.py        the objective follow metric: r and lag from the RENDERED PIXELS.
```

Production files touched, all opt-in and preview-only:
`wholebody_udp_sender.py` `--cue-panel` (default path byte-identical, lazy import);
`sidecar_supervisor.py` forwards it; `write_cue` race fix in BOTH protocols (it was killing runs).
`target_ownership.py` is UNTOUCHED. Unity: `Bootstrap.unity kalidokitBodyTorsoRoll 0 -> 1` only.

## 3. What was MEASURED

```text
123.webm wrong-person frames   ownership off 102 -> F-21 as shipped 40 -> F-21 + gate 2
                               The hand-off segment itself is 0. The residual 2 are a labeller
                               artefact verified by eye, identical in all arms. The hand-over is now
                               DECLARED (release at f678 on the 4.0 s budget, then TARGET_SWITCH).
                               Reproduced identically on the .venv interpreter.
cost of the gate               96 frames (1.60 s) of the LEGITIMATE returning owner withheld
video.webm / 456.webm          bit-identical with the gate on and off. 0 path rejections in either.
gate separation                fastest real single-frame body motion 126.1 px vs a 141 px margin vs
                               real body-to-body jumps of 303.5 / 323.0 / 687.5 px
performance                    +0.204 us/frame median = +0.00061 % of a 30 fps budget; 3 scalars of
                               state, no per-frame allocation (AGENTS.md §4)
tests                          unit 61/61, all Python suites 191/191, adversarial 101/101,
                               f22 120/120, abort test 9/9, supervisor integration 11/11,
                               F-20B deployment 35/35, Unity EditMode 170/170
soak (stability, 3 clips)      17.0 min / 31,851 frames / 0 exceptions / memory ends below warm-up
soak (the gate, 123.webm)      8.0 min / 15,662 frames / 600 path_walked_in / 20 declared switches /
                               0 exceptions / 749 of 749 validator recoveries
```

## 4. What is **NOT TESTED** — read this before claiming anything

```text
NOT TESTED  the gate's FALSE-HOLD RATE with real humans. A returning owner and an intruder walking
            in are the SAME observation stream from a hip position; the gate refuses BOTH by design
            and resolves the ambiguity towards a DECLARED hand-over. How often that costs a real
            installation something is UNKNOWN. This is the single most important open number.
NOT TESTED  any of it against real stereo depth. The replays lift to metric camera space but
            RTMW3D's zrel is relative TO the hip, so the hip sits on a fixed 2.0 m plane and the
            gate was exercised LATERALLY ONLY. Depth makes the gate stricter: the wrong-person
            figures are a pessimistic bound, the availability cost an optimistic one.
NOT TESTED  live two-person behaviour of any kind. Two attempts, neither produced usable evidence
            (a UX defect, then a camera crash). 123.webm is ONE clip with ONE hand-off - a
            real-footage existence proof, not an incidence rate.
NOT TESTED  F-22 L2 hands-near-face against a real subject.
NOT TESTED  the cue banner in a real session. It is proven to parse and render (imaged), but it has
            never been read by an operator at the camera, which is the thing it exists for.
NOT FIXED   the bare `except Exception: return None` in _read_cue() that hid the NameError for this
            long. Correct in intent (a partial read of an atomically-replaced file must not crash
            the frame loop) but indiscriminate. Left alone deliberately: it is the live-critical
            path and the session is tomorrow. AGENTS.md §3 disallows silent excepts - this one
            predates that rule's enforcement and is flagged rather than changed mid-flight.
```

## 4b. What was MEASURED on 2026-09-15 (evening)

```text
cue legibility        13 px cap-height = 4.1-6.2 arcmin at 2-3 m (the OLD banner, at the acuity
                      limit) -> 60 px = 20.5-25.7 arcmin at 2.5 m
cue panel cost        naive 4.29 ms/frame (+11 % of a 30 fps budget) -> cached 0.38 ms median,
                      p95 0.48, max 3.93 - cheaper than the banner it replaces
recorder coverage     libx264 captured 70.2 s of an 86 s session -> h264_nvenc 86.7/86.9 s at 0.999x
avatar follows video  peak Pearson r = +0.714, trailing 300 ms, 435/435 frames, parseErr=0
zrel smoothing        hip-line dZ std 0.0282 -> 0.0147 m; hip yaw std 7.4 -> 3.9 deg;
                      frame-to-frame yaw p95 8.2 -> 1.6 deg
pelvic channel        obliquity 25.1 deg p2p DISCARDED (torsoRoll=0, now fixed); yaw deadzone
                      zeroes 76.6 % of frames; only 33.9 % of pelvic rotation reaches the avatar
slerp A/B             lerp 0.5 r=+0.714 @300 ms vs lerp 1.0 r=+0.691 @267 ms. Removing damping
                      ENTIRELY buys 33 ms and is slightly WORSE. NOT the lever. ADR-060.
```

## 4c. NOT TESTED / INVALID — added 2026-09-15 evening

```text
NOT TESTED  the F-21 live two-person session. UPDATE 2026-09-16: the first attempt WAS RUN with
            two people and produced NO USABLE NUMBER - the capture is discarded. hip_z swung 1.55 m
            (p10-p90) while the subject stood still and 1.7 % of consecutive frames exceeded the
            0.35 m margin, so ownership churned through 6 epochs in 71 s and no rep survived its
            anchor. ZERO path_walked_in firings: the gate was never reached, so this says nothing
            about it in either direction. Still the single most important open item, still needs
            TWO people. See F-21 S33 - and read S33.3 before setting the room up again.
            TWO INSTRUMENT DEFECTS were found and fixed by that attempt: the scorer called a
            DECLARED hand-over SILENT_WRONG_PERSON (a false alarm on the most serious verdict it
            can return), and a rep whose anchor collapsed two seconds after locking was still
            scored. f21_walkin_score_test.py 22/22 -> 32/32.
NOT TESTED  F-22 L2 hands-near-face live.
INVALID     the trunk-yaw noise floor. A session was run and recorded 2565 frames, but the subject
            was NOT in front of the camera - the tracker was locked onto something else in the room.
            The numbers it produced (hip yaw std 15.93 deg while "still") are DISCARDED. Needs ~30 s
            of a person standing still at 0.90 m. It is the last thing gating the deadzone decision.
NOT VALIDATED  Bootstrap.unity torsoRoll 0 -> 1. UPDATE 2026-09-16: the suite HAS now been re-run
            with the change applied - 170/170, 0 failed, via the reflection runner with the editor
            OPEN (mcp__unityMCP__execute_code; the suite does NOT need the editor closed, which is
            what blocked it yesterday). But that result does NOT validate ADR-059 and must not be
            quoted as if it did: NO TEST ANYWHERE REFERENCES torsoRoll. It is a [SerializeField] read
            once at AppBootstrap.cs:386 and passed to SetTuning, so the EditMode suite is entirely
            INSENSITIVE to the change. 170/170 means NO REGRESSION in the tested paths - nothing
            more. Validating ADR-059 needs eyes on the avatar's pelvic roll at runtime.
            Backup of the scene file is at /tmp/Bootstrap.unity.bak.
LIVE FINDING, unfixed: the tracker was observed LOCKED ONTO AN EMPTY OFFICE CHAIR at conf=0.38
            against a 0.30 floor, with a person in frame. An F-21 anchor that lands on furniture
            marks the rep VALID and voids it. CLEAR CHAIRS FROM THE CAPTURE VOLUME.
```

## 5. Next recommended task

Run the live two-person session, under supervision, with the on-screen cue banner:

```bash
.venv\Scripts\python f21_live_protocol.py --supervised
```

Its job is **validation, not development**. Priority order for what it must price:

1. `WALK_IN_OWNER` vs `WALK_IN_IMPOSTOR` — the false-hold rate (§4, item 1). `RELEASE_TIMEOUT_S`
   (4.0 s) is the knob that trades blackout against epoch churn; it was deliberately **not** tuned
   against one clip.
2. The rest of the two-person matrix against real depth.
3. F-22 L2 hands-near-face.

**Do not** upgrade F-21 or F-22 to PASS before that session. **Do not** tune `SWITCH_MARGIN_M`,
`RELEASE_TIMEOUT_S` or any F-22 threshold offline — they now have measured offline baselines and the
next change to them needs live evidence and an ADR (AGENTS.md §10).

## 6. The trap that cost the most time on 2026-09-15

Run every harness with **`.venv\Scripts\python.exe`**, never the bare `python` on PATH. They are
different environments with different `onnxruntime` builds:

```text
.venv\Scripts\python.exe    onnxruntime 1.23.0   ['DmlExecutionProvider', 'CPUExecutionProvider']
C:\Program Files\Python310  onnxruntime 1.23.2   ['Tensorrt...', 'CUDA...', 'CPUExecutionProvider']
```

`rtmw3d_pose.RTMW3D` asks for DirectML and onnxruntime **falls back silently**. Under the system
Python there is no working GPU path at all (its CUDA EP cannot load — `cublasLt64_12.dll` missing),
so inference runs at **183 ms/frame ≈ 5.4 fps instead of ~32 fps**. Correctness is unaffected —
ownership decisions are deterministic and a full replay reproduced 102/40/2 identically under both —
but every throughput, fps and soak-sizing number is wrong. Check
`ort.get_available_providers()` before trusting any timing figure.

---

# 2026-09-16 — the avatar has a POSE FIDELITY metric, and it has been run

## 7. What changed

Read `docs/F26_AVATAR_VS_DEBUG_SKELETON_2026-09-16.md` (§11) and **ADR-062** before touching the
retarget. Short version: the project had no measurement of *avatar pose vs subject pose* — every
avatar number was the avatar against itself, and all of them are satisfied by an avatar that is
stably in the wrong pose. That measurement now exists, has been validated against live data, and has
a before-number.

### Files added / modified

| file | what |
|---|---|
| `Runtime/Core/Models/BoneFidelity.cs` | NEW. One bone's fidelity. In **Core** because Retargeting produces it and Diagnostics consumes it — the only place both see it without a cycle |
| `Runtime/Retargeting/KalidokitControlRigDriver.cs` | `SampleFidelity()` + `FidelityBoneCount`; 9 bones, read-only, allocation-free, caller-owned buffer |
| `Runtime/Diagnostics/AvatarFidelityRecorder.cs` | NEW. One jsonl record per RENDERED frame, sampled on `Application.onBeforeRender`, self-terminating |
| `Runtime/Diagnostics/VirtualMirror.Diagnostics.asmdef` | + `VirtualMirror.Retargeting` (one-way) |
| `python-sidecar~/f26_fidelity_analyze.py` | NEW. Per-bone distribution + follow ratio. **22/22 self-test**. Refuses to emit a combined score |
| `docs/decisions.md` | + ADR-062 |
| `docs/F26_...md` | + §11; §9.1 marked done |
| `docs/tasks.md` | + the 2026-09-16 block |

## 8. Running it

The metric needs Unity in Play mode with a VRM bound, and a pose source. The avatar auto-loads the
last VRM (`TryAutoLoadLastAvatar`), so a fresh Play cycle restores a measurable session on its own.

```text
.venv\Scripts\python.exe f23_video_to_unity.py --video ..\..\video\video.webm --loop 4
  ... attach AvatarFidelityRecorder to AppBootstrap, Begin(rig, path, seconds) ...
.venv\Scripts\python.exe f26_fidelity_analyze.py --in ..\docs\evidence\f26\fidelityA_video.jsonl
```

**A script recompile resets the C# services even though Play mode survives the domain reload** —
`kalidokitControlRig` and `bodyProvider` come back NULL while the scene objects stay. Stop and
re-enter Play mode after any recompile, or the recorder silently has nothing to measure.

## 9. What it found, and what it does NOT license

Arms follow the subject to **0.4–1.1° median**. Legs are **OVER-DRIVEN 1.4–2.3×**. The trunk's median
error is **16.7°**, follow ratio **0.27**, and the avatar's trunk sat **0.00–1.20°** from its own rest
vector across four snapshots with a lateral component of **identically 0.000**. The lean channel does
not move. Both causes are located in code (ADR-062): `torsoRoll = 0` kills lateral lean outright, and
an 8 s adaptive-baseline high-pass removes any *sustained* sagittal lean — which is exactly what
sitting down is.

**Do not**:
- do not quote a single "fidelity score" — the analyser refuses to produce one, deliberately;
- do not read the 16.7° as a YAW result. Axial twist leaves the hip→shoulder line unchanged, so this
  metric is structurally blind to it and says nothing about the F-16/F-17/F-18 blocker;
- do not treat the arms' ~1° as proof the retarget is right. `kalidokitAimArms` aims the bone along
  the same landmark pair the metric compares against — for an aimed channel that is near a tautology.
  It is also why the coordinate mapping had to be validated against `PoseDebugSkeleton` instead;
- do not quote Run B (456.webm, subject ~3 m) as a retarget result — 80–90 % of it is below the
  confidence floor;
- do not treat either run as a sensor result. The video path has **no stereo** (F-23); every joint's
  depth is synthesised. This exercises the retarget only;
- do not change `kalidokitBodyTorsoRoll` or `spineBendBaselineTau` as a "quick fix". Both are
  ADR-level decisions and the cost of changing them is as unmeasured as the cost of leaving them.

## 10. Next recommended task

**Choose a retarget direction** (F-26 §8 / §11.8) — and decide whether SEATED use is in scope, because
that single answer decides whether the trunk high-pass is the top defect or not a defect at all.
Nothing should change in the retarget before that choice; the measurement exists to make the change
provable, and spending it on an unvalidated fix wastes it.

Cheapest things that unblock a real claim, in order: run the metric on an **OAK-D stereo session**;
then build the **ENDPOINT** metric, without which option D (bone-length normalisation) cannot be
judged at all.

Still true from 2026-09-15: run every harness with `.venv\Scripts\python.exe` (§6), and **nothing in
either repository is committed**.

---

# 2026-09-16 (later) — the humanized skeleton, and the skeleton as the product

## 11. F-27 — tracking output is now a SUGGESTION

Read `docs/F27_HUMANIZED_SKELETON_2026-09-16.md` and **ADR-063** before touching the retarget or this
layer. A new stage sits between the existing filtering and the existing retarget:

```text
tracking -> existing filtering -> [ HumanizedSkeleton ] -> existing retarget -> avatar
```

| file | what |
|---|---|
| `Runtime/Core/Humanize/HumanBodyModel.cs` | the ANATOMY: bone table, speed ceilings, joint limits. Every number is a claim about human bodies and is sourced in its comment |
| `Runtime/Core/Humanize/HumanizedSkeleton.cs` | the layer. Pure logic, allocation-free, no Unity object model |
| `Runtime/Core/Humanize/BoneLengthCalibrator.cs` | sliding-window median per bone, left/right averaged |
| `Runtime/Core/Humanize/HumanizedStats.cs` | what it DID — the counters are how "it added no latency" is checked rather than asserted |
| `Runtime/Diagnostics/SyntheticPose.cs` | a known-good body, plus the faults a tracker really emits |
| `Runtime/Diagnostics/PoseMetrics.cs` | measurements taken ON a pose, whatever produced it |
| `Runtime/Diagnostics/HumanizedSkeletonSelfTest.cs` | **32/32**, headless |
| `Runtime/Diagnostics/HumanizedSkeletonRecorder.cs` | BEFORE and AFTER from the same frame, one pass |
| `python-sidecar~/f27_humanized_analyze.py` | the before/after report. **18/18** self-test |

Run the test mode from `execute_code` (it needs no Editor, no Play mode, no avatar):

```csharp
VirtualMirror.Diagnostics.HumanizedSkeletonSelfTest.Run()
```

## 12. What F-27 does and does NOT license

Measured: bone-length deviation **−68 % median, −79 % worst**; **99.6 % of poses touched by no
stateful stage**. The p99 landmark jump is **+19 % worse** and that is a real trade, not noise.

**Do not**:
- do not claim this improves the AVATAR. It measures the POSE. Running F-26 §11's fidelity metric
  with the layer on and off is the experiment that would connect them, and it has not been done;
- do not treat the video-path result as a sensor result. No stereo on that path, and the rejection,
  hold and recovery stages had NOTHING to do on that clip — 0 holds, 0 NaN, 0 collapses in either
  stream. They are proven by fault injection only;
- do not re-add a shoulder cone. One was implemented, measured firing on exactly one pose in a full
  arm sweep — arms straight overhead, F-26's worst avatar failure — and removed. The self-test
  carries that case as a permanent regression guard;
- do not tighten the speed ceilings. They are outlier rejectors set ABOVE what a person in front of a
  mirror produces; a ceiling a real arm can reach is a defect, not a safety margin;
- do not let this layer fabricate confidence. P0-1 must keep seeing an unobserved limb as unobserved.

## 13. F-28 — the skeleton as the product

`Scenes/SkeletonShow.unity`, its own bootstrap and its own assembly. Three modes, switched with
`1` / `2` / `3` / `TAB` or the on-screen buttons; `H` toggles the F-27 layer live. Same production
tracking (`OakDUdpPoseProvider`, `PoseSpaceConverter`, P1-3) with the **retarget skipped entirely** —
which is the point: F-26 located every avatar failure in the retarget, and mode 2 builds the visual
from joint positions, so the body cannot be in a pose the tracker did not report.

Run it exactly like the mirror: start the sidecar, press Play in that scene. 114–118 fps in the
editor. Driven from video only so far; never seen with a person in front of an OAK-D.

## 14. Next recommended task

Run F-26's fidelity metric with the humanized layer ON and OFF. It is cheap, both instruments already
exist, and it is the only measurement that says whether F-27 helped the thing the user actually looks
at. After that: F-27 on a real OAK-D session, where the fault-handling stages will finally have
something to do.

Still true: run every harness with `.venv\Scripts\python.exe` (§6), and **nothing in either
repository is committed**.

---

## 15. The avatar A/B was run (F-27 §8) — and the answer is not the flattering one

`f26_fidelity_analyze.py --before <OFF> --after <ON>` with 4000+ live frames per arm. Before quoting
any of it, know what the reference is: **the RAW tracked pose, in every arm**
(`KalidokitControlRigDriver.SetFidelityReference`, added for exactly this). Without it the humanized
arm would be scored against its own input and would win by construction.

```text
ON vs OFF                0 bones better, 3 worse, 6 unchanged   (arms +1.0 to +1.3 deg)
ON-no-bone-len vs OFF    0 better, 0 worse, 9 unchanged
```

* The velocity clamp, the knee fix and the joint limits cost the avatar **nothing**.
* The whole ~1 deg arm cost is **bone-length normalisation**, isolated by measurement.
* That 1 deg **is** the correction — ARM V2 aims the bone at its input (follow 1.00 everywhere), so
  against a raw reference the error equals how far the layer moved the arm. This metric cannot say
  whether the move was an improvement, because that needs a better reference than the raw tracker.
* **F-27 does not fix the leg over-drive** (follow 1.26-2.06 in all three arms) — that is the
  Kalidokit leg solve, not the input.

**Do not** claim F-27 improves the avatar. **Do not** switch it off on this evidence either: it buys
the protections at no measured cost, and the clip contains none of the faults it exists for (0 holds,
0 NaN, 0 collapses in either stream).

**A correction landed with it.** F-26 §11.5's "the trunk lean channel is dead" was measured against a
RUNTIME `kalidokitBodyTorsoRoll = 0`; the scene ships **1**. Same clip, freshly opened scene: trunk
median **16.7 -> 7.3 deg**, follow **0.27 -> 1.29**. F-26 §11.5 is corrected in place. Always check
the live field values against the SERIALIZED ones before quoting a measurement from a long-running
Play session.

**Next:** run this same A/B on footage that actually contains dropouts and occlusion, or on a live
OAK-D session. Every protective stage in F-27 was idle on this clip, so its value is still unmeasured.
