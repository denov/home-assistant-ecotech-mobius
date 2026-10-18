"""Which PhysicalValues become sensors."""

from custom_components.mobius import protocol as p
from custom_components.mobius.sensor import physical_sensor_indexes


def test_battery_voltage_waits_for_a_battery():
    pv = p.PhysicalValue
    physical = {pv.INPUT_VOLTAGE: 34, pv.BATTERY_VOLTAGE: 0, pv.MOTOR_POWER: 0, 9999: 1}
    assert physical_sensor_indexes(physical) == [pv.INPUT_VOLTAGE, pv.MOTOR_POWER]
    physical[pv.BATTERY_VOLTAGE] = 12
    assert pv.BATTERY_VOLTAGE in physical_sensor_indexes(physical)
