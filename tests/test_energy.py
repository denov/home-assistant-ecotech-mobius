"""Pump energy (kWh) from motor power, for the Energy dashboard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.mobius import protocol as p
from custom_components.mobius.coordinator import DeviceState, TankData
from custom_components.mobius.sensor import MotorEnergySensor

SERIAL = "0000000TESTSN1"


def reading(watts: int, read_at: float) -> TankData:
    adv = p.Advert(42, SERIAL, 0x405F, False)
    state = DeviceState(SERIAL, "AA:AA", adv, physical={p.PhysicalValue.MOTOR_POWER: watts}, read_at=read_at)
    return TankData({SERIAL: state})


@pytest.fixture
def sensor():
    coordinator = SimpleNamespace(known={SERIAL: p.Advert(42, SERIAL, 0x405F, False)}, data=None)
    data = SimpleNamespace(coordinator=coordinator, tank_device_id="tank", tank_id="tank_405f", pan_id=0x405F)
    s = MotorEnergySensor.__new__(MotorEnergySensor)
    MotorEnergySensor.__init__(s, data, SERIAL)
    return s, coordinator


def test_energy_class_fits_the_energy_dashboard(sensor):
    s, _ = sensor
    assert s.device_class == "energy"
    assert s.state_class == "total_increasing"
    assert s.native_unit_of_measurement == "kWh"


def test_integrates_power_between_fresh_readings(sensor):
    s, c = sensor
    c.data = reading(10, 1000.0)
    s._accumulate()
    c.data = reading(10, 1000.0 + 3600)  # 10 W for an hour
    s._accumulate()
    assert s.native_value == pytest.approx(0.01)


def test_kept_reading_is_not_counted_twice(sensor):
    s, c = sensor
    c.data = reading(10, 1000.0)
    s._accumulate()
    c.data = reading(10, 1060.0)
    s._accumulate()
    kwh = s.native_value
    s._accumulate()  # same reading again: a poll that kept the last one after a failed read
    assert s.native_value == kwh


def test_long_gap_is_not_counted(sensor):
    s, c = sensor
    c.data = reading(10, 1000.0)
    s._accumulate()
    c.data = reading(10, 1000.0 + 2 * 3600)  # e.g. Home Assistant was stopped
    s._accumulate()
    assert s.native_value == 0


async def test_total_survives_a_restart_while_unavailable(sensor):
    s, _ = sensor
    # the last state was "unavailable", but the stored sensor data still holds the number
    with (
        patch.object(s, "async_get_last_sensor_data", AsyncMock(return_value=SimpleNamespace(native_value=1.25))),
        patch.object(s, "async_get_last_state", AsyncMock(return_value=SimpleNamespace(state="unavailable"))),
    ):
        await s._restore()
    assert s.native_value == 1.25


async def test_total_restores_from_the_state_of_earlier_versions(sensor):
    s, _ = sensor
    # earlier versions saved no sensor data, only the state
    with (
        patch.object(s, "async_get_last_sensor_data", AsyncMock(return_value=None)),
        patch.object(s, "async_get_last_state", AsyncMock(return_value=SimpleNamespace(state="0.5"))),
    ):
        await s._restore()
    assert s.native_value == 0.5
