"""Build the compiled Viitorx Tracking Engine: a one-folder Windows distribution (`viitorx-tracking.exe` plus its libraries, no Python source).

    python build/build_engine.py check      [--python PY]                       what would be built, and whether the build Python has everything
    python build/build_engine.py stage      [--python PY] [--out DIR]           the flat copy that is compiled
    python build/build_engine.py compile    [--python PY] [--out DIR] [--jobs N]  stage + Nuitka (about 15 minutes cold)
    python build/build_engine.py assemble   [--out DIR]                         notices, manifest.json, zip + SHA-256 for the folder `compile` made
    python build/build_engine.py installer  [--out DIR] [--iscc PATH]           the Inno Setup installer (admin, Program Files)
    python build/build_engine.py all        [...]                               compile, assemble, installer

`--python` is the interpreter of the BUILD environment (Python 3.10 with `requirements-build.txt` installed and Nuitka); default: the one running this script.
`--out` defaults to `build/out` (git-ignored). Nothing in the repository is changed: the build reads the repository and writes only under `--out`.

Why a flat stage: the sidecar's modules sit at the repository root and under `tools/<group>/`, and reach each other through `_sidecar_path` (a sys.path prelude).
A compiled program has no such tree, so every module the engine imports (found by reading the syntax trees, lazy imports included: a plain import scan misses them)
is copied side by side, and Nuitka compiles them into the program.
"""
import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from viitorx_tracking import __version__, WIRE_SPEC_VERSION, manifest, registry  # noqa: E402

EXE_NAME = "viitorx-tracking.exe"
DIST_NAME = "viitorx-tracking"
ENTRY = "engine_entry"
COMPANY = "ViitorCloud"
PRODUCT = "Viitorx Tracking Engine"

#: Folders of the repository that never hold a module the engine imports (research output, harnesses, tests, vendored OAK-D port, documents).
SKIP_DIRS = frozenset((".venv", "evidence", "tests", "__pycache__", "depthai_blazepose", "armaim", "capture", "diagnostics", "ownership",
                       "validation", "deployment", "build", "docs", "scripts", "models", "f19_evidence", "pipeline_logs", "viitorx_tracking"))

#: Imported by the senders at module level but never used on the engine's paths (OAK-D). `bootstrap.ensure_depthai` installs a stand-in at run time.
STAND_IN = frozenset(("depthai",))

#: Documents the compiled folder carries (`print-spec` reads docs/WIRE.md).
SHIPPED_DOCS = ("WIRE.md", "ENGINE.md")

#: Files and folders of the compiled folder that are removed, with the measured reason. Empty until a trim has been measured to be safe.
TRIM = ()


class BuildError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------------------------------------------
# the import closure
# ---------------------------------------------------------------------------------------------------------------------------------

def module_index(root):
    """{module name: [paths]} of every .py outside SKIP_DIRS, at the root and below."""
    index = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".py"):
                index.setdefault(name[:-3], []).append(os.path.join(folder, name))
    return index


def parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def imports_of(path):
    """(top-level names imported anywhere in the file, lazy ones included; relative-import count; dynamic-import call lines)."""
    tree = parse(path)
    names, relative, dynamic = set(), 0, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative += 1
            elif node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            f = node.func
            called = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if called in ("import_module", "__import__"):
                dynamic.append(node.lineno)
    return names, relative, dynamic


def package_seeds(root, index):
    """The modules the engine package itself imports: the closure starts there."""
    seeds = set()
    folder = os.path.join(root, "viitorx_tracking")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            names, _, _ = imports_of(os.path.join(folder, name))
            seeds.update(n for n in names if n in index)
    return sorted(seeds)


def closure(root, index=None, seeds=None):
    """The modules to compile, with what was found while walking them:
    {"modules": {name: path}, "third_party": set, "ambiguous": {name: [paths]}, "relative": {name: count}, "dynamic": {name: [lines]}}."""
    index = index if index is not None else module_index(root)
    queue = list(seeds if seeds is not None else package_seeds(root, index))
    found = {"modules": {}, "third_party": set(), "ambiguous": {}, "relative": {}, "dynamic": {}}
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    while queue:
        name = queue.pop()
        if name in found["modules"]:
            continue
        paths = index[name]
        if len(paths) > 1:
            found["ambiguous"][name] = paths
        found["modules"][name] = paths[0]
        names, relative, dynamic = imports_of(paths[0])
        if relative:
            found["relative"][name] = relative
        if dynamic:
            found["dynamic"][name] = dynamic
        for dep in sorted(names):
            if dep in index:
                queue.append(dep)
            elif dep not in stdlib and dep != "viitorx_tracking":
                found["third_party"].add(dep)
    return found


def third_party_of_package(root):
    """Third-party top-level names the engine package itself imports (the closure walk covers the sidecar modules)."""
    index = module_index(root)
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    found = set()
    folder = os.path.join(root, "viitorx_tracking")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            names, _, _ = imports_of(os.path.join(folder, name))
            found.update(n for n in names if n not in index and n not in stdlib and n != "viitorx_tracking")
    return found


def missing_in(python, names):
    """The names `python` cannot import (checked without importing them: a spec lookup)."""
    if not names:
        return []
    code = "import importlib.util,json,sys; print(json.dumps([n for n in json.loads(sys.argv[1]) if importlib.util.find_spec(n) is None]))"
    out = subprocess.run([python, "-I", "-c", code, json.dumps(sorted(names))], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def report(root, python, out=print):
    """Walk the closure, print it, and return (closure, problems [str])."""
    found = closure(root)
    problems = []
    for name, paths in sorted(found["ambiguous"].items()):
        problems.append("module name %r exists %d times (%s): the flat stage cannot hold both" % (name, len(paths), ", ".join(paths)))
    for name, count in sorted(found["relative"].items()):
        problems.append("module %r uses %d relative import(s): the flat stage has no package for them" % (name, count))
    third = found["third_party"] | third_party_of_package(root)
    absent = [n for n in missing_in(python, third) if n not in STAND_IN]
    for n in absent:
        problems.append("the build Python cannot import %r (install requirements-build.txt)" % n)
    out("closure: %d modules" % len(found["modules"]))
    out("  " + " ".join(sorted(found["modules"])))
    out("third-party imports: " + " ".join(sorted(third)))
    out("stand-in at run time: " + " ".join(sorted(third & STAND_IN)))
    for name, lines in sorted(found["dynamic"].items()):
        out("NOTE dynamic import in %s at line(s) %s (not followed by a scan: check the build runs that path)" % (name, lines))
    for p in problems:
        out("PROBLEM " + p)
    return found, problems


# ---------------------------------------------------------------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------------------------------------------------------------

ENTRY_SOURCE = '''"""Entry point of the compiled viitorx-tracking.exe (generated by build/build_engine.py)."""
import sys

from viitorx_tracking.cli import main

if __name__ == "__main__":
    sys.exit(main())
'''


def stage(root, python, stage_dir, out=print):
    """Write the flat copy that is compiled. Returns the closure. Raises BuildError on any problem."""
    found, problems = report(root, python, out)
    if problems:
        raise BuildError("; ".join(problems))
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    os.makedirs(stage_dir)
    for name, path in sorted(found["modules"].items()):
        shutil.copyfile(path, os.path.join(stage_dir, name + ".py"))
    shutil.copytree(os.path.join(root, "viitorx_tracking"), os.path.join(stage_dir, "viitorx_tracking"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(stage_dir, ENTRY + ".py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(ENTRY_SOURCE)
    os.makedirs(os.path.join(stage_dir, "docs"))
    for doc in SHIPPED_DOCS:
        shutil.copyfile(os.path.join(root, "docs", doc), os.path.join(stage_dir, "docs", doc))
    out("staged %d modules + the engine package into %s" % (len(found["modules"]), stage_dir))
    return found


# ---------------------------------------------------------------------------------------------------------------------------------
# compiling
# ---------------------------------------------------------------------------------------------------------------------------------

def four_part_version(version):
    """'0.1.0' -> '0.1.0.0'; '1.2.3-rc1' -> '1.2.3.0' (Windows file versions are four integers)."""
    core = version.split("-")[0].split("+")[0]
    parts = [int(p) for p in core.split(".")]
    if len(parts) > 4 or any(p < 0 or p > 65535 for p in parts):
        raise BuildError("version %r cannot be a Windows file version" % version)
    return ".".join(str(p) for p in (parts + [0] * 4)[:4])


def nuitka_command(python, stage_dir, nuitka_out, jobs=4, msvc="latest"):
    """The Nuitka command line. `--standalone` writes <nuitka_out>/<entry>.dist."""
    version = four_part_version(__version__)
    return [python, "-m", "nuitka", "--standalone", "--msvc=" + msvc, "--assume-yes-for-downloads",
            "--output-filename=" + EXE_NAME, "--output-dir=" + nuitka_out, "--jobs=%d" % jobs,
            "--windows-console-mode=force",
            "--include-package=viitorx_tracking",
            "--company-name=" + COMPANY, "--product-name=" + PRODUCT, "--file-description=" + PRODUCT,
            "--file-version=" + version, "--product-version=" + version,
            "--include-data-dir=%s=docs" % os.path.join(stage_dir, "docs"),
            os.path.join(stage_dir, ENTRY + ".py")]


def compile_engine(root, python, out_dir, jobs, msvc, out=print):
    """stage + Nuitka. Returns the folder Nuitka wrote (<out>/nuitka/<entry>.dist)."""
    stage_dir = os.path.join(out_dir, "stage")
    nuitka_out = os.path.join(out_dir, "nuitka")
    stage(root, python, stage_dir, out)
    os.makedirs(nuitka_out, exist_ok=True)
    cmd = nuitka_command(python, stage_dir, nuitka_out, jobs, msvc)
    log = os.path.join(out_dir, "nuitka.log")
    out("nuitka: %s" % " ".join(cmd))
    started = time.time()
    with open(log, "w", encoding="utf-8") as f:
        code = subprocess.call(cmd, cwd=stage_dir, stdout=f, stderr=subprocess.STDOUT)
    out("nuitka exit %d after %.0f s (log: %s)" % (code, time.time() - started, log))
    dist = os.path.join(nuitka_out, ENTRY + ".dist")
    if code != 0 or not os.path.isfile(os.path.join(dist, EXE_NAME)):
        raise BuildError("Nuitka failed (exit %d); see %s" % (code, log))
    return dist


# ---------------------------------------------------------------------------------------------------------------------------------
# assembling: trim, notices, manifest, zip
# ---------------------------------------------------------------------------------------------------------------------------------

def folder_size(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(path) for f in files)


def apply_trim(folder, trim=TRIM, out=print):
    """Delete each (relative path, reason) of `trim` that exists; returns the bytes removed. A path that is not there is reported, not an error."""
    removed = 0
    for relative, reason in trim:
        path = os.path.join(folder, relative)
        if os.path.isdir(path):
            size = folder_size(path)
            shutil.rmtree(path)
        elif os.path.isfile(path):
            size = os.path.getsize(path)
            os.remove(path)
        else:
            out("trim: %s is not in the folder (%s)" % (relative, reason))
            continue
        removed += size
        out("trim: removed %s (%d bytes): %s" % (relative, size, reason))
    return removed


NOTICES_CODE = r'''
import importlib.metadata as M, json, sys
rows = []
for name in json.loads(sys.argv[1]):
    try:
        d = M.distribution(name)
    except M.PackageNotFoundError:
        rows.append({"name": name, "version": None})
        continue
    meta = d.metadata
    lic = (meta.get("License-Expression") or meta.get("License") or "").strip()
    if not lic or len(lic) > 120 or "\n" in lic:
        classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = ", ".join(classifiers) if classifiers else (lic.splitlines()[0][:100] + " ..." if lic else "")
    home = meta.get("Home-page") or ""
    if not home:
        for u in (meta.get_all("Project-URL") or []):
            home = u.split(",", 1)[-1].strip()
            break
    rows.append({"name": meta["Name"], "version": d.version, "license": lic or "(not stated in the package metadata)", "home": home})
print(json.dumps(rows))
'''


def runtime_requirements():
    """Package names of requirements-build.txt that are shipped (everything pinned there; Nuitka itself is a build tool and is not listed)."""
    names = []
    with open(os.path.join(HERE, "requirements-build.txt"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line and "==" in line:
                names.append(line.split("==")[0].strip())
    return names


def notices_text(rows, models):
    lines = ["Viitorx Tracking Engine %s: third-party notices" % __version__, "",
             "This folder contains the compiled engine and the libraries below (versions as built). The license field is what each package's own metadata states;",
             "the full license texts are in each package's distribution. This list is generated by build/build_engine.py and is to be reviewed before a public release.", ""]
    for r in sorted(rows, key=lambda r: r["name"].lower()):
        if r.get("version") is None:
            lines.append("%s: NOT INSTALLED in the build environment" % r["name"])
        else:
            lines.append("%s %s  -  %s%s" % (r["name"], r["version"], r["license"], ("  -  " + r["home"]) if r["home"] else ""))
    lines += ["", "Models are NOT part of this folder. `viitorx-tracking fetch-models` downloads them (each under its own license, from the URL below);",
              "the body model is supplied by the user.", ""]
    for m in models:
        lines.append("%s  <-  %s" % (m.name, m.url if m.url else "(supplied by the user)"))
    lines.append("")
    return "\n".join(lines)


def write_notices(folder, python):
    code = subprocess.run([python, "-I", "-c", NOTICES_CODE, json.dumps(runtime_requirements())], capture_output=True, text=True, check=True).stdout
    rows = json.loads(code)
    models = [spec for group in registry.GROUPS for spec in registry.specs_for(group)]
    with open(os.path.join(folder, "THIRD_PARTY_NOTICES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(notices_text(rows, models))
    return rows


MODELS_README = """Model files go here (replace any of them with your own network if it has the same inputs and outputs).

    viitorx-tracking fetch-models              download the hands, face and cutout models (checks size and SHA-256)
    viitorx-tracking fetch-models --check      only report what is missing

The body model (rtmw3d-x-fp16.onnx or rtmw3d-x.onnx) is not downloaded: place it here yourself.
This folder is not covered by the file check (manifest.json).
"""


def assemble(dist, final_dir, python, out_dir, out=print, base_url=""):
    """Turn Nuitka's folder into the release folder `final_dir`, a zip with its SHA-256 and the release descriptor Unity reads. Returns (zip path, sha256)."""
    if os.path.isdir(final_dir):
        shutil.rmtree(final_dir)
    shutil.copytree(dist, final_dir)
    apply_trim(final_dir, TRIM, out)
    shutil.copyfile(os.path.join(REPO, "LICENSE"), os.path.join(final_dir, "LICENSE"))
    write_notices(final_dir, python)
    os.makedirs(os.path.join(final_dir, "models"), exist_ok=True)
    with open(os.path.join(final_dir, "models", "README.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(MODELS_README)
    m = manifest.write(final_dir, __version__)
    out("manifest.json: %d program files" % len(m["files"]))
    bad = manifest.errors(manifest.check(final_dir))
    if bad:
        raise BuildError("the folder does not match its own manifest: %r" % bad)
    zip_path = os.path.join(out_dir, "viitorx-tracking-%s-win-x64.zip" % __version__)
    sha = make_zip(final_dir, zip_path, DIST_NAME)
    out("zip: %s (%d bytes) sha256 %s" % (zip_path, os.path.getsize(zip_path), sha))
    descriptor = write_release_descriptor(zip_path, sha, final_dir, base_url)
    out("release descriptor: %s%s" % (descriptor, "" if base_url else " (no --base-url: its url is empty, fill it in where the zip is published)"))
    return zip_path, sha


def write_release_descriptor(zip_path, sha, final_dir, base_url=""):
    """Write `<zip name without .zip>.release.json` beside the zip: what an installer needs to fetch and check this release (the Unity packages' EngineInstaller reads it).
    `url` is `base_url` + the zip's name (empty when no base URL was given: the publisher fills it in). The SHA-256 and size are those of the zip as built."""
    name = os.path.basename(zip_path)
    unpacked = 0
    for d, _dirs, files in os.walk(final_dir):
        for f in files:
            unpacked += os.path.getsize(os.path.join(d, f))
    base = base_url.rstrip("/") + "/" if base_url else ""
    info = {
        "schema": 1,
        "name": DIST_NAME,
        "version": __version__,
        "platform": "win-x64",
        "file": name,
        "url": base + name if base else "",
        "sha256": sha,
        "sizeBytes": os.path.getsize(zip_path),
        "unpackedBytes": unpacked,
        "topFolder": DIST_NAME,
        "exe": EXE_NAME,
        "wireSpec": WIRE_SPEC_VERSION,
    }
    path = zip_path[:-4] + ".release.json" if zip_path.endswith(".zip") else zip_path + ".release.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
        f.write("\n")
    return path


def make_zip(folder, zip_path, top):
    """Zip `folder` as `top/...` (sorted, so equal folders give equal archives apart from timestamps). Writes `<zip>.sha256` and returns the hex digest."""
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(d, name)
                z.write(full, top + "/" + os.path.relpath(full, folder).replace("\\", "/"))
    digest = manifest._sha256(zip_path)
    with open(zip_path + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write("%s  %s\n" % (digest, os.path.basename(zip_path)))
    return digest


# ---------------------------------------------------------------------------------------------------------------------------------
# the installer
# ---------------------------------------------------------------------------------------------------------------------------------

def find_iscc(explicit=None):
    candidates = [explicit, os.environ.get("ISCC")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
            candidates.append(os.path.join(base, "Programs", "Inno Setup 6", "ISCC.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise BuildError("ISCC.exe (Inno Setup 6) not found: pass --iscc PATH or set ISCC")


def installer_command(iscc, final_dir, out_dir):
    return [iscc, "/Qp", "/DSourceDir=" + final_dir, "/DAppVersion=" + __version__, "/DFileVersion=" + four_part_version(__version__),
            "/DOutputDir=" + out_dir, os.path.join(HERE, "installer.iss")]


def build_installer(final_dir, out_dir, iscc, out=print):
    cmd = installer_command(iscc, final_dir, out_dir)
    out("iscc: %s" % " ".join(cmd))
    code = subprocess.call(cmd)
    path = os.path.join(out_dir, "viitorx-tracking-setup-%s.exe" % __version__)
    if code != 0 or not os.path.isfile(path):
        raise BuildError("Inno Setup failed (exit %d)" % code)
    digest = manifest._sha256(path)
    with open(path + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write("%s  %s\n" % (digest, os.path.basename(path)))
    out("installer: %s (%d bytes) sha256 %s" % (path, os.path.getsize(path), digest))
    return path, digest


# ---------------------------------------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("check", "stage", "compile", "assemble", "installer", "all"))
    ap.add_argument("--python", default=sys.executable, help="the build environment's Python 3.10 (default: this interpreter)")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--msvc", default="latest")
    ap.add_argument("--iscc", default=None)
    ap.add_argument("--base-url", default="", help="where the zip will be published (the release descriptor's url is this + the zip's name); empty leaves url empty")
    a = ap.parse_args(argv)
    out_dir = os.path.abspath(a.out)
    final_dir = os.path.join(out_dir, DIST_NAME)
    os.makedirs(out_dir, exist_ok=True)
    try:
        if a.step == "check":
            _, problems = report(REPO, a.python)
            return 1 if problems else 0
        if a.step == "stage":
            stage(REPO, a.python, os.path.join(out_dir, "stage"))
        if a.step in ("compile", "all"):
            compile_engine(REPO, a.python, out_dir, a.jobs, a.msvc)
        if a.step in ("assemble", "all"):
            assemble(os.path.join(out_dir, "nuitka", ENTRY + ".dist"), final_dir, a.python, out_dir, base_url=a.base_url)
        if a.step in ("installer", "all"):
            build_installer(final_dir, out_dir, find_iscc(a.iscc))
    except BuildError as e:
        print("BUILD FAILED: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
