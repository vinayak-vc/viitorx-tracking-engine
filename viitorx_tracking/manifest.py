"""`manifest.json`: the SHA-256 of every program file of a build, and the check against it at start.

The compiled engine is a normal software folder (the exe, dlls, libs). The check exists so that a user who deletes or edits a file by accident gets
a clear error naming the file instead of a crash somewhere inside a library. It is NOT tamper-proofing: a person who can change a program file can
change the manifest next to it (the build is unsigned for now). Models are excluded: the user may replace them (registry.py reports those).

    manifest.json = {"manifest_version": 1, "engine_version": "...", "files": {"relative/path": {"size": N, "sha256": "..."}, ...}}
"""
import hashlib
import json
import os

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

#: Never part of the check: the manifest itself, the user's models and their downloads in progress, logs, bytecode and caches.
EXCLUDED_DIRS = ("models", "__pycache__", "logs")
EXCLUDED_SUFFIXES = (".part", ".log", ".pyc")


def _excluded(relative):
    parts = relative.replace("\\", "/").split("/")
    if parts[-1] == MANIFEST_NAME and len(parts) == 1:
        return True
    if parts[0] in EXCLUDED_DIRS or any(p == "__pycache__" for p in parts):
        return True
    return parts[-1].endswith(EXCLUDED_SUFFIXES)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def program_files(root):
    """Every file under `root` that belongs to the program, as sorted forward-slash relative paths."""
    found = []
    for folder, dirs, files in os.walk(root):
        for name in files:
            relative = os.path.relpath(os.path.join(folder, name), root).replace("\\", "/")
            if not _excluded(relative):
                found.append(relative)
    return sorted(found)


def build(root, engine_version):
    """The manifest dict for the folder `root`."""
    files = {}
    for relative in program_files(root):
        path = os.path.join(root, relative)
        files[relative] = {"size": os.path.getsize(path), "sha256": _sha256(path)}
    return {"manifest_version": MANIFEST_VERSION, "engine_version": engine_version, "files": files}


def write(root, engine_version):
    """Write `manifest.json` into `root`. Returns the manifest."""
    manifest = build(root, engine_version)
    with open(os.path.join(root, MANIFEST_NAME), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    return manifest


class Problem:
    """One finding of `check`: kind is 'missing', 'modified' or 'unexpected'."""
    __slots__ = ("kind", "path")

    def __init__(self, kind, path):
        self.kind = kind
        self.path = path

    def __repr__(self):
        return "%s: %s" % (self.kind, self.path)


def load(root):
    """The manifest in `root`, or None when there is none (a source checkout has no manifest and is not checked)."""
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check(root, manifest=None, deep=True):
    """[Problem] for the folder against its manifest (empty = every program file is as built). `deep` False compares sizes only.
    Files that are present but not in the manifest are reported as 'unexpected': they are not errors for the engine (a user may add a folder),
    and `unexpected_is_error` is for the caller to decide."""
    manifest = manifest if manifest is not None else load(root)
    if manifest is None:
        return []
    problems = []
    listed = manifest.get("files", {})
    for relative, want in sorted(listed.items()):
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            problems.append(Problem("missing", relative))
            continue
        if os.path.getsize(path) != want["size"]:
            problems.append(Problem("modified", relative))
            continue
        if deep and _sha256(path) != want["sha256"]:
            problems.append(Problem("modified", relative))
    for relative in program_files(root):
        if relative not in listed:
            problems.append(Problem("unexpected", relative))
    return problems


def errors(problems):
    """The problems that stop the engine: a program file that is missing or changed. An extra file does not."""
    return [p for p in problems if p.kind in ("missing", "modified")]


def message(problems, root):
    """The error text for a host or a user: what is wrong, which files, what to do."""
    bad = errors(problems)
    lines = ["The program files in %s are not as they were built (%d problem%s):" % (root, len(bad), "" if len(bad) == 1 else "s")]
    for p in bad[:12]:
        lines.append("  %s  %s" % (p.kind.upper(), p.path))
    if len(bad) > 12:
        lines.append("  ... and %d more" % (len(bad) - 12))
    lines.append("Reinstall the engine (or restore those files). Models are not checked. To run anyway: --no-verify")
    return "\n".join(lines)
