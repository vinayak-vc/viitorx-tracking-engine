"""ONE PROCESS, ONE SOURCE, SEVERAL PIPELINES.

A webcam (and a LAN-camera viewer) can be read by one program at a time, so the hands, face and cutout senders cannot run side by side on one camera.
This is the frame loop of `tools/video/vision_sender.run`, generalised from one pipeline to a list: the source is opened ONCE, each frame is oriented
once, and every pipeline processes it and sends its own datagram(s) to its own port. A pipeline is the same object the single-stream senders use
(process / encode / to_bytes / render / heartbeat_extra / banner / restart), so a stream's bytes on the wire are the same whether it runs alone or here.

The cost of the combination is about the SUM of its parts (measured on an idle GPU, phase 1: hands 3-5 ms, face 2-7 ms, cutout 25-30 ms per frame, together
26-33 fps on a 720p to 1080p source). A pipeline that raises is reported and, after MAX_CONSECUTIVE_ERRORS in a row, dropped; the others carry on. When
none is left the loop ends with exit code 2.

`tools/video/vision_sender.run` is left exactly as it was: the single-stream senders keep their own loop.
"""
import socket
import sys
import time

from . import exitcodes

MAX_CONSECUTIVE_ERRORS = 30


class Stream(object):
    """One pipeline and where it sends. `args` is that stream's parsed argparse namespace (it supplies host, port and the stream's own flags)."""

    def __init__(self, tag, args, pipeline, session_id, wire_label, window_name):
        self.tag = tag
        self.args = args
        self.pipeline = pipeline
        self.session_id = session_id
        self.wire_label = wire_label
        self.window_name = window_name
        self.frames = 0
        self.sent = 0
        self.with_result = 0
        self.infer_ms = 0.0
        self.errors_in_a_row = 0
        self.dropped = False
        self.seq = int(time.time() * 100) % 100000000
        self.sock = None
        self.last_beat = 0.0


def _vsnd():
    import vision_sender
    return vision_sender


def run_many(common, streams, stop, status, parent_watch=None, open_source=None, orient=None, print_fn=None, clock=time.time, perf=time.perf_counter):
    """The whole sender around `streams`. `common` holds the shared source options (video, rotate, flip, crop, loop, realtime, seconds, show, ...).
    Returns the process exit code. `stop` is a lifecycle.StopSignal; `status` a lifecycle.StatusEmitter; the rest are injectable for tests."""
    out = print_fn if print_fn is not None else (lambda text: print(text, flush=True))
    vs = _vsnd()
    open_source = open_source if open_source is not None else vs.open_source
    orient = orient if orient is not None else vs.VS.orient_frame
    tags = ",".join(s.tag for s in streams)

    status.emit("starting", streams=[s.tag for s in streams], video=vs.VS.redact(common.video))
    try:
        kind, live, reader, cap, label = open_source(common.video, common)
    except Exception as e:
        out("[engine] ERROR cannot use --video %r: %s" % (vs.VS.redact(common.video), e))
        status.emit("error", reason="source", detail=str(e))
        return exitcodes.USAGE_OR_SOURCE
    out("[engine]   streams     : %s" % tags)
    out("[engine]   source      : %s  (%s)" % (label, "LIVE: newest frame only" if live else "file"))
    for s in streams:
        for line in s.pipeline.banner():
            out("[%s]   %s" % (s.tag, line))
        s.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out("[%s]   sending to  : %s:%d  %s" % (s.tag, s.args.host, s.args.port, s.wire_label))

    file_fps = cap.get(5) if cap is not None else 0.0  # 5 = cv2.CAP_PROP_FPS
    pacer = vs.FilePacer(file_fps, enabled=(not live and common.realtime))
    t_run = clock()
    result_code = exitcodes.OK
    heartbeat_s = vs.HEARTBEAT_SECONDS
    frames_total = 0

    def heartbeat(s, note=""):
        el = max(1e-6, clock() - t_run)
        extra = ""
        if reader is not None:
            extra = " live=%s dropped=%d reconnects=%d" % ("Y" if reader.connected else "n", reader.dropped, reader.reconnects)
        out("[%s] frames=%d sent=%d with_result=%d fps~%.1f infer=%.1fms %s%s %s"
            % (s.tag, s.frames, s.sent, s.with_result, s.sent / el, s.infer_ms, s.pipeline.heartbeat_extra(), extra, note))
        status.emit("heartbeat", stream=s.tag, frames=s.frames, sent=s.sent, with_result=s.with_result, fps=round(s.sent / el, 1),
                    infer_ms=round(s.infer_ms, 1), dropped=bool(s.dropped))

    for s in streams:
        heartbeat(s, "ready")
    status.emit("ready", streams=[s.tag for s in streams], source=kind, live=bool(live))
    try:
        while True:
            if parent_watch is not None:
                parent_watch.poll()
            if stop.is_set():
                out("[engine] stopping: %s" % stop.reason)
                break
            if common.seconds > 0 and clock() - t_run >= common.seconds:
                break
            if all(s.dropped for s in streams):
                out("[engine] ERROR every stream has failed; stopping")
                result_code = exitcodes.USAGE_OR_SOURCE
                break
            t_grab = None
            if live:
                frame, t_grab = reader.read(heartbeat_s)
                if frame is None and getattr(reader, "fatal", None):
                    out("[engine] ERROR the LAN camera cannot continue: %s" % reader.fatal)
                    status.emit("error", reason="source", detail=str(reader.fatal))
                    result_code = exitcodes.USAGE_OR_SOURCE
                    break
                if frame is None:
                    for s in streams:
                        if clock() - s.last_beat >= heartbeat_s:
                            heartbeat(s, "waiting for frames" if reader.frames_read == 0 else "no new frame")
                            s.last_beat = clock()
                    continue
            else:
                behind = pacer.wait()
                ok, frame = cap.read()
                if not ok:
                    if not common.loop:
                        break
                    cap.set(1, 0)  # cv2.CAP_PROP_POS_FRAMES
                    pacer.restart()
                    for s in streams:
                        s.pipeline.restart()
                    ok, frame = cap.read()
                    if not ok:
                        out("[engine] ERROR %s produced no frames" % label)
                        result_code = exitcodes.USAGE_OR_SOURCE
                        break
                for _ in range(min(behind, 3)):    # a file that cannot keep up drops frames, not time
                    cap.grab()
                t_grab = clock()

            if common.crop:
                frame = frame[common.crop[1]:common.crop[3], common.crop[0]:common.crop[2]]
            frame = orient(frame, common.rotate, common.flip)
            for s in streams:
                if s.dropped:
                    continue
                t0 = perf()
                try:
                    result = s.pipeline.process(frame)
                    cost_ms = (perf() - t0) * 1000.0
                    s.infer_ms = cost_ms if s.frames == 0 else 0.9 * s.infer_ms + 0.1 * cost_ms
                    s.frames += 1
                    if len(result) > 0:
                        s.with_result += 1
                    s.seq += 1
                    now = clock()
                    ctx = vs.SendContext(s.session_id, s.seq, now, (now - t_grab) * 1000.0, kind, frame.shape[1], frame.shape[0], common.flip)
                    payload = s.pipeline.to_bytes(s.pipeline.encode(result, ctx))
                    addr = (s.args.host, s.args.port)
                    if isinstance(payload, (bytes, bytearray)):
                        s.sock.sendto(payload, addr)
                    else:
                        for datagram in payload:
                            s.sock.sendto(datagram, addr)
                    s.sent += 1
                    s.errors_in_a_row = 0
                except Exception as e:
                    s.errors_in_a_row += 1
                    if s.errors_in_a_row == 1:
                        out("[%s] ERROR %s: %s" % (s.tag, type(e).__name__, e))
                        status.emit("error", reason="pipeline", stream=s.tag, detail="%s: %s" % (type(e).__name__, e))
                    if s.errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                        s.dropped = True
                        out("[%s] dropped after %d errors in a row; the other streams carry on" % (s.tag, s.errors_in_a_row))
                        status.emit("error", reason="stream_dropped", stream=s.tag)
            if frames_total == 0 and not live:
                # The first frame pays the models' warm-up (DirectML compiles its graphs); without this the pacer sees a deficit it can only
                # repay by dropping frames.
                pacer.restart()
            frames_total += 1

            if common.show:
                import cv2
                for s in streams:
                    if s.dropped:
                        continue
                    hud = vs.Hud(0.0, s.infer_ms, kind, common.flip, s.sent)
                    cv2.imshow(s.window_name, s.pipeline.render(frame, [], hud))
                if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                    break

            for s in streams:
                if clock() - s.last_beat >= heartbeat_s:
                    heartbeat(s)
                    s.last_beat = clock()
    except KeyboardInterrupt:
        result_code = exitcodes.INTERRUPTED
    finally:
        for s in streams:
            heartbeat(s, "stopping")
        status.emit("stopping", streams=[s.tag for s in streams], code=result_code)
        if reader is not None:
            reader.stop()
        if cap is not None:
            cap.release()
        for s in streams:
            if s.sock is not None:
                s.sock.close()
        if common.show:
            import cv2
            cv2.destroyAllWindows()
    return result_code
