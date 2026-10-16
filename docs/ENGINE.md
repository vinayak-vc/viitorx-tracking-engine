# Viitorx Tracking Engine: the command line (`viitorx_tracking`)

One program, several subcommands, for any host. It reads a camera, a LAN camera, a stream or a file and sends the tracking wires of [`WIRE.md`](WIRE.md) over UDP.
Today it runs from the source tree (`python -m viitorx_tracking <command>` with the sidecar's `.venv`); the compiled `viitorx-tracking.exe` (phase 4) is the same code
behind the same command line.

```text
viitorx-tracking hands   [options]     the hands wire  (UDP 8901)
viitorx-tracking face    [options]     the face wire   (UDP 8902)
viitorx-tracking cutout  [options]     the cutout wire (UDP 8903)
viitorx-tracking body    [options]     the body wire   (UDP 8899), webcam / LAN camera / stream / file (video_udp_sender's options)
viitorx-tracking run     [source options] --stream hands [hands options] --stream face [face options] --stream cutout [cutout options]
viitorx-tracking list-cameras          which camera indices open (this opens each camera briefly)
viitorx-tracking fetch-models [--only hands,face,cutout,person,body] [--check]
viitorx-tracking verify                the program files against manifest.json, and the models against the registry
viitorx-tracking version [--json]
viitorx-tracking print-spec            the wire specification
```

`hands`, `face` and `cutout` take exactly the options of their sender scripts (`tools/hands/hands_udp_sender.py`, `tools/face/face_udp_sender.py`,
`tools/cutout/cutout_udp_sender.py`): `viitorx-tracking hands --help`. Those scripts are unchanged and still work on their own.

## Engine options (any command)

| Option | Meaning |
|---|---|
| `--no-verify` / `--verify {full,size,off}` | how the program files are checked at start against `manifest.json` (default `full`; a source checkout has no manifest and is not checked) |
| `--models-dir DIR` | where the model files are (default `models/` beside the program, or the `VIITORX_MODELS_DIR` environment variable) |
| `--parent-pid N` | stop when process N is gone: a host passes its own pid, so a crashed host does not leave a camera held. Checked about once a second |
| `--exit-on-stdin-close` | stop when standard input reaches end of file |
| `--status` | write `VTSTATUS {json}` lines to standard output (or set `VIITORX_STATUS=1`) |

## One camera, several streams: `run`

A webcam (and a LAN-camera viewer) can be read by one program at a time, so the three senders cannot run side by side. `run` opens the source once, orients each frame once,
and gives every frame to every stream, each sending to its own port:

```text
viitorx-tracking run --video camera:0 --flip --stream hands --port 8901 --max-hands 2 --stream face --port 8902 --stream cutout --port 8903
```

- Everything before the first `--stream` is a **source option** (`--video`, `--rotate`, `--flip`, `--crop`, `--loop`, `--realtime`, `--seconds`, `--show`, `--camera-size`, `--camera-fps`, `--cache-dir`); it is set once.
  A source option repeated with a different value inside a stream is refused. `--port` is a stream's own option, set after its `--stream`.
- After `--stream NAME` come that stream's own options (`--max-hands`, `--max-faces`, `--color jpeg`, ...), the same as its sender's.
- A pipeline that raises is reported, and after 30 errors in a row dropped; the other streams carry on. When none is left the engine exits 2.
- Cost: about the **sum** of the streams (measured on an idle RTX 3060, per frame excluding decoding: hands 3-5 ms, face 2-7 ms, cutout 25-30 ms; together 26-33 fps on a 720p to 1080p source).
  **A real run on a 1080x1920 file, including decoding it, held 18.6 fps for hands + face + cutout** (`123.webm`, 186 frames in 10 s): decoding a large file is not free, and a webcam at 720p decodes faster.
  Face with a real face costs more than the clips measured (an earlier measurement says about 8 ms).

## Models

Registry: `viitorx_tracking/registry.py` (name, URL, exact size and SHA-256 of each file; a test keeps it equal to the `tools/model/fetch_*.py` scripts). `fetch-models` downloads only those https URLs, to a `.part` file,
refuses a stream longer than the expected size, and replaces the target only when the size and SHA-256 match. It downloads nothing that is already right; `--check` downloads nothing at all.

- A file the **user replaced** (different size or hash) is reported `CUSTOM` and left alone: replacing a model is allowed, and the engine does not know whether the new one fits.
- The body model (`rtmw3d-x-fp16.onnx` or `rtmw3d-x.onnx`) has no URL: place it yourself. `body` picks the fp16 file first.
- A missing model is exit **21**, with the command that fixes it (`fetch-models --only hands`), before any camera is opened.

## The file check

A build carries `manifest.json` (the SHA-256 of every program file; `viitorx_tracking/manifest.py` writes it). At start the engine checks the files against it and **stops with exit 20 and the names of the missing or changed files**;
extra files are reported by `verify` but are not errors; models are not checked. `--no-verify` skips it. This is protection against accidents, not tamper-proofing: whoever can edit a program file can edit the manifest beside it, and the build is unsigned.

## Exit codes (`viitorx_tracking/exitcodes.py`)

| Code | Meaning |
|---|---|
| 0 | stopped normally: a file ended, `--seconds` elapsed, the host asked, or the host's process went away |
| 2 | usage error, or a source or model problem (a camera that will not open, a model that will not load); also every stream failed |
| 20 | a program file is missing or was modified (nothing was started) |
| 21 | a required model file is missing (nothing was started) |
| 130 | interrupted (Ctrl+C) |

## The status line

With `--status`, each of these is one line `VTSTATUS {json}` on standard output, among the human log lines: `starting`, `ready`, `heartbeat` (one per stream, about every 2 s), `stopping` (with the exit `code`), `error` (with `reason`: `source`, `models`, `arguments`, `pipeline`, `stream_dropped`).
Every record has `event`, `engine` (version), `pid` and `t` (unix seconds). A host reads them with `lifecycle.parse_status_line` or any JSON parser.

## How it relates to the existing code

`viitorx_tracking` is a layer over the sidecar's modules, not a move of them: the flat modules at the repository root and `tools/` are unchanged apart from one addition (each sender gained `build_pipeline(args)`, which its own `main()` now calls, so
the engine builds the same pipelines). `tools/video/vision_sender.run` still runs the single-stream scripts; `viitorx_tracking/multi.py` is its generalisation to a list of pipelines (the stream's bytes on the wire are the same either way).
`depthai` is not needed: `bootstrap.ensure_depthai()` installs a stand-in only where the real package is absent (the OAK-D import in `wholebody_udp_sender.py` is never used on the engine's paths).

## What is not done (phase 3 scope notes)

- **pip packaging and the compiled exe**: the senders' helper modules live under `tools/`, which `pyproject.toml` does not ship; phase 4 stages them flat for the compiled build. Until then run `python -m viitorx_tracking` from the source tree.
- **`body` is a pass-through** to `video_udp_sender.py` (its own loop, pacing and options, no `VTSTATUS` lines, no `--parent-pid`); it is not part of `run`.
- **`--parent-pid` while waiting for a live source** is noticed after the source's read timeout (a few seconds), not within one.
- **Model shape check** (a replaced model is only reported by size and hash, not checked against the network's expected inputs and outputs): not built.
- **Not run on a live camera**: every run so far read video files (and the loop's source handling is the senders' own).
