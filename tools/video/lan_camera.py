#!/usr/bin/env python3
"""F-50 - LAN CAMERA: receive a phone's camera over WebRTC from the LAN Camera Streaming server, as a
frame source for video_udp_sender.py. A UTILITY, not a sender: it produces frames and nothing else.

WHY THIS EXISTS. F-49 made "track a video instead of the OAK-D" possible; a phone could only get in as
a webcam (DroidCam/Iriun/Camo) or an MJPEG "IP camera" app. The LAN Camera Streaming project
(lan-camera-streaming, a Node server + a browser sender page) streams the phone's camera over WebRTC
with no app to install. This module is the receiving end of that: paste the viewer link into
AppBootstrap -> Video Source, and the sidecar joins the room as the (single) viewer and reads the
phone's video track exactly like a camera.

HOW IT IS USED
  reader = LanCameraReader("https://host/viewer?room=room-abc123").start()   # PIN from CAMERA_PIN
  frame_bgr, grabbed_at = reader.read(2.0)    # the NEWEST frame, or (None, 0.0) after 2 s
  reader.stop()                               # emits room:leave, closes the peer connection

`LanCameraReader` deliberately has the same interface as video_udp_sender.LatestFrameReader (start /
stop / read / frames_read / dropped / reconnects / connected), so the frame loop does not know or care
which one it is reading from.

THE PROTOCOL (the server's own, read from lan-camera-streaming/src/server/signaling.js)
  1. GET {base}/api/config -> {"iceServers": [...]}  (STUN, plus TURN if the server has one)
  2. Socket.IO on the base URL; on EVERY connect event (including automatic reconnects, because the
     server drops room membership when a socket goes away) emit
        room:join {roomId, role: "webrtc-receiver", pin, mediaMode: "webrtc"}   and wait for the ack
  3. The sender creates the OFFER. For every webrtc:offer a NEW RTCPeerConnection is built (the old
     one closed), the offer applied, and webrtc:answer emitted. aiortc does not trickle ICE: its
     candidates are inside the answer SDP, so nothing is sent on webrtc:ice-candidate.
  4. Candidates the sender trickles are queued until the remote description is set, then applied. One
     that cannot be parsed or resolved (an mDNS .local name) is skipped; it must not end the session.
  5. peer:left (the sender went away) closes the peer connection and waits for the next offer.
  The server allows ONE viewer per room, so a browser viewer tab open in the same room blocks this
  reader ("Room already has an active viewer/receiver") - it keeps retrying, so closing the tab fixes it.

THE PIN. The server requires an access PIN to join. It is NEVER taken from the link, never put on a
command line (visible to every process on the machine) and never printed: it arrives in the CAMERA_PIN
environment variable, which Unity sets from AppBootstrap -> Lan Camera Pin for the sidecar it launches.

WHAT IS NOT HERE, on purpose
  * No MJPEG / tunnel-relay fallback. When WebRTC cannot connect (router client isolation, firewall, a
    phone on another network with no TURN) the failure is reported with the likely causes. An MJPEG
    route already exists for a LAN phone via `stream:` (see video_udp_sender.py), and the relay modes
    spend zrok quota; neither is wired in here until somebody needs it.
  * No inference and no pose logic. Frames in, frames out.
  * No rotation. A portrait phone can arrive sideways (aiortc ignores the video-orientation header);
    video_udp_sender's --rotate / --flip are applied downstream, before inference, as for any camera.

Lazy imports: aiortc / python-socketio / aiohttp are imported when a reader STARTS, so importing this
module (to classify a --video value) costs nothing and works without them.
"""

import asyncio
import os
import re
import threading
import time
import urllib.parse
from collections import deque

#: The environment variable the PIN travels in. Same name as the standalone receiver's, so a machine
#: that already exports it needs no Unity-side PIN at all.
PIN_ENV = "CAMERA_PIN"

#: Explicit "this is a LAN camera link" prefix, like `stream:`. Lets a link that is not shaped like a
#: viewer URL (a server mounted elsewhere, `http://host:3000?room=abc`) be forced onto this source.
LAN_PREFIX = "lan:"

#: The path the server's viewer page lives at. A link ending in it AND carrying ?room= is a LAN camera.
VIEWER_PATH = "/viewer"

#: Mirrors RoomManager.validateRoomId in the server: 3-32 letters, digits, '-' or '_'. Checked here so a
#: typo is reported as a typo, not as a server error after a network round trip.
ROOM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")

#: Used when /api/config cannot be read. This is the server's own default STUN entry.
FALLBACK_STUN = "stun:stun.l.google.com:19302"

#: Seconds start() waits for the first join outcome before carrying on. A LAN round trip plus the ack is
#: well under a second; 8 s tolerates a slow tunnel. It is short on purpose: the supervisor kills a
#: sender that is not READY within --ready-timeout (45 s), and this wait comes before the ~10 s model
#: load, python's own start-up (~4 s) and the first frames= line. The reader keeps retrying in the
#: background whatever happens here.
JOIN_WAIT_SECONDS = 8.0

#: Seconds to wait for room:join's ack. The server answers in milliseconds; this is a dead-socket guard.
JOIN_ACK_TIMEOUT_SECONDS = 10.0

#: Retry spacing for a join the server refused for a REASON THAT CAN CLEAR (a viewer tab open, the
#: server's own 30 s block after 5 failures). Capped at 10 s: five refusals in a row make the server
#: block this socket for 30 s, so retrying faster than this only lengthens the wait.
JOIN_RETRY_SECONDS = (2.0, 5.0, 10.0)

#: Server answers that mean "try again later". Anything else (wrong PIN, bad room, media-mode mismatch)
#: cannot be fixed by retrying, so it ends the session instead of looping a restart forever.
RETRYABLE_JOIN_MARKERS = ("already has an active", "too many failed attempts", "internal server error")

#: Backoff while the server itself cannot be reached (not started yet, Wi-Fi gone), seconds. Same shape
#: as LatestFrameReader's, so a phone and a server come back on the same schedule.
RECONNECT_BACKOFF_SECONDS = (0.5, 1.0, 2.0, 5.0)

#: Upper bound on queued trickle candidates. A sender gathers ~10; 256 is a flood guard, not a tuning.
MAX_QUEUED_CANDIDATES = 256

#: stop() waits this long for room:leave + the peer connection to close.
STOP_JOIN_SECONDS = 5.0

#: Longest a single RTCPeerConnection.close() is allowed to take. A healthy close returns in
#: milliseconds. MEASURED 2026-10-10 (tests/test_lan_camera.py, aiortc 1.15.0 / aioice 0.10.2, Windows
#: Proactor loop): closing a connection whose ICE was still `checking` and whose DTLS was still `new`
#: did not return in over 30 s. Unbounded, that would sit inside the offer lock and freeze the receiver
#: for every later offer - exactly the state a phone that cannot reach us leaves behind before it
#: retries. After the timeout the connection is abandoned (its tasks die with the process).
PEER_CLOSE_TIMEOUT_SECONDS = 3.0

ICE_FAILED_HINT = ("ICE failed: no network path to the phone. Check (1) router Wi-Fi client isolation or a "
                   "guest network, (2) the firewall on this PC (allow inbound UDP for the sidecar's "
                   "python.exe), (3) VPN or virtual adapters hiding the LAN route, (4) whether the phone is "
                   "on another network and the server has no TURN entry (TURN_SERVERS in its .env).")


class LanCameraError(Exception):
    """The link, the PIN or the server's answer makes this source unusable. The message is the whole
    explanation and never contains the PIN."""


# ---- pure helpers (no network, no aiortc) ---------------------------------------------------------

def is_lan_camera_link(text):
    """True when `text` names a LAN Camera Streaming viewer: `lan:<anything>`, or an http(s) link whose
    path ends in /viewer and which carries a `room` query value. video_udp_sender.classify_source asks
    this BEFORE it falls back to 'a web page' or 'an MJPEG stream', because a viewer link is both of
    those on the surface (an http link, often to a LAN address) and neither in fact."""
    t = (text or "").strip().strip('"')
    if t.lower().startswith(LAN_PREFIX):
        return True
    parsed = urllib.parse.urlparse(t)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return False
    if not parsed.path.rstrip("/").lower().endswith(VIEWER_PATH):
        return False
    return bool(urllib.parse.parse_qs(parsed.query).get("room"))


def parse_lan_url(spec):
    """(base_url, room_id) for a LAN camera link; raises LanCameraError with the reason otherwise.

    `https://lan.shares.zrok.io/viewer?room=room-abc123` -> ('https://lan.shares.zrok.io', 'room-abc123').
    The path and query are dropped: Socket.IO and /api/config live at the server root. Credentials in
    the link are dropped too (this value is printed). A `pin` query parameter is REFUSED, not stripped:
    a PIN pasted into a link is already in a place that gets logged and screenshotted, and silently
    using it would teach people that is fine.
    """
    text = (spec or "").strip().strip('"')
    if text.lower().startswith(LAN_PREFIX):
        text = text[len(LAN_PREFIX):].strip()
    if not text:
        raise LanCameraError("the LAN camera link is empty")
    if "://" not in text:
        text = "http://" + text
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise LanCameraError("a LAN camera link must be http:// or https://, not '%s:'" % parsed.scheme)
    host = parsed.hostname
    if not host:
        raise LanCameraError("the LAN camera link has no host")
    try:
        port = parsed.port
    except ValueError:
        raise LanCameraError("the LAN camera link has an invalid port")
    query = urllib.parse.parse_qs(parsed.query)
    if "pin" in query:
        raise LanCameraError("the link contains a PIN. Remove it and put the PIN in 'Lan Camera Pin' "
                             "(AppBootstrap) instead - a PIN inside a link ends up in logs")
    rooms = [r.strip() for r in query.get("room", []) if r.strip()]
    if not rooms:
        raise LanCameraError("the LAN camera link has no room. It should look like "
                             "http://<server>/viewer?room=<room id>")
    room = rooms[0]
    if not ROOM_ID_PATTERN.match(room):
        raise LanCameraError("room id '%s' is not valid: 3-32 letters, digits, '-' or '_'" % room)
    if ":" in host:
        host = "[%s]" % host  # hostname drops the brackets of an IPv6 literal; a URL needs them back
    return "%s://%s%s" % (parsed.scheme.lower(), host, (":%d" % port) if port else ""), room


def resolve_pin(environ=None):
    """The access PIN from CAMERA_PIN. Raises LanCameraError when there is none."""
    env = os.environ if environ is None else environ
    pin = (env.get(PIN_ENV) or "").strip()
    if not pin:
        raise LanCameraError("no access PIN. Set 'Lan Camera Pin' on AppBootstrap (or the %s "
                             "environment variable)" % PIN_ENV)
    return pin


def join_error_is_retryable(message):
    """True when a refused room:join can clear by itself (see RETRYABLE_JOIN_MARKERS)."""
    low = (message or "").lower()
    return any(marker in low for marker in RETRYABLE_JOIN_MARKERS)


def candidate_from_payload(payload):
    """An aiortc RTCIceCandidate from a webrtc:ice-candidate payload, or None for the empty
    end-of-candidates marker. Raises ValueError for anything unparseable.

    The wire string is a browser's `candidate:842163049 1 udp 1677729535 1.2.3.4 54321 typ srflx ...`;
    aiortc wants it without the prefix, with sdpMid / sdpMLineIndex set separately.
    """
    from aiortc.sdp import candidate_from_sdp
    raw = payload.get("candidate")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("candidate:"):
        text = text[len("candidate:"):]
    try:
        candidate = candidate_from_sdp(text)
    except (AssertionError, ValueError, IndexError) as e:
        # aiortc asserts on a short candidate line instead of raising; surface it as one error type.
        raise ValueError("unparseable ICE candidate: %r" % (e,))
    sdp_mid = payload.get("sdpMid")
    mline = payload.get("sdpMLineIndex")
    if sdp_mid is None and mline is None:
        mline = 0  # the offer has one m-line, and aiortc insists on one of the two being set
    candidate.sdpMid = sdp_mid
    candidate.sdpMLineIndex = mline
    return candidate


class CandidateQueue:
    """Remote candidates that arrived before the remote description was applied, oldest first."""

    def __init__(self, maxlen=MAX_QUEUED_CANDIDATES):
        self._items = deque(maxlen=maxlen)

    def add(self, payload):
        self._items.append(payload)

    def drain(self):
        items = list(self._items)
        self._items.clear()
        return items

    def clear(self):
        self._items.clear()

    def __len__(self):
        return len(self._items)


def describe_selected_path(pc):
    """'local=host/udp remote=host/udp (direct)' for the nominated ICE pair, or None.

    Candidate TYPES only, never addresses: this is printed. It reads aioice internals (_nominated), so
    it is best-effort by construction and a missing answer means nothing."""
    try:
        for receiver in pc.getReceivers():
            pair = receiver.transport.transport._connection._nominated.get(1)
            if pair is None:
                continue
            local, remote = pair.local_candidate, pair.remote_candidate
            route = "via TURN relay" if "relay" in (local.type, remote.type) else "direct"
            return "local=%s/%s remote=%s/%s (%s)" % (local.type, local.transport, remote.type,
                                                      remote.transport, route)
    except AttributeError:
        return None
    return None


# ---- the reader -----------------------------------------------------------------------------------

class LanCameraReader:
    """Frames from the phone, newest only, on a background thread. Same surface as LatestFrameReader.

    WHY NEWEST-ONLY. The reader thread's job is to keep the track drained and overwrite one slot; the
    consumer (RTMW3D, ~30 ms a frame) takes whatever is there when it asks. A consumer slower than the
    phone therefore skips frames (counted in `dropped`) instead of falling further behind, which is the
    same bound LatestFrameReader gives a webcam. The pixel conversion (to a BGR array) happens in
    read(), on the consumer's thread, only for the frame actually taken.

    Everything network-shaped lives on one asyncio loop in a daemon thread; the public methods are the
    thread-safe boundary. `connected` is True while a peer connection is up and delivering.
    """

    def __init__(self, spec, pin=None, name="lan", clock=time.time, environ=None):
        self.name = name
        self.base_url, self.room_id = parse_lan_url(spec)
        self._pin = pin.strip() if pin else resolve_pin(environ)
        self._clock = clock
        self._cond = threading.Condition()
        self._frame = None
        self._stamp = 0.0
        self._counter = 0
        self._consumed = 0
        self._stop = False
        self._thread = None
        self._joined = threading.Event()
        self.frames_read = 0
        self.dropped = 0
        self.reconnects = 0
        self.connected = False
        #: Why the session ended for good (wrong PIN, bad room...), or None. The frame loop checks it.
        self.fatal = None
        # asyncio state, owned by the loop thread
        self._loop = None
        self._stop_event = None
        self._sio = None
        self._http = None
        self._rtc_config = None
        self._session = None
        self._session_count = 0
        self._offer_lock = None
        self._pending = CandidateQueue()
        self._join_task = None
        self._last_size = (0, 0)

    def __repr__(self):
        # The PIN must not be reachable through a repr that ends up in a log line.
        return "LanCameraReader(room=%r, connected=%r)" % (self.room_id, self.connected)

    # -- thread-safe public surface

    def start(self, wait=JOIN_WAIT_SECONDS):
        """Start the receiver and wait up to `wait` seconds for the server's first answer to the join.

        Raises LanCameraError if the server REFUSED the join for a reason retrying cannot fix (wrong
        PIN, invalid room, media-mode mismatch), so a bad PIN is one clear error instead of a model
        load followed by silence. An unreachable server or an occupied room is NOT an error here: the
        reader keeps trying in the background and the frame loop reports 'waiting for frames'."""
        self._thread = threading.Thread(target=self._thread_main, name="reader-" + self.name, daemon=True)
        self._thread.start()
        self._joined.wait(wait)
        if self.fatal:
            raise LanCameraError(self.fatal)
        return self

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        loop, event = self._loop, self._stop_event
        if loop is not None and event is not None:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # The loop is already closed: the thread ended on its own (fatal error) and there is
                # nothing left to tell.
                pass
        if self._thread is not None:
            self._thread.join(timeout=STOP_JOIN_SECONDS)

    def read(self, timeout):
        """(frame_bgr, grab_time) for the newest frame not yet returned, or (None, 0.0) after `timeout`
        seconds with nothing new. Frames that arrived and were overwritten before this call are counted
        in `dropped`. frame_bgr is a fresh uint8 (H, W, 3) array the caller owns."""
        deadline = self._clock() + timeout
        with self._cond:
            while self._counter == self._consumed and not self._stop:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None, 0.0
                self._cond.wait(remaining)
            if self._counter == self._consumed:
                return None, 0.0
            self.dropped += self._counter - self._consumed - 1
            self._consumed = self._counter
            av_frame, stamp = self._frame, self._stamp
        return av_frame.to_ndarray(format="bgr24"), stamp

    def _publish(self, av_frame):
        """Called on the loop thread for every decoded frame: overwrite the slot, wake the consumer."""
        with self._cond:
            self._frame = av_frame
            self._stamp = self._clock()
            self._counter += 1
            self.frames_read += 1
            self._cond.notify_all()
        size = (av_frame.width, av_frame.height)
        if size != self._last_size:
            # Logged on the first frame and whenever the phone changes resolution (WebRTC adapts it to
            # the bandwidth), which the frame loop also has to notice. Never per frame.
            print("[lan] video %dx%d" % size, flush=True)
            self._last_size = size

    # -- loop thread

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as e:  # noqa: BLE001 - the thread boundary: report it, do not let it vanish
            self._set_fatal("the LAN camera receiver crashed: %s: %s" % (type(e).__name__, e))
        finally:
            self._joined.set()

    def _set_fatal(self, message):
        """Record why this session cannot continue, release start() and wind the loop down."""
        if self.fatal is None:
            self.fatal = message
        self._joined.set()
        if self._loop is not None and self._stop_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                # The loop already closed (this is the thread's last act); nothing is left to stop.
                pass

    async def _run(self):
        try:
            import aiohttp
            import socketio
        except ImportError as e:
            self._set_fatal("the LAN camera needs aiortc, python-socketio and aiohttp in the sidecar "
                            "venv (%s). See python-sidecar~/requirements.txt." % e)
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._offer_lock = asyncio.Lock()
        with self._cond:
            if self._stop:
                return
        self._http = aiohttp.ClientSession()
        try:
            self._rtc_config = await self._load_rtc_config(aiohttp)
            await self._connect(socketio)
            await self._stop_event.wait()
        finally:
            await self._shutdown()

    async def _load_rtc_config(self, aiohttp):
        """RTCConfiguration from /api/config; Google STUN when that cannot be read."""
        from aiortc import RTCConfiguration, RTCIceServer
        servers = []
        try:
            async with self._http.get(self.base_url + "/api/config",
                                      timeout=aiohttp.ClientTimeout(total=5.0)) as response:
                if response.status != 200:
                    raise ValueError("HTTP %d" % response.status)
                payload = await response.json(content_type=None)
            for entry in payload.get("iceServers", []):
                urls = entry.get("urls")
                urls = [urls] if isinstance(urls, str) else urls
                if urls:
                    servers.append(RTCIceServer(urls=[str(u) for u in urls], username=entry.get("username"),
                                                credential=entry.get("credential")))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError) as e:
            # Not fatal: STUN alone is enough on a LAN. Only TURN-dependent setups notice.
            print("[lan] could not read the server's ICE config (%s); using public STUN" % type(e).__name__,
                  flush=True)
        if not servers:
            servers = [RTCIceServer(urls=[FALLBACK_STUN])]
        return RTCConfiguration(iceServers=servers)

    async def _connect(self, socketio):
        """Connect the Socket.IO client, retrying until it works or stop() is called. After the first
        success python-socketio reconnects by itself and the `connect` handler re-joins."""
        self._sio = socketio.AsyncClient(reconnection=True, reconnection_attempts=0, reconnection_delay=1,
                                         reconnection_delay_max=10, logger=False, engineio_logger=False)
        self._register_events()
        failures = 0
        while not self._stop_event.is_set():
            # websocket first (works through an https tunnel); polling-then-upgrade as the fallback.
            for transports in (["websocket"], ["polling", "websocket"]):
                try:
                    await self._sio.connect(self.base_url, transports=transports, wait_timeout=10)
                    print("[lan] connected to the server (%s)" % self._sio.transport(), flush=True)
                    return
                except (socketio.exceptions.ConnectionError, asyncio.TimeoutError, OSError):
                    if self._sio.connected:
                        await self._sio.disconnect()
            wait = RECONNECT_BACKOFF_SECONDS[min(failures, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            if failures % 5 == 0:
                # Sparse on purpose: a server that is simply not started yet would otherwise print forever.
                print("[lan] server not reachable yet; retrying (is the camera server running?)", flush=True)
            failures += 1
            try:
                await asyncio.wait_for(self._stop_event.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def _register_events(self):
        sio = self._sio

        @sio.event
        async def connect():
            self._join_task = asyncio.ensure_future(self._join_loop())

        @sio.event
        async def disconnect():
            if not self._stop:
                self.connected = False
                print("[lan] signaling lost; reconnecting", flush=True)

        @sio.on("peer:joined")
        async def peer_joined(data=None):
            print("[lan] the phone joined the room; waiting for its offer", flush=True)

        @sio.on("webrtc:offer")
        async def webrtc_offer(data=None):
            await self._on_offer(data or {})

        @sio.on("webrtc:ice-candidate")
        async def webrtc_ice_candidate(data=None):
            await self._on_candidate(data or {})

        @sio.on("stream:state")
        async def stream_state(data=None):
            print("[lan] stream state: %s" % (data or {}).get("state"), flush=True)

        @sio.on("peer:left")
        async def peer_left(data=None):
            print("[lan] the phone left the room; waiting for it to come back", flush=True)
            self._pending.clear()
            await self._close_session("phone left")

        @sio.on("room:error")
        async def room_error(data=None):
            print("[lan] server error: %s" % (data or {}).get("message"), flush=True)

    async def _join_loop(self):
        """Join the room; retry a refusal that can clear, stop on one that cannot."""
        import socketio
        payload = {"roomId": self.room_id, "role": "webrtc-receiver", "pin": self._pin,
                   "mediaMode": "webrtc"}
        attempt = 0
        while not self._stop:
            try:
                ack = await self._sio.call("room:join", payload, timeout=JOIN_ACK_TIMEOUT_SECONDS)
            except (socketio.exceptions.TimeoutError, socketio.exceptions.SocketIOError) as e:
                if not self._sio.connected:
                    return  # the reconnect will run this again
                print("[lan] join got no answer (%s); retrying" % type(e).__name__, flush=True)
                await asyncio.sleep(JOIN_RETRY_SECONDS[0])
                continue
            if isinstance(ack, dict) and ack.get("success"):
                self._joined.set()
                print("[lan] joined room '%s' (%s)" % (
                    self.room_id, "phone is present" if ack.get("hasPeer") else "waiting for the phone"),
                    flush=True)
                return
            message = str(ack.get("error", "unknown error")) if isinstance(ack, dict) else "malformed answer"
            if not join_error_is_retryable(message):
                self._set_fatal("the server refused the join: %s" % message)
                return
            wait = JOIN_RETRY_SECONDS[min(attempt, len(JOIN_RETRY_SECONDS) - 1)]
            attempt += 1
            print("[lan] join refused (%s)%s; retrying in %.0f s" % (
                message, " - close any browser viewer open in this room" if "viewer" in message else "", wait),
                flush=True)
            self._joined.set()  # the server answered: not a startup failure, do not hold the model load
            await asyncio.sleep(wait)

    async def _on_offer(self, data):
        import socketio
        from aiortc import RTCPeerConnection, RTCSessionDescription
        sdp, sdp_type = data.get("sdp"), data.get("type")
        # aiortc accepts garbage SDP and answers it with an empty description, so check the shape first.
        if not isinstance(sdp, str) or sdp_type != "offer" or not sdp.startswith("v=0") or "\nm=" not in sdp:
            print("[lan] ignored a malformed offer", flush=True)
            return
        async with self._offer_lock:
            await self._close_session("new offer")
            pc = RTCPeerConnection(configuration=self._rtc_config)
            self._session_count += 1
            if self._session_count > 1:
                self.reconnects += 1
            label = "pc%d" % self._session_count
            session = {"pc": pc, "label": label, "ready": False, "closed": False, "tasks": []}
            self._wire_peer(session)
            print("[lan] [%s] offer received; answering" % label, flush=True)
            try:
                await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
                # No await between marking the description applied and publishing the session, so a
                # candidate cannot fall between the queue and the live path.
                session["ready"] = True
                self._session = session
                for queued in self._pending.drain():
                    await self._add_candidate(session, queued)
                await pc.setLocalDescription(await pc.createAnswer())
            except Exception as e:  # noqa: BLE001 - aiortc raises many types for a bad offer
                print("[lan] [%s] could not answer the offer: %s" % (label, type(e).__name__), flush=True)
                if self._session is session:
                    self._session = None
                await self._close_peer(session)
                return
            try:
                await self._sio.emit("webrtc:answer", {"sdp": pc.localDescription.sdp,
                                                       "type": pc.localDescription.type})
            except (socketio.exceptions.SocketIOError, OSError) as e:
                # The socket dropped between offer and answer. The reconnect re-joins and the phone
                # sends a fresh offer, so this attempt is simply abandoned.
                print("[lan] [%s] could not send the answer: %s" % (label, type(e).__name__), flush=True)

    async def _on_candidate(self, data):
        session = self._session
        if session is None or not session["ready"] or session["closed"]:
            self._pending.add(data)
            return
        await self._add_candidate(session, data)

    async def _add_candidate(self, session, payload):
        try:
            candidate = candidate_from_payload(payload)
            if candidate is not None:
                await session["pc"].addIceCandidate(candidate)
        except Exception as e:  # noqa: BLE001 - one bad candidate (an unresolvable .local) must not end the session
            print("[lan] [%s] skipped a remote candidate (%s)" % (session["label"], type(e).__name__),
                  flush=True)

    def _wire_peer(self, session):
        pc = session["pc"]
        label = session["label"]

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                session["tasks"].append(asyncio.ensure_future(self._recv_loop(track, session)))

        @pc.on("connectionstatechange")
        async def on_state():
            if session["closed"]:
                return
            state = pc.connectionState
            self.connected = (state == "connected")
            print("[lan] [%s] connection %s" % (label, state), flush=True)
            if state == "connected":
                path = describe_selected_path(pc)
                if path:
                    print("[lan] [%s] path: %s" % (label, path), flush=True)
            elif state == "failed":
                print("[lan] " + ICE_FAILED_HINT, flush=True)

    async def _recv_loop(self, track, session):
        """Drain the track as fast as frames arrive, so nothing queues inside aiortc."""
        from aiortc.mediastreams import MediaStreamError
        try:
            while True:
                self._publish(await track.recv())
        except MediaStreamError:
            print("[lan] [%s] video track ended" % session["label"], flush=True)

    async def _close_session(self, reason):
        session, self._session = self._session, None
        if session is not None:
            print("[lan] [%s] closing the peer connection (%s)" % (session["label"], reason), flush=True)
            await self._close_peer(session)

    async def _close_peer(self, session):
        if session["closed"]:
            return
        session["closed"] = True
        self.connected = False
        for task in session["tasks"]:
            task.cancel()
        for task in session["tasks"]:
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(session["pc"].close(), PEER_CLOSE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            print("[lan] [%s] the peer connection did not close within %.0f s; abandoning it"
                  % (session["label"], PEER_CLOSE_TIMEOUT_SECONDS), flush=True)
        except Exception as e:  # noqa: BLE001 - closing is best effort and must not mask the real exit
            print("[lan] closing the peer connection raised %s" % type(e).__name__, flush=True)

    async def _shutdown(self):
        """room:leave, close the peer connection and the socket. Runs on the way out, however it ends."""
        if self._join_task is not None and not self._join_task.done():
            self._join_task.cancel()
        await self._close_session("shutdown")
        if self._sio is not None and self._sio.connected:
            try:
                await asyncio.wait_for(self._sio.emit("room:leave"), 2.0)
                await asyncio.wait_for(self._sio.disconnect(), 3.0)
                print("[lan] left the room", flush=True)
            except (asyncio.TimeoutError, OSError):
                print("[lan] could not leave the room cleanly (the server drops it on disconnect anyway)",
                      flush=True)
        if self._http is not None:
            await self._http.close()
