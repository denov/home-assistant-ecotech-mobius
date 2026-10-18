# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import protocol as p
from .const import OPERATION_MODES, PHYSICAL_SENSORS
from .entity import MobiusConfigEntry, MobiusDeviceEntity, MobiusRuntimeData, MobiusTankEntity

ERROR_STATES = {
    0: "none", 1: "disconnect", 2: "temperature", 3: "stall", 4: "thermistor", 5: "under_voltage",
    6: "power_supply", 7: "schedule_playback", 8: "over_voltage", 9: "over_current",
    10: "lock_detection_current_limit", 11: "abnormal_speed", 12: "abnormal_kt", 13: "stuck_in_open_loop",
    14: "stuck_in_closed_loop", 15: "fan_fault", 16: "driver_temperature", 17: "led_channel_short_circuit",
    18: "led_channel_current_leak", 19: "led_channel_open_circuit", 20: "led_cluster_thermistor",
    21: "led_cluster_over_temperature",
}
OPERATION_STATES = {s.value: s.name.lower() for s in p.OperationState}


async def async_setup_entry(hass: HomeAssistant, entry: MobiusConfigEntry, async_add_entities: AddEntitiesCallback):
    data = entry.runtime_data
    async_add_entities([TankSceneSensor(data), TankSceneRemainingSensor(data)])

    added: set[tuple[str, str]] = set()

    @callback
    def discover() -> None:
        new: list[SensorEntity] = []
        for serial, state in (data.coordinator.data.devices if data.coordinator.data else {}).items():
            wanted: list[tuple[str, Callable[[], SensorEntity]]] = [
                ("mode", lambda s=serial: ModeSensor(data, s)),
                ("operation_state", lambda s=serial: OperationStateSensor(data, s)),
                ("error", lambda s=serial: ErrorSensor(data, s)),
            ]
            for index in state.physical:
                if index in PHYSICAL_SENSORS:
                    key = PHYSICAL_SENSORS[index][0]
                    wanted.append((key, lambda s=serial, i=index: PhysicalSensor(data, s, i)))
            if p.PhysicalValue.MOTOR_POWER in state.physical:
                wanted.append(("motor_energy", lambda s=serial: MotorEnergySensor(data, s)))
            for key, factory in wanted:
                if (serial, key) not in added:
                    added.add((serial, key))
                    new.append(factory())
        if new:
            async_add_entities(new)

    discover()
    entry.async_on_unload(data.coordinator.async_add_listener(discover))


def _label(names: dict[int, str], value: int | None) -> str | None:
    if value is None:
        return None
    return names.get(value, f"unknown_{value}")


class ModeSensor(MobiusDeviceEntity, SensorEntity):
    _attr_icon = "mdi:waves"

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "mode")

    @property
    def native_value(self) -> str | None:
        s = self.device_state
        return _label(OPERATION_MODES, s.operation_mode) if s else None

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        s = self.device_state
        return {"raw": s.operation_mode if s else None}


class OperationStateSensor(MobiusDeviceEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(OPERATION_STATES.values())

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "operation_state")

    @property
    def native_value(self) -> str | None:
        s = self.device_state
        if s is None or s.operation_state not in OPERATION_STATES:
            return None
        return OPERATION_STATES[s.operation_state]


class ErrorSensor(MobiusDeviceEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "error")

    @property
    def native_value(self) -> str | None:
        s = self.device_state
        return _label(ERROR_STATES, s.error_state) if s else None


class PhysicalSensor(MobiusDeviceEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, data: MobiusRuntimeData, serial: str, index: int) -> None:
        key, unit, device_class = PHYSICAL_SENSORS[index]
        super().__init__(data, serial, key)
        self._index = index
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = SensorDeviceClass(device_class) if device_class else None
        if key == "motor_rpm":
            self._attr_icon = "mdi:fan"

    @property
    def native_value(self) -> int | None:
        s = self.device_state
        return s.physical.get(self._index) if s else None


class MotorEnergySensor(MobiusDeviceEntity, RestoreEntity, SensorEntity):
    """kWh from motor power over time, for the Energy dashboard (like ha-apex's energy sensors)."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    # a longer gap between readings (e.g. Home Assistant was stopped) isn't counted
    MAX_GAP_SECONDS = 3600

    def __init__(self, data: MobiusRuntimeData, serial: str) -> None:
        super().__init__(data, serial, "motor_energy")
        self._kwh = 0.0
        self._last_read_at: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        try:
            self._kwh = float(state.state) if state else 0.0
        except ValueError:
            self._kwh = 0.0  # unknown or unavailable

    def _accumulate(self) -> None:
        """Add the power of the latest fresh reading over the time since the previous one."""
        s = self.device_state
        if s is None or s.read_at == self._last_read_at:
            return  # unavailable, or the same reading kept through a failed poll
        last, self._last_read_at = self._last_read_at, s.read_at
        watts = s.physical.get(p.PhysicalValue.MOTOR_POWER)
        if last is not None and watts is not None and 0 < s.read_at - last <= self.MAX_GAP_SECONDS:
            self._kwh += watts * (s.read_at - last) / 3600 / 1000

    @callback
    def _handle_coordinator_update(self) -> None:
        self._accumulate()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._kwh, 6)


class TankSceneSensor(MobiusTankEntity, SensorEntity):
    _attr_icon = "mdi:movie-open-outline"

    def __init__(self, data: MobiusRuntimeData) -> None:
        super().__init__(data, "scene")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        scene = data.scene if data else None
        return scene.name if scene else None


class TankSceneRemainingSensor(MobiusTankEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, data: MobiusRuntimeData) -> None:
        super().__init__(data, "scene_remaining")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        scene = data.scene if data else None
        if scene is None:
            return None
        return scene.timer if scene.active else 0
