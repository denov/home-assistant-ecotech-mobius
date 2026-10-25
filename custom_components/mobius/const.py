# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)

from .protocol import PhysicalValue

DOMAIN = "mobius"
MANUFACTURER = "EcoTech Marine"
LOCAL_NAME = "MOBIUS"

CONF_PAN_ID = "pan_id"
# address -> [model id, serial] of the tank's devices, learned from scan responses
CONF_DEVICES = "devices"

SCAN_INTERVAL_SECONDS = 60
# a device that hasn't advertised for this long is left out of polls
STALE_ADVERT_SECONDS = 300

# OperationMode (attribute 105) names. The values seen on a real tank are in docs/02.
OPERATION_MODES: dict[int, str] = {
    0: "oob",
    1: "basic",
    2: "schedule",
    3: "demo",
    4: "usb_power",
    5: "disco_demo",
    32: "constant_speed",
    33: "lagoon",
    34: "reefcrest",
    35: "short_pulse_speed_set",
    36: "short_pulse_time_set",
    37: "sync_slave",
    38: "anti_sync_slave",
    39: "battery_backup",
    40: "feed_mode",
    41: "setup",
    42: "usb_override",
    43: "oob_mode",
    53: "preset_short_pulse",
    64: "tidal_swell",
    65: "nutrient_transport",
    66: "ecoback_slave",
    67: "gyre_speed_set",
    68: "gyre_time_set",
    70: "preset_gyre",
    72: "motor_off",
    73: "auto_tune_wave",
    74: "fine_tune_wave",
    75: "configuration",
    76: "rf_frequency_config",
    77: "transition",
    78: "expanding_pulse",
    79: "delayed_power",
    80: "vectra_calibration",
    240: "identify",
    255: "error",
}

# PhysicalValues (attribute 101) elements exposed as sensors: key, unit, device class.
# Units checked against an MP40 and a Radion on 2026-10-17 (°C, W, RPM, V, GPH).
PHYSICAL_SENSORS: dict[int, tuple[str, str | None, str | None]] = {
    PhysicalValue.DRIVER_TEMPERATURE: ("driver_temperature", UnitOfTemperature.CELSIUS, "temperature"),
    PhysicalValue.MOTOR_TEMPERATURE: ("motor_temperature", UnitOfTemperature.CELSIUS, "temperature"),
    PhysicalValue.CLUSTER_TEMPERATURE: ("cluster_temperature", UnitOfTemperature.CELSIUS, "temperature"),
    PhysicalValue.MOTOR_POWER: ("motor_power", UnitOfPower.WATT, "power"),
    PhysicalValue.MOTOR_RPM: ("motor_rpm", REVOLUTIONS_PER_MINUTE, None),
    PhysicalValue.BATTERY_VOLTAGE: ("battery_voltage", UnitOfElectricPotential.VOLT, "voltage"),
    PhysicalValue.INPUT_VOLTAGE: ("input_voltage", UnitOfElectricPotential.VOLT, "voltage"),
    PhysicalValue.INTERNAL_TEMPERATURE: ("internal_temperature", UnitOfTemperature.CELSIUS, "temperature"),
    PhysicalValue.GALLONS_PER_HOUR: ("flow", UnitOfVolumeFlowRate.GALLONS_PER_HOUR, "volume_flow_rate"),
    PhysicalValue.MODULE_TEMPERATURE: ("module_temperature", UnitOfTemperature.CELSIUS, "temperature"),
    PhysicalValue.CLUSTER2_TEMPERATURE: ("cluster2_temperature", UnitOfTemperature.CELSIUS, "temperature"),
}

# Light colour channel ids (docs/02) -> sensor key suffix. Brightness (1) is a master
# dimmer, not a colour, so it gets no sensor.
CHANNELS: dict[int, str] = {
    16: "cool_white",
    17: "blue",
    18: "royal_blue",
    19: "green",
    20: "red",
    21: "uv",
    22: "warm_white",
    23: "violet",
    24: "deep_blue",
    25: "deep_red",
    26: "neutral_white",
    27: "yellow",
    28: "amber",
    29: "far_red",
    30: "moonlight",
    31: "moonlight_white",
    32: "moonlight_blue",
    33: "cyan",
    34: "lime",
    35: "blue_and_white",
    36: "red_and_white",
    37: "uv_plus",
    38: "white",
}
