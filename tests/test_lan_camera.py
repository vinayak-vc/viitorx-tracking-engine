#!/usr/bin/env python3
"""F-50 tests for lan_camera.py (the phone-over-WebRTC frame source) and its place in video_udp_sender.py.

THREE LAYERS, each only as heavy as what it needs to prove:

  1. PURE (always runs). Which links ARE a LAN camera and which are not; how a link becomes
     (server, room); that a PIN in a link is refused; where the PIN comes from; which join refusals are
     worth retrying; ICE-candidate parsing and queueing; the newest-frame-only slot; and the offer ->
     answer path against a REAL aiortc peer, including candidates that arrive BEFORE the offer.
  2. AGAINST THE REAL SERVER (runs when CAMERA_SERVER_DIR points at the lan-camera-streaming project).
     It starts that project's real Node server and drives LanCameraReader from a fake phone built on
     aiortc: frames arrive at the right size whichever side joins first, the phone leaving and coming
     back gets a fresh offer, a wrong PIN is one clear error that never echoes the PIN, a second viewer
     is retried rather than fatal, a slow consumer sees fresh frames, and stop() leaves the room.
  3. FULL CHAIN (also needs the model and Assets/Games/video/123.webm). The real video_udp_sender.py is
     launched exactly as Unity launches it - a viewer link in --video, the PIN only in the environment -
     against a fake phone that plays a real person, and the production datagram is received on a UDP port.

Run: .venv\\Scripts\\python.exe tests/test_lan_camera.py   (exit 0 on pass)
     set CAMERA_SERVER_DIR=D:\\path\\to\\lan-camera-streaming   to enable layers 2 and 3.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _sidecar_path  # noqa: F401

import lan_camera as L
import video_udp_sender as V

HERE = os.path.dirname(os.path.abspath(__file__))
SIDECAR_ROOT = os.path.dirname(HERE)
CLIP = os.path.normpath(os.path.join(SIDECAR_ROOT, "..", "..", "video", "123.webm"))
TEST_PIN = "482915"

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %-66s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-66s %s" % (label, detail))


def raises(fn, exc, fragment=""):
    try:
        fn()
    except exc as e:
        return fragment.lower() in str(e).lower()
    return False


# =====================================================================================================
print("is_lan_camera_link / parse_lan_url")
VIEWERS = [
    ("https://lan.shares.zrok.io/viewer?room=room-abc123", "https://lan.shares.zrok.io", "room-abc123"),
    ("http://192.168.1.5:3000/viewer?room=abc", "http://192.168.1.5:3000", "abc"),
    ("http://192.168.1.5:3000/viewer/?room=abc", "http://192.168.1.5:3000", "abc"),
    ("HTTPS://Example.COM/VIEWER?room=abc#frag", "https://example.com", "abc"),
    ("http://[::1]:3000/viewer?room=abc", "http://[::1]:3000", "abc"),
    ("lan:http://host:3000?room=abc", "http://host:3000", "abc"),
    ("lan: 192.168.1.5:3000?room=abc", "http://192.168.1.5:3000", "abc"),
    ('"http://h:3000/viewer?room=abc"', "http://h:3000", "abc"),
    ("http://user:secret@h:3000/viewer?room=abc", "http://h:3000", "abc"),
]
for link, base, room in VIEWERS:
    check(L.is_lan_camera_link(link), "recognised: %s" % V.redact(link))
    check(L.parse_lan_url(link) == (base, room), "-> (%s, %s)" % (base, room), str(L.parse_lan_url(link)))
check("secret" not in L.parse_lan_url("http://user:secret@h:3000/viewer?room=abc")[0],
      "credentials in the link never reach the base url")

NOT_LAN = [
    "http://192.168.1.5:8080/video",           # a phone MJPEG app: a stream, not a viewer
    "https://www.youtube.com/watch?v=abc",     # a page
    "http://example.com/clip.mp4",             # a file
    "http://192.168.1.5:3000/viewer",          # a viewer page with no room
    "http://192.168.1.5:3000/sender?room=abc", # the SENDER page is not a viewer
    "rtsp://cam/viewer?room=abc",              # wrong scheme
    "camera:0", "", "C:/clip.mp4",
]
for link in NOT_LAN:
    check(not L.is_lan_camera_link(link), "not a LAN camera: %r" % link)

check(raises(lambda: L.parse_lan_url("http://h/viewer?room=abc&pin=123456"), L.LanCameraError, "PIN"),
      "a PIN inside the link is REFUSED, not used")
check(raises(lambda: L.parse_lan_url("http://h/viewer"), L.LanCameraError, "no room"), "no room is refused")
check(raises(lambda: L.parse_lan_url("http://h/viewer?room=ab"), L.LanCameraError, "not valid"),
      "a 2-character room id is refused (the server needs 3-32)")
check(raises(lambda: L.parse_lan_url("http://h/viewer?room=has space"), L.LanCameraError, "not valid"),
      "a room id with a space is refused")
check(raises(lambda: L.parse_lan_url("ftp://h/viewer?room=abc"), L.LanCameraError, "http"), "ftp is refused")
check(raises(lambda: L.parse_lan_url("lan:"), L.LanCameraError, "empty"), "an empty lan: is refused")
check(raises(lambda: L.parse_lan_url("http://h:99999/viewer?room=abc"), L.LanCameraError, "port"),
      "an invalid port is refused")

print("")
print("classify_source puts a viewer link on the LAN source, ahead of 'stream' and 'page'")
for link, _, _ in VIEWERS[:6]:
    check(V.classify_source(link) == V.KIND_LAN, "classify: %s" % link)
check(V.KIND_LAN in V.LIVE_KINDS, "the LAN camera is a LIVE source (no loop, no pacing)")
check(V.classify_source("http://192.168.1.5:8080/video") == V.KIND_STREAM, "an MJPEG phone link is still a stream")
check(V.classify_source("https://www.youtube.com/watch?v=abc") == V.KIND_PAGE, "a watch page is still a page")
check(raises(lambda: V.classify_source("http://h/viewer?room=abc&pin=1"), V.SourceError, "PIN"),
      "classify refuses a link with a PIN in it")
check(raises(lambda: V.classify_source("http://h/viewer?room=a"), V.SourceError, "not valid"),
      "classify refuses an invalid room")

print("")
print("resolve_pin / repr / join errors")
check(L.resolve_pin({"CAMERA_PIN": " 123456 "}) == "123456", "the PIN is read from CAMERA_PIN and trimmed")
check(raises(lambda: L.resolve_pin({}), L.LanCameraError, "CAMERA_PIN"), "no PIN is an error that names the variable")
check(raises(lambda: L.resolve_pin({"CAMERA_PIN": "  "}), L.LanCameraError, "PIN"), "a blank PIN is no PIN")
reader_for_repr = L.LanCameraReader("http://h:3000/viewer?room=abc", pin="s3cretPIN")
check("s3cretPIN" not in repr(reader_for_repr) and "s3cretPIN" not in str(reader_for_repr),
      "the PIN is not reachable through repr/str of the reader")
check(raises(lambda: L.LanCameraReader("http://h:3000/viewer?room=abc", environ={}), L.LanCameraError, "PIN"),
      "a reader cannot be built without a PIN")
for message in ("Room already has an active viewer/receiver.",
                "Too many failed attempts. Please wait 20 seconds before retrying.", "Internal server error"):
    check(L.join_error_is_retryable(message), "retry: %s" % message)
for message in ("Invalid access PIN", "Invalid Room ID. Must be 3-32 alphanumeric characters.",
                'Media mode mismatch. This room is configured for "tunnel-relay" mode.', "", None):
    check(not L.join_error_is_retryable(message), "do NOT retry: %s" % message)

print("")
print("ICE candidates")
SRFLX = ("candidate:842163049 1 udp 1677729535 203.0.113.7 54321 typ srflx raddr 192.168.1.20 rport 54321 "
         "generation 0 ufrag abcd network-cost 999")
HOST = "candidate:1 1 udp 2113937151 192.168.1.20 50000 typ host generation 0"
c = L.candidate_from_payload({"candidate": SRFLX, "sdpMid": "0", "sdpMLineIndex": 0})
check(c is not None and (c.ip, c.port, c.protocol, c.type) == ("203.0.113.7", 54321, "udp", "srflx"),
      "a srflx candidate string converts", str((c.ip, c.port, c.type)))
check(c.relatedAddress == "192.168.1.20" and c.sdpMid == "0" and c.sdpMLineIndex == 0,
      "raddr, sdpMid and sdpMLineIndex are carried over")
c2 = L.candidate_from_payload({"candidate": HOST[len("candidate:"):]})
check(c2 is not None and c2.sdpMid is None and c2.sdpMLineIndex == 0, "no prefix, no mid: mline index defaults to 0")
check(L.candidate_from_payload({"candidate": "", "sdpMid": "0", "sdpMLineIndex": 0}) is None,
      "an empty candidate is end-of-candidates")
check(L.candidate_from_payload({"candidate": None}) is None, "a null candidate is ignored")
mdns = "candidate:3 1 udp 2113937151 3f1a9a5e-0e1c-4c53-a7e9-5b6b1fd4d6a1.local 52000 typ host generation 0"
check(L.candidate_from_payload({"candidate": mdns, "sdpMid": "0", "sdpMLineIndex": 0}).ip.endswith(".local"),
      "an mDNS .local candidate parses (resolution is aioice's job)")
for junk in ("candidate:garbage", "candidate:1 1 udp notanumber 1.2.3.4 99 typ host"):
    check(raises(lambda: L.candidate_from_payload({"candidate": junk, "sdpMid": "0", "sdpMLineIndex": 0}),
                 ValueError, "unparseable"), "unparseable is a ValueError: %s" % junk)
q = L.CandidateQueue(maxlen=3)
for i in range(5):
    q.add({"candidate": str(i)})
check(len(q) == 3 and [x["candidate"] for x in q.drain()] == ["2", "3", "4"] and len(q) == 0,
      "the queue keeps order, drains, and is bounded")

print("")
print("the newest-frame-only slot")


class FakeAvFrame:
    """Stands in for av.VideoFrame: what the reader touches is width, height and to_ndarray."""

    def __init__(self, value, width=8, height=6):
        self.value, self.width, self.height = value, width, height
        self.converted = 0

    def to_ndarray(self, format):
        self.converted += 1
        return np.full((self.height, self.width, 3), self.value, dtype=np.uint8)


slot = L.LanCameraReader("http://h:3000/viewer?room=abc", pin="1234")
frames = [FakeAvFrame(i) for i in range(1, 51)]
for f in frames:
    slot._publish(f)
img, stamp = slot.read(0.1)
check(img is not None and int(img[0, 0, 0]) == 50 and img.shape == (6, 8, 3), "read() returns the NEWEST frame, BGR (H,W,3)")
check(slot.dropped == 49 and slot.frames_read == 50, "the 49 overwritten frames are counted as dropped",
      "dropped=%d read=%d" % (slot.dropped, slot.frames_read))
check(sum(f.converted for f in frames) == 1, "only the frame taken is converted (the others cost nothing)")
t0 = time.time()
img, stamp = slot.read(0.15)
check(img is None and stamp == 0.0 and 0.1 < time.time() - t0 < 1.0, "nothing new -> (None, 0.0) after the timeout")
slot._publish(FakeAvFrame(7))
img, _ = slot.read(0.1)
check(img is not None and int(img[0, 0, 0]) == 7 and slot.dropped == 49, "a later frame is delivered and nothing more is dropped")

print("")
print("offer -> answer against a real aiortc peer, with candidates arriving BEFORE the offer")


class StubSio:
    connected = True

    def __init__(self):
        self.emitted = []

    async def emit(self, event, data=None):
        self.emitted.append((event, data))


async def offer_scenario():
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    sys.path.insert(0, HERE)
    import _lan_fake_sender as FS
    reader = L.LanCameraReader("http://h:3000/viewer?room=abc", pin="1234")
    reader._sio = StubSio()
    reader._rtc_config = RTCConfiguration(iceServers=[])
    reader._offer_lock = asyncio.Lock()
    reader._stop_event = asyncio.Event()
    offerer = RTCPeerConnection()
    offerer.addTrack(FS.FrameSourceTrack(lambda tick: FS.synthetic_frame(320, 240, tick)))
    await offerer.setLocalDescription(await offerer.createOffer())

    await reader._on_candidate({"candidate": HOST, "sdpMid": "0", "sdpMLineIndex": 0})
    await reader._on_candidate({"candidate": "candidate:garbage", "sdpMid": "0", "sdpMLineIndex": 0})
    await reader._on_candidate({"candidate": "", "sdpMid": "0", "sdpMLineIndex": 0})
    queued_before = len(reader._pending)
    session_before = reader._session
    await reader._on_offer({"sdp": offerer.localDescription.sdp, "type": "offer"})
    out = {"queued_before": queued_before, "session_before": session_before,
           "queued_after": len(reader._pending), "ready": bool(reader._session and reader._session["ready"]),
           "events": [e for e, _ in reader._sio.emitted]}
    answer = reader._sio.emitted[0][1] if reader._sio.emitted else {}
    out["answer_ok"] = answer.get("type") == "answer" and answer.get("sdp", "").startswith("v=0")
    if out["answer_ok"]:
        await offerer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
    first_pc = reader._session["pc"]
    # a second offer builds a NEW peer connection and closes the first
    offerer2 = RTCPeerConnection()
    offerer2.addTrack(FS.FrameSourceTrack(lambda tick: FS.synthetic_frame(320, 240, tick)))
    await offerer2.setLocalDescription(await offerer2.createOffer())
    await reader._on_offer({"sdp": offerer2.localDescription.sdp, "type": "offer"})
    out["new_pc"] = reader._session["pc"] is not first_pc
    out["old_closed"] = first_pc.connectionState == "closed"
    out["reconnects"] = reader.reconnects
    # malformed offers are ignored and do not disturb the live session
    live = reader._session
    await reader._on_offer({"sdp": "not an sdp", "type": "offer"})
    await reader._on_offer({"sdp": 5, "type": "offer"})
    await reader._on_offer({"sdp": offerer2.localDescription.sdp, "type": "answer"})
    out["malformed_ignored"] = reader._session is live
    await reader._close_session("test")
    for pc in (offerer, offerer2):
        # The same aiortc close hang the reader guards against (PEER_CLOSE_TIMEOUT_SECONDS) can hit the
        # test's own peers, so they are bounded too rather than letting a fixture hang the suite.
        try:
            await asyncio.wait_for(pc.close(), 5.0)
        except asyncio.TimeoutError:
            pass
    return out


res = asyncio.run(offer_scenario())
check(res["queued_before"] == 3 and res["session_before"] is None,
      "candidates before the offer are QUEUED (valid, garbage and end marker alike)")
check(res["queued_after"] == 0 and res["ready"], "applying the offer drains the queue")
check(res["events"] == ["webrtc:answer"] and res["answer_ok"], "exactly one answer is emitted, a valid SDP answer")
check(res["new_pc"] and res["old_closed"] and res["reconnects"] == 1, "a second offer builds a NEW peer connection and closes the old one")
check(res["malformed_ignored"], "malformed offers are ignored and leave the live session alone")


# =====================================================================================================
def find_node_server():
    server_dir = os.environ.get("CAMERA_SERVER_DIR", "")
    entry = os.path.join(server_dir, "src", "server", "index.js")
    if not server_dir or not os.path.isfile(entry):
        return None
    return server_dir


class LoopThread:
    """An asyncio loop on its own thread, so blocking reader.read() calls can run on this one."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


server_dir = find_node_server()
print("")
if server_dir is None:
    print("layers 2 and 3 SKIPPED: set CAMERA_SERVER_DIR to the lan-camera-streaming project folder")
else:
    sys.path.insert(0, HERE)
    import _lan_fake_sender as FS

    port = free_port()
    env = dict(os.environ)
    env.update({"PORT": str(port), "HOST": "127.0.0.1", "ACCESS_PIN": TEST_PIN, "LOG_LEVEL": "warn"})
    for key in ("TURN_SERVERS", "STUN_SERVERS", "HTTPS_CERT_PATH", "HTTPS_KEY_PATH"):
        env.pop(key, None)
    node = subprocess.Popen(["node", os.path.join(server_dir, "src", "server", "index.js")], cwd=server_dir,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    lt = LoopThread()
    readers = []
    senders = []
    try:
        deadline = time.time() + 15
        while True:
            try:
                urllib.request.urlopen(base + "/health", timeout=1).read()
                break
            except OSError:
                if node.poll() is not None or time.time() > deadline:
                    raise SystemExit("the Node server did not start")
                time.sleep(0.2)

        room_counter = [0]

        def new_room():
            room_counter[0] += 1
            return "f50-%d-%d" % (os.getpid(), room_counter[0])

        def link(room):
            return "%s/viewer?room=%s" % (base, room)

        def make_reader(room, pin=TEST_PIN):
            r = L.LanCameraReader(link(room), pin=pin)
            readers.append(r)
            return r

        def make_sender(room, **kw):
            s = FS.FakeSender(base, room, TEST_PIN, **kw)
            senders.append(s)
            return s

        def take(reader, n, timeout):
            got, deadline_t = [], time.time() + timeout
            while len(got) < n and time.time() < deadline_t:
                img, _ = reader.read(1.0)
                if img is not None:
                    got.append(img)
            return got

        print("against the real Node server")
        # -- receiver (viewer) joins first, phone second
        room = new_room()
        r = make_reader(room).start()
        s = make_sender(room, width=640, height=480)
        lt.run(s.start())
        frames_got = take(r, 31, 25)
        check(len(frames_got) >= 31, "more than 30 frames arrive (viewer joined first)", "got %d" % len(frames_got))
        check(all(f.shape == (480, 640, 3) and f.dtype == np.uint8 for f in frames_got),
              "every frame is BGR uint8 at the sent resolution (480, 640, 3)")
        check(r.connected and r.frames_read >= 31 and s.offers_sent == 1, "connected, one offer", "offers=%d" % s.offers_sent)

        # -- the phone leaves and comes back: a fresh offer, a new peer connection, frames resume
        before = r.frames_read
        lt.run(s.leave_and_rejoin())
        deadline_t = time.time() + 15
        while s.offers_sent < 2 and time.time() < deadline_t:
            time.sleep(0.1)
        resumed = take(r, 10, 20)
        check(s.offers_sent == 2 and len(resumed) >= 10 and r.frames_read >= before + 10,
              "phone leaves and rejoins: a fresh offer and frames resume",
              "offers=%d resumed=%d new_frames=%d" % (s.offers_sent, len(resumed), r.frames_read - before))
        check(r.reconnects == 1, "reconnects counts the new session", "reconnects=%d" % r.reconnects)

        # -- stop() emits room:leave: the phone is told the viewer left
        left_before = s.peer_left_count
        r.stop()
        time.sleep(0.5)
        check(s.peer_left_count == left_before + 1, "stop() leaves the room (the phone gets peer:left)")
        lt.run(s.stop())

        # -- phone joins FIRST, viewer second
        room = new_room()
        s = make_sender(room, width=640, height=480)
        lt.run(s.start())
        r = make_reader(room).start()
        got = take(r, 31, 25)
        check(len(got) >= 31, "more than 30 frames arrive (phone joined first)", "got %d" % len(got))
        r.stop()
        lt.run(s.stop())

        # -- wrong PIN: one clear error, never the PIN
        room = new_room()
        t0 = time.time()
        bad = L.LanCameraReader(link(room), pin="000000")
        readers.append(bad)
        try:
            bad.start()
            message = None
        except L.LanCameraError as e:
            message = str(e)
        check(message is not None and "Invalid access PIN" in message, "a wrong PIN raises LanCameraError", str(message))
        check(message is not None and "000000" not in message, "...and the message never contains the PIN")
        check(time.time() - t0 < 6.0, "...promptly (before any model would load)", "%.1fs" % (time.time() - t0))
        check(bad.fatal is not None, "fatal is set so a running loop can notice")
        bad.stop()

        # -- a second viewer in an occupied room is RETRIED, and gets in when the first leaves
        room = new_room()
        first = make_reader(room).start()
        second = make_reader(room).start()  # does not raise: occupied is not fatal
        check(second.fatal is None and not second.connected, "a second viewer is not fatal, just not connected yet")
        s = make_sender(room, width=320, height=240)
        lt.run(s.start())
        got1 = take(first, 5, 15)
        check(len(got1) >= 5, "the first viewer streams while the second waits")
        first.stop()
        got2 = take(second, 5, 40)
        check(len(got2) >= 5 and second.fatal is None, "when the first viewer leaves, the second gets in and streams",
              "got %d" % len(got2))
        second.stop()
        lt.run(s.stop())

        # -- a slow consumer sees FRESH frames and the rest are counted as dropped
        room = new_room()
        s = make_sender(room, width=640, height=480)
        lt.run(s.start())
        r = make_reader(room).start()
        take(r, 3, 20)
        ages = []
        dropped_before = r.dropped
        for _ in range(8):
            time.sleep(0.4)
            img, stamp = r.read(2.0)
            if img is not None:
                ages.append(time.time() - stamp)
        check(len(ages) == 8 and max(ages) < 0.25, "a consumer 12x slower than the phone still gets fresh frames",
              "max age %.0f ms" % (max(ages) * 1000.0 if ages else -1))
        check(r.dropped - dropped_before > 40, "the skipped frames are counted, not queued",
              "dropped %d" % (r.dropped - dropped_before))
        r.stop()
        lt.run(s.stop())

        # -- FULL CHAIN: the real sender, launched as Unity launches it, on a real person
        print("")
        print("full chain: video_udp_sender.py --video <viewer link>, PIN only in the environment")
        model_ok = os.path.isfile(V.EV.DEFAULT_MODEL)
        if not os.path.isfile(CLIP) or not model_ok:
            print("  SKIPPED: needs %s and the model" % CLIP)
        else:
            room = new_room()
            # 360x640: a portrait phone's size. Switched to 288x512 half way through (below).
            s = make_sender(room, width=360, height=640, clip=CLIP)
            lt.run(s.start())
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind(("127.0.0.1", 0))
            udp.settimeout(1.0)
            chain_env = dict(os.environ)
            chain_env["CAMERA_PIN"] = TEST_PIN
            child = subprocess.Popen(
                [sys.executable, "-u", os.path.join(SIDECAR_ROOT, "tools", "video", "video_udp_sender.py"),
                 "--video", link(room), "--host", "127.0.0.1", "--port", str(udp.getsockname()[1])],
                cwd=SIDECAR_ROOT, env=chain_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, encoding="utf-8", errors="replace")
            output = []
            threading.Thread(target=lambda: [output.append(line) for line in child.stdout], daemon=True).start()
            datagrams = []

            def pump(count, seconds):
                """Collect datagrams until `count` MORE have arrived or `seconds` pass."""
                target, deadline_p = len(datagrams) + count, time.time() + seconds
                while len(datagrams) < target and time.time() < deadline_p and child.poll() is None:
                    try:
                        data, _ = udp.recvfrom(65535)
                        datagrams.append(json.loads(data.decode("utf-8")))
                    except socket.timeout:
                        pass

            pump(40, 120)
            first_phase = len(datagrams)
            # PHASE 2: the phone changes resolution (WebRTC does this to fit the bandwidth, and a phone
            # that rejoins may come back at another size). The frame loop derived its crop box and focal
            # length from the first frame, so it must notice and re-derive them, not feed the model a
            # crop for a picture that no longer exists.
            s.width, s.height = 288, 512
            lt.run(s.leave_and_rejoin())
            pump(40, 90)
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
            time.sleep(0.5)
            text = "".join(output)
            check(first_phase >= 40, "the production datagram arrives from a phone over WebRTC",
                  "%d datagrams" % first_phase)
            check("frame size changed 360x640 -> 288x512" in text,
                  "a mid-run resolution change is noticed and the crop/intrinsics re-derived")
            check(len(datagrams) >= first_phase + 40, "...and datagrams keep flowing at the new size",
                  "%d more" % (len(datagrams) - first_phase))
            check(len(set(d["sid"] for d in datagrams)) == 1, "one producer session id throughout (no restart)")
            check(len(datagrams) > 0 and all(len(d.get("lm", [])) == 33 and "sid" in d and "seq" in d for d in datagrams),
                  "each one is the production shape: 33 landmarks, sid, seq")
            check(len(datagrams) > 0 and all(d["src"] == [0] * 33 for d in datagrams),
                  "src is all 0: honest, there is no stereo")
            check("[lan] joined room" in text and "frames=" in text, "the sender log shows the LAN join and the frames= readiness marker")
            check(TEST_PIN not in text, "the PIN appears nowhere in the sender's output")
            check("LAN camera, room '%s'" % room in text, "the source line names the LAN camera and its room")
            lt.run(s.stop())
            udp.close()
    finally:
        for r in readers:
            r.stop()
        for s in senders:
            try:
                lt.run(s.stop(), 10)
            except Exception as e:  # noqa: BLE001 - best-effort teardown of a test fixture
                print("  (teardown: %s)" % type(e).__name__)
        lt.close()
        node.terminate()
        try:
            node.wait(5)
        except subprocess.TimeoutExpired:
            node.kill()

print("")
print("-" * 78)
print("%d/%d assertions passed" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
