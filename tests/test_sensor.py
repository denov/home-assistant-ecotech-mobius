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
    icons = ((1, "mdi:light-flood-down"), (4, "mdi:waves"), (5, "mdi:pump"), (99, "mdi:help-circle-outline"))
    for primitive, icon in icons:
        coordinator.data = TankData({adv.serial: DeviceState(adv.serial, "", adv, primitive=primitive)})
        assert sensor.icon == icon


def test_light_channels_become_percent_sensors():
    from types import SimpleNamespace

    from custom_components.mobius.coordinator import DeviceState, TankData
    from custom_components.mobius.sensor import ChannelSensor, channel_sensor_ids

    intensity = {1: 1000, 18: 770, 16: 129, 99: 5}  # Brightness and unknown ids get no sensor
    assert channel_sensor_ids(intensity) == [16, 18]

    adv = p.Advert(195, "0000000TESTSN3", 0x405F, False)
    coordinator = SimpleNamespace(known={adv.serial: adv}, data=None)
    data = SimpleNamespace(coordinator=coordinator, tank_device_id="tank", tank_id="tank_405f", pan_id=0x405F)
    sensor = ChannelSensor.__new__(ChannelSensor)
    ChannelSensor.__init__(sensor, data, adv.serial, 18)
    coordinator.data = TankData({adv.serial: DeviceState(adv.serial, "", adv, primitive=1, intensity=intensity)})
    assert sensor.translation_key == "channel_royal_blue"
    assert sensor.native_value == 77.0
    assert sensor.native_unit_of_measurement == "%"
