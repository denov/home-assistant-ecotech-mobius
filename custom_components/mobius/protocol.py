# SPDX-License-Identifier: AGPL-3.0-or-later
"""EcoTech Mobius wire protocol: FSCI framing, C2 attributes, CoAP relay, adverts.

Pure Python, no Home Assistant imports, so it can be unit tested and reused by
the CLI. See docs/01-ble-transport.md and docs/02-attributes-and-control.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import struct

SYNC = 0x02

SERVICE_UUID = "01ff0100-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
RX_DATA_UUID = "01ff0101-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
RX_FINAL_UUID = "01ff0102-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
TX_DATA_UUID = "01ff0103-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
TX_FINAL_UUID = "01ff0104-ba5e-f4ee-5ca1-eb1e5e4b1ce0"


class OpGroup(IntEnum):
    C2CI_REQUEST = 0xDE
    C2CI_CONFIRM = 0xDF
    FSCI_REQUEST = 0xCE
    FSCI_CONFIRM = 0xCF


class Op(IntEnum):
    GET_ATTR_V1 = 23
    SET_ATTR_V1 = 24
    COAP_REQUEST = 25
    COAP_INDICATION = 26
    GET_ATTR_V2 = 28
    SET_ATTR_V2 = 29
    GET_OPCODES = 41


class Attr(IntEnum):
    ATTRIBUTE_TABLE_VERSION = 0
    FIRMWARE_VERSION = 1
    HARDWARE_REVISION = 2
    SERIAL_NUMBER = 3
    MODEL = 4
    NAME = 5
    PRIMITIVE_TYPE = 8
    PHYSICAL_VALUES = 101
    POWER_STATE = 102
    OPERATION_STATE = 104
    OPERATION_MODE = 105
    ERROR_STATE = 107
    GROUP_BITMAP = 108
    PIN_CODE = 109
    CONFIGURED_SCENES = 400
    CURRENT_SCENE = 401
    SCENE_TIMEOUT = 402
    SCENE_TIMER = 403
    MOTOR_SPEED = 700
    FEED_MODE_MAX_SPEED = 702
    FEED_MODE_RETURN_DELAY = 802
    NETWORKED_THREAD_DEVICES = 1001
    MESH_LOCAL_ADDRESSES = 1005
    INTENSITY = 1500
    PUCK_TEMPERATURE = 1511


class SceneId(IntEnum):
    EMPTY = 0
    FEED_MODE = 1
    BATTERY_BACKUP = 2
    ALL_OFF = 3
    COLOR_CYCLE = 4
    DISCO = 5
    THUNDERSTORM = 6
    CLOUD_COVER = 7
    ALL_ON = 8
    ALL_50 = 9


class OperationState(IntEnum):
    OOB = 0
    LIVE_DEMO = 1
    SCENE = 2
    SCHEDULE = 3


class PhysicalValue(IntEnum):
    DRIVER_TEMPERATURE = 1
    MOTOR_TEMPERATURE = 2
    CLUSTER_TEMPERATURE = 3
    MOTOR_POWER = 4
    MOTOR_RPM = 5
    BATTERY_VOLTAGE = 6
    INPUT_VOLTAGE = 7
    INTERNAL_TEMPERATURE = 8
    FAN_SPEED = 9
    GALLONS_PER_HOUR = 11
    SUPPLY_CURRENT = 12
    MODULE_TEMPERATURE = 14
    MOTOR_SPINNING = 15
    CLUSTER2_TEMPERATURE = 16


class Status(IntEnum):
    SUCCESS = 0
    FAILED = 1
    INVALID_INSTANCE = 2
    INVALID_ELEMENT = 3
    NOT_PERMITTED = 4
    INVALID_MODE = 5
    NO_MEM = 6
    UNSUPPORTED_ATTRIBUTE = 7
    EMPTY_ENTRY = 8
    INVALID_VALUE = 9
    BUSY = 17
    INVALID_RANGE = 20
    INVALID_SIZE = 21
    ENTRY_NOT_FOUND = 255


MODELS: dict[int, str] = {
    10: "VorTech MP10wES",
    11: "VorTech MP10wQD",
    30: "Radion XR30w",
    31: "Radion XR30w G2",
    32: "Radion XR30w Pro",
    33: "Radion XR30w G3",
    34: "Radion XR30w G3 Pro",
    35: "Radion XR30w G4",
    36: "Radion XR30w G4 Pro",
    37: "Radion XR30w FW Pro",
    40: "VorTech MP40wES",
    41: "VorTech MP40wQD",
    42: "VorTech MP40w G3 QD",
    60: "VorTech MP60wES",
    61: "VorTech MP60wQD",
    144: "Vectra M1",
    145: "Vectra L1",
    146: "Vectra S1",
    147: "Vectra S2",
    148: "Vectra M2",
    149: "Vectra L2",
    160: "Radion XR15w G3 Pro",
    161: "Radion XR15w FW",
    162: "Radion XR15w G4 Pro",
    163: "Radion XR15w FW Pro",
    176: "Radion XR15 G5 Pro",
    177: "Radion XR15 G5 Blue",
    192: "Radion XR30 G5 Pro",
    193: "Radion XR30 G5 Blue",
    195: "Radion (model 195)",  # codename only in the app; seen on a user's tank 2026-10-17
}


class ProtocolError(Exception):
    """Malformed frame or unexpected response."""


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE, as in com.c2.comm.utilities.Crc.crc16."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


@dataclass(slots=True)
class Frame:
    group: int
    op: int
    msg_id: int
    payload: bytes = b""
    rsv0: int = 0
    rsv1: int = 0

    def encode(self) -> bytes:
        body = struct.pack(
            "<BBHBBH",
            self.group,
            self.op,
            self.msg_id,
            self.rsv0,
            self.rsv1,
            len(self.payload),
        ) + self.payload
        return bytes([SYNC]) + body + struct.pack("<H", crc16(body))

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        if len(data) < 11 or data[0] != SYNC:
            raise ProtocolError(f"not an FSCI frame: {data.hex()}")
        group, op, msg_id, rsv0, rsv1, length = struct.unpack_from("<BBHBBH", data, 1)
        if len(data) < 11 + length:
            raise ProtocolError(f"truncated frame: {data.hex()}")
        (crc,) = struct.unpack_from("<H", data, 9 + length)
        if crc != crc16(data[1 : 9 + length]):
            raise ProtocolError(f"bad CRC: {data.hex()}")
        return cls(group, op, msg_id, bytes(data[9 : 9 + length]), rsv0, rsv1)

    def set_group(self, group: int) -> None:
        """Tank group broadcast bits, as in Request.setGroup."""
        if not 0 <= group < 64:
            raise ValueError(group)
        self.rsv0 = (self.rsv0 & 0x03) | (group << 2)

    @property
    def response_op(self) -> tuple[int, int]:
        return OpGroup.C2CI_CONFIRM if self.group == OpGroup.C2CI_REQUEST else self.group + 1, self.op


def chunk(data: bytes, mtu: int) -> list[bytes]:
    """Split a frame into writes; the last one goes to TX_FINAL."""
    return [data[i : i + mtu] for i in range(0, len(data), mtu)] or [b""]


# --- attribute get/set -------------------------------------------------------


def get_attrs_payload(attrs: list[tuple[int, int, int]], wide: bool) -> bytes:
    """attrs: (attr_id, start_index, count). count 0xFFFF = all elements."""
    fmt = "<HHH" if wide else "<HBB"
    out = b""
    for attr, index, count in attrs:
        if not wide:
            index, count = index & 0xFF, count & 0xFF
        out += struct.pack(fmt, attr, index, count)
    return out


def set_attrs_payload(attrs: list[tuple[int, int, list[bytes]]], wide: bool) -> bytes:
    """attrs: (attr_id, start_index, elements). Elements are zero padded to the longest."""
    out = b""
    for attr, index, elements in attrs:
        size = max((len(e) for e in elements), default=0)
        if wide:
            out += struct.pack("<HHHH", attr, index, len(elements), size)
        else:
            out += struct.pack("<HBBB", attr, index, len(elements), size)
        out += b"".join(e.ljust(size, b"\x00") for e in elements)
    return out


@dataclass(slots=True)
class AttrValue:
    """Elements of one attribute keyed by element index.

    A device may answer one request with several sparse blocks for the same
    attribute (seen for PhysicalValues on an MP40), so blocks are merged.
    """

    attr: int
    items: dict[int, bytes] = field(default_factory=dict)

    @property
    def index(self) -> int:
        return min(self.items, default=0)

    @property
    def elements(self) -> list[bytes]:
        return [self.items[i] for i in sorted(self.items)]

    @property
    def first(self) -> bytes:
        return self.items[self.index]


def parse_get_response(data: bytes, wide: bool) -> tuple[int, dict[int, AttrValue]]:
    """Parse a GetC2Attr confirm payload into (status, {attr: value})."""
    if not data:
        raise ProtocolError("empty get response")
    status = data[0]
    values: dict[int, AttrValue] = {}
    if status != Status.SUCCESS:
        return status, values
    fmt = "<HHHH" if wide else "<HBBB"
    head = struct.calcsize(fmt)
    pos = 1
    while pos < len(data):
        if pos + head > len(data):
            raise ProtocolError(f"truncated attribute header at {pos}: {data.hex()}")
        attr, index, count, size = struct.unpack_from(fmt, data, pos)
        pos += head
        end = pos + count * size
        if end > len(data):
            raise ProtocolError(f"truncated attribute {attr}: {data.hex()}")
        value = values.setdefault(attr, AttrValue(attr))
        for i in range(count):
            value.items[index + i] = bytes(data[pos + i * size : pos + (i + 1) * size])
        pos = end
    return status, values


def parse_opcodes(data: bytes) -> bytes:
    """GetOpCodes confirm: status | u16 version | u16 count | opcodes."""
    if len(data) < 5:
        raise ProtocolError(f"short opcodes response: {data.hex()}")
    (count,) = struct.unpack_from("<H", data, 3)
    return bytes(data[5 : 5 + count])


# --- CoAP relay ---------------------------------------------------------------


def coap_wrap(ipv6: bytes, token: int, inner: bytes, method: int = 1, ctype: int = 0) -> bytes:
    """Payload of a DE 19 request carrying `inner` to the mesh node `ipv6`."""
    if len(ipv6) != 16:
        raise ValueError("ipv6 must be 16 bytes")
    return ipv6[::-1] + struct.pack("<iHHB", token, method, len(inner), ctype) + inner


@dataclass(slots=True)
class CoapIndication:
    source: bytes
    token: int
    code: int
    ctype: int
    inner: bytes


def coap_unwrap(data: bytes) -> CoapIndication:
    """Payload of a DF 1A frame. Source byte order is unconfirmed (docs/02)."""
    if len(data) < 25:
        raise ProtocolError(f"short CoAP indication: {data.hex()}")
    token, code, length, ctype = struct.unpack_from("<iHHB", data, 16)
    return CoapIndication(bytes(data[:16]), token, code, ctype, bytes(data[25 : 25 + length]))


@dataclass(slots=True, frozen=True)
class MeshDevice:
    """One NetworkedThreadDevices entry (docs/02): where to relay to, and who is there."""

    ipv6: bytes
    model_id: int
    serial: str


def parse_mesh_devices(value: AttrValue, own_ipv6: bytes) -> list[MeshDevice]:
    """Mesh-local IPv6 = the mesh prefix (first 8 bytes of our own address) + the entry's interface id."""
    return [
        MeshDevice(own_ipv6[:8] + e[:8], struct.unpack_from("<H", e, 12)[0], text(e[14:28]))
        for _i, e in sorted(value.items.items())
        if len(e) >= 28 and any(e)
    ]


# --- values -------------------------------------------------------------------


def u8(b: bytes) -> int:
    return b[0]


def u16(b: bytes) -> int:
    return struct.unpack_from("<H", b)[0]


def i32(b: bytes) -> int:
    return struct.unpack_from("<i", b)[0]


def u64(b: bytes) -> int:
    return int.from_bytes(b[:8].ljust(8, b"\x00"), "little")


def text(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", "replace")


def tank_group(bitmap: int) -> int:
    """Tank.parseGroupBitmap: lowest set bit at positions 2..31 (1-based)."""
    for g in range(2, 32):
        if bitmap & (1 << (g - 1)):
            return g
    return 0


@dataclass(slots=True)
class SceneRecord:
    id: int
    timeout: int
    name: str
    data: bytes

    @classmethod
    def decode(cls, b: bytes) -> SceneRecord:
        sid, timeout = struct.unpack_from("<HH", b)
        return cls(sid, timeout, text(b[4:20]), bytes(b[20:]))


# --- advertisements -----------------------------------------------------------


@dataclass(slots=True)
class Advert:
    model_id: int
    serial: str
    pan_id: int
    legacy: bool
    has_error: bool = False
    device_specific: int = 0
    raw: bytes = field(default=b"", repr=False)

    @property
    def model(self) -> str:
        return MODELS.get(self.model_id, f"Unknown ({self.model_id})")

    @property
    def is_light(self) -> bool:
        return self.model.startswith("Radion")


def parse_advert(manufacturer_data: dict[int, bytes]) -> Advert | None:
    """Parse HA/bleak manufacturer_data. The app treats the "company id" as payload bytes."""
    for company_id, rest in manufacturer_data.items():
        b = company_id.to_bytes(2, "little") + bytes(rest)
        adv = _parse_raw_advert(b)
        if adv is not None:
            return adv
    return None


def _parse_raw_advert(b: bytes) -> Advert | None:
    """Peripheral.parseManufactureData."""
    n = len(b)
    if n == 19 and b[0] == 1:
        model, _epoch, spec, pan = struct.unpack_from("<HIiH", b, 2)
        return Advert(model, b[14:19].hex().upper(), pan, False, False, spec, b)
    if n == 16 and b[0] == 1:
        (model,) = struct.unpack_from("<H", b, 2)
        spec, pan = struct.unpack_from("<iH", b, 5)
        return Advert(model, b[11:16].hex().upper(), pan, False, False, spec, b)
    if n in (18, 25, 28) and b[0] in (1, 2):
        (model,) = struct.unpack_from("<H", b, 2)
        spec, pan = struct.unpack_from("<iH", b, 5)
        if b[0] == 2:
            flags = b[1]
            serial = text(b[11:25])
            return Advert(model, serial, pan, not flags & 0x02, bool(flags & 0x10), spec, b)
        if n == 18:
            serial = (
                f"{b[11]:02d}{(b[12] << 8) | b[13]:04d}"
                f"{(b[14] << 16) | (b[15] << 8) | b[16]:05d}{b[17]:03d}"
            )
        else:
            serial = text(b[11:25])
        return Advert(model, serial, pan, False, False, spec, b)
    return None
