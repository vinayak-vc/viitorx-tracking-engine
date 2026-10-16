# Viitorx Tracking wire specification

**Spec version 1.0 (2026-10-16).** The contract between the tracking engine (this repo) and any receiver in any engine: Unity today, Unreal or anything that can read UDP and parse JSON later.
Canonical copy: this file, in the engine repo. The code that defines each wire is named at the top of its section; **`tests/test_wire_spec.py` fails if this file and that code disagree**
(field names, offsets, ports, the 52 blendshape names), so a change to a wire has to change the encoder, this file and the fixtures together.

Nothing here has been run against a receiver written in a language other than C# and Python. The Unity receivers (`Runtime/Vision`) and the Python reference receivers
(`tools/*/..._udp_receiver.py`, `hand_wire.parse_message`, `face_wire.parse_message`, `cutout_wire.Assembler`) are the only two implementations checked against it.

## 1. Streams

| Stream | Default port | Encoding | Defined by | Spec version field |
|---|---|---|---|---|
| Body | `8899` | JSON, one object per datagram | `wholebody_udp_sender.py`, `multiperson_udp_sender.py`, `tools/video/video_udp_sender.py` (section 6) | **none** (legacy, "v0") |
| Hands | `8901` | JSON | `hand_wire.py` | `ver` = 1 |
| Face | `8902` | JSON | `face_wire.py` | `ver` = 1 |
| Cutout | `8903` | **binary**, one frame split into datagrams | `cutout_wire.py` | `ver` = 1 (byte 4) |

`8897` is the body supervisor's single-instance lock port, not a data port. Ports are settings of the engine and of the receiver: nothing else depends on these numbers. `8904` and up are unassigned.

**One sender per port.** Two senders on one port interleave their messages; a receiver tells producers apart by `sid` (JSON wires) or `session` (cutout) and treats a new value as a new producer.

## 2. Rules for every wire

**Transport.** UDP, loopback (`127.0.0.1`) by default. One message per datagram, except the cutout, where one frame is several datagrams. There is **no authentication, no encryption, no delivery or ordering guarantee**. A receiver that listens on a network interface other than loopback accepts datagrams from anyone on that network.

**Versioning (JSON wires and cutout).**
1. `ver` is an integer. A receiver **MUST ignore** (and should count) a datagram whose `ver` it does not know. It must not guess.
2. Adding an **optional** field does **not** change `ver`. A receiver **MUST ignore members it does not know**, and must accept members in any order and with any whitespace.
3. Removing a field, renaming it, changing its type, unit, order or meaning, or making an optional field mandatory **does** change `ver`.
4. The blendshape order has its own number, `bsv` (face), because it changes on its own schedule. `bsv` 1 is the 52 names of section 4.
5. The cutout header carries `header_len`: bytes between the known fields and `header_len` are skipped, so a later version may append header fields. A reader still checks `ver` first.
6. `kind` names the message type. A receiver ignores a `kind` it does not know.
7. The **body wire has no `ver` and no `kind`.** A receiver cannot tell its versions apart, so an incompatible change to it must use a new port or a new `kind`, never a silent change on 8899.

**Space and units.** Unless a field says otherwise: camera space is **X right, Y down, Z forward** (the OpenCV convention, right-handed); metres; degrees for angles; image coordinates are fractions of the frame with **y down**.
`mir` = 1 means the sender flipped the picture left-right BEFORE the model (a selfie view): every x, every position and `yaw` / `roll` then describe the flipped picture.
An engine with a different convention converts on receipt (section 7); the wire never changes for it.

**Estimates, not measurements.** With a single camera there is no stereo. Every `pos`, `wl` scale and head pose is an estimate from the subject's apparent size and an **assumed** field of view (`fpx`). `lm` (image landmarks) and `bs` (blendshapes) do not depend on the assumption.

**Absent subjects.** A hand or face that is not in the picture is **not in the list**: there is no placeholder and nothing is predicted beyond a `HELD` frame or two. A frame with nobody is still sent, with `n` = 0, so a receiver sees the stream is alive.
The body wire has no such rule: no body, no datagram.

**Receiver behaviour (recommended, what the Unity receivers do).**
- Reject, and count by reason, anything that does not parse; never throw on a datagram.
- `sid` change = new producer: drop what you remember by `id`. Within one `sid`, drop `seq` less than or equal to the last accepted.
- Stale = no datagram for about 0.5 s. A stream that was live and went quiet is `stale`; one that never spoke is `no stream`.
- Bound everything a sender can make you allocate (the cutout does this by rule, section 5).

## 3. Hands (`kind` "hands", port 8901)

One datagram per processed frame. Compact UTF-8 JSON.

### 3.1 Message members <!-- spec:hands.header -->

| Member | Type | Meaning |
|---|---|---|
| `ver` | int | 1 |
| `kind` | string | `"hands"` |
| `sid` | string | producer session; a new value is a new producer |
| `seq` | int | increases by one per datagram within a session |
| `t` | float | sender wall clock, unix seconds, when the datagram was built |
| `lat` | float | ms from the frame being read to the datagram being built |
| `src` | string | `camera` / `stream` / `lan` / `file` |
| `img` | [int, int] | width and height of the frame the model saw, after rotation and flip |
| `fpx` | float | focal length in pixels the 3D position used (**assumed**) |
| `mir` | 0 or 1 | the picture was mirrored before the model |
| `n` | int | number of entries in `hands`; must equal its length |
| `hands` | array | the hands in view, possibly empty |

### 3.2 Hand entry <!-- spec:hands.entry -->

| Member | Type | Meaning |
|---|---|---|
| `id` | int | stable per hand, never reused within a session |
| `side` | `"L"` / `"R"` | which hand. The sender decides it from the network's handedness label and the `--sides` setting (auto: follows `mir`). **Not checked against a known hand** (H-01 NOT TESTED) |
| `hd` | float 0..1 | averaged handedness score the label came from |
| `conf` | float 0..1 | landmark network confidence this frame |
| `st` | 0 / 1 | 0 TRACKED (read this frame), 1 HELD (last values, a frame or two stale) |
| `lm` | 21 x [x, y, z] | image landmarks: x / width, y / height, z / width (smaller z is nearer); y down |
| `wl` | 21 x [x, y, z] | metres, hand-centred: x right, y **down**, z away from the camera |
| `pos` | [X, Y, Z] | metres, camera space, the hand's centre. **Absent** when the 3D fit was refused |
| `why` | string | present only when `pos` is absent: `shape` / `degenerate` / `scale` / `residual` / `range` |
| `fing` | 5 floats 0..1 | curl of thumb, index, middle, ring, pinky |
| `ext` | 5 x 0/1 | 1 when that finger is extended (with hysteresis) |
| `pinch` | float 0..1 | 1 when thumb and index tips touch |
| `ges` | string | `OPEN` `FIST` `POINT` `VICTORY` `ROCK` `THUMBS_UP` `OK` `NONE` (a closed set, stabilised over a few frames) |

Landmark order is MediaPipe's: 0 wrist; 1-4 thumb; 5-8 index; 9-12 middle; 13-16 ring; 17-20 pinky. A `pos` of a hand is only present when the fit holds; never use a stale or invented one.
A receiver must treat an entry with `lm` / `wl` not 21 x 3, `side` not `L` / `R`, `fing` / `ext` not 5 long, or `pos` not 3 long as malformed (reference: `hand_wire.parse_message`).

## 4. Face (`kind` "face", port 8902)

### 4.1 Message members <!-- spec:face.header -->

| Member | Type | Meaning |
|---|---|---|
| `ver` | int | 1 |
| `kind` | string | `"face"` |
| `sid` | string | producer session |
| `seq` | int | +1 per datagram within a session |
| `t` | float | sender wall clock, unix seconds |
| `lat` | float | ms from frame read to datagram built |
| `src` | string | `camera` / `stream` / `lan` / `file` |
| `img` | [int, int] | frame size the model saw |
| `fpx` | float | assumed focal length in pixels |
| `mir` | 0 or 1 | picture mirrored before the model |
| `bsv` | int | blendshape ordering of `bs`: 1 = the 52 names below |
| `n` | int | number of entries in `faces`; must equal its length |
| `faces` | array | the faces in view, possibly empty |

### 4.2 Face entry <!-- spec:face.entry -->

| Member | Type | Meaning |
|---|---|---|
| `id` | int | stable per face, never reused within a session |
| `conf` | float 0..1 | the mesh network's face presence this frame |
| `st` | 0 / 1 | 0 TRACKED, 1 HELD |
| `bs` | 52 floats 0..1 | blendshape scores in the order below. **Absent** with `--no-blendshapes` |
| `lm` | 478 (or 468) x [x, y, z] | image landmarks as for hands: 468 mesh points then 10 iris points. **Absent** with `--landmarks none`; **468 long** with `--landmarks mesh` |
| `pos` | [X, Y, Z] | metres, camera space, the point between the eyes. **Absent** when refused |
| `why` | string | present only when `pos` is absent: `degenerate` / `range` |
| `yaw` | float | degrees; > 0 the nose moves toward the image's right |
| `pitch` | float | degrees; > 0 the nose moves down |
| `roll` | float | degrees; > 0 the eye line tilts clockwise on screen |
| `rot` | 9 floats | the head's rotation, row-major 3x3; columns are the head's x, y, z axes in camera axes; **an upright face looking straight at the camera is the identity** |
| `pose` | 0 / 1 | 0 when the landmarks were degenerate: `yaw` / `pitch` / `roll` / `rot` are then not valid |

Head pose is a geometric estimate from four landmarks (outer eye corners 33 and 263, forehead 10, chin 152), not MediaPipe's own facial transformation matrix. `pos` uses an assumed 0.09 m between the outer eye corners.

### 4.3 The 52 blendshapes (`bsv` 1) <!-- spec:face.blendshapes -->

Index 0 is `_neutral` (MediaPipe's own extra channel: how neutral the face is). Indexes 1-51 are the ARKit-compatible blendshapes. **There is no `tongueOut`.**
`Left` and `Right` are the **subject's** left and right, as in ARKit. Bind by **name**, never by ARKit's own order.

```
_neutral browDownLeft browDownRight browInnerUp browOuterUpLeft browOuterUpRight cheekPuff cheekSquintLeft cheekSquintRight eyeBlinkLeft eyeBlinkRight
eyeLookDownLeft eyeLookDownRight eyeLookInLeft eyeLookInRight eyeLookOutLeft eyeLookOutRight eyeLookUpLeft eyeLookUpRight eyeSquintLeft eyeSquintRight
eyeWideLeft eyeWideRight jawForward jawLeft jawOpen jawRight mouthClose mouthDimpleLeft mouthDimpleRight mouthFrownLeft mouthFrownRight mouthFunnel
mouthLeft mouthLowerDownLeft mouthLowerDownRight mouthPressLeft mouthPressRight mouthPucker mouthRight mouthRollLower mouthRollUpper
mouthShrugLower mouthShrugUpper mouthSmileLeft mouthSmileRight mouthStretchLeft mouthStretchRight mouthUpperUpLeft mouthUpperUpRight
noseSneerLeft noseSneerRight
```

Which eye of an avatar a `Left` score drives (a mirror shows the subject's left eye on the viewer's left; a photo of someone else shows it on the right) is the receiver's choice. **Not checked on a live face** (H-02 NOT TESTED).

Sizes: a face with all 478 landmarks is about 13 KB; two faces about 25 KB. The Unity host keeps `--max-faces` to 4 (about 51 KB), inside the 65 507-byte UDP limit; the sender itself does not cap it, and a datagram over that limit cannot be sent.

## 5. Cutout (port 8903, binary)

Carries image data, so it is binary. **All integers are little-endian.** A frame is one picture and one person mask; it is far bigger than a datagram (1280x720 RGBA is 3.7 MB), so a frame is cut into **chunks**, and **every datagram begins with the whole 72-byte header**, so any one datagram says which frame it belongs to and where it goes. A receiver keeps no state to read a header.

### 5.1 Header (72 bytes, at the start of every datagram) <!-- spec:cutout.header -->

| Off | Size | Type | Name | Meaning |
|---|---|---|---|---|
| 0 | 4 | bytes | `magic` | `VXCO` |
| 4 | 1 | u8 | `ver` | 1 |
| 5 | 1 | u8 | `kind` | 1 = a frame; anything else is ignored |
| 6 | 2 | u16 | `header_len` | 72 in version 1; bytes between the known fields and `header_len` are skipped |
| 8 | 4 | u32 | `session` | a new random value per sender process = a new producer |
| 12 | 4 | u32 | `seq` | frame number, +1 per frame, wraps at 2^32 (compare as a signed 32-bit difference) |
| 16 | 8 | f64 | `t` | sender wall clock, unix seconds, when the frame was packed |
| 24 | 4 | f32 | `lat_ms` | ms from camera frame read to frame packed |
| 28 | 4 | f32 | `infer_ms` | ms of segmentation work for this frame |
| 32 | 2 | u16 | `chunk_index` | 0 .. chunk_count-1 |
| 34 | 2 | u16 | `chunk_count` | at least 1 |
| 36 | 4 | u32 | `chunk_offset` | where this chunk's bytes go inside the frame's payload |
| 40 | 4 | u32 | `payload_len` | the whole payload: colour section plus mask section |
| 44 | 1 | u8 | `flags` | bit 0 mirrored, bit 1 live source (0 = a file), bit 2 nobody found |
| 45 | 1 | u8 | `color_enc` | 0 none, 1 raw RGBA32, 2 JPEG |
| 46 | 1 | u8 | `mask_enc` | 1 raw A8, 2 deflate (a raw RFC 1951 stream of the A8 image, no zlib header) |
| 47 | 1 | u8 | `model_id` | 0 unknown, 1 selfie general (256x256), 2 selfie landscape (256x144) |
| 48 | 2 | u16 | `color_w` | 0 when `color_enc` is 0 |
| 50 | 2 | u16 | `color_h` | |
| 52 | 2 | u16 | `mask_w` | the mask is often smaller than the picture: it is stretched over it |
| 54 | 2 | u16 | `mask_h` | |
| 56 | 4 | u32 | `color_len` | bytes of the colour section |
| 60 | 4 | u32 | `mask_len` | bytes of the mask section; `color_len` + `mask_len` = `payload_len` |
| 64 | 2 | u16 | `fg_permille` | foreground area x 1000 |
| 66 | 1 | u8 | `people` | people cut out: the larger of the people tracked and the foreground groups, capped at 255; 0 = nobody |
| 67 | 1 | u8 | `reserved1` | 0 |
| 68 | 4 | u32 | `reserved2` | 0 (room for a payload checksum) |

### 5.2 Payload

The colour section, then the mask section, laid end to end, row-major, **top row first**.
- raw RGBA32: `R, G, B, 255` per pixel (the alpha byte is **not** the mask; the mask is its own section); `color_len` must equal `color_w * color_h * 4`.
- JPEG: a baseline JPEG of the picture. A decoder may hand the rows back bottom-up; the wire's rows are top-first, so flip if yours does.
- raw A8: one byte per mask pixel, 0 = background, 255 = person, soft in between; `mask_len` must equal `mask_w * mask_h`.
- deflate: the A8 image as a raw RFC 1951 stream; it must inflate to exactly `mask_w * mask_h` bytes (refuse more, or less).

### 5.3 What a receiver does

1. Check, in this order and reject with the named reason: length at least 72 (`short`); `magic` (`magic`); `ver` (`version`); `kind` (`kind`); `header_len` between 72 and the datagram length (`header`); `chunk_count` at least 1 and `chunk_index` below it, the chunk non-empty and inside the payload (`chunk`); `color_len + mask_len = payload_len` and the section rules above (`size`); every dimension at most 4096 and the mask at least 1x1 (`dimensions`); `color_enc` in 0..2 and `mask_enc` in 1..2 (`encoding`).
2. Chunks of one frame share `(session, seq)`. Write each chunk's bytes at `chunk_offset`; the frame is complete when every `chunk_index` has arrived once **and** the bytes received equal `payload_len` (overlaps or gaps are refused as `size`).
3. A chunk for a **newer** `seq` abandons an unfinished older frame (never wait: a late live picture is worse than a missing one). A chunk for an **older** `seq` is ignored. A repeated chunk is ignored. A chunk of an already-complete frame is a duplicate, not a new assembly. A new `session` is a new producer: drop the partial frame.
4. Later chunks of a frame must repeat the first chunk's `payload_len`, `chunk_count`, encodings, dimensions and section lengths; a chunk that does not is refused (`header`).
5. **Bound allocation.** `payload_len` is capped at 48 MiB and every dimension at 4096, so a sender that lies cannot make a receiver allocate without limit.
6. `seq` wraps: a frame is newer when `(a - b) & 0xFFFFFFFF` is between 1 and 0x7FFFFFFF.

**Chunk size.** The default 60 000 payload bytes fills a loopback datagram. Over a real network the IP layer would cut that into about 40 fragments and lose the whole datagram if one fragment is lost; a network receiver wants about 1 200 (`--chunk-bytes 1200`) with `--color jpeg --mask-enc deflate`. **These network settings are unit-tested with golden datagrams but have never been run between two machines.**

## 6. Body (port 8899, legacy, no `ver`)

Defined by the senders, not by a wire module; the README's "UDP wire contract" and the sender sources are the detail. One JSON object per datagram, one datagram per frame **with a body**. **No `ver`, no `kind`**: see the versioning rule 7. Space: camera space, X right, Y down, Z forward. Joint order is **BlazePose's 33** (0 nose, 1-3 left eye inner / eye / outer, 4-6 right eye inner / eye / outer, 7 left ear, 8 right ear, 9-10 mouth left / right, 11 left shoulder, 12 right shoulder, 13 left elbow, 14 right elbow, 15 left wrist, 16 right wrist, 17-22 pinkies / index fingers / thumbs, 23 left hip, 24 right hip, 25 left knee, 26 right knee, 27 left ankle, 28 right ankle, 29 left heel, 30 right heel, 31 left foot index, 32 right foot index). Joints the model does not give are zeros.

### 6.1 Single person members <!-- spec:body.single -->

| Member | Type | Meaning |
|---|---|---|
| `lm` | 33 x [x, y, z, vis] | body, hip-relative metres, joint order above |
| `lh` | 21 x [x, y, z] | left hand, hip-relative metres; **omitted** when not tracked |
| `rh` | 21 x [x, y, z] | right hand; omitted when not tracked |
| `xyz` | [x, y, z] | measured mid-hip, **millimetres**, camera space (the root position) |
| `src` | 33 x 0/1 | 1 = depth-measured, 0 = hip-plane fallback (debug) |
| `seq` | int | monotonic frame id within a session |
| `sid` | string | producer session |
| `t` | float | send time, unix seconds |
| `st` | 33 x int | trust channel: -1 no tracker covers the joint, 0..4 TRACKED / WEAK / PREDICTED / LOST / RECOVERING |
| `own` | string | ownership state (`LOCKED` ...); **omitted** when ownership is not running (absent is not "not locked") |
| `lat` | float | measured camera-to-payload latency, ms |
| `fr` | int | what anchored the root: 0 hips (measured), 1 shoulders, 2 nose (estimated). **Absent = hips.** Video senders only |

The OAK-D sender measures depth; the **video senders estimate it** (the engine has no OAK-D support, so every body from the engine is estimated: `src` is all 0, unless the sender is run with `--assume-measured`, which only relabels it).

### 6.2 Multi-person members <!-- spec:body.multi -->

The multi-person senders send `persons` and also republish the most-established person at the root in the single-person shape above, so a single-person receiver sees one person and needs no change.

| Member | Type | Meaning |
|---|---|---|
| `persons` | array | one entry per posed person: `id`, `state` (`CONFIRMED` / `LOST`), `lm`, `xyz`, `src`, `st`, `fr`, `lh`, `rh` as above; `id` is stable and never reused |
| `n` | int | people posed this frame |
| `ndet` | int | people the detector saw (can exceed `n`: the GPU budget is the limit) |
| `ntrack` | int | people the tracker is holding, posed or not |

## 7. Receiving in an engine with another convention

**Unity** (what `Runtime/Vision` does, tested): flip `y` of every position, `wl` and the head rotation (y up, left-handed); conjugate the head rotation by the y reflection; leave image landmarks in image space (y down).
**Unreal** (derived from the axes, **not implemented or tested**): X forward = wire Z, Y right = wire X, Z up = minus wire Y, metres x 100 for centimetres; build rotations in the same mapped basis.
In any engine: do the conversion once, at parse time, and keep image coordinates (`lm`) in image space.

## 8. Conformance

A receiver is conformant for a stream when it passes the **golden datagrams** and the checklist below.

**Files** (in the Unity repo, `Tests/EditMode/VisionFixtures/`, written by the production encoders from synthetic subjects of known pose; regenerate with `tools/video/make_vision_fixtures.py`; `tests/test_vision_fixtures.py` fails if they drift):

| Files | What they pin |
|---|---|
| `hands_two`, `hands_empty`, `hands_held_refused`, `hands_four_gestures`, `hands_pretty_reordered_extra` (`.json`) | two hands; `n` 0; a HELD hand and a refused position; every gesture; reordered members, whitespace and an unknown member |
| `face_full`, `face_mesh_only`, `face_no_landmarks`, `face_no_blendshapes`, `face_refused`, `face_two`, `face_empty`, `face_exponent` (`.json`) | the full face; 468 landmarks; no landmarks; no blendshapes; a refused position; two faces; `n` 0; numbers written in exponent form |
| `bad_version`, `bad_kind`, `bad_count`, `bad_landmarks`, `bad_side`, `bad_truncated`, `bad_not_json` | a datagram that must be refused, each for its own reason |
| `real_hands`, `real_face` | one datagram each, as the real networks wrote it (format coverage, not regenerated) |
| `cutout_rgba_00..03` | one frame in four chunks (raw RGBA, raw mask); `cutout_next_00..03` the next frame; `cutout_other_00..03` a frame of another session; `cutout_jpeg_00..02` a JPEG picture; `cutout_maskonly_00` a deflated mask with no colour |
| `cutout_bad_*`, `cutout_truncated` | one malformed header or sizes each: `magic`, `version`, `kind`, `header_len`, `chunk_index`, `sizes`, `dimension`, `encoding`; `cutout_bad_inflate_00` a consistent header whose deflate stream inflates to more than it declares; `cutout_truncated` a 40-byte datagram |
| `args_*.txt` | not wire: the engine command lines the Unity host builds (see the engine CLI contract test) |
| `conformance.json` | the **expected result of every file above** in machine-readable form (section 8.1) |

### 8.1 `conformance.json`

For every datagram file: the stream, whether it must be accepted or the reason it must be refused, and for accepted files the headline values a receiver must read back (counts, ids, sides, sizes, a few numbers). It is generated by `tools/video/make_wire_conformance.py` from the Python reference parsers and checked for drift by `tests/test_wire_spec.py`. A receiver in another language can drive its whole conformance test from this one file.

### 8.2 Checklist for a new receiver

JSON wires (hands, face):
1. Parse every positive file; the counts and values equal `conformance.json`.
2. Refuse every `bad_*` file, each for the right reason; never throw.
3. Read `hands_pretty_reordered_extra`: members reordered, whitespace, an unknown member are all fine.
4. Accept an absent `pos` (with `why`), `bs`, `lm`; never invent a value for them.
5. Read numbers in exponent notation (`1e-05`).
6. `n` that disagrees with the list is refused. An unknown `ver` is refused even if the rest would parse.
7. `sid` change resets what you remember by `id`; `seq` not greater than the last, same `sid`, is dropped.
8. Stale after about 0.5 s without a datagram.

Cutout:
9. Reassemble `cutout_rgba_00..03` in any order, with one chunk repeated, into one frame equal to `conformance.json`.
10. A frame of the next `seq` arriving while one is unfinished abandons the old one; an older `seq` is ignored; a new `session` drops the partial frame.
11. Refuse every `cutout_bad_*` and `cutout_truncated` with the named reason, never throwing.
12. Flip a JPEG's rows if your decoder returns them bottom-up; sample the mask with the picture's top row first.
13. Cap every allocation (48 MiB, 4096 per side) before allocating.

## 9. Status and what is not covered

- Verified: the field tables against the encoders (`tests/test_wire_spec.py`); the fixtures against the encoders (`tests/test_vision_fixtures.py`); the C# receivers against the fixtures; the senders' command lines against their parsers.
- **Not verified:** any receiver other than C# and Python; the cutout over a real network between two machines (chunk size 1 200, JPEG, deflate); the body wire's members beyond a source check (the body senders are older and have no encoder module); `side` of a hand and the eye assignment of a face on a live subject; accuracy of any estimated `pos` / `wl` / head pose against a ruler.
- Changing a wire: edit the encoder, this file, the fixtures (`make_vision_fixtures.py`) and `conformance.json`, and bump `ver` if rule 3 of section 2 says so; both repos need an ADR.
