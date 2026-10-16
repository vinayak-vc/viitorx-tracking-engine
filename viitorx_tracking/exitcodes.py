"""The engine's exit codes. A host (Unity, Unreal, a script) reads them to say why the engine stopped.

0   the engine ran and stopped normally: finished a file, `--seconds` elapsed, the host asked it to stop, or the host's process went away
2   a usage error, or a source or model problem the existing senders also report with 2 (a camera that will not open, a model that will not load)
20  a program file is missing or was modified (docs: `manifest.json`); nothing was started. `--no-verify` skips the check
21  a required model file is missing, or is not the file this engine was measured with and could not be loaded; nothing was started
130 interrupted (Ctrl+C)
"""

OK = 0
USAGE_OR_SOURCE = 2
FILES_MODIFIED = 20
MODELS_MISSING = 21
INTERRUPTED = 130
