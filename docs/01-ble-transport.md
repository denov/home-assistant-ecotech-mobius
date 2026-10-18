# 01 — BLE transport

Source: jadx decompile at `~/app-decompiled/sources`. All paths below are relative to `com/c2/comm/`.

## Topology

- The phone connects over BLE to **one** device in a tank group (any device with the same `panId`).
- That device forwards requests to the other devices over the **Thread mesh**, addressed by each device's 16-byte mesh-local IPv6 address.
- Routing lives in `Comm.sendRequest` (`Comm.java:~60-170`):
  - target == connected device → send the FSCI frame directly
  - otherwise, if the target's IPv6 is known → wrap it in a CoAP-relay frame (below) and send it to the connected device
  - a cloud route (`McpDeviceRequest` via `CloudManager`) also exists; see doc 04
- **No pairing, bonding, encryption or passcode at the transport layer** was found. Any app-level passcode is still to be checked (settings/passcode).

## Discovery (advertisements)

- The app scans with **no filter** (`bluetooth/BluetoothService.java:98`) and identifies devices from AD type `0xFF` (manufacturer specific), `bluetooth/Peripheral.java:getManufacturerData`.
- The app parses the **whole** AD payload, including the 2 bytes BLE treats as the company id. In Home Assistant / bleak terms, `manufacturer_data` is `{company_id: rest}` with `company_id = b[0] | b[1] << 8`. So `b[0]` (format version) and `b[1]` (flags) show up as the "company id". Rebuild `b = company_id.to_bytes(2, "little") + rest` before parsing.

`Peripheral.parseManufactureData(b)` handles these formats (all integers little-endian):

| len(b) | b[0] | layout |
|---|---|---|
| 19 | 1 | `[1]` pendingNotif, `[2:4]` model u16, `[4:8]` epoch u32, `[8:12]` devSpecific i32, `[12:14]` panId, `[14:19]` serial (hex) |
| 16 | 1 | `[1]` pendingNotif, `[2:4]` model, `[5:9]` devSpecific, `[9:11]` panId, `[11:16]` serial (hex) |
| 18/25/28 | 2 | `[1]` flags, `[2:4]` model, `[5:9]` devSpecific, `[9:11]` panId, `[11:25]` serial ASCII |
| 18/25/28 | 1 | `[1]` pendingNotif, `[2:4]` model, `[5:9]` devSpecific, `[9:11]` panId, serial = decimal-formatted bytes 11..17 (len 18) or ASCII 11..25 |
| 12 | – | legacy: model = `b[2]`, decimal serial from bytes 3..9 |

Flags byte (format 2): bit0 pendingNotifications, bit1 **set = NOT legacy**, bit2 installing, bit3 manualDiscovery, bit4 hasError.

Legacy devices are only offered a firmware update in the app (`mobius/legacy/*`). There is no separate legacy control protocol to implement.

Relevant models (`M.Model`, `M.java:1543`):

| id | model |
|---|---|
| 10, 11 | VorTech MP10w ES / QD |
| 40, 41, 42 | VorTech MP40w ES / QD / G3 QD |
| 60, 61 | VorTech MP60w ES / QD |
| 30–37, 39 | Radion XR30w (G1…G4 Pro, FW Pro, prototype) |
| 160–163, 175 | Radion XR15w (G3 Pro, FW, G4 Pro, FW Pro, prototype) |
| 176, 177 | Radion XR15 G5 Pro / G5 Blue |
| 192, 193 | Radion XR30 G5 Pro / G5 Blue |
| 144–149 | Vectra M1, L1, S1, S2, M2, L2 |

`panId` groups devices into one mesh network (= one tank), so one BLE connection per panId is enough.

## GATT (`bluetooth/PeripheralConnection.java:45-59`)

| UUID | Role |
|---|---|
| `01FF0100-BA5E-F4EE-5CA1-EB1E5E4B1CE0` | General service |
| `01FF0101-…` RX_DATA | notify: device→phone, non-final chunk |
| `01FF0102-…` RX_FINAL | notify: device→phone, final chunk (end of frame) |
| `01FF0103-…` TX_DATA | write w/o response: phone→device, non-final chunk |
| `01FF0104-…` TX_FINAL | write w/o response: phone→device, final chunk |
| `01FF5550-…` | OTAP service (firmware update; ignore) |
| `0000180A-…` | Device Information (2A29 mfr, 2A24 model, 2A25 serial, 2A27 hw, 2A26 fw) |

Connection sequence:
1. Connect, then request MTU 500 (`requestMtu(ServiceStarter.ERROR_UNKNOWN)` = 500).
2. Discover services, then enable notifications (CCCD `0x2902` = `01 00`) on RX_DATA, then RX_FINAL (and indications on OTAP command if present).
3. Read the Device Information characteristics.
4. Send a Get-attribute request for `MeshLocalAddresses` to learn the connected device's own IPv6.

Chunking (`sendRequest`, line ~90): split the frame into `MTU`-sized pieces. The app uses **20** unless the MTU callback raised it. Write every piece except the last to TX_DATA and the last to TX_FINAL. A frame that fits in one chunk goes straight to TX_FINAL. Each write waits for `onCharacteristicWrite` before the next one.

Reassembly: append RX_DATA notifications to a buffer. On RX_FINAL, append, parse the whole buffer as one frame, then clear it.

## FSCI frame (`requests/Request.java:getData`, `FsciHelper.java`)

```
offset size  field
0      1     sync = 0x02
1      1     opGroup
2      1     opCode
3      2     messageId  (u16 LE, app counter 1..19999)
5      1     rsv0: bits0-1 FSCI instance, bits2-7 group
6      1     rsv1: bit0 = do-not-respond, bit1 = superuser
7      2     payload length (u16 LE)
9      n     payload
9+n    2     CRC16 (LE) over bytes [1 .. 9+n)  (opGroup through payload)
```

CRC: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout) (`utilities/Crc.java`).

Op groups (`M.java:55-62`):

| value | name |
|---|---|
| `0xDE` | C2CI_Request (app protocol requests) |
| `0xDF` | C2CI_Confirm (responses to `0xDE`) |
| `0xCE` / `0xCF` | FSCI_Request / Confirm (NXP Thread stack commands) |
| `0xBE` / `0xBF` | QOTAP request / confirm (firmware update) |

A response matches a request when `(opGroup, opCode) == request.(copGroup, copCode)` **and** messageId is equal. For `0xCF` the messageId isn't checked (`BaseConnection.handleBytes`).

The app allows at most 4 outstanding requests, one per destination address. Timeout is 5 s for direct requests and 20 s for CoAP-relayed ones, with one retry before failing.

## Attribute opcodes (C2CI, group `0xDE`/`0xDF`)

| op | meaning | used when |
|---|---|---|
| 23 (`0x17`) | Get attributes, 1-byte index/count | device lacks op 28 |
| 28 (`0x1C`) | Get attributes, 2-byte index/count | in `supportedOpCodes` |
| 24 (`0x18`) | Set attributes, 1-byte fields | device lacks op 29 |
| 29 (`0x1D`) | Set attributes, 2-byte fields | in `supportedOpCodes` |
| 37 / 32 | Get attribute element counts | |
| 25 (`0x19`) | CoAP relay request | |
| 26 (`0x1A`) | CoAP relay response / indication (device→phone) | |

`supportedOpCodes` comes from `GetOpCodesRequest`; see doc 02.

### Get (op 28) payload, repeated per attribute
`attrId u16 | startIndex u16 | count u16` (op 23: `attrId u16 | idx u8 | count u8`)

### Get response data (`responses/GetC2AttrFsciResponse.java`)
`status u8` then repeated `attrId u16 | index | count | elemLen | count × elemLen bytes` (index/count/len are u16 for op 28 and u8 for op 23). Status 0 = success.

### Set (op 29) payload, repeated
`attrId u16 | startIndex u16 | count u16 | elemLen u16 | count × elemLen bytes` (elements zero-padded to the longest). Op 24 uses u8 for index/count/len.

## CoAP relay (`requests/coap/CoapRequest.java`, `responses/coap/CoapResponse.java`)

Request: an outer FSCI frame `DE 19` whose payload is:
```
16  target mesh-local IPv6, byte-REVERSED
4   token (i32 LE, app counter from 101)
2   method (1 GET, 2 POST, 3 PUT, 4 DELETE)   app always sends GET for relayed frames
2   inner length
1   CoAP type (0 Con, 1 Non, 2 Ack, 3 Rst)    app uses Con
n   inner = a complete FSCI frame (02 … crc) for the target device
```

Response: arrives as an unsolicited `DF 1A` frame. Payload:
```
16  source IPv6        (TODO: confirm byte order against a capture)
4   token (matches the request)
2   CoAP response code (205 content, 404 …)
2   inner length
1   type
n   inner FSCI frame (the target's DF xx response)
```

## Unsolicited frames
- `DF 1A` with no matching token: an indication from a mesh device. Its inner frame is processed like any response (e.g. attribute change pushes).
- `CF 50..55`: Thread network events (scan/create/join/commissioning). Not needed.
- Device-initiated `DE`/`CE` requests are logged and ignored (`respond()` is a no-op).

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

def fsci_encode(group: int, op: int, msg_id: int, payload: bytes, rsv=b"\x00\x00") -> bytes:
    body = struct.pack("<BBH2sH", group, op, msg_id, rsv, len(payload)) + payload
    return b"\x02" + body + struct.pack("<H", crc16(body))

def fsci_decode(frame: bytes):
    assert frame[0] == 0x02
    group, op, msg_id, rsv, n = struct.unpack_from("<BBH2sH", frame, 1)
    payload = frame[9:9 + n]
    (crc,) = struct.unpack_from("<H", frame, 9 + n)
    assert crc == crc16(frame[1:9 + n])
    return group, op, msg_id, rsv, payload

def coap_wrap(ipv6: bytes, token: int, inner: bytes) -> bytes:
    return ipv6[::-1] + struct.pack("<iHHB", token, 1, len(inner), 0) + inner
```

Example: Get `MeshLocalAddresses` (id from doc 02) with op 23, msgId 1:
`fsci_encode(0xDE, 0x17, 1, struct.pack("<HBB", ATTR_ID, 0, 1))`.
