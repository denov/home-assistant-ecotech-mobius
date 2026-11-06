# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import time
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .client import SceneState
from .entity import MobiusConfigEntry, MobiusRuntimeData, MobiusTankEntity

# how long the switch shows a command's result while no device has been read since
ASSUME_SECONDS = 120


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities([FeedModeSwitch(entry.runtime_data)])


class FeedModeSwitch(MobiusTankEntity, SwitchEntity):
    """On while Feed mode runs. Off resumes the schedule, which ends Feed mode early."""

    _attr_icon = "mdi:fish"

    def __init__(self, data: MobiusRuntimeData) -> None:
        super().__init__(data, "feed_mode")
        # (on, time.monotonic() of the device's ack) of the last command
        self._sent: tuple[bool, float] | None = None

    @property
    def available(self) -> bool:
        # commands go through whichever device is in range, not the last poll
        return True

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if self._sent is not None:
            # after a command only readings taken since count; until one arrives, show the command's result
            on, sent_at = self._sent
            if (scene := data.scene_since(sent_at) if data else None) is not None:
                return _feed_mode(scene)
            if time.monotonic() - sent_at < ASSUME_SECONDS:
                return on
        scene = data.scene if data else None
        return None if scene is None else _feed_mode(scene)

    async def async_turn_on(self, **kwargs: Any) -> None:
        sent_at = await self.coordinator.async_tank_command(
            "start feed mode", lambda c, group: c.start_scene(p.SceneId.FEED_MODE, group)
        )
        self._sent = (True, sent_at)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        sent_at = await self.coordinator.async_tank_command("stop feed mode", lambda c, group: c.exit_scene(group))
        self._sent = (False, sent_at)
        self.async_write_ha_state()


def _feed_mode(scene: SceneState) -> bool:
    return scene.active and scene.scene_id == p.SceneId.FEED_MODE
