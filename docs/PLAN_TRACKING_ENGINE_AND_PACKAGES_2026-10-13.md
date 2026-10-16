# PLAN - Viitorx Tracking Engine and Unity packages (2026-10-13)

**Status: PLAN ONLY. Nothing in this document has been implemented, and implementation is ON HOLD** until the user finishes one more large feature; the user will then ask for that feature to be studied and added to this plan. All packaging work happens on a branch named **`package-v1`** (created then, in both repos; not before).

**Nothing in this document has been implemented.** Section 9 amends sections 1, 5 and 6 for the cutout work (S-01, HE-01); where they disagree, section 9 wins. Agreed with the user in conversation on 2026-10-13; every "decided" line below is the user's answer. Open items are listed at the end. A copy of this plan lives in both repos (`docs/PLAN_TRACKING_ENGINE_AND_PACKAGES_2026-10-13.md`).

Goal: use the hands, face and body tracking from any other project as a plug-in, with the Python side shipped as a compiled, portable program that nobody has to set up, and the Unity side as UPM packages. Unreal and other engines come later; for them the **wire** is the product.

## 1. Decisions (the user's, 2026-10-13)

| Topic | Decision |
|---|---|
| Engine | **One engine, one folder, one exe** `viitorx-tracking.exe`, subcommands `hands`, `face`, `body`, `list-cameras`, `fetch-models`, `verify`, `version`, `print-spec`. Product name **Viitorx Tracking Engine**. (The user left the one-exe-or-two question to the agent; this is the agent's call.) |
| Sources | Webcam, LAN camera (viewer link), URL / stream, video file. **No OAK-D in the compiled engine.** The Python project keeps OAK-D as an optional extra for people who run Python directly. |
| Body depth | **Estimated**, not stereo-measured (accepted). |
| Compiled | A normal software folder: exe + dlls + libs + data, **no `.py` files**. Build tool: Nuitka standalone (PyInstaller one-folder is the fallback only: it ships bytecode). Single giant file is NOT required. |
| Models | In `models/` **next to the exe**, replaceable by the user. Not embedded. A replaced model gets a shape check and a warning, not a refusal. |
| Protection | Installed under `Program Files` (read-only for normal users). At start the engine checks every program file against `manifest.json` and **stops with a clear error naming the file**. `--no-verify` is the developer escape hatch. Models are excluded. Unsigned for now (SmartScreen will warn). |
| Installer | **Inno Setup, installs to Program Files, needs admin once - written into the script** (`PrivilegesRequired=admin`, `DefaultDirName={autopf}\Viitorx\TrackingEngine`). The `models\` folder gets user write access; nothing else does. |
| Platform | Windows for v1. macOS and Linux "later". Keep the wire and spec portable. |
| Unity | Minimum **6000.3**. |
| Packages | Family `com.viitorx.avtar.tracking`: umbrella plus `.core`, `.sidecar`, `.vision`, `.body`. Registry or git URL: decided later; git URL works from the start. |
| v1 scope | **Hands, face and body together.** |
| Unreal | Skipped for now. v1 only keeps the wire engine-neutral. |
| Live testing | The user has tested the tracking results and accepts them for now; **not a gate for packaging.** (Recorded honestly: the agent has not seen a live camera, and the Unity Play-mode checks of H-03/H-04 are still open. Work on the project continues after packaging.) |
| Plan storage | Written into both repos' docs. |
| Repos | **Reuse the two existing repos, no new ones** (same GitHub account, `vinayak-vc`). Rename the Python repo to `viitorx-tracking-engine`. Work on branch `package-v1` in both. |
| Timing | On hold until the user's next big feature is done; then study it, amend this plan, start at phase 0. |

## 2. Engine folder

```
viitorx-tracking/
  viitorx-tracking.exe
  *.dll, lib/, ...             compiled, no .py
  models/                      palm, hand landmark, face detector / mesh / blendshape, RTMW3D, person detector (not in git)
  manifest.json                SHA-256 of every program file (not models)
  THIRD_PARTY_NOTICES.txt
  LICENSE
```

Host-neutral behaviour (every engine needs it, Unity's job object is not available elsewhere): `--parent-pid` (exit when the host dies), clean exit on stdin close, a JSON status line on stdout, stable exit codes (including one for "file modified"), `print-spec`.

## 3. How Unity finds the engine

Checked in this order, editor and player:

1. A path set in the package settings: `viitorx-tracking.exe`, **or a Python project folder plus its Python** (run the source directly: "use the Python project instead of the exe").
2. Environment variable `VIITORX_TRACKING_ENGINE`.
3. Per-user install `%LOCALAPPDATA%\Viitorx\TrackingEngine\<version>`.
4. `StreamingAssets/Viitorx/TrackingEngine/viitorx-tracking.exe` (player builds are told to put the folder there; the editor offers a one-click copy).
5. Not found: prompt to **download the pinned release** (SHA-256 baked into the package) or enter a path. In a player (no file dialog) the prompt is a path box in the F6 panel, remembered in PlayerPrefs.

Both an installer and the in-Unity download exist ("both"). The downloaded copy is marked read-only and verified by the same manifest check.

## 4. Repositories - can the two existing repos be reused?

Measured today: the Python repo (`vinayak-vc/viitorx-vrm-model-python`) tracks 350 files, 72 MB of pack: `evidence/` 142 files (research output), `tools/` 96 files (harnesses), `depthai_blazepose/` (a vendored third-party OAK-D port), 31 tests, 9 docs, and the production modules at the root. The Unity repo (`vinayak-vc/viitorx-vrm-avtar-unity`) tracks 948 files, 66 MB: `Runtime/` 361, `docs/` 224, `Showcase/` 179, `Tests/` 55, `Scenes/` 44; its root is already a UPM package (`package.json`) and the Python repo is a git submodule of it.

**Recommendation: reuse both, restructure in place; do not create new repos now.**

- **Python repo becomes the engine repo.** Rename it on GitHub to `viitorx-tracking-engine` (GitHub redirects the old URL; history, issues and the submodule link keep working). Inside: a proper package (`viitorx_tracking/`), `build/` (Nuitka, Inno Setup, manifest), `tests/`, `docs/`. Move the research (`evidence/`, harness `tools/`, `depthai_blazepose/`) into a `research/` folder, or leave it on a tagged branch, so the shipped engine and its README are clean. History stays.
- **Unity repo becomes the packages repo (monorepo).** Packages as sub-folders (`Packages/com.viitorx.avtar.tracking.core/` ...). UPM installs a sub-folder with `git URL?path=/Packages/...#tag`. The existing app (Experiences, SkeletonShow, showcases, scenes) stays in the repo as the **sample/dogfood application** that consumes the packages. The submodule is replaced by the engine's pinned release (the Python project can still be pointed at directly: section 3, step 1).
- **Trade-off, stated:** UPM clones the whole repository (66 MB, with showcases) to install one package. That is acceptable for v1. If clone size matters later, split the packages into their own light repo then; the sub-folder layout makes that a `git filter-repo` away.
- **New repos only if** the user wants a clean public history (no experiments, no internal docs) or a different owner. That is a free choice later; nothing in this plan blocks it.
- **The GitHub token the user pasted into the chat is NOT used and must not be kept in the conversation or any file: revoke it and, when repository work starts, authenticate with `gh auth login` instead.** Renaming a repository needs only the owner account.

## 5. Phases and gates

| # | Phase | Output | Gate |
|---|---|---|---|
| 0 | Housekeeping | Revoke the pasted token. Decide repo reuse (section 4). Tag the current state of both repos (`pre-packaging`). | User confirms section 4. |
| 1 | **Compile spike** | Nuitka standalone of the hands and face senders with onnxruntime-directml, opencv, numpy. Measure: size, cold start, DirectML inside the frozen build, a clean Windows VM with no Python, antivirus / SmartScreen reaction. | **Go / no-go.** On no-go: PyInstaller one-folder, or a different freezing approach. |
| 2 | Wire spec | `WIRE.md` for hands (8901), face (8902), body (8899): fields, spaces, units, versioning, `ver` bump policy. Golden datagrams plus a conformance checklist (the existing fixtures are the start). | Spec reviewed. |
| 3 | Engine repo | Python package; one CLI; camera / URL / LAN-camera source code extracted from `tools/`; `body` on webcam, LAN, URL and file (single and multi-person); `fetch-models` with SHA-256; model shape check; `verify` + `manifest.json`; parent-pid, exit codes, status line. Split extras: `vision` (numpy, onnxruntime, opencv), `body`, `oak` (dev only, not compiled). | Python tests pass (existing 390 + new). |
| 4 | Compiled build + installer | Add `body` to the Nuitka build. Build script: folder, manifest, zip, SHA-256, **Inno Setup script (admin install, Program Files, `models\` writable)**. CI publishes a GitHub release. | Clean-VM run with no Python passes; start check rejects a modified file. |
| 5 | Unity packages | `.core`, `.sidecar` (locator in the order of section 3, downloader, SHA-256 verify, launcher; replaces the path lookup that only finds `Assets/Games/<game>/python-sidecar~`), `.vision` (receivers, `VisionHost`, F6 panel, views, blendshape driver, `Samples~` = hands and face scenes, a `VisionBootstrap` for projects without AppBootstrap), `.body` (video/webcam body provider, retargeting, VRM, IK). Every dependency declared in each `package.json`. | Headless build and tests per package. |
| 6 | Dogfood | This project consumes the packages and the engine release; AppBootstrap keeps its settings section. | Existing scenes and tests unchanged. |
| 7 | Docs and release | README, CHANGELOG and getting-started per package; `THIRD_PARTY_NOTICES`; v1 tags. | User sign-off. |

Every phase keeps the project discipline: headless compile and tests, mutation checks for new logic, and an explicit NOT TESTED list in the docs.

## 6. Packages and what moves

| Package | From today's code | Depends on |
|---|---|---|
| `...tracking.core` | `Runtime/Core` | nothing |
| `...tracking.sidecar` | `SidecarProcessLauncher`, `SidecarPaths`, `SidecarLocator`, `VideoSourceSpec`, a vendored `NativeProcess` (today from the local `Utility` module) | core |
| `...tracking.vision` | `Runtime/Vision`, `VisionHost`, `VisionControlPanel`, `VisionSettings`, samples | core, sidecar |
| `...tracking.body` | OAK-D/video body provider, `Runtime/Retargeting`, `Avatar`, `IK` | core, sidecar, UniVRM, Animation Rigging (Mediapipe only if kept) |
| `...tracking` (umbrella) | metapackage | the four above |

UniVRM (`com.vrmc.vrm`) is not on Unity's registry and Mediapipe (`com.github.homuler.mediapipe`) is installed from git/OpenUPM: the `.body` package must document this or declare scoped-registry / git dependencies. This is the heaviest part of "all together".

## 7. Risks

- Nuitka with onnxruntime-directml, opencv and numpy is untested; the spike exists for this.
- Unsigned compiled exe: SmartScreen and antivirus may warn or flag.
- Licences: opencv wheels bundle LGPL ffmpeg; RTMW3D and the hand / face ONNX conversions have their own terms; notices must ship. "Free" does not remove the notice obligations.
- Two programs cannot read one webcam: running hands and face together on one camera needs a combined mode (not planned for v1; each scene runs its own sender).
- Body depth is estimated: positions suit a mirror, not metric measurement. State this in the docs.
- The agent has not seen a live camera or Unity Play mode for H-01..H-04 (the user has tested the results and accepts them); packaging does not re-test tracking quality.

## 8. Open items

1. ~~Confirm section 4~~ Decided: reuse both repos, rename the Python one.
2. ~~Which GitHub account owns the repositories~~ Decided: the same repos, same account.
3. ~~C compiler for Nuitka~~ **Checked 2026-10-13 on this machine:** Visual Studio Community 2022 17.14 with the C++ tools (MSVC 14.44.35207, `cl.exe` under `VC\Tools\MSVC.44.35207in\Hostx64d`, not on PATH: Nuitka finds it itself or runs from a Developer prompt), Windows SDK 10.0.26100.0, and **Inno Setup 6** (`ISCC.exe`) are installed. Python is 3.10.0 (the project venv too). Nuitka and its helpers are not installed: phase 1 creates a separate build venv for them (not the runtime venv). Disk: about 37 GB free on D:.
4. The next feature the user will add (unknown scope): re-read this plan against it before phase 0.
5. Registry choice (git URL first; OpenUPM or a private registry later).

## 9. Amendments after S-01 (cutout) and HE-01 (Human Echo), 2026-10-16

The user's next feature was the cutout (S-01, ADR-103, UDP 8903) and Human Echo (HE-01, ADR-104). Studied read-only on 2026-10-16 against `docs/S01_CUTOUT_2026-10-13.md` and `docs/HE01_HUMAN_ECHO_2026-10-14.md`: the Python modules, the 26 golden cutout datagrams, the Unity receivers, textures, view and shaders, and the Launcher rows exist; the Python suites re-run with exactly the documented counts (100 / 66 / 35 / 93 / 36). The user confirmed on 2026-10-16: **this is the whole feature for now; cutout goes into the v1 engine; the render package is split off; Human Echo stays outside the packages; the docs' stale "not tested" lines are to be corrected when work resumes.**

### 9.1 What changes

| Area | Change |
|---|---|
| Wire spec (phase 2) | Four wires, not three: hands 8901 and face 8902 (JSON), body 8899, **cutout 8903 (binary, chunked)**: a 72-byte header on every datagram (session, frame number, chunk index / count / offset, sizes, encodings, flags, model id, people, latency figures), colour as raw RGBA32 / JPEG / none, mask as raw A8 / raw deflate, top row first, newer frame abandons an older unfinished one. `WIRE.md` describes it separately from the JSON streams and ships the 26 `cutout_*.bin` goldens as its conformance set. |
| Engine subcommands | Add **`cutout`**. Models it needs, all fetched into `models/` with SHA-256 checks: the MediaPipe selfie segmenter (general 256x256 and landscape 256x144, 0.46 MB each) and the person detector `yolox_s.onnx` (36 MB; `fetch_yolox_s.py` exists). Without the detector the sender falls back to the whole picture and says so. |
| **One-camera mode (new, v1)** | Today hands, face and cutout are three programs that each open the camera, and a webcam or a LAN-camera viewer can be read by one program at a time. The engine gets **`run --streams hands,face,cutout`** (any subset): ONE process opens the source once and runs the requested pipelines on each frame, each sending to its own port. The single-stream subcommands stay. The Unity host starts one process for whatever streams the scenes want (today: one process per stream) and restarts it when the wanted set changes. **Cost, unmeasured:** the pipelines share one GPU and one frame budget (alone: face about 8 ms, cutout 17-22 ms per frame), so the combination may fall below 30 fps. Phase 1 (spike) measures hands + face + cutout together on one source before this is promised. The shared frame loop `vision_sender.run` takes one pipeline today; it must take a list. |
| depthai | **Not needed at runtime (OAK-D is excluded).** The only problem is an import: `wholebody_udp_sender.py` does `import depthai` at module level and every vision sender reaches it through `vision_sender` -> `video_udp_sender` -> `wholebody_udp_sender`. **Decision: the engine build includes a tiny stub `depthai` module (build-time only; a previous measurement saw zero accesses), so no source file changes and `wholebody_udp_sender.py` stays byte-identical.** A clean extraction can replace the stub later; it is not needed for v1. The compiled engine also leaves out the `onnx` package (FP16 tooling) and aiortc / PyAV only if LAN-camera streaming is dropped (it is not: it is a v1 source, so they stay and their LGPL / FFmpeg notices ship). |
| Unity packages (phase 5) | `.vision` is split: **`.vision`** (receivers, parsers, host, F6 panel, hands and face views, blendshape driver; depends on Core and Sidecar only) and **`.cutout`** (cutout wire, assembler, textures, `CutoutView`, `Cutout.shader` and `CutoutBackdrop.shader`, `CutoutStreamBinder`; depends on `.vision` and **`com.unity.render-pipelines.universal`**, because the shader includes URP's `Core.hlsl`). The umbrella depends on all five. |
| Stays outside the packages | **Human Echo** (`Showcase/HumanEcho`): it depends on FluXY (`Assets/FluXY`, a third-party asset that must not be redistributed in a package) and on compute shaders. Also Chromatic Rain, Fluid Disruption, the Launcher and AppBootstrap. They remain the sample application that consumes the packages. The hands, face and cutout sample scenes may ship as `Samples~` of their packages (they need no FluXY). |
| Settings | `VisionSettings` now has about 55 settings (cutout added 20): a cutout section in the F6 panel and `VisionSenderArguments.Cutout`. The sender command lines stay pinned by the `args_*.txt` fixtures that both the C# tests and the Python argparse parsers consume; the engine renames nothing without updating them. |
| Engine size and cost | The model set is now palm + hand landmark, face detector + mesh + blendshape, selfie x2, yolox_s, RTMW3D (body): all beside the exe. Cutout on loopback is about 117 MB/s raw at 720p; the engine's defaults for another machine are `--color jpeg --mask-enc deflate --chunk-bytes 1200` (tested only with golden datagrams, never between two machines). |
| Docs to correct when work resumes | The H-01 to H-04 and S-01 "NOT TESTED" lists are stale: `Editor.log` shows `VisionHost` launching the hands and face senders on `camera:0`, ~30 fps, Auto start and stop working; the user has tested the results and accepts them; the S-01 doc records Play-mode checks on two video files but its "a person on the webcam" item is the user's to restate. Correct from that evidence, and say what the log does not show (what was on screen). |

### 9.2 Phase changes

- **Phase 1 (spike)** also measures: hands + face + cutout in one process on one webcam (fps, GPU time, memory over a few minutes), and the frozen build with the depthai stub.
- **Phase 2** adds the cutout wire to `WIRE.md` and its conformance checklist.
- **Phase 3** adds the `cutout` subcommand and `run --streams`; generalises `vision_sender` from one pipeline to a list; keeps `wholebody_udp_sender.py` untouched.
- **Phase 4** adds the cutout models to the model set and the manifest rules (models still excluded from the program-file check).
- **Phase 5** splits `.vision` / `.cutout`, adds the one-process host (a stream set change restarts one process), and declares the URP dependency.
- **Phase 7** unchanged; the user has accepted the tracking results.

### 9.3 New risks

- The one-process combination may not hold 30 fps on the user's GPU (the GPU clock already falls under the paced duty cycle, measured in S-01).
- Frame rate and latency of cutout over a real network are unmeasured.
- A shader package requires URP; projects on the built-in pipeline cannot use `.cutout`.
- The Launcher, Build Settings and the Editor-only scene registration stay per-machine; packages do not fix that.

## 10. Phase 1 result (compile spike), 2026-10-16

Work area (outside both repos): `D:\viitorx-engine-spike` (`stage.py`, `combined_spike.py`, `SPIKE_LOG.md`). The repos were only read. Build: Python 3.10.0, Nuitka 4.3, MSVC 14.3, `--standalone`; Dependency Walker (`depends22_x64.zip`, dependencywalker.com) downloaded with the user's permission.

**Verdict: GO for Nuitka standalone.**

| Check | Result |
|---|---|
| Output | **0 `.py`, 0 `.pyc`**; 131 compiled files; **330 MB** (opencv 99, FFmpeg for the LAN camera 63, onnxruntime 45, numpy 37, exe 36, DirectML 18, cryptography 10). Models are extra: the eight vision models 49 MB; RTMW3D 185 MB (fp16) / 369 MB. |
| Start | 0.24 s (`version`), 0.45-0.74 s (`--help` of a sender); one folder, nothing unpacked; runs with only the Windows folders on PATH (no Python reachable). |
| Antivirus | Windows Defender: no detection. Other products and SmartScreen: NOT tested. |
| DirectML | Works inside the frozen build: every network of hands, face and cutout on `DmlExecutionProvider`. |
| Senders | hands, face and cutout run from the exe on video files; models found in `models/` beside the exe. |
| depthai | The build-time stub is enough: 44 staged modules, no `depthai` package, no `onnx`. The staging must read the AST: a `sys.modules` scan (39 modules) missed five lazily imported modules. |
| Parity, frozen vs source, whole clip `123.webm` | Same frame counts and the same frames-with-a-result: hands 775 / 330, cutout 775 / 571 (people 0 at the end). Hands landmark runs 519 vs 514 (1%): not bit-identical (tracking decisions depend on wall-clock time). |
| LAN camera | The frozen exe reaches the LAN reader and the aiohttp request for the server's ICE config. **Not a WebRTC session**: needs the camera server, its PIN and a free viewer slot. |

**One process, several pipelines (the "one-camera mode" question), idle RTX 3060, source mode, flat out, per frame including encode and packing:**

| Clip | hands | face | cutout | hands + face | hands + face + cutout |
|---|---|---|---|---|---|
| `video.webm` 720x1280, one person | 3.1 ms | 1.5 ms (no face found) | 25.0 ms | 6.2 ms | **30.4 ms (p95 35.4) = 33 fps possible** |
| `123.webm` 1080x1920, hands present | 4.8 | 5.4 | 25.5 | 9.0 | **36.3 (p95 43.7) = 27.6 fps possible** |
| `147_1080p.mp4` 1920x1080, two people | 4.2 | 2.4 | 27.9 | 7.1 | **38.7 (p95 43.1) = 25.9 fps possible** |

Reading: the cost of the combination is about the SUM of the parts (no penalty from sharing the GPU), and the cutout is 70-80% of it. At 720p one process holds about 30 fps; at 1080p sources about 26-28. **No clip contains a face the detector finds, so the face cost with a real face is unmeasured here** (H-02 measured 7.9-8.5 ms for a close frontal face: that would add 5-6 ms: expect 23-28 fps for all three at 1080p, about 30 at 720p). Ways to recover headroom, none built: run the cutout on every second frame, or one thread per pipeline. A webcam run with a person in front is needed for a real number (the camera needs the user's permission each time).

**Still open from phase 1:** a real WebRTC session in the frozen build; a clean Windows machine or VM without the dev tools or the Visual C++ runtime; the face cost on a real face; size trimming (opencv ships an FFmpeg next to PyAV's, numpy's OpenBLAS); the engine's messages still say "run tools/model/fetch_hand_models.py" and "Lan Camera Pin on AppBootstrap", and `interpreter :` prints a `python.exe` that does not exist (phase 3).

## 11. Phase 2 result (wire spec), 2026-10-16

**Done: `python-sidecar~/docs/WIRE.md` (spec version 1.0)**, in the engine repo, with the checks that keep it true.

| Piece | What it is |
|---|---|
| `docs/WIRE.md` | All four wires in one document: ports, transport, the versioning rules (unknown `ver` ignored; adding an optional member does not bump `ver`; unknown members ignored; the body wire has NO `ver`, so an incompatible body change must use a new port or `kind`), space and units, hands (members, entry, gestures), face (members, entry, the 52 names, head pose), the cutout (72-byte header table, payload, the receiver algorithm, caps), the body wire (single and multi-person members, BlazePose joint order), conversion notes for Unity (tested) and Unreal (derived, NOT tested), a 13-point receiver checklist, and what is not verified. |
| `tools/video/make_wire_conformance.py` -> `Tests/EditMode/VisionFixtures/conformance.json` | The expected result of all 48 golden datagram files, in one machine-readable file, produced by the Python reference parsers: accepted values (counts, ids, sides, gestures, states, sizes) or the refusal reason; for each cutout chunk family the reassembled frame with the SHA-256 of its colour and mask sections. A receiver in another language drives its conformance test from this file. |
| `tests/test_wire_spec.py` | **64 assertions**: the field tables in `WIRE.md` equal the encoders' members (hands, face), the cutout header table equals the struct's field names, order, offsets and sizes, the 52 blendshape names equal `face_wire.BLENDSHAPE_NAMES`, the ports and the allocation caps match the code, every body table member appears as a key in a body sender, every cutout reject reason is listed, and `conformance.json` on disk equals what the parsers produce today. |
| Mutation check | **11 of 11** injected breakages caught: a renamed member, a changed offset and size, two swapped blendshape names, a changed port, an invented member, a renamed body member, a removed marker, a renamed reject reason, and two edits of `conformance.json`. |
| Regression | `test_vision_fixtures` 93/93, `test_vision_cli_contract` 36/36, `test_hand_wire` 34/34, `test_face_wire` 36/36, `test_cutout_wire` 66/66, all unchanged. |

**Corrections the spec work made to older statements:** the body wire README block omits `sid` (the sender sends it); the `--max-faces` cap of 4 is the Unity host's, not the sender's; for the `bad_*.json` fixtures the Python reference raises one error type, so the exact reason is advisory and only "refused" is checked.

**Not done in phase 2 (open):** a C# test that consumes `conformance.json` (the C# tests assert the same values by hand today); a receiver in a third language (the Unreal conversion is derived, not run); moving the golden files out of the Unity repo into the engine repo (they stay where the C# tests read them; the generator writes them there); the body wire has no encoder module, so its table is checked only against the senders' source, not against bytes.

## 12. Phase 3 result (the engine package), 2026-10-16

**Done in the engine repo (`python-sidecar~`, branch `package-v1`): the package `viitorx_tracking/`, `docs/ENGINE.md`, `tests/test_engine.py`.**

**A deliberate deviation from section 5:** phase 3 was planned as "proper Python package; extract the source handling from `tools/`". It was built as a **layer over the existing modules**, not a physical move:
the flat modules at the repository root and the senders under `tools/` are unchanged, so the Unity project that runs the Python source directly, the existing suites (390+ assertions) and the compiled build all use the
same code. The one change to existing code: each sender (`hands_udp_sender`, `face_udp_sender`, `cutout_udp_sender`) gained `build_pipeline(args)`, `wire_label()` and `STREAM_TAG`, and its `main()` now calls them (behaviour identical:
the existing suites pass and each sender was run for 5 s from source afterwards). The physical restructure (moving `tools/video` source handling to a root module, a pip-installable layout) is deferred to phase 4, where the compiled build
stages the files flat anyway.

| Piece | What it is |
|---|---|
| `viitorx_tracking/cli.py`, `__main__.py` | `viitorx-tracking <hands | face | cutout | body | run | list-cameras | fetch-models | verify | version | print-spec>`, engine options `--no-verify` / `--verify`, `--models-dir`, `--parent-pid`, `--exit-on-stdin-close`, `--status`. `hands`, `face`, `cutout` take their sender scripts' own options; `body` passes through to `video_udp_sender.py`. |
| `multi.py` | **`run`: one process, one source, several streams.** The generalisation of `vision_sender.run` from one pipeline to a list (that function is untouched). Source options set once before the first `--stream`; each stream sends to its own port; a pipeline that raises is reported and dropped after 30 errors in a row; the others carry on. |
| `registry.py`, `fetch.py` | The model registry (name, URL, exact size and SHA-256), `fetch-models` (`.part` file, size cap, hash check, never overwrites a user-replaced file, `--check` downloads nothing). A test keeps the registry equal to the `tools/model/fetch_*.py` scripts. All 10 local model files match the registry by hash. |
| `manifest.py` | `manifest.json` (SHA-256 of every program file), the start-up check (exit 20, names the files), `verify`. Models, logs and downloads are excluded. |
| `lifecycle.py` | `--parent-pid` (Windows `OpenProcess` + `WaitForSingleObject`), stdin-close exit, the `VTSTATUS {json}` status line (`starting`, `ready`, `heartbeat`, `stopping`, `error`). |
| `bootstrap.py` | The `depthai` stand-in, installed only where the package is absent: the engine needs no OAK-D, and the senders reach `import depthai` through `wholebody_udp_sender.py`, which stays byte-identical. |
| `exitcodes.py` | 0 normal, 2 usage / source / model load, 20 program file modified, 21 model missing, 130 interrupted. |

**Verified (source mode, this machine):**
- `tests/test_engine.py` **113/113**: depthai stand-in, registry vs the fetch scripts, manifest (build, check, exclusions), fetch (a stream that never ends is cut off at the file's size), lifecycle, the loop with scripted pipelines and real UDP sockets (one orient per frame, each stream on its own port, a failing stream dropped while the other runs, restart on loop, stop signal, parent gone, source failure), the CLI and its exit codes, **and a real three-stream run (`hands` + `face` + `cutout` on `123.webm`) read back off the wire: every datagram parses, cutout frames reassemble with nothing refused, one session id per stream, exit 0, VTSTATUS lines on stdout.**
- **Mutation check: 40 of 41 injected breakages caught** (35 first; five got sharper tests: a size change in the size-only look, `.part` files, the stream cap, a status-prefix lookalike, failures counted "in a row"); the 41st (a short download accepted) is equivalent: the SHA-256 check refuses the same download.
- By hand: `--parent-pid` of a missing process stops in 1.4 s with exit 0; a closed stdin stops in 1.4 s; a missing model is exit 21 with the fetch command; a missing file source is exit 2; `fetch-models --check` and `verify` report all 10 local models OK.
- Regression: `test_wire_spec` 64/64, `test_vision_fixtures` 93/93, `test_vision_cli_contract` 36/36, hands/face/cutout wires 34 / 36 / 66, `test_cutout_engine` 100/100, `test_hand_tracking` 52/52, `test_face_tracking` 46/46, `test_video_source` 157/157.

**Measured, and different from the phase 1 sum:** `run` with all three streams on `123.webm` (1080x1920, decoded in the same process) held **18.6 fps** (186 frames in 10 s), against 27.6 fps predicted from the per-frame processing times alone: **decoding a 1080x1920 file is not free**, and the loop is single-threaded. A webcam at 720p decodes faster; not measured here.

**Not done (open):** a model shape check for replaced models (only size and hash are compared); `body` inside `run` (it is a pass-through with its own loop, no `VTSTATUS`, no `--parent-pid`); `--parent-pid` while waiting for a live source is noticed after the source's read timeout (a few seconds); the engine was never run on a live camera, a LAN-camera session or in the frozen build (phase 4); pip packaging (the senders' helper modules under `tools/` are not shipped by `pyproject.toml`); threads per pipeline to recover frame rate.

## 13. Phase 4 result (compiled build and installer), 2026-10-16

**Done in the engine repo (`python-sidecar~`, branch `package-v1`): `build/` (`build_engine.py`, `installer.iss`, `requirements-build.txt`, `README.md`), `tests/test_build.py`, `tests/verify_build.py`, `.github/workflows/engine-release.yml`.**
Build environment: Python 3.10, Nuitka 4.3, MSVC 14.3, Inno Setup 6 (all on this machine).

| Piece | What it is |
|---|---|
| `build_engine.py check / stage / compile / assemble / installer / all` | Reads the syntax trees of `viitorx_tracking/` and everything it reaches (44 modules; lazy imports included), refuses ambiguous module names, relative imports and packages the build Python lacks (`depthai` is the one allowed absentee), copies them FLAT into `stage/`, runs Nuitka standalone with the engine package included whole and `docs/` as data, then `assemble`: `LICENSE`, `THIRD_PARTY_NOTICES.txt` (generated from package metadata and the model registry), `models/README.txt`, `manifest.json`, a zip with its SHA-256. Nothing in the repository is changed by a build. |
| `installer.iss` | Inno Setup: Program Files, **administrator once**, `models\` writable by every user, registry `HKLM\Software\Viitorx\TrackingEngine\InstallDir` and the machine variable `VIITORX_TRACKING_ENGINE`, unchecked option to run `fetch-models`, uninstall removes everything, fixed `AppId` so a later setup upgrades in place. Unsigned. |
| `requirements-build.txt` | The 41 pinned packages that end up in the folder (equal to `requirements.lock.txt` where both list one, minus `depthai`). |
| `.github/workflows/engine-release.yml` | Builds on a clean `windows-latest`, runs the GPU-free tests and a smoke run, attaches the zip and the installer to a release for a tag `engine-v*`. **NOT TESTED**: never run on GitHub. |

**Amendment to section 3 (a decision of mine, small and reversible):** the installer puts the engine in Program Files, a place section 3's discovery order does not list. So it records the install in the registry and in the machine environment variable `VIITORX_TRACKING_ENGINE` (step 2 of the order). A host started before the install does not see a new variable until it restarts; the registry value is always current. **Phase 5's locator should read the registry value as well.**

**Measured:**
- Compile: **348 s cold** (6 jobs, 500 C files, no cache), **180 s** for the rebuild after a change. Output: folder **345 MB (137 files)**, zip **125 MB**, installer **85.7 MB**. Largest parts: `cv2` 98 MiB, `av.libs` 62, `onnxruntime` 45, `numpy.libs` 36, the exe 36, `directml.dll` 18. **Nothing was trimmed**: `TRIM` is empty because no removal has been measured safe (opencv's own FFmpeg next to PyAV's is the candidate; a file or URL source may use it).
- `tests/test_build.py` **99/99**; mutation check **20 of 21** injected breakages caught (the 21th, `[0] * 4` to `[0] * 3`, is equivalent: every real version has at least one part). Four first survived (a tautological test, a missing short-version case, a stage that kept old files, one mutant text that did not apply) and got sharper tests.
- `tests/verify_build.py` against the real folder: **65/65** (final run, on the final folder; its zip SHA-256 changes with every build). The folder has no `.py` / `.pyc`, the Visual C++ runtime beside the exe, a manifest that matches every file. Every command and exit code of ENGINE.md behaves frozen: version, print-spec, verify, `fetch-models --check` (exit 1 on an empty folder), missing model exit 21 with the command that fixes it (no repository script named), missing file exit 2, unknown command exit 2, `--parent-pid` of a dead process exit 0. **The file check on a copy of the folder:** a file with one byte appended is exit 20 with its name (`av.libs/avcodec-62-...dll`), a deleted one is exit 20 `MISSING`, `--no-verify` starts anyway, an extra file, a file in `models\` and a missing `manifest.json` are not errors; no stack trace in any case.
- Real runs of the compiled exe on the 1080x1920 clip (idle GPU, RTX 3060, decoding included, flat out): `hands` 71 fps, `face` 56 fps, `cutout` 26.7 fps, `run` with all three **19.5 fps**, `body` 25.7 fps at the start. Every network runs on DirectML in the frozen build. Every datagram parses; cutout frames reassemble with nothing refused; the VTSTATUS lines are `starting, ready, stopping` with the engine version.
- **Parity, source vs compiled, whole clip (`--no-loop`): the counts are identical** (hands, face, cutout 775 each; body 775 frames, 590 sent, 185 skipped, anchors 503/59/28 in both). **The values are not byte-equal, and two runs of the SOURCE are not either**: source against source, 41.7 % of the hands messages, 0.8 % of the face messages and 18.2 % of the cutout masks differ (hands: 98.3 % of messages have the same fields; cutout worst mean grey-level difference 3.9); compiled against source: 42.7 %, 0.8 % and 17.7 % (97.9 % same fields; worst 1.5). The compiled build differs from the source no more than the source differs from itself. A real difference is seen by the same test: with the clip flipped, 100 % of the face and hands messages differ. **The cause of the run-to-run variation is not isolated** (suspect: each frame is stamped with the wall clock, which the filters and the re-detect schedule read). Phase 1's "outputs match" was a count comparison; this is the first value comparison.
- Windows Defender: no detection recorded for the engine (`Get-MpThreatDetection` empty after the new build and every run above). Other products and SmartScreen not tested.

**Not done (open):**
- **The installer was compiled but never RUN.** It needs administrator rights and changes system settings (Program Files, HKLM, the machine environment); that is for the user to do. Steps: run `viitorx-tracking-setup-0.1.0.exe`, tick the models box, then `& "$env:ProgramFiles\Viitorx\TrackingEngine\viitorx-tracking.exe" verify`, then check `$env:VIITORX_TRACKING_ENGINE` in a NEW shell, then uninstall and see that the folder and the registry key are gone.
- **`fetch-models` was never run in the compiled exe against the network** (the models here were already present; a real download is about 49 MB from the registry's URLs). The TLS libraries are in the folder; whether the frozen download works is unproven.
- No clean Windows machine (no Windows Sandbox / Hyper-V here): the exe ran with `PATH` cut to the Windows folders (no Python reachable), but this machine has the Visual C++ runtime installed system-wide.
- A real WebRTC LAN-camera session, a webcam, SmartScreen, antivirus other than Defender, signing.
- `body` stays a pass-through (no `VTSTATUS`, no `--parent-pid`) and does not exit when a file ends (it stays alive and silent, as the script always did); not in `run`.
- pip packaging (`pip install`-able layout) is deferred: nothing in the plan's gates needs it, and the compiled build does not.
- Size trimming, the model shape check, threads per pipeline (all as in section 12).

## 14. Phase 5 design (Unity packages), 2026-10-16

Read-only survey of the Unity repo on 2026-10-16 (nothing moved yet). It answers: what each package takes, what has to be cut apart first, and how this project keeps compiling while it happens.

### 14.1 What the code looks like today

- 16 runtime assemblies. **`VirtualMirror.Core`** (37 files, references nothing) holds the pose / hand / face / crowd frames, the provider interfaces, the humanizer and the avatar / camera interfaces. **`VirtualMirror.Vision`** (39 files, references only Core) holds the three wire parsers, the receivers, the providers, the hands / face / cutout views, the blendshape driver, `VisionSettings`, `VisionSenderArguments` and the cutout shaders.
- **`VirtualMirror.Tracking`** (20 files) is one assembly that mixes three things: the OAK-D / video body provider (`OakDUdpPoseProvider`, `TrackingStreamHealth`), the sidecar process code (`SidecarLocator`, `SidecarPaths`, `SidecarProcessLauncher`, `SidecarGuards`, `SidecarSingleInstanceLock`, `SidecarStatusModel`, `VideoSourceSpec`), and two older in-Unity inference paths (`MediaPipe/` needs `Mediapipe.Runtime`, `Sentis/` needs `Unity.InferenceEngine`). Its asmdef references `Mediapipe.Runtime`, `Unity.InferenceEngine`, `Newtonsoft.Json` and **`Utility`**.
- **The sidecar code is self-contained:** it uses only `ILogService` (Core), `Newtonsoft.Json` (`SidecarStatusModel`) and one outside type, **`Modules.Utility.NativeProcess`** (`Assets/Modules/Utility/StartExternalProcess.cs`, 993 lines, which also needs `Modules.Utility.Utility`). That is the one real extraction: the package must carry its own copy.
- **`VisionHost`, `VisionControlPanel`, `CutoutStreamBinder` sit in `VirtualMirror.App`** (the AppBootstrap assembly). `VisionHost` takes `ILogService` and `VisionSettings` and names `AppBootstrap` only in comments, so it moves cleanly. `VisionControlPanel` names AppBootstrap in label text only. `CutoutStreamBinder` uses `AppBootstrap.Instance`: that one is the AppBootstrap binding and stays app-side (the package gets a binder that takes the host directly).
- **Cutout is not separable by folder alone:** `VisionSettings`, `VisionSenderArguments` and `UdpDatagramReceiver` mention cutout. The settings and the sender arguments (data) stay in `.vision`; the wire, assembler, receiver, textures, look, view and shaders move to `.cutout`.
- The tests are one assembly (`VirtualMirror.Tests`, 55 files) covering everything; they have to be split per package.
- The repository root is both a UPM package (`cloud.viitor.virtual-mirror`) and a folder inside `Assets/Games/` of this project, so today its files are compiled as assets. The Unity Editor is open on this project right now (project lock held), so batch mode cannot open it: compile and tests run headless through the generated csproj files as in `headless-unity-verification`.

### 14.2 The split (assembly names unchanged unless stated)

| Package | Takes | Needs |
|---|---|---|
| `...tracking.core` | all of `Runtime/Core` | nothing |
| `...tracking.sidecar` | `Sidecar*` (6 files), `VideoSourceSpec`, a vendored copy of `NativeProcess` + the helpers it uses, the engine locator of plan section 3 (adds the registry value and `VIITORX_TRACKING_ENGINE`, today it only finds `Assets/Games/<game>/python-sidecar~`) | core, `com.unity.nuget.newtonsoft-json` |
| `...tracking.vision` | the rest of `Runtime/Vision` without cutout, `VisionHost`, `VisionControlPanel`, a `VisionBootstrap` for projects without AppBootstrap, `Samples~` hands and face scenes | core, sidecar |
| `...tracking.cutout` | the cutout wire / receiver / textures / look / view, the two shaders, a binder that takes the host, `Samples~` cutout scene | vision, `com.unity.render-pipelines.universal` |
| `...tracking.body` | body provider (`OakDUdpPoseProvider`, health), Retargeting, Avatar, IK, the Kalidokit solver, the body host | core, sidecar, UniVRM, Animation Rigging; NOT `MediaPipe/` or `Sentis/` |
| `...tracking` | metapackage | the five above |

Kept out of every package (as agreed): Human Echo, Chromatic Rain, Fluid Disruption, the Launcher, AppBootstrap, `Showcase.Shared`, FluXY. They stay the sample application.

### 14.3 What has to be cut apart before anything moves

1. **Sidecar out of `VirtualMirror.Tracking`:** a new assembly with its seven files; `Tracking` references it. Nothing else changes for callers (namespace `VirtualMirror.Tracking.OakD` kept, so no `using` changes elsewhere).
2. **`NativeProcess` vendored:** copy under its own namespace into the sidecar package so this project never has two `Modules.Utility.NativeProcess`; the original in `Assets/Modules/Utility` stays untouched. The tests of `SidecarProcessLauncher` must still pass against the copy.
3. **`VisionHost`, `VisionControlPanel` out of `VirtualMirror.App` into the vision assembly** (App then references Vision, which it already does).
4. **Cutout out of `VirtualMirror.Vision`** into its own assembly that references Vision; `VisionSettings` / `VisionSenderArguments` keep the cutout data.
5. **Body:** `MediaPipe/` and `Sentis/` leave `VirtualMirror.Tracking` (into an assembly of their own that stays in the app, not in a package), so the package assembly stops referencing `Mediapipe.Runtime` and `Unity.InferenceEngine`.
6. **Tests split** per package with `Tests~` / `Tests` asmdefs, and the golden-datagram fixtures (`Tests/EditMode/VisionFixtures`) move with the vision package.

### 14.4 How this project keeps compiling, and how "another project can use it" is proven

- A file move keeps its `.meta`, so every GUID (scenes, prefabs, scriptable objects) stays valid. Assembly names stay `VirtualMirror.*`: renaming them would touch every asmdef and gains nothing now.
- **Recommendation:** the packages live in `Packages/` at the repository root (as in section 4); in THIS project they are ordinary assets (the folder is inside `Assets`), so nothing in the project changes except where files sit. **The proof that the packages work in another project is a fresh, empty Unity 6000.3 project outside this one, whose `manifest.json` points at the package folders (`file:`), compiled and tested in batch mode** (it holds no lock). That is phase 6's dogfood, in a form that needs nothing from the Assets copy.
- Making THIS project consume the packages through `manifest.json` would need the folders hidden from the Assets import (a name ending in `~`) and is not needed for the proof; it is a choice for the user (question asked 2026-10-16).

### 14.5 Order

5a: core, sidecar, vision, cutout, the vision bootstrap and samples, per-package tests, a fresh-project compile. 5b: body + umbrella. A gate (compile every assembly headless, all existing tests, a mutation check on moved logic, no GUID changed) after each step; the existing test counts are the baseline.

### 14.6 Risks

- A mass move while the Editor is open makes it reimport and recompile; a GUID lost to a missing `.meta` breaks a scene silently. Gate: every moved file's `.meta` moves with it and `git status` shows renames, no deletions.
- The package folders inside `Assets` and a second copy referenced from a manifest would give duplicate assembly names: never do both in one project.
- `.body` pulls in UniVRM (`com.vrmc.vrm`, not on Unity's registry) and Animation Rigging: its `package.json` can declare only registry packages; UniVRM must be installed by the user from its own source (documented), exactly as today.
- Headless compile uses generated csproj files (explicit file lists, gitignored): a new assembly needs its csproj by hand until the Editor regenerates it.

## 15. Phase 5a result (core, sidecar, vision, cutout extracted), 2026-10-16

The user's answers of 2026-10-16: packages in `Packages/` as ordinary assets in this project with a fresh test project as the proof; assembly names stay `VirtualMirror.*`; the Editor stays open (it reloads on each move).

**Done (Unity repo, branch `package-v1`):** four packages under `Packages/`, every file moved with its `.meta`.

| Package | Assembly | Holds | Depends on |
|---|---|---|---|
| `com.viitorx.avtar.tracking.core` | `VirtualMirror.Core` | all of the former `Runtime/Core` | nothing |
| `com.viitorx.avtar.tracking.sidecar` | `VirtualMirror.Sidecar` (NEW, namespace `VirtualMirror.Tracking.OakD` kept) | `Sidecar*` (6 files) and `VideoSourceSpec` out of `VirtualMirror.Tracking`; a vendored copy of `NativeProcess` (`VirtualMirror.Sidecar.Native`, from `Assets/Modules/Utility`, which is untouched) | core, Newtonsoft |
| `com.viitorx.avtar.tracking.vision` | `VirtualMirror.Vision` | the former `Runtime/Vision` (parsers, receivers, providers, views, blendshape driver, settings, and the cutout WIRE / assembler / receiver / textures) plus `VisionHost` and `VisionControlPanel` (they were in the AppBootstrap assembly) | core, sidecar |
| `com.viitorx.avtar.tracking.cutout` | `VirtualMirror.Cutout` (NEW, namespace `VirtualMirror.Vision` kept) | `CutoutView`, `CutoutLook` and the two URP shaders | vision, `com.unity.render-pipelines.universal` 17.3.0 |

**One refinement of section 14:** the cutout's transport (wire, assembler, receiver, textures) stays in `.vision` and only its rendering (view, look, shaders) is `.cutout`, because `VisionHost` runs the cutout receiver and the plan's other direction (cutout depends on vision) would otherwise be a cycle. `VirtualMirror.Tracking` lost its reference to `Utility`; `App`, `SkeletonShow`, `Editor`, `Tests` and `CutoutShowcase` gained references to the new assemblies; nothing else in the application changed.

**Tests split per package** (`Tests/Editor/` in each, assemblies `VirtualMirror.<X>.Tests`): core 18, sidecar 96, vision 186 (with the 112 golden-datagram fixtures and `conformance.json`), cutout 20. The Python side follows: `tools/video/make_vision_fixtures.py` `FIXTURE_DIR` now points to `Packages/com.viitorx.avtar.tracking.vision/Tests/Editor/VisionFixtures`; `test_vision_fixtures` 93/93, `test_vision_cli_contract` 36/36, `test_wire_spec` 64/64 pass against it. `VisionFixtures` reads next to its OWN file now (it read next to the caller, which broke for tests of another package: found by the real Unity run).

**Path map (old to new)** for anyone reading older documents: `Runtime/Core` to `Packages/com.viitorx.avtar.tracking.core/Runtime`; `Runtime/Vision` to `Packages/com.viitorx.avtar.tracking.vision/Runtime` (cutout view, look and shaders: `...cutout/Runtime`); `Runtime/Tracking/OakD/Sidecar*.cs`, `VideoSourceSpec.cs` to `...sidecar/Runtime`; `Runtime/Bootstrap/VisionHost.cs`, `VisionControlPanel.cs` to `...vision/Runtime/Host`; `Tests/EditMode/VisionFixtures` and the vision / cutout / sidecar / core tests to the packages' `Tests/Editor`.

**Verified:**
- A GUID inventory (every `.meta` of the repository) before and after: **796 GUIDs before; none lost, none duplicated, no orphan metas; 182 moved, 39 new** (folders and the new package files).
- Every one of the 38 assemblies of the repository compiles (csproj files generated from the file system, `dotnet build`, so the open Editor's own project files are not relied on); a deliberate compile error is caught by the same harness.
- The headless minimal runner (the one with the known ECall / missing-module failures) gives **the same result before and after, test for test: 517 passed, 97 failed, 2 skipped, identical failing set** (80 `ECall`, 17 missing UnityEngine modules: environmental). The 517 are now spread over five test assemblies.
- **A real Unity 6000.3.9f1 run in a FRESH, empty project** (`D:\viitorx-package-test`, outside this repository): its `manifest.json` references the four packages by `file:` (copies of the package folders), URP and Newtonsoft come from the registry; `-runTests -testPlatform EditMode` over the four package test assemblies: **320 of 320 passed** (core 18, sidecar 96, vision 186, cutout 20), no compile error. This includes the 80 tests the headless runner cannot run (`VisionHostTests`, which need real UnityEngine) and the cutout view / textures tests, which need a GPU: **with `-nographics` two classes failed for that reason (the JPEG read-back and the view tests, 18 failures); with the GPU enabled all pass.** The first run also found the fixture-path bug above.
- Mutation check: the real-Unity run failed 17 tests when the fixture path was wrong and passed when fixed; the harness failed on a deliberate compile error. No mutation check of the moved logic itself: the code was not changed, only moved (plus the vendored copy of `NativeProcess` with its namespace and one log call changed).

**Not done in 5a (next, in this order):**
1. The engine in the sidecar package: today the launcher still starts the Python source tree (`python-sidecar~`); the locator of section 3 (setting, `VIITORX_TRACKING_ENGINE`, registry `InstallDir`, per-user install, StreamingAssets, download of the pinned release with SHA-256), starting `viitorx-tracking.exe <stream> ...` with `--parent-pid`, and the one-process `run --stream` mode in the host. The pinned release does not exist yet (nothing is published): the pin stays data, empty until a release is made.
2. A `VisionBootstrap` for projects without AppBootstrap and the `Samples~` (hands, face, cutout scenes), then the real-Unity run of a sample in the fresh project.
3. `.body` and the umbrella package (5b).
4. `AppBootstrap`'s Vision section and the documents that name old paths were not rewritten (Python docs were).

**Known / to decide:** the repository root is still a UPM package (`cloud.viitor.virtual-mirror`, all-in-one, `package.json` unchanged). A project must NOT install it together with these four packages (duplicate assembly names). The all-in-one package's future (keep, or retire it when 5b and phase 6 are done) is the user's call.

## 16. Phase 5b result (the engine in the Unity packages), 2026-10-16

The user said yes to the engine integration. Two steps, as proposed: (1) find the compiled engine and start it per stream, (2) one process for several streams.

**Done:**

| Where | What |
|---|---|
| `...sidecar` | `EngineLocator` / `EngineLocation` (pure, every disk access through three delegates): the order of section 3 with the machine-wide install added: settings path (or the Python project), `VIITORX_TRACKING_ENGINE`, registry `InstallDir`, `%LOCALAPPDATA%\Viitorx\TrackingEngine\<version>` (highest version, or the pinned one exactly), `StreamingAssets/Viitorx/TrackingEngine`, last the Python source tree. A settings path that does not hold the engine is an ERROR, never a reason to try the next place; a bad environment value is only noted. `EngineRegistry` (advapi32). `SidecarLocator.ResolveEngine`. `SidecarProcessLauncher` starts `viitorx-tracking.exe <command> <options> --parent-pid <Unity's pid>` (no model path: the engine finds `models\` beside itself); the Python and supervisor paths are unchanged. |
| `...vision` | `VisionSettings` has an Engine section (`EnginePath`, `UsePythonProject`, `PythonProjectPath`). `VisionHost` locates the engine at each launch and says which one in the F6 panel (`EngineDescription`) and the log; no engine is a clear error naming every place looked at. **One process, one camera:** when two or more streams are wanted and the engine is the compiled one, the host starts ONE `viitorx-tracking run --video ... --stream hands ... --stream face ... --stream cutout ...` (`VisionSenderArguments.Run`); a change in the wanted set restarts it, one stream left goes back to its own process, a Python project is never given the combined command (the Python senders have no `run`). |
| engine | `--flip` is now PER STREAM in `run` (plan section 12 had it as a source option): the camera is read and rotated once, a stream that wants the mirror gets its own mirrored copy. Needed because hands and face mirror (selfie view) and the cutout does not (Unity mirrors it on the GPU) while all three share one camera. `_build_streams` now validates every stream against the others BEFORE loading any model. |
| fixtures | `args_run_default.txt`, `args_run_custom.txt` (written by `tools/video/make_run_fixtures.py` from the single-stream fixtures): the combined line must configure each stream EXACTLY as its own line does. |

**Verified:**
- Real Unity 6000.3.9f1, fresh project, GPU on: **368 of 368** (core 18, sidecar 127, vision 203, cutout 20). That includes tests that start the REAL compiled engine (`D:\viitorx-engine-build\viitorx-tracking`) from `VisionHost` on a real clip: hands, face and cutout arrive in Unity's receivers from ONE process (`viitorx-tracking.exe` count +1, not +3), the process count is back to what it was after `Dispose`, and turning the face off leaves hands streaming (it goes back to its own process).
- Python: `test_engine` 123/123 (9 new: per-stream flip with scripted pipelines, `mirror_copy`, the conflict check), `test_vision_cli_contract` 54/54 (18 new: the combined fixtures parsed with the engine's own splitter and the senders' parsers; every stream's namespace equals its single-stream line's), `test_wire_spec` 64/64, `test_vision_fixtures` 93/93, `test_build` 99/99.
- Mutation checks: engine flip logic **9 of 9** caught; locator and launcher **12 of 13** (the survivor is equivalent: a file existing implies its folder exists); the host's combined mode **7 of 7** in REAL Unity (one by a hang: dropping `StopGroup()` from `Dispose` leaves four engine processes running and Unity does not exit; one first survived and the Python-project test was strengthened).
- Headless runner against the baseline: **no old test changed result**; 11 new tests fail there for the known reason (real UnityEngine needed) and pass in real Unity. GUIDs: 796 before, **0 lost**, 0 duplicates.
- Found by the real runs, not by the headless ones: `Microsoft.Win32.Registry` is not in Unity's default .NET Standard 2.1 profile (the registry read is raw advapi32 now); `VisionFixtures` must read next to its own file.

**Not done / open:**
- **The download of the pinned release** (`EngineInstaller`: download, SHA-256, extract to `%LOCALAPPDATA%\Viitorx\TrackingEngine\<version>`, run `verify`) is not written. No release exists, so there is nothing to pin; it needs a decision on the URL.
- **`VisionBootstrap`** (the host for projects without AppBootstrap) and the **`Samples~`** scenes; `.body` and the umbrella package (5c); the documents that name old paths.
- **Not tried:** a real webcam or LAN camera through the combined mode (only a video file); the installer-written registry value (the key does not exist until the installer has run: the read is tested on a value every Windows has); an install under `Program Files` (the stand-in tests run from a temp folder whose path contains a space); the one-process mode on a GPU with the user's own app running.
- `VisionHost` still lives in the namespace `VirtualMirror.App` although its assembly is `VirtualMirror.Vision` (kept so no `using` changes); `AppBootstrap`'s Vision section is unchanged and now shows the three engine fields.

## 17. Phase 5c result (VisionBootstrap and the samples), 2026-10-16

**Done:**

| Where | What |
|---|---|
| `...vision` | **`VisionBootstrap`**: one component that owns the `VisionSettings` (the whole Inspector), creates the `VisionHost`, ticks it, adds the F6 panel and disposes it; a second one is refused and removed so the first keeps the ports. **`VisionHostRegistry`**: where a scene finds the host (AppBootstrap registers its host there too, two lines). **`VisionHostClient`**: base of a component that asks the host for streams (it binds when the host appears, lets go on disable or when the host goes away). `UnityDebugLogService`. The F6 key is compiled only where the legacy Input Manager is on (`ENABLE_LEGACY_INPUT_MANAGER`), so a project on the Input System package alone still compiles and runs. |
| `...vision` `Samples~/HandsAndFace` | `HandsAndFaceSample` (hands skeletons, face point cloud and head axes, status box; one engine process for both), its asmdef, README and a built scene. |
| `...cutout` `Samples~/Cutout` | `CutoutSample` (buttons for mode and backdrop; keys 1-3 with the legacy Input Manager), its asmdef, README, a built scene and the two materials. |
| both `package.json` | the `samples` entries the Package Manager shows under Samples. |

**How the sample scenes were made, and why that matters:** a scene refers to its scripts by GUID, and the Package Manager's Import copies `Samples~` with its metas. So the sample scripts' metas are FIXED in the package (deterministic), the scenes are built by code (`SampleSceneBuilder`, outside the packages) in the fresh test project after importing the samples the way the Import button does, and the generated `.unity`, `.mat` and metas are copied back. Checked: every GUID in the scenes and materials resolves to a file of the packages (scripts, shaders, materials), no missing script.

**Verified:**
- Real Unity, fresh project: the new Play-mode assembly `VirtualMirror.Vision.PlayTests` **9 of 9** (host created and registered, panel bound, receivers listening, ports freed on destroy, a second bootstrap refused, a client that wakes first binds later, disable/enable of a client, a client follows its host away and to the next).
- **The samples were imported with the Package Manager's own API** (`UnityEditor.PackageManager.UI.Sample.Import`, what the Import button calls: both samples found, imported to `Assets/Samples/Viitorx Tracking Vision/0.1.0/Hands and Face` and `.../Viitorx Tracking Cutout/0.1.0/Cutout`), and **the imported scenes played with the REAL compiled engine and a video file: 3 of 3.** Hands and face scene: 5+ hands and 5+ face messages, exactly one `viitorx-tracking.exe` process more than before, the sample's `Hands` and `Face` views exist; cutout scene: 5+ cutout frames, the mask texture has person pixels, the view built its quad; and both scenes load with no engine and the host says "not found" (no hands arrive).
- Mutation checks in real Unity: **9 of 9** caught on the bootstrap, client and registry (one first survived: nothing asserted that the panel is bound; a test was added).
- The earlier suites are unchanged (core 18, sidecar 127, vision 203 with the host tests, cutout 20 = 368 in real Unity; headless baseline: no old test changed result). All Play-mode tests together: 12 of 12 (9 bootstrap + 3 sample). Running them together found a test-isolation fault of MINE, twice: a VisionBootstrap survives scene loads by design (`dontDestroyOnLoad`) and a sample scene stays loaded after its test, so the next test saw a registered host and a client asking for streams. Both test classes now remove them first.

**Not done / open:** `.body` and the umbrella package (5d); the download of a pinned release (no release exists); the samples were never run against a real webcam or the Editor's Game view by a person (only Play-mode tests with a clip: what they LOOK like on screen is not checked); the hands/face sample does not draw an avatar (by design); documents that name old paths; the sample scene builder and the sample Play-mode tests live in `D:\viitorx-engine-spike\unity-tools` (outside the repositories).

## 18. Phase 5d result (the body package and the umbrella), 2026-10-16

**Done:**

| Where | What |
|---|---|
| `...tracking.body` | NEW package. Moved with their GUIDs: `Runtime/Tracking` (`VirtualMirror.Tracking`: `OakDUdpPoseProvider`, `TrackingStreamHealth`, joint filter, fake providers), `Runtime/Retargeting`, `Runtime/Avatar`, `Runtime/IK`. New: `Runtime/Host` (`VirtualMirror.Body`: `BodyBootstrap`, `BodyAvatarDriver`), `BodySettings` and `BodyEngineArguments` (in `VirtualMirror.Tracking`), an Editor assembly with the UniVRM warning, sample *Body Avatar* (`Samples~`, scene + a 180 KB humanoid VRM as a `.bytes` asset). |
| `VirtualMirror.Tracking.Inference` | NEW application-side assembly: `MediaPipe/` and `Sentis/` left `VirtualMirror.Tracking`, so the package references neither `Mediapipe.Runtime` nor `Unity.InferenceEngine`. `VirtualMirror.App` references it. |
| `...tracking` (umbrella) | NEW package with no code: names core, sidecar, vision, cutout and body at 0.1.0; README with the install steps, UniVRM and the engine. |
| core / vision | `UnityDebugLogService` moved from vision to core (namespace `VirtualMirror.Core`). |
| engine repo | `tools/video/video_udp_sender.py`: the argparse parser extracted into `build_parser()` (no behaviour change); `tests/test_body_cli_contract.py` (16 assertions) parses the body command-line fixtures with it. |

**Decisions** (ADR-106): UniVRM is optional at compile time through `defineConstraints` + `versionDefines` (`VIITORX_UNIVRM`); `BodyAvatarDriver` is a COPY of the application's Kalidokit path, not a shared class (the application's per-frame avatar path could not be verified with the Editor holding the project, so it was not touched); the umbrella only pins versions.

**Verified:**
- Headless against the ORIGINAL committed code (git export, same runner, 12 test assemblies): all 843 original tests still exist, none went from passing to failing; the only new failures headless are 11 tests that need a real Unity or the engine.
- Real Unity, fresh project, **WITHOUT UniVRM**: zero compile errors; `VirtualMirror.Body`, `.Retargeting` and `.Avatar` are not built, `Tracking` and `IK` are; 405 edit-mode tests pass (core 18, sidecar 127, vision 200 + 3 ignored, cutout 20, tracking 40); the Editor logs the one warning.
- Real Unity, fresh project, **WITH UniVRM 0.126.0** (local copies, no download) and Animation Rigging 1.4.1: edit mode **529 of 529** with the engine environment set (526 passed and 3 ignored without it): core 18, sidecar 127, vision 203, cutout 20, tracking 40, retargeting 121. Play mode also `VirtualMirror.Vision.PlayTests` 9 of 9 (37 of 37 together).
- Play mode, `VirtualMirror.Body.PlayTests`: **35 of 35** (44 of 44 with `Vision.PlayTests`): a real VRM loaded through UniVRM with its control rig, driven by a fake provider (both upper arms move, hip follow with scale, no follow when out of range with the reason in the log, neutral retaken, unbinding stops every bone, an avatar without the control rig refused with the reason), `BodyBootstrap` lifecycle (port held and freed, second bootstrap refused, avatar replaced and destroyed under any parent, a late load leaves nothing, engine missing / source unusable reported with no process, tilt published), and **the real compiled engine**: `BodyBootstrap` starts `viitorx-tracking body` on a clip, the stream goes Live (20+ datagrams, 0 refused), the avatar's upper arm leaves the T-pose by 89.9 degrees, exactly one engine process more, and none after the bootstrap is destroyed.
- The imported Body Avatar sample scene (Package Manager `Sample.Import`) played with the real engine and the clip: Live, avatar posed (89.0 degrees), and with no engine the avatar loads, stands in its T-pose and the status says "engine: not found". Samples run: 5 of 5 (re-run at the end: see below) (the two earlier samples regenerated with the same builder and played again). One frame of the scene was saved and looked at: a blocky humanoid in a mid-motion pose in front of the camera, whole figure in view.
- Mutation checks: command line **24 of 24** caught headless (the one survivor, an empty depth interval, got its own test and is caught); engine path 3 of 3 (one only because the leaked engine process hangs Unity, not by an assertion); driver and bootstrap in Play mode **15 of 19**. The 4 survivors: removing the `!IsBound` early return in `BodyAvatarDriver.Update` (no observable difference, the unbound control rig ignores the pose: equivalent), removing the `IsPoseStale` guard (unobserved), the `destroyed` branch of `LoadAvatarAsync` and `lifetime.Cancel()` (each is covered by the other: cancellation stops a load, the flag disposes a late one; neither alone changes what a test can see). The run also exposed a test of mine that proved nothing (the port-freed check bound 127.0.0.1, which succeeds while a wildcard receiver holds the port); fixed, and the provider-dispose mutant is now caught. Python: `test_body_cli_contract` 16/16.

**Added after the report (body datagram tests, `Tests/Runtime/BodyWirePlayTests.cs`):** real body-wire datagrams (docs/WIRE.md section 6: 33 `lm` points, `lh`/`rh`, `xyz`, `seq`, `sid`, `t`) sent over loopback UDP to a `BodyBootstrap` with a real VRM. 7 tests: arms at the sides put the avatar's upper arms down (below -45 degrees), raising them brings them back to level, a fist of the LEFT hand curls only the left fingers (then only the right), `TrackHands = false` moves no finger, a stream that stops reaches `StaleFailsafe` after 2 s and the avatar is eased to its T-pose, a stream that returns is followed again, and the driver announces each stall and each recovery once (two stalls, two of each). Mutation: 7 mutants of the failsafe, release, hands switch and finger apply, 5 caught at first; of the 2 survivors one (recovery flag) got the per-stall log test and is caught together with its twin, the other (applying a frame during a failsafe) is equivalent because the receiver returns no frame while the stream is stale.

**Added after the Editor was closed (2026-10-16): the engine rebuilt, and the application's avatar path measured against the package's.**
- Engine rebuilt with `build_parser()` in the body sender (191 s, nuitka cache reused): zip sha256 `33e57b12...bf25`, installer sha256 `d3dc8565...d618` (the installer was built, not run); `verify_build.py` against the new folder **65/65**; `test_build` 99, `test_engine` 123, `test_vision_cli_contract` 54, `test_wire_spec` 64, `test_body_cli_contract` 16.
- **The measurement** (`Tests/PlayMode/AppBootstrapWireEquivalencePlayTests.cs` in the application's repo, a permanent test; real Unity batch run on the main project): the real `AppBootstrap` (scene "Mirror", production values: tilt 6 degrees) and `BodyBootstrap` get the SAME body-wire datagrams over UDP with the same VRM; 24 bones are compared at 0.4 s into and at the end of five phases (arms down, arms out, left fist, right fist, arms down). The application's settings file is moved aside and put back. **Result: 0.00 degrees on every bone in every phase between the application and the package, and 0.00 between two package runs.** The sequence is static poses (no walking, no fast motion), so this proves the settled and the converging pose, not dynamics.
- **It also found a real bug in the package.** With a correctly handed person (their LEFT side at +X of the camera picture) the application's arms go OUT from the body, the package's CROSSED the chest. Cause: the retargeting works in the avatar's model frame and binds to the avatar as it stands; the application turns every avatar 180 degrees on load (`AvatarSessionController`, before the control rig binds), `BodyBootstrap` did not. My earlier tests used a datagram with left and right swapped and only compared arm elevation, so they could not see it. Fix: `BodySettings.AvatarYawDegrees` (180), applied before `Bind`; the camera of the sample moved to -Z. New tests: arms held out go out and not across (35 -> 37 body play tests), and with yaw 0 they cross (the setting matters). The wire sender now uses the real camera handedness.
- After the fix: Body play tests **37/37**, samples **5/5** (rebuilt scenes, one saved frame looked at: the figure is large because the person in the clip walks toward the camera and the avatar follows the hip), edit mode **529/529**, `Vision.PlayTests` 9/9, GUIDs 0 lost / 0 orphans.

**Not done / open:**
- `BodyAvatarDriver` and `AppBootstrap` still carry the same orchestration twice, but they are now MEASURED equal for static poses. Merging means moving the application's per-frame code (filter for non-OAK providers, debug skeleton, diagnostics, face, the legacy FK/IK path, about forty serialized tunables) behind the driver; the new test is the safety net for it. It is a refactor of the production path with no new behaviour: a decision, not a gap.
- The wrist path (`ApplyWrist`, weight 0 by default) and the `IsPoseStale` guard are unobserved by any test.
- Nothing was run with a real webcam or a LAN camera; the body model (185 MB) is not shipped or downloaded by `fetch-models` (no URL): a user must place it. Depth is estimated.
- The umbrella resolves only when the family is reachable from a registry or the manifest; to my knowledge Unity does not read git URLs inside a package's dependencies (not tested). No registry, no release, no tag exists.
- The packages were never installed from a git URL, a registry or a second machine. The OpenUPM scoped-registry text in the READMEs was not run (UniVRM came from local copies to avoid a download).
- `docs/H-0x`, `S-01` and other documents that name old paths are still not rewritten.
- The engine was not rebuilt after the `build_parser()` extraction (the compiled folder is functionally the same); rebuild at the next release.

## 19. Release preparation (phase 7, first part), 2026-10-17

The merge of `BodyAvatarDriver` with `AppBootstrap` was skipped by decision; this is the release work that needed no decision from the owner and no push.

**Done:**

| Where | What |
|---|---|
| engine: `build/build_engine.py` | `assemble` writes `viitorx-tracking-<version>-win-x64.release.json` (schema, version, platform, file, `url` = `--base-url` + file name or empty, `sha256` and `sizeBytes` of the zip, `unpackedBytes`, `topFolder`, `exe`, `wireSpec`); `--base-url`; `test_build` 106/106 |
| engine: `.github/workflows/engine-release.yml` | a step that fails when the tag is not `engine-v<__version__>`; `--base-url=` built from `github.repository` and the tag (passed as `--base-url=$base` because PowerShell 5.1 drops an empty quoted argument); the descriptor is uploaded and attached. YAML parsed; **never run on GitHub** |
| `...sidecar` | `EngineRelease` (the descriptor, parsed as untrusted input), `EngineInstaller` (download over https, or loopback http for tests; a zip on disk; size and SHA-256 checked BEFORE anything is unpacked; unpack into a temporary folder with entry, size, count and path-length checks; `version --json` must name the descriptor's version; move into `%LOCALAPPDATA%\Viitorx\TrackingEngine\<version>`, where the locator already looks; nothing left on failure or cancel), `EngineProbe`, `EngineCompatibility`, the Editor menu *Tools > Viitorx > Tracking Engine > Install From Release File...* (assembly `VirtualMirror.Sidecar.Editor`; uses a zip saved beside the descriptor instead of downloading) |
| `...vision`, `...body` | `VisionHost` (one stream and several) and `BodyBootstrap` ask the engine once (`version --json`, remembered per file) and REFUSE one whose wire spec major number differs from the one these packages read (1), with a message; an exe that cannot answer is not blocked (starting it reports what is wrong) |
| every package | `Third Party Notices.md` (Kalidokit MIT, One Euro filter, UniVRM / Animation Rigging / Newtonsoft as required-not-contained, the sample avatar); the umbrella got the `LICENSE.md` it lacked; `...cutout` declares `...core` (its assembly references it) |
| `tools/release/` | `release_check.py` (the gate: one version, dependencies exactly that version, the umbrella names exactly the other five, README / CHANGELOG heading / LICENSE equal to the repository's / notices, a `.meta` for everything Unity imports and none orphaned, no GUID twice, every assembly reference resolves inside the package or to a declared dependency, UniVRM only under a define constraint, nothing from the application, no leftovers, no path of a developer's machine; `--release` also fails on "(unreleased)"), `bump_version.py` (`bump` / `finalize`) and their tests (38 and 18) |
| docs | `docs/INSTALL.md` (user), `docs/RELEASING.md` (maintainer: the gate, cutting both releases, three ways to host the packages with what each needs, what is not done), the engine's `ENGINE.md` and `build/README.md` (the descriptor), `docs/README.md` (where code moved: old path -> new), the current-facing documents that named moved paths rewritten (architecture, overview, the vision README, WIRE.md, roadmap); the dated reports keep the paths of their day |

**Verified (real Unity, fresh project, UniVRM, the real engine release):**
- Edit mode **565 of 565** (core 18, sidecar 162, vision 204, cutout 20, tracking 40, retargeting 121; no ignored test: the engine environment was set); play mode **47 of 47** (body 38, vision 9); samples **5 of 5**; the umbrella project **179 of 179**.
- **The real engine zip** (125 MB) was served from a loopback server, verified, unpacked, started once and moved into place, through the API and through the menu's offline path (the zip beside the descriptor); the locator finds it. A descriptor naming another version than the engine's is refused; an exe that exits 1 on `version --json` is refused; every wrong download (SHA, size, truncated, longer than said, 404, announced length) installs nothing and leaves nothing.
- **Found by running in Unity, not by the headless runner:** (1) cancelling a download that waits for data did not stop it (Mono's HttpClient ignores the token in a pending read): the stream is now closed on cancel, and the catch accepts the exceptions that raises; (2) the engine has 112-character paths and a long install root exceeded Windows' 260: the temporary folder name is short and the unpack refuses, with the length and the limit in the message, instead of failing in the middle.
- **The gate found, on the real packages:** the umbrella had no `LICENSE.md`; `...cutout` used core's assembly without declaring it. (Rewriting the moved paths also showed that the vision README still said its tests lived in the application; they moved in phase 5a.)
- Mutation: installer and descriptor 24 of 24 headless (3 first survivors got tests), the path-length check caught headless, and the cancel pair (the stream closed on cancel; the exception filter) 2 of 2, which only Unity can show; compatibility 6 of 6 headless, host 3 of 3 and body 1 of 1 in Unity; the gate 30 of 30 rules (the first run let 5 survive: tests that matched another rule's message, made specific).

**Not done / open:**
- **Nothing is pushed, tagged or published.** No install from a git URL or a registry has been run; `docs/INSTALL.md` says so. The workflow has never run on GitHub; the installer (.exe) was never run.
- **The installer never met a real release**: no release exists, so the `url` of the descriptor has never pointed at a real server, and https was not exercised (loopback http only).
- The packages' registry question is open (git URLs / OpenUPM / own registry: `docs/RELEASING.md`). Unity does not, to my knowledge, install the umbrella's siblings from git URLs.
- Engine and packages are matched by the wire spec MAJOR only; code signing is a release decision; there is no CI for the Unity suites (they need a licensed Unity and, for some tests, a GPU); the gate could run anywhere Python does.
- The `fetch-models` download in the frozen exe, the real webcam / LAN camera runs, a clean machine and the Unreal plugin are as before.

## 20. The first install from git, 2026-10-17

The owner pushed branch `package-v1` of the Unity repository (commit `3db7df3`, with the six packages, `tools/release/` and the docs) and of the engine repository (`7855bc8`, with the release descriptor and the workflow). No tag exists yet.

**Run (a fresh Unity 6000.3 project, manifest with the five packages by git URL `...?path=/Packages/<name>#package-v1`; Animation Rigging and UniVRM as local copies so that nothing else is downloaded):**
- all five packages resolved from GitHub into `Library/PackageCache` (the repository is 16 MB packed);
- edit mode **565 of 565**, play mode **47 of 47** (with the real engine), samples imported with the Package Manager from the git-installed packages and played **5 of 5**: the bytes that were pushed are the bytes that were tested;
- **measured:** a manifest with ONLY the umbrella by git URL fails to resolve (*Package [com.viitorx.avtar.tracking.core@0.1.0] cannot be found*, and the same for each sibling), so `docs/INSTALL.md` and the umbrella's README now say: from git, name the packages, never the umbrella;
- new `tools/release/git_install_block.py <ref> [packages] [--apply manifest.json]` prints (or merges) the manifest lines with each package's dependencies added and never the umbrella; 16 tests, 9 of 9 mutants caught; its output was installed in the fresh project and 341 edit-mode tests ran from it.

**Not done / open:**
- no tag: `#v0.1.0` is the same syntax but untested; after the owner tags, run `git_install_block.py v0.1.0 --apply` in a fresh project;
- the Unity repository's commit records an older engine commit (`d1bb349`) for the `python-sidecar~` submodule; the engine's pushed branch is at `7855bc8`: the next commit of the Unity repository should update the pointer;
- the engine release workflow has not run (it needs a pushed tag `engine-v0.1.0`); UniVRM through the OpenUPM registry was not run; nothing was run on another machine or with a real webcam.
