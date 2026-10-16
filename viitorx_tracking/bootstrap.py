"""What has to be true before any sender module is imported.

  * the sidecar's modules are importable (in the repository tree; in a compiled build they are already part of the program), and
  * `depthai` is importable. The vision senders reach `wholebody_udp_sender` through `vision_sender` -> `video_udp_sender`, and that module does
    `import depthai` at module level although nothing on the engine's paths ever uses it (OAK-D is not part of the engine). Where the real package is
    not installed (a vision-only install, the compiled build) a stand-in is registered so the import succeeds, and any attempt to USE it fails with a
    sentence that says why. Where the real package IS installed (a developer with an OAK-D) it is left alone.
"""
import os
import sys
import types

#: The repository root (the folder that holds `evidence_paths.py`), or the program folder of a compiled build.
ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _depthai_stand_in():
    module = types.ModuleType("depthai")
    module.__doc__ = "Stand-in for the OAK-D package (the Viitorx Tracking Engine has no OAK-D support)."
    module.__file__ = "<viitorx_tracking stand-in>"
    module.__viitorx_stand_in__ = True

    def _refuse(name):
        raise RuntimeError("OAK-D (depthai) is not part of the Viitorx Tracking Engine: %r" % name)

    module.__getattr__ = _refuse
    return module


def ensure_depthai():
    """Make `import depthai` succeed. Returns True when a stand-in was installed, False when the real package (or an earlier stand-in) is there."""
    existing = sys.modules.get("depthai")
    if existing is not None:
        return bool(getattr(existing, "__viitorx_stand_in__", False))
    try:
        import depthai  # noqa: F401
        return False
    except ImportError:
        sys.modules["depthai"] = _depthai_stand_in()
        return True


def ensure_sidecar_path():
    """Put the sidecar root and every tools/ group on sys.path (a no-op in a compiled build, where the modules are built in)."""
    if os.path.isfile(os.path.join(ENGINE_ROOT, "_sidecar_path.py")):
        if ENGINE_ROOT not in sys.path:
            sys.path.insert(0, ENGINE_ROOT)
        import _sidecar_path  # noqa: F401


def prepare():
    """Everything above, once. Returns whether a depthai stand-in was installed."""
    ensure_sidecar_path()
    return ensure_depthai()
