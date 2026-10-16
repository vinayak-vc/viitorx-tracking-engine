"""The `viitorx-tracking` command line.

    viitorx-tracking hands   [options]      the hands wire  (UDP 8901), one stream
    viitorx-tracking face    [options]      the face wire   (UDP 8902)
    viitorx-tracking cutout  [options]      the cutout wire (UDP 8903)
    viitorx-tracking body    [options]      the body wire   (UDP 8899), from a camera / LAN camera / stream / file (video_udp_sender's options)
    viitorx-tracking run     [source options] --stream hands [hands options] --stream face [face options] --stream cutout [cutout options]
                                            ONE process, ONE source, several streams (a webcam can be read by one program at a time)
    viitorx-tracking list-cameras           which camera indices open
    viitorx-tracking fetch-models [--only hands,face,cutout,person,body] [--check] [--models-dir DIR]
    viitorx-tracking verify                 the program files against manifest.json, and the models against the registry
    viitorx-tracking version [--json]       engine and wire-spec versions
    viitorx-tracking print-spec             the wire specification (docs/WIRE.md)

Engine options, accepted with any command: --no-verify | --verify {full,size,off}, --models-dir DIR, --parent-pid N, --exit-on-stdin-close, --status.
Every other option is the stream's own (the same as the stream's sender script: `viitorx-tracking hands --help`).
Exit codes: viitorx_tracking/exitcodes.py. A source that does not open or a model that does not load exits 2; a modified program file 20; a missing model 21.
"""
import argparse
import json
import os
import sys

from . import __version__, WIRE_SPEC_VERSION, bootstrap, exitcodes, fetch, lifecycle, manifest, multi, registry

USAGE = __doc__

STREAMS = ("hands", "face", "cutout")


def _engine_parser():
    ap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)  # --model (the body sender's) must not read as --models-dir
    ap.add_argument("--no-verify", action="store_true", help="skip the check of the program files against manifest.json")
    ap.add_argument("--verify", choices=("full", "size", "off"), default="full", help="how the program files are checked at start (default full)")
    ap.add_argument("--models-dir", default=None, help="where the model files are (default: models/ beside the program, or VIITORX_MODELS_DIR)")
    ap.add_argument("--parent-pid", type=int, default=None, help="stop when this process is gone (a host passes its own pid)")
    ap.add_argument("--exit-on-stdin-close", action="store_true", help="stop when standard input reaches end of file")
    ap.add_argument("--status", action="store_true", help="write VTSTATUS {json} lines to standard output (starting / ready / heartbeat / stopping / error)")
    return ap


def _split_engine_options(argv):
    """(engine options namespace, the remaining arguments)."""
    return _engine_parser().parse_known_args(argv)


def _stream_module(tag):
    """The sender module of a stream. Static imports, so a compiled build includes each."""
    if tag == "hands":
        import hands_udp_sender as m
    elif tag == "face":
        import face_udp_sender as m
    elif tag == "cutout":
        import cutout_udp_sender as m
    else:
        raise ValueError("unknown stream %r" % tag)
    return m


#: The model options of each stream: (argparse attribute, required, the registry group a missing file is fetched with).
MODEL_OPTIONS = {
    "hands": (("palm_model", True, "hands"), ("hand_model", True, "hands")),
    "face": (("detector_model", True, "face"), ("mesh_model", True, "face"), ("blendshape_model", "blendshapes", "face")),
    "cutout": (("general_model", True, "cutout"),),
}


def rebase_models(tag, parser, a, directory):
    """Point every model option the user did not set at `directory`. An option left at its default (or None) becomes <directory>/<its file name>."""
    for attr, _required, _group in MODEL_OPTIONS[tag]:
        if getattr(a, attr) == parser.get_default(attr):
            setattr(a, attr, os.path.join(directory, os.path.basename(getattr(a, attr))))
    if tag == "cutout":
        if getattr(a, "landscape_model") == parser.get_default("landscape_model"):
            a.landscape_model = os.path.join(directory, os.path.basename(a.landscape_model))
        if getattr(a, "detector_model", None) in (None, parser.get_default("detector_model")):
            a.detector_model = os.path.join(directory, "yolox_s.onnx")


def missing_models(tag, a):
    """[(path, group)] of required model files that do not exist, for the parsed arguments of one stream."""
    out = []
    for attr, required, group in MODEL_OPTIONS[tag]:
        need = required is True or (isinstance(required, str) and getattr(a, required, True))
        path = getattr(a, attr)
        if need and not os.path.isfile(path):
            out.append((path, group))
    return out


def _check_files(engine, out, root=None):
    """Exit code for the program-file check, or None when it passes or is not applicable (a source checkout has no manifest)."""
    mode = "off" if engine.no_verify else engine.verify
    if mode == "off":
        return None
    root = root if root is not None else bootstrap.ENGINE_ROOT
    loaded = manifest.load(root)
    if loaded is None:
        return None
    problems = manifest.check(root, loaded, deep=(mode == "full"))
    if manifest.errors(problems):
        out(manifest.message(problems, root))
        return exitcodes.FILES_MODIFIED
    return None


def _models_directory(engine):
    return registry.models_dir(bootstrap.ENGINE_ROOT, engine.models_dir)


def _lifecycle(engine):
    stop = lifecycle.StopSignal()
    status = lifecycle.StatusEmitter(enabled=engine.status or os.environ.get("VIITORX_STATUS") == "1")
    watch = lifecycle.ParentWatch(engine.parent_pid, stop) if engine.parent_pid else None
    if engine.exit_on_stdin_close:
        lifecycle.watch_stdin(stop)
    return stop, status, watch


def _split_streams(rest):
    """`run` arguments -> (common source arguments, [(stream name, its arguments)]). Everything before the first --stream is common."""
    head = []
    segments = []
    current = None
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "--stream":
            if i + 1 >= len(rest):
                raise ValueError("--stream needs a name: one of %s" % ", ".join(STREAMS))
            current = (rest[i + 1], [])
            segments.append(current)
            i += 2
            continue
        (current[1] if current is not None else head).append(token)
        i += 1
    return head, segments


#: Source options every stream shares; they are set once, before the first --stream.
_COMMON_DESTS = ("video", "rotate", "flip", "crop", "loop", "realtime", "seconds", "show", "camera_size", "camera_fps", "cache_dir")


def _build_streams(segments, head, engine, directory, out):
    """Parse each stream's options with its own parser, check the models, build the pipelines. Returns (streams, None) or (None, exit code)."""
    streams = []
    first = None
    ports = []
    for name, args in segments:
        if name not in STREAMS:
            out("unknown stream %r (one of %s)" % (name, ", ".join(STREAMS)))
            return None, exitcodes.USAGE_OR_SOURCE
        module = _stream_module(name)
        parser = module.build_parser()
        parser.prog = "viitorx-tracking %s" % name
        a = parser.parse_args(head + args)
        if first is None:
            first = a
        else:
            for dest in _COMMON_DESTS:
                if getattr(a, dest) != getattr(first, dest):
                    out("the source option --%s must be the same for every stream: set it once, before the first --stream" % dest.replace("_", "-"))
                    return None, exitcodes.USAGE_OR_SOURCE
        if a.port in ports:
            out("two streams were given the same UDP port (%d): one sender per port" % a.port)
            return None, exitcodes.USAGE_OR_SOURCE
        ports.append(a.port)
        rebase_models(name, parser, a, directory)
        missing = missing_models(name, a)
        if missing:
            groups = sorted(set(g for _p, g in missing))
            for path, _g in missing:
                out("[%s] ERROR a model file is missing: %s" % (name, path))
            out("Run: viitorx-tracking fetch-models --only %s%s" % (",".join(groups), (" --models-dir %s" % directory) if engine.models_dir else ""))
            return None, exitcodes.MODELS_MISSING
        try:
            pipeline = module.build_pipeline(a)
        except FileNotFoundError as e:
            out("[%s] ERROR cannot load the models: %s" % (name, e))
            return None, exitcodes.MODELS_MISSING
        except (ValueError, RuntimeError) as e:
            out("[%s] ERROR cannot load the models: %s" % (name, e))
            return None, exitcodes.USAGE_OR_SOURCE
        import vision_sender as VSND
        prefix = {"hands": "hd", "face": "fc", "cutout": "cu"}[name]
        session = module.SESSION_ID if hasattr(module, "SESSION_ID") else VSND.new_session_id(prefix)
        streams.append(multi.Stream(name, a, pipeline, session, module.wire_label(), module.WINDOW_NAME))
    return streams, None


def cmd_stream(command, rest, engine, out):
    """`hands` / `face` / `cutout`: one stream through the shared one-source loop."""
    code = _check_files(engine, out)
    if code is not None:
        return code
    stop, status, watch = _lifecycle(engine)
    streams, failure = _build_streams([(command, rest)], [], engine, _models_directory(engine), out)
    if failure is not None:
        status.emit("error", reason="models" if failure == exitcodes.MODELS_MISSING else "arguments")
        return failure
    return multi.run_many(streams[0].args, streams, stop, status, watch)


def cmd_run(rest, engine, out):
    code = _check_files(engine, out)
    if code is not None:
        return code
    try:
        head, segments = _split_streams(rest)
    except ValueError as e:
        out(str(e))
        return exitcodes.USAGE_OR_SOURCE
    if not segments:
        out("run needs at least one stream: viitorx-tracking run --video camera:0 --stream hands --stream face")
        return exitcodes.USAGE_OR_SOURCE
    if any(t == "--port" or t.startswith("--port=") for t in head):
        out("--port is a stream's own option: put it after the --stream it belongs to")
        return exitcodes.USAGE_OR_SOURCE
    names = [n for n, _ in segments]
    if len(set(names)) != len(names):
        out("each stream can be run once: %s" % ", ".join(names))
        return exitcodes.USAGE_OR_SOURCE
    stop, status, watch = _lifecycle(engine)
    streams, failure = _build_streams(segments, head, engine, _models_directory(engine), out)
    if failure is not None:
        status.emit("error", reason="models" if failure == exitcodes.MODELS_MISSING else "arguments")
        return failure
    return multi.run_many(streams[0].args, streams, stop, status, watch)


def cmd_body(rest, engine, out):
    """The body wire, through video_udp_sender (its own loop, pacing and options; it reads sys.argv)."""
    code = _check_files(engine, out)
    if code is not None:
        return code
    directory = _models_directory(engine)
    argv = list(rest)
    if not any(t == "--model" or t.startswith("--model=") for t in argv):
        chosen = None
        for name in ("rtmw3d-x-fp16.onnx", "rtmw3d-x.onnx"):
            if os.path.isfile(os.path.join(directory, name)):
                chosen = os.path.join(directory, name)
                break
        if chosen is None:
            out("[body] ERROR no body model: place rtmw3d-x-fp16.onnx (or rtmw3d-x.onnx) in %s, or pass --model PATH" % directory)
            return exitcodes.MODELS_MISSING
        argv = ["--model", chosen] + argv
    import video_udp_sender as VS
    saved = sys.argv
    sys.argv = ["viitorx-tracking body"] + argv
    try:
        return VS.main() or 0
    finally:
        sys.argv = saved


def cmd_list_cameras(out):
    import video_udp_sender as VS
    return VS.list_cameras() or 0


def cmd_fetch_models(rest, engine, out):
    ap = argparse.ArgumentParser(prog="viitorx-tracking fetch-models", description="Put the model files where the engine looks for them.")
    ap.add_argument("--only", default=None, help="comma-separated groups (%s); default: every group with a download" % ",".join(registry.GROUPS))
    ap.add_argument("--check", action="store_true", help="download nothing: report what is there")
    a = ap.parse_args(rest)
    groups = None
    if a.only:
        groups = [g.strip() for g in a.only.split(",") if g.strip()]
        for g in groups:
            if g not in registry.GROUPS:
                out("unknown group %r (one of %s)" % (g, ", ".join(registry.GROUPS)))
                return exitcodes.USAGE_OR_SOURCE
    directory = _models_directory(engine)
    out("models folder: %s" % directory)
    return fetch.run(directory, groups, a.check, out)


def cmd_verify(engine, out):
    root = bootstrap.ENGINE_ROOT
    loaded = manifest.load(root)
    code = exitcodes.OK
    if loaded is None:
        out("program files: no manifest.json here (a source checkout): not checked")
    else:
        problems = manifest.check(root, loaded, deep=True)
        if manifest.errors(problems):
            out(manifest.message(problems, root))
            code = exitcodes.FILES_MODIFIED
        else:
            out("program files: all %d are as built (engine %s)" % (len(loaded.get("files", {})), loaded.get("engine_version")))
            extra = [p for p in problems if p.kind == "unexpected"]
            if extra:
                out("  (%d extra file%s present, not an error)" % (len(extra), "" if len(extra) == 1 else "s"))
    directory = _models_directory(engine)
    out("models folder: %s" % directory)
    missing_required = 0
    for spec, state, _path in registry.status(directory):
        out("  %-8s %-40s %s" % (state.upper(), spec.name, spec.group))
        if state == registry.MISSING and spec.required:
            missing_required += 1
    if code == exitcodes.OK and missing_required:
        out("%d required model file%s missing: run viitorx-tracking fetch-models" % (missing_required, "" if missing_required == 1 else "s"))
        code = exitcodes.MODELS_MISSING
    return code


def cmd_version(rest, out):
    info = {"engine": __version__, "wire_spec": WIRE_SPEC_VERSION, "wires": {"body": 0, "hands": 1, "face": 1, "cutout": 1},
            "ports": {"body": 8899, "hands": 8901, "face": 8902, "cutout": 8903}}
    if "--json" in rest:
        out(json.dumps(info, sort_keys=True))
    else:
        out("viitorx-tracking %s (wire spec %s; hands ver 1, face ver 1, cutout ver 1, body unversioned)" % (__version__, WIRE_SPEC_VERSION))
    return exitcodes.OK


def cmd_print_spec(out):
    path = os.path.join(bootstrap.ENGINE_ROOT, "docs", "WIRE.md")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            out(f.read())
        return exitcodes.OK
    out("docs/WIRE.md is not next to this program. Wire spec %s: body UDP 8899 (no ver), hands 8901 (ver 1), face 8902 (ver 1), cutout 8903 (ver 1)." % WIRE_SPEC_VERSION)
    return exitcodes.OK


def main(argv=None, out=None):
    out = out if out is not None else (lambda text: print(text, flush=True))
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        out(USAGE)
        return exitcodes.OK
    command = argv[0]
    engine, rest = _split_engine_options(argv[1:])
    bootstrap.prepare()
    if command in STREAMS:
        return cmd_stream(command, rest, engine, out)
    if command == "run":
        return cmd_run(rest, engine, out)
    if command == "body":
        return cmd_body(rest, engine, out)
    if command == "list-cameras":
        return cmd_list_cameras(out)
    if command == "fetch-models":
        return cmd_fetch_models(rest, engine, out)
    if command == "verify":
        return cmd_verify(engine, out)
    if command == "version":
        return cmd_version(rest, out)
    if command == "print-spec":
        return cmd_print_spec(out)
    out("unknown command: %s" % command)
    out(USAGE)
    return exitcodes.USAGE_OR_SOURCE


if __name__ == "__main__":
    sys.exit(main())
