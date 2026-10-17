# Viitorx Tracking Monitor: specification for release 0.1.3

**Status: DROPPED by the owner on 2026-10-17, kept as a record (ADR-109). Nothing in this file was built and release 0.1.3 does not contain it.** It stays because its section 2 (what the engine can and cannot tell another program) and section 11.1 (measured costs, including the Windows `os.replace` failure while a reader holds a file open) are true whatever happens to the idea. Read the rest as a draft that was never approved: Section 13 lists the questions that change the design; every "proposed" below is a default the owner may overturn.
Every statement about the engine as it is today was read from the code of engine 0.1.2 (file named in brackets); anything about the new program is a requirement, not a measurement, and the budgets in section 11 are proposals until step M0 has measured them.

## 1. What this is

A small Windows program, **`viitorx-tracking-monitor.exe`**, separate from the engine, that a person opens to **see what the tracking engine is doing right now**, to **see and download its models**, to **read its logs**, and to **learn how to use it**. It reports; it does not control.

Requirements in the owner's words (2026-10-17), numbered so the rest of the file can point at them:

| # | Requirement |
|---|---|
| R1 | The user can see which models exist and which are present, and can download the missing ones. |
| R2 | The user can see how many clients are connected to the engine. (See section 4: UDP has no connections; this section decides what is shown instead.) |
| R3 | The user can see the logs. |
| R4 | The UI is separate from the engine, only visualises, and only reports what is going on **currently**. |
| R5 | It helps the user understand the engine and guides them on how to use it. |

**Non-goals (proposed):** starting, stopping or configuring engines (R4 says report); showing camera pictures, skeletons or landmarks; a tray icon or start-with-Windows; remote monitoring of another machine; telemetry or update checks (the program makes no network request of its own); history beyond the current session of each engine; macOS or Linux (the engine releases are Windows x64 only).

## 2. What exists today, and why the UI cannot be built on it alone

Read from engine 0.1.2:

1. **Status leaves the engine only on its own standard output.** `StatusEmitter` writes `VTSTATUS {json}` lines (`starting`, `ready`, `heartbeat`, `stopping`, `error`; every line has engine version, pid, wall-clock time) [`viitorx_tracking/lifecycle.py`]. A heartbeat per stream carries `stream`, `frames`, `sent`, `with_result`, `fps`, `infer_ms`, `dropped`, every 2 s (`HEARTBEAT_SECONDS`) [`multi.py`, `tools/video/video_udp_sender.py`]. The Unity launcher starts the engine with its standard output redirected to the host [`SidecarProcessLauncher.cs`, `RedirectStandardOutput = true`], so **a second program cannot read it**: a pipe has one reader. The launcher passes `--parent-pid` but **not `--status`**, so an engine started by Unity does not even emit the `VTSTATUS` lines; only the human lines (which include the same counters) are printed.
2. **Logs are the same human lines on the same standard output.** The installer creates no `logs\` folder; the uninstaller deletes one if it exists (`installer.iss`, `[UninstallDelete]`); the manifest excludes it [`manifest.py`]. Nothing writes it. Under Program Files only `models\` is writable by users.
3. **The engine never learns who receives.** Each stream calls `sendto((host, port))` on a UDP socket; `host` defaults to `127.0.0.1` and **can be another machine** (`--host`) [`multi.py`, `vision_sender.py`]. There is no handshake, no acknowledgement and no connection, so "connected clients" does not exist as a fact in the engine (the wire spec says the same: no delivery guarantee, `docs/WIRE.md` section 2).
4. **Models:** `registry.status(directory)` knows name, group, size, SHA-256, URL and state (OK / MISSING / CUSTOM / absent) of all ten model files; eight have a download, the two body models do not; `fetch-models` downloads them (https only, size and SHA-256 checked, `.part` file) [`registry.py`, `fetch.py`]. `verify` prints this as text only [`cli.py`].
5. **One sender per port** is the engine's rule (`docs/WIRE.md` section 1): two engines on one port interleave.

So a separate monitor needs three things the engine does not yet give: **where each running engine publishes its state, where it writes its log, and a machine-readable list of the models.** That is the engine side of 0.1.3 (section 6). Everything else the UI can do on its own.

## 3. Decisions taken in this draft

| # | Decision | Why |
|---|---|---|
| D1 | The monitor is a **separate program and a separate release asset**; the engine does not start it and does not need it. | R4; the engine must stay usable headless, from a host, with no UI installed. |
| D2 | The engine **publishes** its state to a per-process file and writes a log file; the monitor **reads** them. No server in the engine, no port. | A file needs no new port (`8897` and `8899`-`8903` are in use, `8904` and up are unassigned, `docs/WIRE.md` section 1), no firewall prompt, no new attack surface, and works when nobody is watching. |
| D3 | Data goes under a new folder **`%LOCALAPPDATA%\Viitorx\TrackingEngineData\`** (`run\`, `logs\`), not under the engine's install root `...\TrackingEngine\`. | The Unity locator treats every child of the install root as a candidate version folder [`EngineLocator.UserInstall`]. Read from the code, a folder without `viitorx-tracking.exe` is skipped, but nothing should depend on that. |
| D4 | "Clients" are shown as **destinations** and **bound receivers** (section 4), never as "connected". | UDP has no connection; calling a bound port a client would be a claim nothing measures. |
| D5 | The only action in the monitor is **downloading models**, and it is done by **calling the engine's own `fetch-models`**. | One implementation of the https / size / SHA-256 rules (R1 needs a download, R4 forbids more). |
| D6 | **Engine 0.1.3 is additive.** The wire specification stays 1.0; no wire, port or existing option changes; `version --json` gains one field. | `docs/WIRE.md` rule 3: only removals or changes of meaning change `ver`. |
| D7 | The monitor works with engine **0.1.3 and later**. An older engine is shown as "installed, publishes no status (needs 0.1.3)". | The files do not exist before 0.1.3. |

## 4. What "clients" can honestly mean (R2)

Three things are knowable; the UI shows these and labels them exactly so:

| Shown as | What it is | Source | What it does NOT say |
|---|---|---|---|
| **Engines running** | one row per engine process: pid, command (`run`, `hands`, `body`, ...), source kind, uptime | the run files of section 7.1, live pids only | nothing about who uses the output |
| **Sends to** | for each stream, `host:port` | the run file (the engine knows it exactly) | whether anyone is there |
| **Receivers bound to the port** | processes that have a UDP socket bound to that port on this machine, by name and pid (the engine's own pid excluded) | the operating system's UDP table (Windows `GetExtendedUdpTable`, owner pid), checked once a second | that the receiver reads, parses or uses the data; a destination on another machine (`host` not loopback / not this machine) is shown as "remote: cannot be checked" |

Example: the Unity Editor in Play mode with a vision receiver bound to 8901 appears as `Unity.exe (pid 1234) on 8901`; the same Editor stopped shows "nothing bound to 8901". That is the whole claim. A count in the header reads "2 receivers bound on this machine", not "2 clients connected".

If the owner wants a true client count, the engine and every receiver would need a protocol (a hello and a keep-alive datagram): a wire change in two repositories, an ADR in both, and receivers in Unity, Unreal and anything else would have to implement it. **Not proposed for 0.1.3** (Q1).

## 5. The monitor program

### 5.1 Form

One window, four tabs (section 8), a status bar. Single instance per user (a second launch focuses the first). No administrator rights. No installation needed beyond unzipping, but the installer can add it (Q3). Closing it stops nothing.

### 5.2 Technology (proposed, Q2)

| Option | For | Against |
|---|---|---|
| **A. Python with tkinter / ttk, compiled by Nuitka** (proposed) | the pipeline already exists and is proven on a clean GitHub runner; no new language or runtime; tkinter ships with Python, so no new dependency; a status monitor needs tables, a text pane and buttons, which ttk has | the look is plain; accessibility and high-DPI need care (set DPI awareness explicitly) |
| B. Python with PySide6 (Qt) | modern look, real tables and model/view | adds a very large dependency (size not measured here), a more difficult Nuitka build, LGPL notices |
| C. .NET (WPF or WinForms), self-contained | native look | a second toolchain and a second CI job; the team's engine work is Python |
| D. A local web page served by the engine | no second program | the engine would listen on a port (D2 says no) and R4 says a separate program |

Step M0 (section 12) measures A before it is committed to: the size of a Nuitka-built tkinter program, its start time, its idle CPU, and whether it passes the "unsigned exe on a clean machine" test. If A fails a budget the owner confirms, B is the fallback.

### 5.3 Structure (so the logic can be tested without a window)

```
monitor/
  model.py        read run files, logs, models --json, the OS UDP table; no widgets
  rules.py        the guidance rules of section 9: (state) -> list of (level, message, hint)
  view_*.py       one module per tab; only widgets; no decisions
  guide/          the text of the Guide tab (plain text files, English)
  app.py          the loop: poll once a second, call the model, hand the result to the views
```

`model.py` and `rules.py` take their inputs as arguments (a folder, a list of pids, a UDP table) so a test can feed them anything. The view modules contain no logic that is not a direct copy of a field.

## 6. Changes to the engine for 0.1.3 (all additive)

| # | Change | Detail |
|---|---|---|
| E1 | **Run file** | every engine process writes `%LOCALAPPDATA%\Viitorx\TrackingEngineData\run\<pid>.json` at start, rewrites it on every heartbeat (2 s, the existing interval) and deletes it on a clean stop. Written to a temporary file and moved over the old one (`os.replace`), so a reader never sees half a file. **The write runs on its own background thread, never in the frame loop**, one file per process (not per stream), at most one write per heartbeat; the frame loop only hands it a snapshot. **A failed replace is expected and harmless**: on Windows `os.replace` fails with a permission error while another process holds the target open (measured, section 11.1: 795 of 2000 attempts failed with a reader hammering the file); the writer catches `OSError`, skips that beat and tries again at the next, and logs one line if it fails for 10 beats in a row. The reader opens the file with delete sharing (`FILE_SHARE_DELETE`) or, failing that, retries within the same poll. On by default and **independent of `--status`** (an engine started by Unity does not pass it); `--no-status-file` turns it off. Contents in section 7.1. Cost: section 11.1. |
| E2 | **Log file** | the same human lines the engine prints are also appended to `...\TrackingEngineData\logs\engine-<pid>-<start>.log`, size-capped and rotated (proposed: 2 MB per file, the 5 newest files of one process; files of processes gone for more than 7 days are deleted at the next start; Q4). On by default; `--no-log-file` turns it off. Lines carry a timestamp prefix in the file (stdout is unchanged). |
| E3 | **`viitorx-tracking models --json`** | one JSON document: models folder, and per file name, group, size, sha256, url or null, state (`ok`, `missing`, `custom`, `absent`), required. Exit code 0 always (a listing, not a check). |
| E4 | **`version --json`** gets `"status_schema": 1` | so the monitor knows which run-file generation to expect. |
| E5 | **Models folder reported** | the run file and `models --json` carry the folder the engine actually uses (`--models-dir` / `VIITORX_MODELS_DIR` / beside the exe), so the monitor shows and downloads to the same one. |

Not changed: the wires, the ports, `VTSTATUS` lines, exit codes, `fetch-models` behaviour, the manifest and `verify` (the data folder is outside the install folder, so the file check is unaffected).

Source paths in the run file are reduced to **kind plus file name** (`camera:0`, `video: 123.webm`, `rtsp: <host only>`); a stream URL's user, password and query are never written. Full command lines are not written.

## 7. Data contracts

### 7.1 Run file, schema 1 (`run\<pid>.json`)

```json
{
  "schema": 1,
  "engine": "0.1.3",
  "wire_spec": "1.0",
  "pid": 22976,
  "parent_pid": 1234,
  "started": "2026-10-17T11:51:02+05:30",
  "updated": "2026-10-17T11:53:40+05:30",
  "state": "running",
  "exe": "C:\\Program Files\\Viitorx\\TrackingEngine\\viitorx-tracking.exe",
  "command": "run",
  "source": {"kind": "camera", "label": "camera:0", "live": true},
  "models_dir": "C:\\Program Files\\Viitorx\\TrackingEngine\\models",
  "streams": [
    {"stream": "hands", "host": "127.0.0.1", "port": 8901, "frames": 3120, "sent": 3120, "with_result": 2877, "fps": 29.8, "infer_ms": 6.1, "dropped": false}
  ]
}
```

`state` is `starting`, `ready`, `running`, `stopping`, `error` (with `reason`). `updated` is the time of the last heartbeat. A reader treats a file as **live** only if the pid exists **and** that process started at `started` (a recycled pid must not revive a dead engine) **and** `updated` is less than 3 heartbeats old (6 s); a file whose pid is gone is shown once as "stopped" (with the last state: `stopping` = clean, anything else = ended unexpectedly) and removed by the monitor after 60 s. A reader ignores members it does not know and refuses a `schema` it does not know, with a message naming both versions.

### 7.2 Models listing (`models --json`)

```json
{"models_dir": "C:\\...\\models", "models": [
  {"name": "palm_detection_mediapipe_2023feb.onnx", "group": "hands", "size": 3900000, "sha256": "...", "url": "https://...", "state": "ok", "required": true},
  {"name": "rtmw3d-x.onnx", "group": "body", "size": 0, "sha256": "", "url": null, "state": "absent", "required": false}
]}
```

(Values here are illustrative; the real sizes and hashes come from `registry.py`.)

### 7.3 Where files live and who can read them

`%LOCALAPPDATA%` is per user: a monitor sees the engines of the user it runs as, and no others (an engine started by a Windows service or by another logged-in user is invisible; stated in the Guide). Files are created with the user's default permissions; they hold no pictures, landmarks or credentials (section 6).

## 8. Screens

### 8.1 Overview (the first tab)

```
+-- Viitorx Tracking Monitor ------------------------------------------------------------+
| Engine installed: 0.1.3  (C:\Program Files\Viitorx\TrackingEngine, found by: registry)  |
+-----------------------------------------------------------------------------------------+
| ENGINES RUNNING (1)                                                                     |
|  pid 22976  run   camera:0   up 00:02:38   started by pid 1234 (Unity.exe)              |
|    hands  -> 127.0.0.1:8901   29.8 fps   infer 6.1 ms   sent 3120   with result 92 %    |
|    face   -> 127.0.0.1:8902   29.7 fps   infer 4.7 ms   sent 3118   with result 88 %    |
|    cutout -> 127.0.0.1:8903   27.6 fps   infer 11 ms    sent 2890   with result 99 %    |
+-----------------------------------------------------------------------------------------+
| RECEIVERS BOUND ON THIS MACHINE (2)                                                     |
|  8901  Unity.exe (pid 1234)      8902  Unity.exe (pid 1234)      8903  nothing bound    |
+-----------------------------------------------------------------------------------------+
| WHAT TO DO NEXT                                                                         |
|  ! Nothing is bound to 8903: the cutout frames go nowhere. Start the application that   |
|    reads the cutout, or stop the cutout stream.                                         |
+-----------------------------------------------------------------------------------------+
```

"Started by" is the parent pid's process name when that process exists. `with result` is `with_result / frames` over the engine's lifetime (the engine reports counters, not rates: the monitor does not claim a recent rate). The "What to do next" area is the output of the rules of section 9; with no findings it reads "Nothing to do: all streams are being received." only if every stream has a bound receiver, otherwise it lists them.

### 8.2 Models

A table of the ten files: name, group, size, state, what uses it, and a **Download missing** button (disabled when nothing is downloadable and missing; the two body models show "no download: place the file in <folder>" and an **Open folder** button). The download calls `viitorx-tracking.exe fetch-models --models-dir <the folder in use>`, shows the engine's per-file lines as they arrive and refreshes the table after. Progress is per file (the engine prints per file, not per byte): the table says so rather than faking a bar. Failure shows the engine's message and exit code. If the folder is not writable the monitor says so and names the folder; it never asks for administrator rights.

### 8.3 Logs

A list of log files (engine pid, start time, size, "running" / "ended"), a text pane that follows the end of the selected file while it grows, a level filter (lines with `error` / `warn` / everything: the engine's lines carry no level today, so the filter is by keywords until the engine marks levels; stated in the tab), **Copy**, **Open folder**. Large files are read from the tail (the last 2000 lines), never loaded whole.

### 8.4 Guide

Static pages inside the program, plain English, each short:

1. **What is this**: the engine reads a camera or a video, finds hands, faces, a cut-out person or a body, and sends the result as UDP messages to your application; this window shows that; it does not run the engine.
2. **Quick start**: the three ways to get an engine running (from Unity: the packages start it; from a command line with copy-able examples for `hands`, `face`, `cutout`, `body`, `run`; from a script) and the ports 8899, 8901, 8902, 8903.
3. **Reading the Overview**: what each column means and what it does not (the section 4 table, in plain words).
4. **Models**: what each group is for, which have a download, where to put the body models.
5. **Troubleshooting**: each message of section 9 with its cause and fix.
6. **About**: engine and monitor versions, wire spec, install folder, data folder, unsigned warning (ADR-108), links to the docs.

The Guide is text files in the program's folder (`guide\*.txt`), so a correction needs no rebuild of the program logic.

## 9. Guidance rules (the "help the user understand" logic, R5)

`rules.py` maps the observed state to messages. Each row is a unit test.

| Observed | Level | Message | Hint |
|---|---|---|---|
| no engine installed found by the locator order (env, registry, per-user) | error | The tracking engine is not installed. | Run the installer or Unity's *Install From Release File*. |
| installed engine older than 0.1.3 | info | This engine does not publish status (needs 0.1.3). | Update the engine. |
| no engine running | info | No engine is running. | Start your application (Unity starts it) or a command from the Guide. |
| a stream's port has no bound receiver and the destination is this machine | warn | Nothing is bound to `<port>`: the `<stream>` messages go nowhere. | Start the application that reads it, or check that it uses `<port>`. |
| the port is bound by a process other than the engine and the engine has no stream on it | info | `<name>` has `<port>` bound but no engine sends there. | Fine if you start the engine later. |
| two live engines send to the same port | error | Two engines send to `<port>` (one sender per port). | Stop one; the receiver sees interleaved messages. |
| `frames` increasing, `with_result` not increasing for 10 s (poll-to-poll comparison, source is live) | warn | The engine sees nobody. | Check the camera view, lighting and distance. (A file source ends: shown as "finished", not as a fault.) |
| `dropped` true on two consecutive heartbeats | warn | The engine is dropping frames. | Close other GPU work, lower the resolution (Guide). |
| `updated` older than 6 s, pid alive | error | The engine has stopped reporting (hung?). | Restart it. |
| pid gone, last `state` not `stopping` | error | The engine ended unexpectedly. | See the log: `<file>`. |
| a model with a download is missing | warn | `<n>` models are missing. | Models tab: Download missing. |
| destination is not this machine | info | `<stream>` sends to `<host>`: receivers there cannot be checked. | none |
| everything above is clear | ok | Nothing to do. | none |

Thresholds above (10 s, 6 s) are proposals; M3 tests them against real runs and records what was measured.

## 10. Security and privacy

- The monitor opens **no listening socket** and makes **no network request** except through `fetch-models` (https URLs from the engine's registry; size and SHA-256 are checked by the engine, as today).
- It runs as the user, never elevated. It writes only to its own settings file and, through the engine, to the models folder. It deletes run files of dead engines (after 60 s) and nothing else.
- It reads other processes' names and pids from the OS tables a standard user may read; it does not read their memory or their data.
- Run files and logs hold no frames, landmarks or credentials (section 6); log lines can contain file paths, and **Copy** says so.
- The program is **unsigned** (ADR-108): SmartScreen warns on first run, as for the engine. It is also built by the same workflow and published with a `.sha256`.

## 11. Budgets and acceptance (proposed; M0 confirms or replaces each)

| # | Criterion | How it is measured |
|---|---|---|
| A1 | **Kill criterion:** the engine with E1 and E2 on is not measurably slower than 0.1.2 on the same clip (`fps`, `infer_ms`, and the longest gap between two sent datagrams); if it is, the writes are made opt-in or the monitor is dropped from 0.1.3 | the same clip through 0.1.2 and 0.1.3, 5 runs each, GPU on, `time.perf_counter`; the longest inter-datagram gap read off the wire |
| A2 | A killed engine (`taskkill /f`) is shown as "ended unexpectedly" within 10 s; a clean stop as "stopped" | real engine, real kill |
| A3 | Receivers bound: a Python UDP socket on 8901 appears within 2 s of binding and disappears within 2 s of closing (shown for a test port in 11.1); the Unity Editor in Play mode appears as `Unity.exe` | real sockets; one real Unity run |
| A4 | A recycled pid does not show a dead engine as live | a fabricated run file with a live pid but another start time |
| A5 | `models --json` agrees with `verify` for every file in four states (ok, missing, custom, absent) | folder fixtures |
| A6 | Download missing in an empty folder ends with all 8 files `ok` and the table refreshed; a read-only folder gives a clear message | real network (as done for 0.1.2: 8 files, 48.8 MB), real read-only folder |
| A7 | Idle monitor with one engine running uses little CPU and memory: numbers recorded, budget set by the owner after M0 (no figure is claimed here) | `Get-Process` sampling over 5 minutes |
| A8 | Every row of section 9 has a test that fails when the row is mutated (rule removed, threshold moved, message swapped) | mutation run, as for the installer |
| A9 | The built monitor starts from a clean GitHub runner's output folder and shows the Overview, Models and Guide without an engine installed | workflow smoke step, plus a screenshot taken in a real session |
| A10 | The upgrade 0.1.2 to 0.1.3 (section 12, M6) keeps models, replaces files, updates registry and the machine variable, and the monitor shows 0.1.3 afterwards | the real installers, owner's elevated shell, `verify_installed_engine.ps1` |

### 11.1 Cost, measured in isolation (2026-10-17, this machine, Windows with Defender on, default settings)

`cost_probe.py` (a scratch script outside the repositories) timed each operation with `time.perf_counter`:

| Operation | n | mean | p99 | max |
|---|---|---|---|---|
| run file: 807 bytes, temporary file + `os.replace` | 2000 | 0.76 ms | 0.96 ms | 6.2 ms |
| the same, a reader reading the file as fast as it can | 2000 | 0.77 ms | 1.06 ms | 8.3 ms |
| log: append one heartbeat line + flush | 2000 | 0.005 ms | 0.012 ms | 0.09 ms |
| monitor poll: Windows UDP table (`GetExtendedUdpTable`, 31 sockets) | 300 | 0.083 ms | 0.097 ms | 0.37 ms |

Also measured: a test socket bound to `127.0.0.1:18901` **was found in the UDP table with its owner pid, and was gone 0.2 s after it closed**; **795 of the 2000 writes failed while the reader hammered the file** (hence the rule in E1). **What this says and does not say:** one status write per 2 s heartbeat costs about 0.8 ms of one thread, which is under 0.05 % of a second; the worst single write seen was 8 ms. It was NOT measured inside the engine's frame loop, under GPU load, with the real engine's 0.1.3 code, or with another antivirus; that is A1. The monitor sends **no network traffic of its own** (it reads local files and asks the operating system for its socket table) and touches neither the UDP data path nor the GPU; the idle cost of the monitor's own window is not measured (M0).

## 12. Plan

| Step | Content | Done when |
|---|---|---|
| **M0 spike** | a tkinter window built by Nuitka in the existing workflow shape; measure size, start time, idle CPU; check the SmartScreen and clean-machine behaviour | numbers recorded; the owner confirms the technology (Q2) |
| **M1 engine** | E1-E5 with tests (`tests/`): atomic write, stale cleanup, rotation and pruning, source redaction, `models --json` agrees with `verify`; A1 measured | engine tests green; the 0.1.2-versus-0.1.3 timing recorded |
| **M2 model** | `model.py` and `rules.py` with table-driven tests and mutation runs (A4, A5, A8) | tests and mutants as for the installer |
| **M3 views** | the four tabs; A2, A3 and A6 measured against the real engine and real sockets | numbers recorded |
| **M4 build** | build step and installer entry (if Q3 says so); the monitor in `engine-release.yml` (build, smoke, upload, attach); descriptor and `.sha256` for the monitor | a manual workflow run passes; A9 |
| **M5 docs** | `docs/ENGINE.md` (run file, log, `models --json`), `docs/INSTALL.md` (monitor, unsigned warning), the Guide texts, ADR, Unity repository notes | `release_check.py --release` silent |
| **M6 release and upgrade test** | tag `engine-v0.1.3`; install the 0.1.2 setup, then the 0.1.3 setup over it; `verify_installed_engine.ps1` before and after; uninstall; A10 | the checks of A10 pass; plan section written |

The Unity package family is bumped to 0.1.3 with the engine, by the owner's rule of one version across both repositories (section 24 of the plan). The packages need no code change: `EngineCompatibility` reads only the wire spec, which stays 1.0; the README gains a line about the monitor.

## 13. Questions for the owner

| # | Question | Proposed answer |
|---|---|---|
| Q1 | Is "bound receivers" an acceptable meaning of "clients connected" (section 4), or do you want a real client protocol? | acceptable for 0.1.3; a protocol is a separate, later, two-repository decision |
| Q2 | Technology: tkinter compiled with Nuitka (A), or Qt, or .NET? | A, after the M0 measurements |
| Q3 | Does the engine installer also install the monitor (a Start-menu shortcut, a ticked task), or is it only a separate download? | an optional, ticked task; also published as its own zip |
| Q4 | Logging on by default, with 2 MB x 5 files per process and 7 days of history of dead processes? | yes; `--no-log-file` for anyone who does not want files |
| Q5 | Run files and logs per user (`%LOCALAPPDATA%`), so a monitor sees only its own user's engines? | yes |
| Q6 | Must the monitor ever start or stop an engine, or change settings? | no in 0.1.3 (R4); revisit after use |
| Q7 | Guide language: English only? | English only for 0.1.3 |
| Q8 | Name: "Viitorx Tracking Monitor", exe `viitorx-tracking-monitor.exe`, versioned with the engine? | yes |

## 14. Risks and what is not known

- **The monitor's own size and start time are unmeasured** (M0). If a Nuitka tkinter program is unexpectedly large or slow, the technology decision changes.
- **OS UDP table:** `GetExtendedUdpTable` shows bound sockets and their owner pid; a plain loopback socket of the same user was found and dropped correctly (11.1). **Not tried:** a Unity process, other users' processes, `SO_REUSEADDR` sharing. Limits to confirm in M3: a socket bound with `SO_REUSEADDR` by several processes appears once per process; a receiver in another user's session or an elevated process may show an owner pid but not a name to a standard user (shown as "pid N").
- **The heartbeat is 2 s**, so every number on screen is up to 2 s old, and a stopped engine is noticed after at most one missed heartbeat plus the monitor's poll.
- **A process that crashes hard leaves a run file** (and its log): the monitor shows it as "ended unexpectedly" and cleans up; a machine that never runs the monitor keeps the files until the engine's next start prunes them (E2). The run file is tiny; the log is capped.
- **`--status` and the Unity hosts are unchanged:** a host that pipes the output still gets the same lines; the run file is additional and does not need `--status`. Not tested: that a host which holds an engine for hours produces a run file that stays correct for hours (M1 measures a run of the length of the longest clip; a long run is a separate measurement).
- **Face tracking on a real face and the webcam are not part of this specification**; the monitor reports what the engine does, it does not prove tracking quality (plan section 25 and ADR-108).
