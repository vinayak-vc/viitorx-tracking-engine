# Changelog

All notable changes to the Virtual Mirror Python sidecar are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This component versions alongside the
[Unity SDK](https://github.com/vinayak-vc/viitorx-vrm-avtar-unity). The **UDP wire contract** is the
public interface and is what semver applies to here.

## [Unreleased]

### Added

- **Cutout: the person in a camera picture, isolated (S-01).** A third sender for the Unity cutout. `tools/cutout/cutout_udp_sender.py`
  finds each person (the existing YOLOX-s detector and F-32 tracker), cuts a square crop per person with the other people's boxes painted
  out of it, segments it with MediaPipe's selfie network (ONNX on the existing `onnxruntime-directml`: **no new pip package**;
  `tools/model/fetch_cutout_models.py` fetches exactly two 0.46 MB files by size and SHA-256), mattes the answer (guided filter, temporal
  smoothing, edge ramp, speck and pinhole cleanup) and sends the picture and the mask together.
  - `cutout_segmenter.py`, `cutout_matte.py`, `cutout_engine.py`, `cutout_wire.py`, `cutout_overlay.py` at the root;
    `tools/cutout/{cutout_udp_sender,cutout_udp_receiver,cutout_eval}.py`; `scripts/run_cutout_demo.bat`.
  - **A NEW wire message** on UDP **8903**: binary (a picture does not survive being text), a frame cut into datagrams that each carry
    the whole 72-byte header, colour raw RGBA / JPEG / none, mask raw / deflate. The body (8899), hands (8901) and face (8902) wires are
    unchanged. Documented byte by byte in `cutout_wire.py`; pinned by 26 golden datagrams (`tools/video/make_vision_fixtures.py`) the Unity
    tests read.
  - `tools/video/vision_sender.py` may now be given a LIST of datagrams per frame (the hands and face senders return one: unchanged).
  - Measured here: 17 ms an engine frame for one person, 20-22 ms for two; the isolation of people halves the foreground outside their boxes
    on the two-dancer clip and raises one-frame flicker from 1.75 % to 2.01 %. A webcam run of 11 s (numbers only): 29.3 fps, 0 datagrams
    lost; it saw nobody. **A person on a live webcam is NOT tested.** A small selfie network: dark clothes on a dark background, floor
    lights and people who touch are wrong in ways no flag fixes.
  - 265 new assertions (`tests/test_cutout_{wire,matte,engine}.py` 201, the cutout part of `test_vision_fixtures.py` 48 and of
    `test_vision_cli_contract.py` 16); 16 of 16 injected bugs caught.
- **The Unity host's command lines are tested against the senders (H-04).** `tests/test_vision_cli_contract.py` parses the command-line
  fixtures the Unity repo asserts with the hands and face senders' own argument parsers. No sender change.

- **Fixtures for the Unity receivers (H-03).** `tools/video/make_vision_fixtures.py` writes golden hands and face datagrams, made by the
  production encoders, into the Unity repo; `tests/test_vision_fixtures.py` fails if they drift. No wire change.

- **Face only, no body (H-02).** A sibling of the hands pipeline for a camera that sees a face and no person. MediaPipe's face
  detector, 478-point face mesh and 52-score blendshape network, as community ONNX conversions of the TFLite models in
  `face_landmarker.task`, run on the existing `onnxruntime-directml` (**no new pip package**; `tools/model/fetch_face_models.py`
  fetches them by size and SHA-256).
  - `face_landmarker.py`, `face_tracking.py`, `face_geometry.py`, `face_wire.py`, `face_overlay.py` at the root;
    `tools/face/face_udp_sender.py` (webcam, LAN camera, stream, file; `--show` is the demo), `face_udp_receiver.py`,
    `face_eval.py`; `scripts/run_face_demo.bat`.
  - **A NEW wire message** `{ver:1, kind:"face"}` on UDP **8902**. The body (8899) and hands (8901) datagrams are unchanged. Per face:
    a stable id, 52 blendshape scores by `BLENDSHAPE_NAMES` (index 0 `_neutral`, 1-51 ARKit-compatible, **no tongueOut**), 478
    landmarks, head pose, an estimated 3D position (absent when refused). Documented in `face_wire.py`.
  - Shared code extracted from the hands: `roi_geometry.py` (crop maths) and `landmark_filters.py` (a vectorised One-Euro bank,
    identical arithmetic to `smoothing.OneEuro`, tested to 1e-12); `tools/video/vision_sender.py` is the frame loop both senders use.
  - Head pose and position are **estimates**; the ONNX files are a third party's conversion whose parity with Google's was not
    reproduced. Not run on a live camera.
  - 183 new assertions (`tests/test_face_{landmarker,geometry,tracking,wire}.py`, `test_landmark_filters.py`); the hands' 207 pass
    unchanged; 42 of 42 injected bugs caught across both pipelines.
- **Fixed:** playing a FILE paid DirectML's 250 ms first-frame warm-up as a frame deficit and dropped frames to repay it, so the picture
  jumped for a second. The pacer now restarts after the first frame.

- **Hands only, no body (H-01).** A separate pipeline for a camera that sees a hand and no person. MediaPipe's palm
  detector and hand-landmark network, as OpenCV Zoo ONNX files, run on the existing `onnxruntime-directml` (**no new pip
  package**; `tools/model/fetch_hand_models.py` fetches them by size and SHA-256).
  - `hand_landmarker.py`, `hand_tracking.py`, `hand_geometry.py`, `hand_gestures.py`, `hand_wire.py`, `hand_overlay.py` at
    the root; `tools/hands/hands_udp_sender.py` (webcam, LAN camera, stream, file; `--show` is the demo),
    `hands_udp_receiver.py`, `hands_eval.py`; `scripts/run_hands_demo.bat`.
  - **A NEW wire message** `{ver:1, kind:"hands"}` on UDP **8901**. The body datagram on 8899 is unchanged. Per hand: a stable
    id, side, 21 image landmarks, 21 metric world landmarks, an estimated 3D position (absent when the fit is refused),
    finger curls, pinch and a gesture. Documented in `hand_wire.py`.
  - The 3D position is an **estimate** from the hand's apparent size and an assumed field of view (`--hfov`); it is not
    measured against a ruler. Not run on a live camera.
  - 207 assertions in `tests/test_hand_{landmarker,geometry,gestures,tracking,wire}.py`; 25 of 25 injected bugs caught.

- **Per-person filter chains (F-33).** `person_filters.py` gives every tracked person their own
  complete P0 + P1-1 + F-22 chain, pooled on their **track id** (ADR-071). F-32 shipped
  multi-person with all of it switched off and said so; this closes that.
  - Keyed on the id, never on list position: the tracker re-sorts most-established-first every
    frame, so an index-keyed bank hands one person's One-Euro history to another.
  - Each person MEASURES their own sample rate, because this loop does not run at camera rate.
    One-Euro derives velocity as `delta * freq`; told 30 while sampled at 16 it over-estimates
    speed by 1.9x and opens up when it should damp. At 10 fps the naive port is worse than no
    filter at all on the median frame (35.6 mm vs 33.5) while costing 400 ms of lag.
  - `smoothing.py` gained `set_freq()`. Purely ADDITIVE - the diff has zero deleted lines and the
    single-person sender never calls it.
  - The FEET (WholeBody 17-22) and HEAD (0-4) joined the filter group, which the single-person
    sender never put them in. Implausible single-frame steps (>300 mm in 33 ms) 2158 -> 77.
  - `tools/diagnostics/f33_filter_bench.py` measures against KNOWN ground truth, so lag is
    reported next to jitter - without that, "the output moved less" cannot be told from
    over-smoothing.
  - 46 unit tests, including a guard that reads `wholebody_udp_sender.py`'s source and fails if a
    shared default drifts apart from `FilterConfig`.
  - `wholebody_udp_sender.py` remains **byte-identical**.

- **Multi-person sender (F-32).** `multiperson_udp_sender.py` detects every person in frame on the
  OAK-D's VPU, assigns each a stable id, poses the most-established ones on the host GPU and streams
  them all in one datagram. The production `wholebody_udp_sender.py` is **byte-identical** - this is
  a separate file precisely so the measured single-person stack (P0, P1-1, P1-4, F-21, F-22, F-08)
  is not put at risk. It imports that sender's building blocks, so there is one definition of the
  landmark contract.
  - `assignment.py` - pure-numpy optimal assignment. scipy is not in the venv and is not worth
    ~30 MB for one function. Greedy is suboptimal on 54.6% of random 4x4 cost matrices and its
    failure mode is exactly an ID swap between crossing people.
  - `person_tracker.py` - associates in 3-D using metric depth, which no IoU-based tracker can:
    two people overlapping on screen at different distances are trivially separable in Z.
    19 unit tests including the crossing case.
  - `tools/video/f32_multiperson_video.py` - drives the multi-person wire from recorded video.
  - **Backward compatible**: the most-established person is republished at the payload root in the
    single-person shape, so every existing consumer works unchanged.
  - **Measured**: detector free on the VPU (rgb 29.8 -> 29.7 fps); RTMW3D 20.7 ms per person with a
    fixed batch of 1, so `--max-poses` defaults to 3.

- **Trust channel on the wire (F-29).** Three new OPTIONAL fields, so this is a backward-compatible
  addition exactly as `sid` was — a consumer that ignores them is unaffected, and nothing in the
  sidecar reads them back:
  - `st` — P1-1/P1-4 tracking state per JointId slot, as 33 ints (`-1` no tracker, `0` TRACKED,
    `1` WEAK, `2` PREDICTED, `3` LOST, `4` RECOVERING). Values are `joint_tracker.TrackingState`'s
    own ints rather than a parallel enum that could drift out of step. Captured **after** P1-4, so
    the reported state matches the geometry actually emitted. `-1` is a claim, not padding: only
    the 12 joints in `DEFAULT_TRACKED` have a tracker, and reporting the rest as healthy would
    overstate what the system knows.
  - `own` — the F-21 ownership state. **Omitted entirely** under `--no-ownership`, so a consumer
    can distinguish "identity tracking is not running" from "running but not locked".
  - `lat` — measured camera-timestamp to payload-built latency, in ms.
- `build_joint_states()`, shared by the sender and the video harness.
- **`tools/video/f23_video_to_unity.py` now runs the real `SkeletonTracker` and `TargetOwnership`,**
  so the video path reports genuine states instead of none. Two deliberate limits, documented in
  the file: geometry is **not** written back (which keeps this harness's emitted landmarks
  byte-identical to before, so earlier measurements stay valid, and keeps every `src` flag honestly
  0), and `depth_valid=True` is passed because that input feeds only P1-1's suspicion score —
  passing False for all 133 keypoints would drive every joint permanently WEAK.

### Notes

- **Feet were already on the wire and still are.** `build_body_landmarks` has always emitted
  `FOOT_TO_JOINTID` — heels to JointId 29/30, big toes to 31/32 — inside `lm`. Measured 431/431 and
  330/330 frames on the two regression clips. No change was needed; the consumer simply never drew
  those four slots.

### Fixed

- **`tools/video/f32_multiperson_video.py` drove `PersonTracker` from the WALL CLOCK.** A file
  replay that reads the wall clock is not reproducible - the tracker's constant-velocity prediction
  is scaled by `dt`, so the same clip yields different identities on a faster machine, or simply on
  a second run. It surfaced as an A/B whose two halves disagreed about which ids existed. Both the
  tracker and the filters now run on the video's own timeline. The live sender is unaffected: there
  the wall clock IS the frame clock.

### Known

- Lag is 233 ms at 30 fps. Not introduced by F-33 - it is the accepted single-person tuning - but it
  must be quoted alongside any jitter figure.
- A slow, confident drift is still followed (847 mm of an injected 850 mm). P1-4 catches it and is
  rejected for production; both senders share the blind spot deliberately.
- P1-1's horizons are counted in FRAMES and this loop is slower than the single-person one, so a
  6-frame prediction spans ~375 ms at 16 fps. Only the P0 smoother adapts to the real rate.

## [0.1.0] — 2026-09-16

### Added

- `setup_sidecar.ps1` — one-time machine setup. Creates the virtualenv, installs pinned
  dependencies, and verifies that `DmlExecutionProvider` actually loaded. That last check is the
  point: onnxruntime falls back to CPU **without raising**, turning ~31 ms/frame into ~183 ms, so
  the failure is detected at install time instead of surfacing later as unexplained slowness.
- `requirements.lock.txt` — exact `pip freeze` of the working environment.
- `evidence_paths` — the single source of truth for capture locations.
  `VIRTUAL_MIRROR_EVIDENCE_DIR` redirects the whole tree.
- `_sidecar_path` — import shim letting harnesses under `tools/` and `tests/` resolve project
  modules.
- `pyproject.toml`, `LICENSE` (MIT), `.gitattributes` and this changelog.

### Changed

- **Repository layout.** The root now holds only the production path: `sidecar_supervisor.py`,
  `wholebody_udp_sender.py` and the nine modules the sender imports. The 72 investigation harnesses
  moved into `tools/` (grouped by dependency cluster: `capture`, `ownership`, `validation`, `video`,
  `diagnostics`, `deployment`, `armaim`) and `tests/`. Root went from 96 files to 18.

  This is enforced, not merely tidy: the Unity build copies the sidecar root verbatim, so anything
  left there ships to every player.
- **Evidence.** `oak_v4_evidence/`, `arm_v1_evidence/`, `arm_v2_evidence/` and `arm_v3_evidence/`
  are now `evidence/oak_v4`, `evidence/arm_v1`, `evidence/arm_v2` and `evidence/arm_v3`.
- Launcher scripts moved to `scripts/` and re-based with `cd /d "%~dp0.."` so they still resolve the
  repository root from one level down.
- `README.md` — layout, Tests and device-free sections rewritten; they had described roughly a dozen
  scripts deleted in `29ec57e` and printed commands that no longer ran.

### Fixed

- **Seven modules hardcoded an absolute `C:\Unity\...` path** to a checkout present on no machine —
  this project lives on `D:` — making them dead on arrival for every user including their author.
- **Thirteen harnesses built evidence paths from a per-file `HERE`.** After the reorganisation those
  resolved to `tools/<group>/oak_v4_evidence/`, which does not exist. Silent, and invisible to the
  self-tests because the affected harnesses need hardware.
- `requirements.txt` had `onnxruntime-directml` **commented out** while the working environment had
  it installed. A setup performed from that file produced a sidecar with no inference at all.
- Docstring usage examples pointed at `"D:\...\video\123.webm"`, a placeholder nobody could paste
  and run; they now use the real relative path and the post-reorganisation script locations.
- `f16_consolidate` and `f16_edge_check` carried byte-identical 13-line `load()` functions; the
  shared implementation is now `f16_configs.load_rows()`.

### Notes

- Python **3.10 exactly**. `depthai` publishes `cp310` wheels only, and on 3.11+ pip cannot resolve
  it — with an error that never mentions the Python version.
- `depthai_blazepose/` is vendored MIT source from
  [geaxgx/depthai_blazepose](https://github.com/geaxgx/depthai_blazepose). It is the superseded
  Phase-1 path and is not imported by the production pipeline. Its `LICENSE.txt` must stay with it.

[Unreleased]: https://github.com/vinayak-vc/viitorx-vrm-model-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vinayak-vc/viitorx-vrm-model-python/releases/tag/v0.1.0
