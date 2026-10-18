# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .entity import MobiusConfigEntry, MobiusDeviceEntity, MobiusRuntimeData, MobiusTankEntity


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    data = entry.runtime_data
    async_add_entities([FeedModeSensor(data)])

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


class FeedModeSensor(MobiusTankEntity, BinarySensorEntity):
    _attr_icon = "mdi:fish"

    def __init__(self, data: MobiusRuntimeData) -> None:
        super().__init__(data, "feed_mode")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        scene = data.scene if data else None
        if scene is None:
            return None
        return scene.active and scene.scene_id == p.SceneId.FEED_MODE


class ProblemSensor(MobiusDeviceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "problem")

    @property
    def is_on(self) -> bool | None:
        s = self.device_state
        return s.error_state != 0 if s else None
