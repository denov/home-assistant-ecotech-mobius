# SPDX-License-Identifier: AGPL-3.0-or-later
"""EcoTech Mobius: local BLE control of Radion, Vectra and VorTech devices."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_PAN_ID, DOMAIN
from .coordinator import MobiusCoordinator
from .entity import MobiusConfigEntry, MobiusRuntimeData, tank_device, tank_id

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry) -> bool:
    pan_id = entry.data[CONF_PAN_ID]
    coordinator = MobiusCoordinator(hass, entry, pan_id)
    await coordinator.async_config_entry_first_refresh()
    tank = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **tank_device(pan_id))
    entry.runtime_data = MobiusRuntimeData(coordinator, pan_id, tank.id)
    remove_old_feed_mode_entities(hass, pan_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def remove_old_feed_mode_entities(hass: HomeAssistant, pan_id: int) -> None:
    """Feed mode was a button and a binary sensor before it became a switch."""
    reg = er.async_get(hass)
    for domain in (Platform.BUTTON, Platform.BINARY_SENSOR):
        if entity_id := reg.async_get_entity_id(domain, DOMAIN, f"{tank_id(pan_id)}_feed_mode"):
            reg.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: MobiusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MobiusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a pump or light the last poll didn't report; anything still there would come back."""
    data = entry.runtime_data
    current = data.coordinator.data.devices.keys() if data.coordinator.data else set()
    ids = {i for domain, i in device.identifiers if domain == DOMAIN}
    return not ids & (current | {data.tank_id})
