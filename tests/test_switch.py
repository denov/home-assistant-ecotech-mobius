"""The Feed mode switch: on starts Feed mode, off resumes the schedule."""

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobius import protocol as p, remove_old_feed_mode_entities
from custom_components.mobius.client import SceneState
from custom_components.mobius.const import CONF_PAN_ID, DOMAIN
from custom_components.mobius.coordinator import DeviceState, TankData
from custom_components.mobius.switch import FeedModeSwitch

from .test_client import FakeDevice

SERIAL = "0000000TESTSN1"


def tank_data(scene: SceneState) -> TankData:
    adv = p.Advert(42, SERIAL, 0x405F, False)
    return TankData({SERIAL: DeviceState(SERIAL, "AA:AA", adv, scene=scene)})


def make_switch():
    coordinator = SimpleNamespace(data=None, async_tank_command=AsyncMock())
    data = SimpleNamespace(coordinator=coordinator, tank_id="tank_405f", pan_id=0x405F)
    switch = FeedModeSwitch.__new__(FeedModeSwitch)
    FeedModeSwitch.__init__(switch, data)
    return switch, coordinator


def test_on_while_feed_mode_runs():
    switch, coordinator = make_switch()
    assert switch.is_on is None, "unknown until a poll reads the scene"
    coordinator.data = tank_data(SceneState(p.SceneId.FEED_MODE, 500, 600))
    assert switch.is_on is True
    coordinator.data = tank_data(SceneState(p.SceneId.ALL_OFF, 500, 600))
    assert switch.is_on is False
    coordinator.data = tank_data(SceneState(p.SceneId.EMPTY, 0, 3600))
    assert switch.is_on is False
    assert switch.available


async def sent_by(command) -> p.Frame:
    """Run a command's action against a fake device; the Set frame it sends."""
    from custom_components.mobius.client import MobiusClient

    dev = FakeDevice({})
    client = MobiusClient(dev)
    await client.start()
    _what, action = command.call_args.args
    await action(client, 2)
    return dev.received[-1]


async def test_turn_on_starts_feed_mode():
    switch, coordinator = make_switch()
    await switch.async_turn_on()
    frame = await sent_by(coordinator.async_tank_command)
    assert frame.payload == struct.pack("<HHHH", p.Attr.CURRENT_SCENE, 0, 1, 4) + struct.pack("<HH", 1, 0)
    assert frame.rsv0 == 2 << 2


async def test_turn_off_resumes_the_schedule():
    switch, coordinator = make_switch()
    await switch.async_turn_off()
    frame = await sent_by(coordinator.async_tank_command)
    assert frame.payload == struct.pack("<HHHH", p.Attr.OPERATION_STATE, 0, 1, 1) + b"\x03"
    assert frame.rsv0 == 2 << 2


async def test_setup_removes_the_old_feed_mode_button_and_sensor(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: 0x405F}, unique_id="405f")
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for domain in ("button", "binary_sensor"):
        reg.async_get_or_create(domain, DOMAIN, "tank_405f_feed_mode", config_entry=entry)
    other = reg.async_get_or_create("button", DOMAIN, "tank_405f_all_off", config_entry=entry)
    remove_old_feed_mode_entities(hass, 0x405F)
    assert reg.async_get_entity_id("button", DOMAIN, "tank_405f_feed_mode") is None
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, "tank_405f_feed_mode") is None
    assert reg.async_get(other.entity_id) is not None
