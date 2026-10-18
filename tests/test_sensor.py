"""Which PhysicalValues become sensors."""

from custom_components.mobius import protocol as p
from custom_components.mobius.sensor import physical_sensor_indexes


def test_battery_voltage_waits_for_a_battery():
    pv = p.PhysicalValue
    physical = {pv.INPUT_VOLTAGE: 34, pv.BATTERY_VOLTAGE: 0, pv.MOTOR_POWER: 0, 9999: 1}
    assert physical_sensor_indexes(physical) == [pv.INPUT_VOLTAGE, pv.MOTOR_POWER]
    physical[pv.BATTERY_VOLTAGE] = 12
    assert pv.BATTERY_VOLTAGE in physical_sensor_indexes(physical)


def test_mode_icon_shows_the_device_type():
    from types import SimpleNamespace
    from custom_components.mobius.coordinator import DeviceState, TankData
    from custom_components.mobius.sensor import ModeSensor

    adv = p.Advert(149, "0000000TESTSN2", 0x405F, False)
    coordinator = SimpleNamespace(known={adv.serial: adv}, data=None)
    data = SimpleNamespace(coordinator=coordinator, tank_device_id="tank", tank_id="tank_405f", pan_id=0x405F)
    sensor = ModeSensor.__new__(ModeSensor)
    ModeSensor.__init__(sensor, data, adv.serial)
    for primitive, icon in ((1, "mdi:light-flood-down"), (4, "mdi:waves"), (5, "mdi:pump"), (99, "mdi:help-circle-outline")):
        coordinator.data = TankData({adv.serial: DeviceState(adv.serial, "", adv, primitive=primitive)})
        assert sensor.icon == icon
