# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .entity import MobiusConfigEntry, MobiusRuntimeData, MobiusTankEntity


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities([FeedModeSwitch(entry.runtime_data)])


class FeedModeSwitch(MobiusTankEntity, SwitchEntity):
    """On while Feed mode runs. Off resumes the schedule, which ends Feed mode early."""

    _attr_icon = "mdi:fish"

    def __init__(self, data: MobiusRuntimeData) -> None:
        super().__init__(data, "feed_mode")

    @property
    def available(self) -> bool:
        # commands go through whichever device is in range, not the last poll
        return True

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        scene = data.scene if data else None
        if scene is None:
            return None
        return scene.active and scene.scene_id == p.SceneId.FEED_MODE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_tank_command(
            "start feed mode", lambda c, group: c.start_scene(p.SceneId.FEED_MODE, group)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_tank_command("stop feed mode", lambda c, group: c.exit_scene(group))
