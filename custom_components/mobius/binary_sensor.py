# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MobiusConfigEntry, MobiusDeviceEntity, MobiusRuntimeData


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    data = entry.runtime_data
    added: set[str] = set()

    @callback
    def discover() -> None:
        serials = set(data.coordinator.data.devices) if data.coordinator.data else set()
        new = [ProblemSensor(data, s) for s in serials - added]
        added.update(serials)
        if new:
            async_add_entities(new)

    discover()
    entry.async_on_unload(data.coordinator.async_add_listener(discover))


class ProblemSensor(MobiusDeviceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "problem")

    @property
    def is_on(self) -> bool | None:
        s = self.device_state
        return s.error_state != 0 if s else None
