# SPDX-License-Identifier: AGPL-3.0-or-later
"""EcoTech Mobius: local BLE control of Radion, Vectra and VorTech devices."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, split_entity_id
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
    migrate_feed_mode_entities(hass, entry, pan_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def migrate_feed_mode_entities(hass: HomeAssistant, entry: MobiusConfigEntry, pan_id: int) -> None:
    """Feed mode was a button and a binary sensor before it became a switch.

    The switch takes over the button's entity ID, so it matches the tank's other
    entities; a new ID would start with the device's area (HA 2026.10).
    """
    reg = er.async_get(hass)
    unique_id = f"{tank_id(pan_id)}_feed_mode"
    for domain in (Platform.BUTTON, Platform.BINARY_SENSOR):
        if not (entity_id := reg.async_get_entity_id(domain, DOMAIN, unique_id)):
            continue
        reg.async_remove(entity_id)
        if not reg.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id):
            object_id = split_entity_id(entity_id)[1]
            reg.async_get_or_create(
                Platform.SWITCH, DOMAIN, unique_id, config_entry=entry, suggested_object_id=object_id
            )


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
