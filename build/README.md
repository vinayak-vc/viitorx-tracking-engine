# Building the compiled engine and its installer

The release is a **folder** (`viitorx-tracking\`, `viitorx-tracking.exe` plus its libraries, no Python source) in a **zip** and an **installer** (Inno Setup). Both are made from this repository by
[`build_engine.py`](build_engine.py); nothing in the repository is changed by a build (output goes under `build/out`, or `--out`).

## What you need (build machine, Windows 10/11 x64)

| Tool | Version measured | Notes |
|---|---|---|
| Python | 3.10 (64-bit) | the build environment, below |
| Visual Studio Build Tools | MSVC 14.3, "Desktop development with C++" | Nuitka compiles C; `--msvc=latest` picks it |
| Nuitka | 4.3 | `pip install nuitka==4.3 ordered-set zstandard` (build tools, not shipped) |
| Dependency Walker | downloaded by Nuitka on first use | Nuitka asks; the build passes `--assume-yes-for-downloads`, which downloads `depends22_x64.zip` from dependencywalker.com into `%LOCALAPPDATA%\Nuitka`. Do that once on a machine you trust, or place the tool there beforehand |
| Inno Setup | 6.x | only for the installer step (`ISCC.exe`; found in Program Files, or `--iscc PATH`, or the `ISCC` variable) |

```text
py -3.10 -m venv buildenv
buildenv\Scripts\python -m pip install -r build\requirements-build.txt
buildenv\Scripts\python -m pip install nuitka==4.3 ordered-set zstandard
```

The build environment must **not** contain `depthai` (OAK-D is not part of the engine; `viitorx_tracking/bootstrap.py` installs a stand-in at run time) and must use `onnxruntime-directml`, never plain `onnxruntime`.

## Steps

```text
buildenv\Scripts\python build\build_engine.py check      # what would be compiled; is everything importable in the build Python
buildenv\Scripts\python build\build_engine.py compile --out D:\engine-build --jobs 6     # stage + Nuitka (about 15 minutes cold)
buildenv\Scripts\python build\build_engine.py assemble --out D:\engine-build              # notices, manifest.json, zip + SHA-256
buildenv\Scripts\python build\build_engine.py installer --out D:\engine-build             # Inno Setup
buildenv\Scripts\python build\build_engine.py all --out D:\engine-build                   # all three
```

Outputs in `--out`:

| File | What |
|---|---|
| `viitorx-tracking\` | the release folder (exe, libraries, `docs\WIRE.md`, `LICENSE`, `THIRD_PARTY_NOTICES.txt`, `manifest.json`, empty `models\`) |
| `viitorx-tracking-<version>-win-x64.zip` + `.sha256` | the folder as a zip (the in-Unity download fetches this and checks the SHA-256) |
| `viitorx-tracking-<version>-win-x64.release.json` | the release descriptor the Unity packages' `EngineInstaller` reads: schema, version, platform, file name, `url` (the `--base-url` you gave + the file name; empty without it), `sha256` and `sizeBytes` of the zip, `unpackedBytes`, `topFolder`, `exe`, `wireSpec`. Keep a copy where you trust it: the SHA-256 in it is what the download is checked against |
| `viitorx-tracking-setup-<version>.exe` + `.sha256` | the installer |
| `stage\`, `nuitka\`, `nuitka.log` | intermediate output; `nuitka\` is reused by the next compile, so a second build only recompiles what changed |

The version is `viitorx_tracking.__version__`. Models are NOT in the zip or the installer: `viitorx-tracking fetch-models` downloads the hands, face and cutout models (size and SHA-256 are checked); the body model is placed by the user.

## How the flat stage works

`build_engine.py` reads the syntax trees of `viitorx_tracking/` and of every module it reaches (lazy imports inside functions included: a plain import scan missed five modules in the phase 1 spike) and copies them side by side into `stage/`.
It stops, with the reason, if two modules share a name (the flat stage cannot hold both), if a module uses a relative import, or if the build Python cannot import a third-party package the closure needs.
`depthai` is the one allowed absentee. Dynamic imports (`importlib.import_module`, `__import__`) are listed as notes, not followed: today there are none.

## What the installer does ([`installer.iss`](installer.iss))

- Installs to `C:\Program Files\Viitorx\TrackingEngine` (the user can choose another folder); **asks for administrator rights once**.
- Makes `models\` writable for every user (`users-modify`), so `fetch-models` and replacing a model need no administrator rights. The program files stay read-only for users.
- Records the install for hosts: `HKLM\Software\Viitorx\TrackingEngine\InstallDir` and the machine environment variable `VIITORX_TRACKING_ENGINE` = the path of the exe. (A program started before the install does not see the variable until it restarts; the registry value is always current.)
- Offers, unchecked, to download the models at the end (runs `viitorx-tracking fetch-models`; if it fails, the engine says so the first time it needs a model, exit 21).
- Uninstall removes the program, `models\` and `logs\`, the registry values and the environment variable.
- Silent install, for a script or an IT department: `viitorx-tracking-setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART [/DIR="D:\Tools\Viitorx"] [/TASKS=fetchmodels]`.
- Installing the same or a newer version over an old one upgrades it in place (the `AppId` never changes).
- **The installer and the exe are not code-signed.** Windows SmartScreen shows "unknown publisher" until they are; signing needs a certificate and is a release decision, not a build one.

## Checking a build

```text
.venv\Scripts\python tests\test_build.py                 # the build scripts and the installer script (no compile)
.venv\Scripts\python tests\verify_build.py D:\engine-build\viitorx-tracking     # a REAL build: files, commands, the file check, real runs on a clip, parity with the source
```

`verify_build.py` needs the models (`--models`, default the repository's `models\`) and a clip (`--clip`); it uses the GPU. It never writes into the folder under test (the file-check section works on a copy).
