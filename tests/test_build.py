#!/usr/bin/env python3
"""Phase 4 tests: the build scripts (build/build_engine.py, build/installer.iss): the import closure of the flat stage, the Nuitka command line, trimming,
notices, the zip and its SHA-256, the release folder's own manifest, the installer script (statically, and compiled by Inno Setup when it is installed).

Nothing here compiles the engine (that takes 15 minutes: tests/verify_build.py checks the result of a real build). Fakes are small temp folders.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "build"))

import build_engine as B  # noqa: E402
from viitorx_tracking import manifest  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-76s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-76s %s" % (label, detail))


def tmpdir():
    return tempfile.mkdtemp(prefix="vt_build_")


def write(path, text="", binary=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary is not None:
        with open(path, "wb") as f:
            f.write(binary)
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def fake_repo(files):
    root = tmpdir()
    write(os.path.join(root, "viitorx_tracking", "__init__.py"), "")
    for relative, text in files.items():
        write(os.path.join(root, relative), text)
    return root


# ------------------------------------------------------------------------------------------------------------------------------ closure
print("CLOSURE: which modules are compiled")
root = fake_repo({
    "viitorx_tracking/cli.py": "import alpha\nimport os\n",
    "alpha.py": "import beta\nimport numpy\n\ndef f():\n    import gamma\n    from delta import x\n",
    "beta.py": "import json\n",
    "gamma.py": "import cv2\n",
    "tools/group/delta.py": "import socket\n",
    "tools/group/unused.py": "import nothing\n",
    "tests/test_alpha.py": "import alpha\nimport pytest_thing\n",
    "evidence/alpha_out.py": "import junk\n",
    "build/b.py": "import junk2\n",
})
index = B.module_index(root)
check(set(index) == {"alpha", "beta", "gamma", "delta", "unused"}, "the index skips tests, evidence, build and the engine package itself", " ".join(sorted(index)))
found = B.closure(root)
check(sorted(found["modules"]) == ["alpha", "beta", "delta", "gamma"], "the closure follows imports, lazy ones inside functions included", " ".join(sorted(found["modules"])))
check("unused" not in found["modules"], "a module nothing imports is left out")
check(found["third_party"] == {"numpy", "cv2"}, "third-party names are collected, the standard library is not", str(sorted(found["third_party"])))
check(not found["ambiguous"] and not found["relative"] and not found["dynamic"], "a clean tree reports nothing")
check(B.third_party_of_package(root) == set(), "the engine package's own imports: only the standard library and the closure here")

root2 = fake_repo({
    "viitorx_tracking/cli.py": "import alpha\nimport requests\n",
    "alpha.py": "import importlib\nfrom . import sibling\nmod = importlib.import_module('x')\nz = __import__('y')\n",
    "tools/a/same.py": "", "tools/b/same.py": "", "beta.py": "import same\n",
})
root2_alpha = os.path.join(root2, "alpha.py")
write(root2_alpha, open(root2_alpha, encoding="utf-8").read() + "import beta\n")
found2 = B.closure(root2)
check("same" in found2["ambiguous"] and len(found2["ambiguous"]["same"]) == 2, "two modules with one name are reported (a flat stage cannot hold both)")
check(found2["relative"] == {"alpha": 1}, "a relative import is reported")
check(found2["dynamic"] == {"alpha": [3, 4]}, "dynamic imports are reported with their lines", str(found2["dynamic"]))
check(B.third_party_of_package(root2) == {"requests"}, "a third-party import in the engine package is seen")

print("CLOSURE: the real repository")
real = B.closure(ROOT)
for name in ("hands_udp_sender", "face_udp_sender", "cutout_udp_sender", "video_udp_sender", "vision_sender", "host_person_detector", "host_multiperson",
             "lan_camera", "evidence_paths", "_sidecar_path", "rtmw3d_pose", "frame_orientation"):
    check(name in real["modules"], "the engine's closure includes %s" % name)
check(not real["ambiguous"] and not real["relative"], "no ambiguous module names, no relative imports in the real tree")
check(real["third_party"] <= {"aiohttp", "aiortc", "cv2", "depthai", "numpy", "onnxruntime", "socketio", "av"}, "the real tree's third-party set is the one the build environment installs", str(sorted(real["third_party"])))
check(not any(n.startswith(("f23_", "f60_", "make_")) or n.endswith("_eval") for n in real["modules"]), "no research harness is pulled into the engine")
check(os.path.isfile(os.path.join(ROOT, "build", "requirements-build.txt")), "requirements-build.txt exists")
names = [n.lower().replace("_", "-") for n in B.runtime_requirements()]
check("onnxruntime-directml" in names and "onnxruntime" not in names and "depthai" not in names, "the build set has DirectML onnxruntime, not the plain one, and no depthai")
for top, dist in (("cv2", "opencv-python"), ("numpy", "numpy"), ("onnxruntime", "onnxruntime-directml"), ("aiortc", "aiortc"), ("aiohttp", "aiohttp"), ("socketio", "python-socketio")):
    check(dist in names, "%s (import %s) is a pinned requirement" % (dist, top))
pins = [l.strip() for l in open(os.path.join(ROOT, "build", "requirements-build.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")]
check(all("==" in p for p in pins), "every build requirement is pinned exactly")
lock = {l.split("==")[0].strip().lower(): l.split("==")[1].strip() for l in open(os.path.join(ROOT, "requirements.lock.txt"), encoding="utf-8") if "==" in l and not l.startswith("#")}
drift = [p for p in pins if p.split("==")[0].lower() in lock and lock[p.split("==")[0].lower()] != p.split("==")[1] and p.split("==")[0].lower() != "protobuf"]
check(not drift, "the build pins equal requirements.lock.txt where both list a package (protobuf is the known difference)", str(drift))

print("STAGE: the flat copy")
saved_missing = B.missing_in
B.missing_in = lambda python, names: []
try:
    out_lines = []
    stage_dir = os.path.join(tmpdir(), "stage")
    found3 = B.stage(ROOT, sys.executable, stage_dir, out_lines.append)
    files = set(os.listdir(stage_dir))
    check(all(m + ".py" in files for m in found3["modules"]), "every closure module is a .py beside the entry")
    check(B.ENTRY + ".py" in files and "viitorx_tracking" in files and "docs" in files, "entry, engine package and docs are staged")
    check(sorted(os.listdir(os.path.join(stage_dir, "docs"))) == ["ENGINE.md", "WIRE.md"], "the shipped docs are WIRE.md and ENGINE.md (print-spec reads WIRE.md)", str(os.listdir(os.path.join(stage_dir, "docs"))))
    write(os.path.join(stage_dir, "stray_from_an_earlier_build.py"), "x = 1\n")
    B.stage(ROOT, sys.executable, stage_dir, lambda s: None)
    check(not os.path.exists(os.path.join(stage_dir, "stray_from_an_earlier_build.py")), "staging again starts from an empty stage (a module dropped from the closure does not linger)")
    check(not any("__pycache__" in d for d, _, _ in os.walk(stage_dir)), "no bytecode is staged")
    same = all(open(os.path.join(stage_dir, m + ".py"), "rb").read() == open(p, "rb").read() for m, p in found3["modules"].items())
    check(same, "staged modules are byte-identical to the repository's")
    check(open(os.path.join(stage_dir, B.ENTRY + ".py"), encoding="utf-8").read() == B.ENTRY_SOURCE, "the entry is the generated one")
    # The flat stage imports on its own: the senders and the CLI resolve from the stage and nothing comes from the repository.
    def probe_code(modules):
        return ("import sys, os, importlib; stage = sys.argv[1]; sys.path.insert(0, stage)\n"
                "from viitorx_tracking import bootstrap; bootstrap.prepare()\n"
                "mods = [importlib.import_module(n) for n in %r]\n"
                "print(all(os.path.abspath(m.__file__).startswith(os.path.abspath(stage)) for m in mods))" % (modules,))
    senders = ["hands_udp_sender", "face_udp_sender", "cutout_udp_sender", "video_udp_sender", "vision_sender", "viitorx_tracking.cli"]
    probe = subprocess.run([sys.executable, "-B", "-c", probe_code(senders), stage_dir], cwd=stage_dir, capture_output=True, text=True, timeout=120)
    check(probe.returncode == 0 and probe.stdout.strip() == "True", "the flat stage imports every sender and the CLI, all from the stage",
          (probe.stderr.strip().splitlines() or [probe.stdout.strip()])[-1][:120])
    # A module the closure missed would make the probe fail: remove one and see.
    os.remove(os.path.join(stage_dir, "host_person_detector.py"))
    probe2 = subprocess.run([sys.executable, "-B", "-c", probe_code(senders + ["host_person_detector"]), stage_dir], cwd=stage_dir, capture_output=True, text=True, timeout=120)
    check(probe2.returncode != 0, "the probe notices a missing module (mutation check of the probe itself)")
finally:
    B.missing_in = saved_missing

print("STAGE: refuses a problem")
root4 = fake_repo({"viitorx_tracking/cli.py": "import a\n", "a.py": "import b\n", "tools/x/b.py": "", "tools/y/b.py": ""})
saved_missing = B.missing_in
B.missing_in = lambda python, names: []
try:
    try:
        B.stage(root4, sys.executable, os.path.join(tmpdir(), "s"), lambda s: None)
        refused = False
    except B.BuildError as e:
        refused = "exists 2 times" in str(e)
    check(refused, "an ambiguous module name stops the stage with a BuildError")
finally:
    B.missing_in = saved_missing
check(B.missing_in(sys.executable, ["os", "json", "definitely_not_a_module_xyz"]) == ["definitely_not_a_module_xyz"], "missing_in reports only what the build Python cannot import")
check(B.missing_in(sys.executable, []) == [], "missing_in of nothing is nothing")

# ------------------------------------------------------------------------------------------------------------------------------ nuitka
print("NUITKA: the command line")
check(B.four_part_version("2") == "2.0.0.0" and B.four_part_version("2.1") == "2.1.0.0", "a short version is padded to four parts")
check(B.four_part_version("0.1.0") == "0.1.0.0" and B.four_part_version("1.2.3-rc1") == "1.2.3.0" and B.four_part_version("2.0.0.7") == "2.0.0.7", "a semver becomes a four-part file version")
for bad in ("1.2.3.4.5", "70000.0.0"):
    try:
        B.four_part_version(bad)
        ok = False
    except B.BuildError:
        ok = True
    check(ok, "version %r is refused" % bad)
cmd = B.nuitka_command("py.exe", "S:/stage", "S:/out", jobs=3, msvc="14.3")
check(cmd[:3] == ["py.exe", "-m", "nuitka"] and "--standalone" in cmd, "it runs Nuitka standalone with the given Python")
check("--output-filename=viitorx-tracking.exe" in cmd and "--jobs=3" in cmd and "--msvc=14.3" in cmd, "exe name, jobs and compiler are passed")
check("--include-package=viitorx_tracking" in cmd, "the engine package is included whole")
check(cmd[-1].replace("\\", "/") == "S:/stage/engine_entry.py", "the generated entry is the main script")
check(any(a.startswith("--include-data-dir=") and a.endswith("=docs") for a in cmd), "docs/WIRE.md is shipped as data")
check(any(a == "--file-version=" + B.four_part_version(B.__version__) for a in cmd) and "--windows-console-mode=force" in cmd, "Windows version info is set and the exe is a console program")
check("--assume-yes-for-downloads" in cmd and not any(a.startswith("--assume-yes-for-downloads=") for a in cmd), "the downloads flag takes no value (the first spike run died on `=yes`)")
check(not any("depthai" in a for a in cmd), "depthai is not forced into the build")

# ------------------------------------------------------------------------------------------------------------------------------ trim
print("TRIM")
folder = tmpdir()
write(os.path.join(folder, "a", "big.dll"), binary=b"x" * 1000)
write(os.path.join(folder, "b.txt"), binary=b"y" * 10)
write(os.path.join(folder, "keep.dll"), binary=b"z" * 5)
lines = []
removed = B.apply_trim(folder, (("a", "folder"), ("b.txt", "file"), ("nothere.dll", "absent")), lines.append)
check(removed == 1010 and not os.path.exists(os.path.join(folder, "a")) and not os.path.exists(os.path.join(folder, "b.txt")), "listed folders and files are removed and the bytes counted", str(removed))
check(os.path.exists(os.path.join(folder, "keep.dll")), "an unlisted file stays")
check(any("nothere.dll is not in the folder" in l for l in lines), "a listed path that is absent is reported, not an error")
check(B.apply_trim(folder, (), lambda s: None) == 0, "no trim list removes nothing")
check(isinstance(B.TRIM, tuple) and all(len(t) == 2 and t[1] for t in B.TRIM), "every real trim entry has a stated reason")

# ------------------------------------------------------------------------------------------------------------------------------ notices
print("NOTICES")
text = B.notices_text([{"name": "numpy", "version": "1.26.4", "license": "BSD-3-Clause", "home": "https://numpy.org"},
                       {"name": "Missing", "version": None}], [type("S", (), {"name": "m.onnx", "url": "https://x/m.onnx"})(), type("S", (), {"name": "body.onnx", "url": None})()])
check("numpy 1.26.4  -  BSD-3-Clause  -  https://numpy.org" in text, "a package line has name, version, license and home page")
check("Missing: NOT INSTALLED in the build environment" in text, "a package missing from the build environment is said, not hidden")
check("m.onnx  <-  https://x/m.onnx" in text and "body.onnx  <-  (supplied by the user)" in text, "each model's source (or 'supplied by the user') is listed")
real_rows = json.loads(subprocess.run([sys.executable, "-I", "-c", B.NOTICES_CODE, json.dumps(["numpy", "definitely-not-installed-xyz"])], capture_output=True, text=True, check=True).stdout)
check(real_rows[0]["version"] and real_rows[0]["license"] and real_rows[1]["version"] is None, "metadata lookup: a real package has version and license, a missing one is None", str(real_rows[0]))

# ------------------------------------------------------------------------------------------------------------------------------ zip
print("ZIP")
folder = tmpdir()
write(os.path.join(folder, "viitorx-tracking.exe"), binary=b"MZ" + b"0" * 100)
write(os.path.join(folder, "lib", "a.dll"), binary=b"a" * 500)
write(os.path.join(folder, "models", "m.onnx"), binary=b"m" * 500)
zpath = os.path.join(tmpdir(), "e.zip")
digest = B.make_zip(folder, zpath, "viitorx-tracking")
with zipfile.ZipFile(zpath) as z:
    names = z.namelist()
check(all(n.startswith("viitorx-tracking/") for n in names), "every entry has the top folder prefix")
check(set(names) == {"viitorx-tracking/viitorx-tracking.exe", "viitorx-tracking/lib/a.dll", "viitorx-tracking/models/m.onnx"}, "the archive holds exactly the folder's files, forward slashes")
check(digest == hashlib.sha256(open(zpath, "rb").read()).hexdigest(), "the returned digest is the archive's SHA-256")
check(open(zpath + ".sha256", encoding="utf-8").read() == "%s  e.zip\n" % digest, "the .sha256 file is `<hex>  <name>`")
digest2 = B.make_zip(folder, zpath, "viitorx-tracking")
with zipfile.ZipFile(zpath) as z:
    check(z.testzip() is None and digest2 != "", "the archive passes its own CRC test")

# ------------------------------------------------------------------------------------------------------------------------------ assemble
print("ASSEMBLE: the release folder")
dist = tmpdir()
write(os.path.join(dist, B.EXE_NAME), binary=b"MZ" + b"1" * 200)
write(os.path.join(dist, "dll", "x.dll"), binary=b"d" * 300)
write(os.path.join(dist, "docs", "WIRE.md"), "# spec\n")
out_dir = tmpdir()
final_dir = os.path.join(out_dir, B.DIST_NAME)
lines = []
zip_path, sha = B.assemble(dist, final_dir, sys.executable, out_dir, lines.append)
check(os.path.isfile(os.path.join(final_dir, "LICENSE")) and os.path.isfile(os.path.join(final_dir, "THIRD_PARTY_NOTICES.txt")), "LICENSE and THIRD_PARTY_NOTICES.txt are in the folder")
check(os.path.isfile(os.path.join(final_dir, "models", "README.txt")), "models/ exists with its README")
m = manifest.load(final_dir)
check(m is not None and m["engine_version"] == B.__version__ and B.EXE_NAME in m["files"] and "dll/x.dll" in m["files"], "manifest.json lists the program files")
check("models/README.txt" not in m["files"] and "manifest.json" not in m["files"], "the models folder and the manifest itself are outside the manifest")
check("LICENSE" in m["files"] and "THIRD_PARTY_NOTICES.txt" in m["files"], "the notices are covered by the manifest")
check(manifest.errors(manifest.check(final_dir)) == [], "the folder passes its own check")
check(os.path.basename(zip_path) == "viitorx-tracking-%s-win-x64.zip" % B.__version__ and os.path.isfile(zip_path + ".sha256"), "the zip is named by version and has a .sha256")
with zipfile.ZipFile(zip_path) as z:
    check("viitorx-tracking/manifest.json" in z.namelist(), "the zip carries manifest.json")
desc_path = zip_path[:-4] + ".release.json"
check(os.path.isfile(desc_path), "a release descriptor is written beside the zip")
desc = json.load(open(desc_path, encoding="utf-8"))
check(desc["schema"] == 1 and desc["version"] == B.__version__ and desc["file"] == os.path.basename(zip_path) and desc["platform"] == "win-x64", "descriptor: schema, version, file name, platform")
check(desc["sha256"] == sha == hashlib.sha256(open(zip_path, "rb").read()).hexdigest(), "descriptor: the SHA-256 is the zip's own")
check(desc["sizeBytes"] == os.path.getsize(zip_path) and desc["unpackedBytes"] == sum(os.path.getsize(os.path.join(d, f)) for d, _x, fs in os.walk(final_dir) for f in fs), "descriptor: zip size and unpacked size are measured")
check(desc["topFolder"] == "viitorx-tracking" and desc["exe"] == B.EXE_NAME and desc["wireSpec"] == B.WIRE_SPEC_VERSION, "descriptor: top folder, exe name, wire spec")
check(desc["url"] == "", "descriptor: without a base URL the url is empty (not guessed)")
zp2, sha2 = B.assemble(dist, final_dir, sys.executable, out_dir, lambda s: None, base_url="https://example.invalid/releases/v1/")
check(json.load(open(zp2[:-4] + ".release.json", encoding="utf-8"))["url"] == "https://example.invalid/releases/v1/" + os.path.basename(zp2), "descriptor: url = base URL + zip name, one slash")
os.remove(os.path.join(final_dir, "dll", "x.dll"))
check([p.kind for p in manifest.errors(manifest.check(final_dir))] == ["missing"], "deleting a program file after the build is caught by the check (the start-up check's input)")
# a second assemble over an existing folder starts clean
write(os.path.join(final_dir, "stray.txt"), "stray")
B.assemble(dist, final_dir, sys.executable, out_dir, lambda s: None)
check(not os.path.exists(os.path.join(final_dir, "stray.txt")), "assembling again replaces the folder instead of adding to it")

# ------------------------------------------------------------------------------------------------------------------------------ installer
print("INSTALLER: the script")
iss = open(os.path.join(ROOT, "build", "installer.iss"), encoding="utf-8").read()
check("PrivilegesRequired=admin" in iss, "it asks for administrator rights (Program Files)")
check("DefaultDirName={autopf}\\Viitorx\\TrackingEngine" in iss, "the default folder is under Program Files")
check('Name: "{app}\\models"; Permissions: users-modify' in iss, "models\\ is writable by every user")
check("ArchitecturesInstallIn64BitMode=x64compatible" in iss, "it installs as 64-bit")
check("AppId={{6F0C3E2A-8B54-4D1E-9A7B-5C2D8E41F7A3}" in iss, "AppId is the fixed one (a later setup upgrades this install)")
check("VIITORX_TRACKING_ENGINE" in iss and "InstallDir" in iss, "the install folder is recorded for hosts (environment variable and registry)")
check('Tasks: fetchmodels' in iss and 'Name: "fetchmodels"' in iss and "Flags: unchecked" in iss, "downloading models is an unchecked option, not a surprise")
check('Type: filesandordirs; Name: "{app}\\models"' in iss, "uninstall removes the models folder")
check("SignTool" not in iss, "the installer is unsigned (no signing step is claimed)")
cmd = B.installer_command("ISCC.exe", "F:/f", "O:/o")
check(cmd[0] == "ISCC.exe" and "/DSourceDir=F:/f" in cmd and "/DAppVersion=" + B.__version__ in cmd and "/DOutputDir=O:/o" in cmd and cmd[-1].endswith("installer.iss"), "the Inno Setup command passes folder, version and output")

try:
    iscc = B.find_iscc()
except B.BuildError:
    iscc = None
print("INSTALLER: compiled by Inno Setup (%s)" % (iscc or "not installed: skipped"))
if iscc:
    src = tmpdir()
    write(os.path.join(src, B.EXE_NAME), binary=b"MZ" + b"2" * 2000)
    write(os.path.join(src, "LICENSE"), "licence text\n")
    write(os.path.join(src, "lib", "a.dll"), binary=b"a" * 3000)
    write(os.path.join(src, "models", "README.txt"), "models\n")
    write(os.path.join(src, "models", "big.onnx"), binary=b"b" * 4000)
    outd = tmpdir()
    code = subprocess.call(B.installer_command(iscc, src, outd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    setup = os.path.join(outd, "viitorx-tracking-setup-%s.exe" % B.__version__)
    check(code == 0 and os.path.isfile(setup), "the script compiles", "%d bytes" % (os.path.getsize(setup) if os.path.isfile(setup) else 0))
    if os.path.isfile(setup):
        # The setup file's PE header carries the file version and the publisher.
        blob = open(setup, "rb").read()
        check(B.four_part_version(B.__version__).encode("utf-16-le") in blob or B.__version__.encode("utf-16-le") in blob, "the setup carries the version")
        check("ViitorCloud".encode("utf-16-le") in blob, "the setup carries the publisher")
    # mutation: a script without SourceDir must be refused by the compiler
    bad = subprocess.run([iscc, "/Qp", "/DOutputDir=" + outd, os.path.join(ROOT, "build", "installer.iss")], capture_output=True, text=True)
    check(bad.returncode != 0, "the compiler refuses the script when SourceDir is missing")

print("FIND_ISCC")
explicit = os.path.join(tmpdir(), "ISCC.exe")
write(explicit, binary=b"x")
check(B.find_iscc(explicit) == explicit, "an explicit path that exists is used")
saved_env = dict(os.environ)
try:
    os.environ["ProgramFiles(x86)"] = os.environ["ProgramFiles"] = os.environ["LOCALAPPDATA"] = os.path.join(tmpdir(), "none")
    os.environ.pop("ISCC", None)
    try:
        B.find_iscc()
        raised = False
    except B.BuildError as e:
        raised = "ISCC.exe" in str(e)
    check(raised, "no Inno Setup anywhere is a clear BuildError")
finally:
    os.environ.clear()
    os.environ.update(saved_env)

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
