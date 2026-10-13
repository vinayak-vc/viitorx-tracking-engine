# PLAN - Viitorx Tracking Engine and Unity packages (2026-10-13)

**Status: PLAN ONLY. Nothing in this document has been implemented, and implementation is ON HOLD** until the user finishes one more large feature; the user will then ask for that feature to be studied and added to this plan. All packaging work happens on a branch named **`package-v1`** (created then, in both repos; not before).

**Nothing in this document has been implemented.** Agreed with the user in conversation on 2026-10-13; every "decided" line below is the user's answer. Open items are listed at the end. A copy of this plan lives in both repos (`docs/PLAN_TRACKING_ENGINE_AND_PACKAGES_2026-10-13.md`).

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
