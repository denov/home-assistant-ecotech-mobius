"""MobiusCoordinator polls and commands, with only the BLE connection faked."""

import asyncio
import struct
import time
from types import SimpleNamespace
from unittest.mock import patch

from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobius import protocol as p
from custom_components.mobius.const import CONF_PAN_ID, DOMAIN
from custom_components.mobius.coordinator import (
    ABORT_AFTER_CONNECT_FAILURES,
    UNAVAILABLE_AFTER_FAILURES,
    MobiusCoordinator,
)

from .test_client import FakeDevice

PAN = 0x405F
MOD = "custom_components.mobius.coordinator"


def device_attrs(name: bytes) -> dict[int, bytes]:
    return {
        p.Attr.NAME: name.ljust(16, b"\x00"),
        p.Attr.PRIMITIVE_TYPE: b"\x04",
        p.Attr.ERROR_STATE: b"\x00\x00",
        p.Attr.GROUP_BITMAP: struct.pack("<Q", 0b11),
        p.Attr.OPERATION_STATE: b"\x03",
        p.Attr.OPERATION_MODE: struct.pack("<H", 34),
        p.Attr.CURRENT_SCENE: b"\x00\x00\x00\x00",
        p.Attr.SCENE_TIMER: b"\x00\x00",
        p.Attr.SCENE_TIMEOUT: struct.pack("<H", 3600),
        p.Attr.PHYSICAL_VALUES: struct.pack("<i", 2330),
    }


class Tank:
    """Two pumps in range; `down` addresses fail to connect, `log` records connections in order."""

    def __init__(self):
        self.seen = {
            serial: (SimpleNamespace(address=addr, rssi=rssi), p.Advert(42, serial, PAN, False))
            for serial, addr, rssi in (("SERIAL_A", "AA:AA", -70), ("SERIAL_B", "BB:BB", -60))
        }
        self.down: set[str] = set()
        self.attempts: list[str] = []  # every connect, in order, failed or not
        self.connections: list[FakeDevice] = []
        self.log: list[str] = []
        self.gate: dict[str, asyncio.Event] = {}
        self.disconnect_error = False
        # address -> attributes that differ from device_attrs()
        self.attrs: dict[str, dict[int, bytes]] = {}

    async def connect(self, _cls, ble_device, _name, **_kw):
        addr = ble_device.address
        self.attempts.append(addr)
        if addr in self.gate:
            await self.gate[addr].wait()
        if addr in self.down:
            raise BleakError(f"{addr} timed out")
        dev = FakeDevice(device_attrs(addr.encode()) | self.attrs.get(addr, {}))

        async def disconnect():
            # log what this connection was for, in the order connections finish
            sets = any(f.op in (p.Op.SET_ATTR_V1, p.Op.SET_ATTR_V2) for f in dev.received)
            self.log.append((addr, "command" if sets else "read"))
            if self.disconnect_error:
                raise BleakError("disconnect failed")
            return True

        dev.disconnect = disconnect
        self.connections.append(dev)
        return dev


@pytest.fixture
def tank(hass):
    t = Tank()
    with (
        patch(f"{MOD}.MobiusCoordinator.advertised", lambda _self: dict(t.seen)),
        patch(f"{MOD}.establish_connection", t.connect),
        patch(f"{MOD}.bluetooth.async_ble_device_from_address", lambda _h, addr, **_k: SimpleNamespace(address=addr)),
        patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: []),
    ):
        yield t


@pytest.fixture
def coordinator(hass, tank):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: PAN}, unique_id="405f")
    entry.add_to_hass(hass)
    return MobiusCoordinator(hass, entry, PAN)


async def test_poll_reads_every_device(coordinator, tank):
    data = await coordinator._async_update_data()
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B"}
    assert data.devices["SERIAL_A"].operation_mode == 34
    assert data.devices["SERIAL_A"].physical == {0: 2330}


async def test_device_keeps_last_reading_through_brief_failures(coordinator, tank):
    coordinator.data = await coordinator._async_update_data()
    tank.down.add("BB:BB")
    for _ in range(UNAVAILABLE_AFTER_FAILURES - 1):
        coordinator.data = await coordinator._async_update_data()
        assert "SERIAL_B" in coordinator.data.devices, "one bad connect must not hide the device"
    coordinator.data = await coordinator._async_update_data()
    assert "SERIAL_B" not in coordinator.data.devices
    tank.down.clear()
    coordinator.data = await coordinator._async_update_data()
    assert "SERIAL_B" in coordinator.data.devices


async def test_failed_disconnect_keeps_the_reading(coordinator, tank):
    tank.disconnect_error = True
    data = await coordinator._async_update_data()
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B"}


async def test_command_does_not_wait_for_the_whole_poll(coordinator, tank):
    # the bridge's (B's) connection hangs; a button press must go out before the poll moves on to A
    tank.gate["BB:BB"] = asyncio.Event()
    poll = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    with patch.object(coordinator, "async_request_refresh"):
        command = asyncio.create_task(
            coordinator.async_tank_command("feed", lambda c, g: c.start_scene(p.SceneId.FEED_MODE, g))
        )
        await asyncio.sleep(0)
        tank.gate["BB:BB"].set()
        await asyncio.gather(poll, command)
    # the strongest device (B) carries the command, between B's read and A's direct read
    assert tank.log == [("BB:BB", "read"), ("BB:BB", "command"), ("AA:AA", "read")]


async def test_connects_once_per_attempt(coordinator, tank):
    calls = []

    async def counting(cls, dev, name, **kw):
        calls.append(kw.get("max_attempts"))
        return await tank.connect(cls, dev, name, **kw)

    with patch(f"{MOD}.establish_connection", counting):
        await coordinator._async_update_data()
    assert calls and all(n == 1 for n in calls), "a dead device must not cost two 20 s attempts"


async def test_device_missing_from_scan_keeps_last_reading(coordinator, tank):
    coordinator.data = await coordinator._async_update_data()
    gone = tank.seen.pop("SERIAL_B")
    for _ in range(UNAVAILABLE_AFTER_FAILURES - 1):
        coordinator.data = await coordinator._async_update_data()
        assert "SERIAL_B" in coordinator.data.devices, "a device that skips one scan must not vanish"
    coordinator.data = await coordinator._async_update_data()
    assert "SERIAL_B" not in coordinator.data.devices
    tank.seen["SERIAL_B"] = gone


def _info(address: str, manufacturer_data: dict[int, bytes]):
    return SimpleNamespace(
        address=address, name="MOBIUS", rssi=-70, time=time.monotonic(),
        manufacturer_data=manufacturer_data, service_uuids=[p.SERVICE_UUID],
    )


def _scan_response(model: int, serial: str, pan: int) -> dict[int, bytes]:
    raw = (
        bytes([2, 0x02]) + struct.pack("<H", model) + b"\x00" + struct.pack("<iH", 0, pan)
        + serial.encode().ljust(17, b"\x00")
    )
    return {int.from_bytes(raw[:2], "little"): raw[2:]}


async def test_device_map_survives_restart(hass):
    """Scan responses only come with active scans; after a restart passive adverts must be enough."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: PAN}, unique_id="405f")
    entry.add_to_hass(hass)
    active = [_info("AA:AA", _scan_response(42, "0000000TESTSN1", PAN))]
    with patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: active):
        assert set(MobiusCoordinator(hass, entry, PAN).advertised()) == {"0000000TESTSN1"}

    passive = [_info("AA:AA", {})]
    with patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: passive):
        seen = MobiusCoordinator(hass, entry, PAN).advertised()  # a fresh coordinator = after restart
    assert set(seen) == {"0000000TESTSN1"}
    assert seen["0000000TESTSN1"][1].model_id == 42


def _add_device(tank, serial: str, addr: str, rssi: int) -> None:
    tank.seen[serial] = (SimpleNamespace(address=addr, rssi=rssi), p.Advert(42, serial, PAN, False))


def _feed(coordinator):
    return coordinator.async_tank_command("feed", lambda c, g: c.start_scene(p.SceneId.FEED_MODE, g))


async def test_poll_stops_when_the_radio_path_is_down(coordinator, tank):
    _add_device(tank, "SERIAL_C", "CC:CC", -80)
    coordinator.data = await coordinator._async_update_data()
    tank.down.update({"AA:AA", "BB:BB", "CC:CC"})  # e.g. the proxy can't connect to anything
    coordinator.data = await coordinator._async_update_data()
    assert len(tank.attempts) == 3 + ABORT_AFTER_CONNECT_FAILURES, "each dead connect costs ~40 s"
    assert set(coordinator.data.devices) == {"SERIAL_A", "SERIAL_B", "SERIAL_C"}, "skipped devices keep their reading"


async def test_poll_after_an_aborted_poll_still_tries_once(coordinator, tank):
    tank.down.update({"AA:AA", "BB:BB"})
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    tank.attempts.clear()
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert len(tank.attempts) == 1
    tank.down.clear()  # the proxy is back
    tank.attempts.clear()
    data = await coordinator._async_update_data()
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B"}


async def test_command_tries_once_when_the_radio_path_is_down(coordinator, tank):
    tank.down.update({"AA:AA", "BB:BB"})
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    tank.attempts.clear()
    with pytest.raises(HomeAssistantError):
        await _feed(coordinator)
    assert len(tank.attempts) == 1


async def test_command_tries_the_next_device_when_one_is_down(coordinator, tank):
    tank.down.add("BB:BB")  # the strongest device
    with patch.object(coordinator, "async_request_refresh"):
        await _feed(coordinator)
    assert tank.attempts == ["BB:BB", "AA:AA"]
    assert tank.log == [("AA:AA", "command")]


async def test_poll_cannot_slip_in_between_command_attempts(coordinator, tank):
    tank.down.add("BB:BB")
    tank.gate["BB:BB"] = asyncio.Event()
    with patch.object(coordinator, "async_request_refresh"):
        command = asyncio.create_task(_feed(coordinator))
        await asyncio.sleep(0)
        poll = asyncio.create_task(coordinator._async_update_data())
        await asyncio.sleep(0)
        tank.gate["BB:BB"].set()
        await asyncio.gather(command, poll)
    assert tank.attempts[:2] == ["BB:BB", "AA:AA"]
    assert tank.log[0] == ("AA:AA", "command"), "the poll must not read between the command's attempts"


async def test_opcodes_are_asked_once_per_device(coordinator, tank):
    await coordinator._async_update_data()
    tank.connections.clear()
    await coordinator._async_update_data()
    sent = [f.op for dev in tank.connections for f in dev.received]
    assert p.Op.GET_OPCODES not in sent
    assert p.Op.GET_ATTR_V2 in sent, "the cached opcodes still select the 2-byte variants"


async def test_active_scans_only_for_this_tank(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: PAN}, unique_id="405f")
    entry.add_to_hass(hass)
    infos = [
        _info("AA:AA", _scan_response(42, "0000000TESTSN1", PAN)),
        _info("BB:BB", {}),  # not identified yet
        _info("CC:CC", _scan_response(42, "0000000TESTSN2", 0x1234)),  # a neighbour's tank
    ]
    registered: dict[str, list] = {}

    def register(_hass, cb, matcher, _mode):
        registered[matcher["address"]] = [cb, False]
        return lambda: registered[matcher["address"]].__setitem__(1, True)

    with (
        patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: infos),
        patch(f"{MOD}.bluetooth.async_register_callback", register),
    ):
        coordinator = MobiusCoordinator(hass, entry, PAN)
        coordinator._track_addresses()
        assert set(registered) == {"AA:AA", "BB:BB"}
        cb, _ = registered["BB:BB"]
        cb(_info("BB:BB", _scan_response(42, "0000000TESTSN3", 0x1234)), None)  # BB turns out to be a neighbour's
        assert registered["BB:BB"][1], "its active scan is dropped"
        assert not registered["AA:AA"][1]


PREFIX = bytes.fromhex("fd00000000000001")


def _iid(n: int) -> bytes:
    return bytes([n]) * 8


def _mesh_tank(tank, relays: set[str]):
    """B (the strongest) bridges to the mesh: A, B itself and a Vectra no proxy hears.

    `relays` are the serials B's relay answers for.
    """
    members = {"SERIAL_A": (1, 42), "SERIAL_B": (2, 42), "SERIAL_V": (3, 149)}
    entries = [_iid(n) + bytes(4) + struct.pack("<H", model) + serial.encode().ljust(14, b"\x00")
               for serial, (n, model) in members.items()] + [bytes(28)]
    connect = tank.connect

    async def bridge_connect(cls, ble_device, name, **kw):
        dev = await connect(cls, ble_device, name, **kw)
        own_n = members["SERIAL_A" if ble_device.address == "AA:AA" else "SERIAL_B"][0]
        dev.attrs[p.Attr.MESH_LOCAL_ADDRESSES] = PREFIX + _iid(own_n)
        dev.attrs[p.Attr.NETWORKED_THREAD_DEVICES] = entries
        dev.mesh = {PREFIX + _iid(members[s][0]): device_attrs(s.encode()) for s in relays}
        return dev

    return bridge_connect


async def test_poll_reads_the_tank_through_one_connection(coordinator, tank):
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_A", "SERIAL_V"})):
        data = await coordinator._async_update_data()
    assert tank.attempts == ["BB:BB"], "one connection for the whole tank"
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B", "SERIAL_V"}, "the mesh reaches the Vectra no proxy hears"
    assert coordinator.known["SERIAL_V"].model_id == 149
    assert data.devices["SERIAL_V"].physical == {0: 2330}


async def test_bridge_is_read_directly_not_through_its_own_relay(coordinator, tank):
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_A", "SERIAL_V"})):
        await coordinator._async_update_data()
    bridge = tank.connections[0]
    relayed_to = {bytes(f.payload[:16][::-1]) for f in bridge.received if f.op == p.Op.COAP_REQUEST}
    assert relayed_to == {PREFIX + _iid(1), PREFIX + _iid(3)}, "A and the Vectra, never B itself (no answer)"


async def test_relay_failure_falls_back_to_a_direct_connection(coordinator, tank, monkeypatch):
    monkeypatch.setattr("custom_components.mobius.client.COAP_TIMEOUT", 0.01)
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_V"})):  # A's relay is silent
        data = await coordinator._async_update_data()
    assert tank.attempts == ["BB:BB", "AA:AA"]
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B", "SERIAL_V"}


async def test_next_strongest_device_bridges_when_the_first_is_down(coordinator, tank):
    tank.down.add("BB:BB")
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_B", "SERIAL_V"})):
        data = await coordinator._async_update_data()
    assert tank.attempts == ["BB:BB", "AA:AA"]
    assert set(data.devices) == {"SERIAL_A", "SERIAL_B", "SERIAL_V"}, "B is still read, through A's relay"


def test_mesh_requests_fail_fast():
    from custom_components.mobius import client
    assert client.COAP_TIMEOUT <= 5, "relayed reads answer in ~0.5 s; a dead device must not stall a poll"


async def test_lights_report_their_channel_intensities(coordinator, tank):
    connect = tank.connect

    async def with_a_light(cls, ble_device, name, **kw):
        dev = await connect(cls, ble_device, name, **kw)
        if ble_device.address == "AA:AA":
            dev.attrs[p.Attr.PRIMITIVE_TYPE] = b"\x01"  # VisualV1
            dev.attrs[p.Attr.INTENSITY] = [struct.pack("<H", 770)]  # one channel at element index 0 in the fake
        return dev

    with patch(f"{MOD}.establish_connection", with_a_light):
        data = await coordinator._async_update_data()
    assert data.devices["SERIAL_A"].intensity == {0: 770}
    assert data.devices["SERIAL_B"].intensity == {}
    pump = next(d for d in tank.connections if p.Attr.INTENSITY not in d.attrs)
    asked = {struct.unpack_from("<H", f.payload)[0] for f in pump.received if f.op == p.Op.GET_ATTR_V2}
    assert p.Attr.INTENSITY not in asked, "pumps reject Intensity; asking would cost a failed request"


def _as_light(tank, serial: str) -> None:
    info, adv = tank.seen[serial]
    tank.seen[serial] = (info, p.Advert(195, adv.serial, PAN, False))


async def test_a_pump_bridges_before_a_slightly_stronger_light(coordinator, tank):
    _as_light(tank, "SERIAL_B")  # B (-60) is a light, A (-70) a pump
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_B", "SERIAL_V"})):
        await coordinator._async_update_data()
    assert tank.attempts == ["AA:AA"], "a light read directly reports mode 50, so the pump bridges"


async def test_a_light_bridges_when_no_pump_is_close(coordinator, tank):
    _as_light(tank, "SERIAL_B")
    _add_device(tank, "SERIAL_A", "AA:AA", -80)
    with patch(f"{MOD}.establish_connection", _mesh_tank(tank, {"SERIAL_A", "SERIAL_V"})):
        await coordinator._async_update_data()
    assert tank.attempts == ["BB:BB"]


async def test_a_bridging_light_keeps_its_mesh_mode(coordinator, tank):
    _as_light(tank, "SERIAL_B")
    tank.down.add("AA:AA")  # only the light can bridge
    connect = tank.connect

    async def light_reports_50(cls, ble_device, name, **kw):
        dev = await connect(cls, ble_device, name, **kw)
        dev.attrs[p.Attr.OPERATION_MODE] = struct.pack("<H", 50)
        return dev

    with patch(f"{MOD}.establish_connection", light_reports_50):
        data = await coordinator._async_update_data()
        assert data.devices["SERIAL_B"].operation_mode is None, "50 is not a mode; unknown until read via the mesh"
        data.devices["SERIAL_B"].operation_mode = 2  # as a mesh read reports it
        coordinator.data = data
        data = await coordinator._async_update_data()
    assert data.devices["SERIAL_B"].operation_mode == 2


async def test_kept_reading_does_not_decide_the_tank_scene(coordinator, tank):
    tank.attrs["BB:BB"] = {p.Attr.CURRENT_SCENE: struct.pack("<HH", p.SceneId.FEED_MODE, 0),
                           p.Attr.SCENE_TIMER: struct.pack("<H", 500)}
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data.scene.name == "feed_mode"
    # Feed mode ended, but B can't be read: its kept reading still says feed mode
    tank.down.add("BB:BB")
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data.devices["SERIAL_B"].kept
    assert not coordinator.data.devices["SERIAL_A"].kept
    assert coordinator.data.scene.name == "none"


async def test_command_without_a_tank_group_is_not_sent(coordinator, tank):
    no_group = {p.Attr.GROUP_BITMAP: struct.pack("<Q", 0b1)}
    tank.attrs = {"AA:AA": no_group, "BB:BB": no_group}
    with pytest.raises(HomeAssistantError, match="tank group"):
        await _feed(coordinator)
    sets = [f for d in tank.connections for f in d.received if f.op in (p.Op.SET_ATTR_V1, p.Op.SET_ATTR_V2)]
    assert sets == [], "a Set without a group reaches only the connected device"
