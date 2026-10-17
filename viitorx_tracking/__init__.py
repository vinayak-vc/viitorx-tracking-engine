"""Viitorx Tracking Engine: one program with subcommands (hands, face, cutout, body, run, ...) that reads a camera, a LAN camera, a stream or a file
and sends the tracking wires of docs/WIRE.md over UDP.

This package is a LAYER over the sidecar's modules, not a move of them: the flat modules at the repository root (hand_*.py, face_*.py, cutout_*.py,
...) and the senders under tools/ are unchanged, so the Unity project that runs the Python source directly, the existing tests and the compiled
build all use the same code. What this package adds is what every host needs from an engine: one command line, the model registry and
`fetch-models`, the file check against `manifest.json`, one process running several pipelines on one camera (`run`), and the lifecycle a host
needs (`--parent-pid`, a clean exit on stdin close, a JSON status line, stable exit codes).
"""

#: The engine's own version (semver). The wires have their own: docs/WIRE.md.
__version__ = "0.1.2"

#: The wire spec this engine implements (docs/WIRE.md).
WIRE_SPEC_VERSION = "1.0"
