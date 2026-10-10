"""TEST HELPER for test_lan_camera.py: a stand-in for the phone's browser sender page.

Speaks the SENDER side of the LAN Camera Streaming protocol with aiortc and python-socketio: join as
`sender`; when the viewer is present (hasPeer in the join ack, or peer:joined later) create a fresh offer
carrying a video track and apply the answer. The track is either a synthetic moving pattern or a real
clip played in a loop, so the same helper drives both the plumbing tests and the full-chain test that
needs a PERSON for the pose model to find.

It does not trickle candidates (aiortc embeds them in the SDP), exactly like the receiver.
"""
import asyncio

import av
import cv2
import numpy as np
import socketio
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack


def synthetic_frame(width, height, tick):
    """BGR image: a gradient with a white box that moves every tick, so consecutive frames differ."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    image[:, :, 1] = np.linspace(255, 0, height, dtype=np.uint8)[:, None]
    x = (tick * 7) % max(width - 60, 1)
    y = (tick * 3) % max(height - 60, 1)
    image[y:y + 60, x:x + 60] = (255, 255, 255)
    return image


class FrameSourceTrack(VideoStreamTrack):
    """A 30 fps video track from `frame_at(tick) -> BGR array`."""
    kind = "video"

    def __init__(self, frame_at):
        super().__init__()
        self._frame_at = frame_at
        self._tick = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame.from_ndarray(self._frame_at(self._tick), format="bgr24")
        self._tick += 1
        frame.pts = pts
        frame.time_base = time_base
        return frame


class ClipFrames:
    """Frames of a video file, looped, resized to (width, height) when one is given."""

    def __init__(self, path, size=None):
        self._cap = cv2.VideoCapture(path)
        self._size = size
        self._last = None

    def __call__(self, tick):
        ok, frame = self._cap.read()
        if not ok:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        if ok:
            if self._size is not None:
                frame = cv2.resize(frame, self._size)
            self._last = frame
        return self._last


class FakeSender:
    def __init__(self, base_url, room_id, pin, width=640, height=480, clip=None, media_mode="webrtc"):
        self.base_url = base_url
        self.room_id = room_id
        self.pin = pin
        self.width = width
        self.height = height
        self.clip = clip
        self.media_mode = media_mode
        self.offers_sent = 0
        self.peer_left_count = 0
        self._pc = None
        self._sio = socketio.AsyncClient(reconnection=False)
        sio = self._sio

        @sio.on("peer:joined")
        async def peer_joined(data=None):
            await self._send_offer()

        @sio.on("webrtc:answer")
        async def webrtc_answer(data):
            if self._pc is not None:
                await self._pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))

        @sio.on("peer:left")
        async def peer_left(data=None):
            self.peer_left_count += 1
            await self._close_pc()

    def _track(self):
        if self.clip:
            return FrameSourceTrack(ClipFrames(self.clip, (self.width, self.height) if self.width else None))
        return FrameSourceTrack(lambda tick: synthetic_frame(self.width, self.height, tick))

    async def _join(self):
        ack = await self._sio.call(
            "room:join",
            {"roomId": self.room_id, "role": "sender", "pin": self.pin, "mediaMode": self.media_mode},
            timeout=10)
        if not ack.get("success"):
            raise RuntimeError("fake sender could not join: %s" % ack.get("error"))
        # The server emits peer:joined only to whoever was ALREADY in the room, so a sender that joins
        # second learns the viewer is there from hasPeer in its own ack (the real sender page does too).
        if ack.get("hasPeer"):
            await self._send_offer()

    async def start(self):
        await self._sio.connect(self.base_url, transports=["websocket"], wait_timeout=10)
        await self._join()

    async def _send_offer(self):
        await self._close_pc()
        pc = RTCPeerConnection()
        self._pc = pc
        pc.addTrack(self._track())
        await pc.setLocalDescription(await pc.createOffer())
        self.offers_sent += 1
        await self._sio.emit("webrtc:offer", {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

    async def _close_pc(self):
        pc, self._pc = self._pc, None
        if pc is not None:
            await pc.close()

    async def leave_and_rejoin(self):
        await self._sio.emit("room:leave")
        await self._close_pc()
        await asyncio.sleep(0.3)
        await self._join()

    async def stop(self):
        if self._sio.connected:
            await self._sio.emit("room:leave")
            await self._sio.disconnect()
        await self._close_pc()
