# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DeviceState, MobiusCoordinator


@dataclass
class MobiusRuntimeData:
    coordinator: MobiusCoordinator
    pan_id: int
    # device registry id of the tank, which every pump and light hangs off
    tank_device_id: str

    @property
    def tank_id(self) -> str:
        return f"tank_{self.pan_id:04x}"


type MobiusConfigEntry = ConfigEntry[MobiusRuntimeData]


def tank_device(tank_id: str, pan_id: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, tank_id)},
        name=f"Mobius tank {pan_id:04X}",
        manufacturer=MANUFACTURER,
        model="Mobius tank",
    )


def device_label(serial: str, state: DeviceState | None, model: str) -> str:
    """The device's own name, else its model; plus the serial's tail, which tells devices apart."""
    base = (state.name if state and state.name else "") or model
    return f"{base} {serial[-4:]}"


class MobiusTankEntity(CoordinatorEntity[MobiusCoordinator]):
    """Belongs to the tank as a whole (scenes, feed mode)."""

    _attr_has_entity_name = True

    def __init__(self, data: MobiusRuntimeData, key: str) -> None:
        super().__init__(data.coordinator)
        self._attr_unique_id = f"{data.tank_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = tank_device(data.tank_id, data.pan_id)


class MobiusDeviceEntity(CoordinatorEntity[MobiusCoordinator]):
    """Belongs to one pump or light."""

    _attr_has_entity_name = True

    def __init__(self, data: MobiusRuntimeData, serial: str, key: str) -> None:
        super().__init__(data.coordinator)
        self.serial = serial
        adv = data.coordinator.known[serial]
        state = data.coordinator.data.devices.get(serial) if data.coordinator.data else None
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            connections={(CONNECTION_BLUETOOTH, state.address)} if state and state.address else set(),
            name=device_label(serial, state, adv.model),
            manufacturer=MANUFACTURER,
            model=adv.model,
            model_id=str(adv.model_id),
            serial_number=serial,
            via_device_id=data.tank_device_id,
        )

    @property
    def device_state(self) -> DeviceState | None:
        data = self.coordinator.data
        return data.devices.get(self.serial) if data else None

    @property
    def available(self) -> bool:
        return super().available and self.device_state is not None
