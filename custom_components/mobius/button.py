# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol as p
from .entity import MobiusConfigEntry, MobiusRuntimeData, MobiusTankEntity

# key -> (what, icon, scene id or None for "back to schedule")
BUTTONS: dict[str, tuple[str, str, int | None]] = {
    "all_off": ("turn everything off", "mdi:power-off", p.SceneId.ALL_OFF),
    "all_on": ("turn everything on", "mdi:power", p.SceneId.ALL_ON),
    "resume": ("resume the schedule", "mdi:calendar-clock", None),
}


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    async_add_entities(SceneButton(entry.runtime_data, key) for key in BUTTONS)


class SceneButton(MobiusTankEntity, ButtonEntity):
    """Tank-wide scene broadcast, like the scene tiles on the app's dashboard. Feed mode is a switch."""

    def __init__(self, data: MobiusRuntimeData, key: str) -> None:
        super().__init__(data, key)
        self._what, self._attr_icon, self._scene = BUTTONS[key]

    @property
    def available(self) -> bool:
        # commands go through whichever device is in range, not the last poll
        return True

    async def async_press(self) -> None:
        if self._scene is None:
            await self.coordinator.async_tank_command(self._what, lambda c, group: c.exit_scene(group))
        else:
            scene = self._scene
            await self.coordinator.async_tank_command(self._what, lambda c, group: c.start_scene(scene, group))
