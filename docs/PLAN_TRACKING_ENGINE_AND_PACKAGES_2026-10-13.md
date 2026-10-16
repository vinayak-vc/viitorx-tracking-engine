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
