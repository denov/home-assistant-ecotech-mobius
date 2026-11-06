"""The Feed mode switch: on starts Feed mode, off resumes the schedule."""

import struct
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobius import migrate_feed_mode_entities, protocol as p
from custom_components.mobius.client import SceneState
from custom_components.mobius.const import CONF_PAN_ID, DOMAIN
from custom_components.mobius.coordinator import DeviceState, TankData
from custom_components.mobius.switch import ASSUME_SECONDS, FeedModeSwitch

from .test_client import FakeDevice

SERIAL = "0000000TESTSN1"


FEED = SceneState(p.SceneId.FEED_MODE, 500, 600)
NONE = SceneState(p.SceneId.EMPTY, 0, 3600)


def tank_data(scene: SceneState, read_at: float = 0.0) -> TankData:
    adv = p.Advert(42, SERIAL, 0x405F, False)
    return TankData({SERIAL: DeviceState(SERIAL, "AA:AA", adv, scene=scene, read_at=read_at)})


def make_switch(acked_at: float | None = None):
    """The switch; its commands are acknowledged at `acked_at` (default: now)."""
    command = AsyncMock(side_effect=lambda *_a: time.monotonic() if acked_at is None else acked_at)
    coordinator = SimpleNamespace(data=None, async_tank_command=command)
    data = SimpleNamespace(coordinator=coordinator, tank_id="tank_405f", pan_id=0x405F)
    switch = FeedModeSwitch.__new__(FeedModeSwitch)
    FeedModeSwitch.__init__(switch, data)
    switch.async_write_ha_state = lambda: None
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


async def test_shows_the_command_straight_away():
    switch, coordinator = make_switch()
    coordinator.data = tank_data(NONE)
    await switch.async_turn_on()
    assert switch.is_on is True, "no poll needed to show Feed mode on"
    coordinator.data = tank_data(FEED)
    await switch.async_turn_off()
    assert switch.is_on is False


async def test_a_reading_from_before_the_command_does_not_count():
    switch, coordinator = make_switch()
    before = time.monotonic()
    await switch.async_turn_off()
    coordinator.data = tank_data(FEED, read_at=before)  # e.g. read earlier in the same poll
    assert switch.is_on is False


async def test_a_reading_after_the_command_decides():
    switch, coordinator = make_switch()
    await switch.async_turn_on()
    coordinator.data = tank_data(FEED, read_at=time.monotonic())
    assert switch.is_on is True
    coordinator.data = tank_data(NONE, read_at=time.monotonic())
    assert switch.is_on is False, "the devices say Feed mode isn't running"


async def test_the_command_result_expires_without_a_newer_reading():
    switch, coordinator = make_switch(acked_at=time.monotonic() - ASSUME_SECONDS - 1)
    await switch.async_turn_on()
    coordinator.data = tank_data(NONE)
    assert switch.is_on is False, "back to the last reported state"


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
    migrate_feed_mode_entities(hass, entry, 0x405F)
    assert reg.async_get_entity_id("button", DOMAIN, "tank_405f_feed_mode") is None
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, "tank_405f_feed_mode") is None
    assert reg.async_get(other.entity_id) is not None


async def test_switch_takes_over_the_old_button_entity_id(hass):
    # since HA 2026.10 a new entity's ID starts with its area: switch.tank_mobius_tank_405f_feed_mode
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: 0x405F}, unique_id="405f")
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create(
        "button", DOMAIN, "tank_405f_feed_mode", config_entry=entry, suggested_object_id="mobius_tank_405f_feed_mode"
    )
    migrate_feed_mode_entities(hass, entry, 0x405F)
    assert reg.async_get_entity_id("switch", DOMAIN, "tank_405f_feed_mode") == "switch.mobius_tank_405f_feed_mode"


async def test_existing_switch_keeps_its_entity_id(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: 0x405F}, unique_id="405f")
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create(
        "switch", DOMAIN, "tank_405f_feed_mode", config_entry=entry, suggested_object_id="my_feed_switch"
    )
    reg.async_get_or_create(
        "button", DOMAIN, "tank_405f_feed_mode", config_entry=entry, suggested_object_id="mobius_tank_405f_feed_mode"
    )
    migrate_feed_mode_entities(hass, entry, 0x405F)
    assert reg.async_get_entity_id("switch", DOMAIN, "tank_405f_feed_mode") == "switch.my_feed_switch"
