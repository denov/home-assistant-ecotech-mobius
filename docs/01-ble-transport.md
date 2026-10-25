# 01 — BLE transport

How a Mobius device talks over Bluetooth Low Energy. This was worked out by watching traffic between Home Assistant and real devices through an ESPHome proxy (`tools/mobius_cli.py -v` logs every frame in hex). The devices tested are listed in [02](02-attributes-and-control.md#test-tank).

## Topology

- Every device of a tank is on one **Thread mesh**. The mesh is identified by its `panId`, which is in each device's advert.
- A client connects over BLE to **one** device of the tank (the *bridge*).
- Requests for the bridge itself are sent to it directly.
- Requests for any other device of the tank are wrapped in a **CoAP relay** frame (below), addressed to the target's mesh-local IPv6. The bridge forwards them over the mesh and passes the answer back.
- No pairing, bonding or encryption is needed. The devices accept a plain connection and answer requests straight away.

## Advertisements

- The **passive** advert has only the flags, the 128-bit service UUID `01ff0100-ba5e-f4ee-5ca1-eb1e5e4b1ce0` and the local name `MOBIUS`.
- The model, serial and panId are in the **scan response**, so they only show up with **active** scanning.
- They're in manufacturer-specific data (AD type `0xFF`), and the device uses the **whole** AD payload as its own data. The first 2 bytes, which BLE treats as the company id, are really a format byte and a flags byte. Home Assistant and bleak report the data as `{company_id: rest}` with `company_id = b[0] | b[1] << 8`. Rebuild `b = company_id.to_bytes(2, "little") + rest` before parsing.

`b` layouts the integration accepts (all integers little-endian):

| len(b) | b[0] | layout |
|---|---|---|
| 18 / 25 / 28 | 2 | `[1]` flags, `[2:4]` model u16, `[5:9]` device-specific i32, `[9:11]` panId u16, `[11:25]` serial, ASCII |
| 18 / 25 / 28 | 1 | `[1]` pending notifications, `[2:4]` model, `[5:9]` device-specific, `[9:11]` panId, serial = ASCII `[11:25]`, or decimal-formatted from bytes 11–17 when len is 18 |
| 19 | 1 | `[1]` pending notifications, `[2:4]` model, `[4:8]` epoch u32, `[8:12]` device-specific, `[12:14]` panId, `[14:19]` serial as hex |
| 16 | 1 | `[1]` pending notifications, `[2:4]` model, `[5:9]` device-specific, `[9:11]` panId, `[11:16]` serial as hex |

Flags byte (format 2): bit 1 is set on current devices (clear = legacy firmware); bit 4 is set while the device reports an error. The integration doesn't use the other bits.

Model ids seen on tested devices: 42 (VorTech MP40w G3 QD), 149 (Vectra L2) and 195 (a current-generation Radion). The full list the integration names is `MODELS` in `protocol.py`.

## GATT

| UUID | Role |
|---|---|
| `01FF0100-BA5E-F4EE-5CA1-EB1E5E4B1CE0` | Mobius service |
| `01FF0101-…` RX_DATA | notify: device → client, a chunk that isn't the last |
| `01FF0102-…` RX_FINAL | notify: device → client, the last chunk of a frame |
| `01FF0103-…` TX_DATA | write without response: client → device, a chunk that isn't the last |
| `01FF0104-…` TX_FINAL | write without response: client → device, the last chunk of a frame |
| `0000180A-…` | standard Device Information service |

The device also has a firmware update service (`01FF5550-…`), which the integration leaves alone.

Connection sequence:
1. Connect. The BLE stack (or the ESPHome proxy) negotiates the MTU.
2. Enable notifications on RX_DATA and RX_FINAL.
3. Send requests.

**Sending:** split the frame into chunks of at most `MTU − 3` bytes (at least 20). Write every chunk except the last to TX_DATA, and the last one to TX_FINAL. A frame that fits in one chunk goes straight to TX_FINAL.

**Receiving:** append each RX_DATA notification to a buffer. On RX_FINAL, append it, decode the whole buffer as one frame, then clear the buffer.

A device accepts **one BLE connection at a time**. While the phone app is connected, other clients can't connect to that device.

## Frame

```
offset size  field
0      1     sync = 0x02
1      1     opGroup
2      1     opCode
3      2     messageId  (u16 LE, chosen by the client; the integration counts 1..19999)
5      1     rsv0: bits 0-1 instance, bits 2-7 group (see 02, Control)
6      1     rsv1: bit 0 = do not respond
7      2     payload length (u16 LE)
9      n     payload
9+n    2     CRC16 (LE) over bytes [1 .. 9+n), i.e. opGroup through payload
```

CRC: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, not reflected, no final xor). The CRC of every frame received from a device checks out with this.

Op groups:

| value | meaning |
|---|---|
| `0xDE` | request |
| `0xDF` | confirm: the answer to a `0xDE` request, with the same opCode |
| `0xCE` / `0xCF` | Thread stack request / confirm. The integration ignores these |

A response belongs to a request when its opGroup is `0xDF`, its opCode equals the request's, and its messageId is the same.

The integration sends one request at a time per connection. It waits 5 s for an answer and retries once.

## Opcodes (group `0xDE` / `0xDF`)

| op | meaning |
|---|---|
| 23 (`0x17`) | Get attributes, 1-byte index / count / length |
| 24 (`0x18`) | Set attributes, 1-byte index / count / length |
| 25 (`0x19`) | CoAP relay request |
| 26 (`0x1A`) | CoAP relay response (device → client) |
| 28 (`0x1C`) | Get attributes, 2-byte index / count / length |
| 29 (`0x1D`) | Set attributes, 2-byte index / count / length |
| 41 (`0x29`) | Get supported opcodes |

**Get supported opcodes** has an empty payload. Its answer is `status u8 | u16 | count u16 | count × opcode u8`. If the list holds `0x1C` / `0x1D`, the 2-byte variants can be used; otherwise use `0x17` / `0x18`. Every tested device answered with status `0xFF` and no list, and the 1-byte variants work on all of them. The 2-byte variants haven't been seen in use.

### Get payload, repeated per attribute
Op 23: `attrId u16 | startIndex u8 | count u8`. Op 28: `attrId u16 | startIndex u16 | count u16`.

Several attributes can be asked for in one request. A count of all ones (`0xFF` / `0xFFFF`) asks for every element.

### Get response
`status u8`, then, repeated: `attrId u16 | startIndex | count | elemLen | count × elemLen bytes`. Index, count and length are u8 for op 23 and u16 for op 28.

- Status 0 = success. The status codes the integration names are `Status` in `protocol.py`; 7 (unsupported attribute) is the one seen in practice.
- An array attribute can come back as several blocks of the same `attrId`, each with its own start index (e.g. `PhysicalValues`, doc 02).

### Set payload, repeated per attribute
Op 24: `attrId u16 | startIndex u8 | count u8 | elemLen u8 | count × elemLen bytes`. Op 29: the same with u16 index / count / length.

The answer is `status u8` followed by 2 more bytes (`00 ffff` seen), which the integration doesn't use.

## CoAP relay

Request: a `DE 19` frame whose payload is:
```
16  target mesh-local IPv6, byte-reversed
4   token (i32 LE, chosen by the client)
2   method (1 = GET)
2   inner length (u16 LE)
1   CoAP type (0 = confirmable)
n   inner = a complete frame (02 … crc) for the target device
```

The bridge first answers with a `DF 19` frame of its own, which only says the relay was accepted. The integration ignores it.

Response: a separate, unsolicited `DF 1A` frame from the bridge. Its payload is:
```
16  source IPv6
4   token, the same as in the request
2   CoAP response code
2   inner length
1   type
n   inner = the target's complete DF frame
```

The integration matches a response to its request by the token, then decodes the inner frame like a direct answer. It hasn't needed the source address, so its byte order isn't confirmed.

Relayed reads answer in 0.3–0.5 s per device. A relayed request to the bridge itself gets no answer: read the bridge directly.

## Python reference

```python
import struct

def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc

def frame_encode(group: int, op: int, msg_id: int, payload: bytes, rsv=b"\x00\x00") -> bytes:
    body = struct.pack("<BBH2sH", group, op, msg_id, rsv, len(payload)) + payload
    return b"\x02" + body + struct.pack("<H", crc16(body))

def frame_decode(frame: bytes):
    assert frame[0] == 0x02
    group, op, msg_id, rsv, n = struct.unpack_from("<BBH2sH", frame, 1)
    payload = frame[9:9 + n]
    (crc,) = struct.unpack_from("<H", frame, 9 + n)
    assert crc == crc16(frame[1:9 + n])
    return group, op, msg_id, rsv, payload

def coap_wrap(ipv6: bytes, token: int, inner: bytes) -> bytes:
    return ipv6[::-1] + struct.pack("<iHHB", token, 1, len(inner), 0) + inner
```

Example: get `MeshLocalAddresses` (1005, doc 02), all elements, with op 23 and messageId 1:
`frame_encode(0xDE, 0x17, 1, struct.pack("<HBB", 1005, 0, 0xFF))`.
