"""MobiusClient against a fake device that answers like the firmware is expected to."""

import asyncio
import struct

import pytest

from custom_components.mobius import protocol as p
from custom_components.mobius.client import MobiusClient


class FakeDevice:
    """Minimal bleak-client stand-in: reassembles TX writes, answers via RX notifications."""

    mtu_size = 23

    def __init__(self, attrs: dict[int, bytes | list[bytes]], opcodes: bytes = b"\x1c\x1d\x29", mesh=None):
        self.attrs = attrs
        self.opcodes = opcodes
        # mesh-local IPv6 -> attrs of a device this one relays to; others get no answer
        self.mesh: dict[bytes, dict] = mesh or {}
        self.handlers = {}
        self.tx = bytearray()
        self.received: list[p.Frame] = []

    async def start_notify(self, uuid, cb):
        self.handlers[uuid] = cb

    async def disconnect(self):
        return True

    async def write_gatt_char(self, uuid, data, response=False):
        self.tx += data
        if uuid == p.TX_FINAL_UUID:
            frame = p.Frame.decode(bytes(self.tx))
            self.tx.clear()
            self.received.append(frame)
            asyncio.get_running_loop().call_soon(self._answer, frame)

    def _notify(self, data: bytes):
        parts = p.chunk(data, 20)
        for part in parts[:-1]:
            self.handlers[p.RX_DATA_UUID](None, bytearray(part))
        self.handlers[p.RX_FINAL_UUID](None, bytearray(parts[-1]))

    def _reply(self, req: p.Frame, payload: bytes) -> bytes:
        return p.Frame(p.OpGroup.C2CI_CONFIRM, req.op, req.msg_id, payload).encode()

    def _handle(self, req: p.Frame, attrs: dict) -> bytes:
        if req.op == p.Op.GET_OPCODES:
            return self._reply(req, b"\x00\x01\x00" + struct.pack("<H", len(self.opcodes)) + self.opcodes)
        if req.op == p.Op.GET_ATTR_V2:
            out = b"\x00"
            for attr, idx, _count in struct.iter_unpack("<HHH", req.payload):
                if attr not in attrs:
                    return self._reply(req, bytes([p.Status.UNSUPPORTED_ATTRIBUTE]))
                v = attrs[attr]
                items = v if isinstance(v, list) else [v]
                out += struct.pack("<HHHH", attr, idx, len(items), len(items[0])) + b"".join(items)
            return self._reply(req, out)
        if req.op == p.Op.SET_ATTR_V2:
            return self._reply(req, b"\x00")
        raise AssertionError(req)

    def _answer(self, req: p.Frame):
        if req.op == p.Op.COAP_REQUEST:
            ip = req.payload[:16][::-1]
            token, _m, n, _t = struct.unpack_from("<iHHB", req.payload, 16)
            inner = p.Frame.decode(req.payload[25 : 25 + n])
            if ip not in self.mesh:
                return  # nobody there: the relay never answers
            reply = self._handle(inner, self.mesh[ip])
            body = ip + struct.pack("<iHHB", token, 205, len(reply), 2) + reply
            self._notify(p.Frame(p.OpGroup.C2CI_CONFIRM, p.Op.COAP_INDICATION, 0, body).encode())
            return
        self._notify(self._handle(req, self.attrs))


@pytest.fixture
def device():
    return FakeDevice(
        {
            p.Attr.GROUP_BITMAP: struct.pack("<Q", 0b11),
            p.Attr.CURRENT_SCENE: struct.pack("<H", 1),
            p.Attr.SCENE_TIMER: struct.pack("<H", 120),
            p.Attr.SCENE_TIMEOUT: struct.pack("<H", 600),
            p.Attr.NAME: b"Left MP40".ljust(16, b"\x00"),
        }
    )


async def test_start_detects_wide_opcodes(device):
    c = MobiusClient(device)
    await c.start()
    assert c.wide


async def test_scene_state(device):
    c = MobiusClient(device)
    await c.start()
    s = await c.scene_state()
    assert s.active and s.name == "feed_mode" and s.timer == 120


async def test_feed_mode_is_group_broadcast(device):
    c = MobiusClient(device)
    await c.start()
    group = await c.tank_group()
    assert group == 2
    await c.start_scene(p.SceneId.FEED_MODE, group)
    sent = device.received[-1]
    assert sent.op == p.Op.SET_ATTR_V2
    assert sent.rsv0 >> 2 == 2
    assert sent.payload.hex() == "910100000100040001000000"


async def test_exit_scene(device):
    c = MobiusClient(device)
    await c.start()
    await c.exit_scene(2)
    assert device.received[-1].payload.hex() == "6800000001000100" + "03"


async def test_coap_relay(device):
    device.mesh[bytes(range(16))] = device.attrs
    c = MobiusClient(device)
    await c.start()
    values = await c.get_attrs([p.Attr.NAME], target_ipv6=bytes(range(16)))
    assert p.text(values[p.Attr.NAME].elements[0]) == "Left MP40"
    outer = device.received[-1]
    assert outer.op == p.Op.COAP_REQUEST
    assert outer.payload[:16] == bytes(range(16))[::-1]


async def test_timeout_after_retry(device, monkeypatch):
    monkeypatch.setattr("custom_components.mobius.client.DIRECT_TIMEOUT", 0.01)
    c = MobiusClient(device)
    await c.start()
    device._answer = lambda req: None
    with pytest.raises(TimeoutError):
        await c.get_attrs([p.Attr.NAME])
    assert not c._pending


async def test_start_with_known_opcodes_skips_the_round_trip(device):
    c = MobiusClient(device)
    await c.start(opcodes=b"\x1c\x1d")
    assert not device.received
    assert c.wide
