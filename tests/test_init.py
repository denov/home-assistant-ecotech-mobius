"""Integration setup helpers."""

from types import SimpleNamespace

from custom_components.mobius import async_remove_config_entry_device, protocol as p
from custom_components.mobius.const import DOMAIN
from custom_components.mobius.coordinator import DeviceState, TankData


def _entry(serials: list[str]):
    devices = {s: DeviceState(s, "", p.Advert(42, s, 0x405F, False)) for s in serials}
    coordinator = SimpleNamespace(data=TankData(devices))
    return SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator, tank_id="tank_405f"))


async def test_only_devices_that_left_the_tank_can_be_removed(hass):
    entry = _entry(["0000000TESTSN1"])
    gone = SimpleNamespace(identifiers={(DOMAIN, "0000000TESTSN2")})
    present = SimpleNamespace(identifiers={(DOMAIN, "0000000TESTSN1")})
    tank = SimpleNamespace(identifiers={(DOMAIN, "tank_405f")})
    assert await async_remove_config_entry_device(hass, entry, gone)
    assert not await async_remove_config_entry_device(hass, entry, present)
    assert not await async_remove_config_entry_device(hass, entry, tank)
