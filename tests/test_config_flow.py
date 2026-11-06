"""The user flow: pick a tank in range that isn't set up yet."""

import asyncio
import struct
import time
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mobius import protocol as p
from custom_components.mobius.const import CONF_PAN_ID, DOMAIN

PAN = 0x405F
MOD = "custom_components.mobius.config_flow"


def _info(address: str, pan: int | None):
    """A Mobius device as HA's Bluetooth cache has it; with `pan`, its scan response came in."""
    data = {}
    if pan is not None:
        raw = (
            bytes([2, 0x02]) + struct.pack("<H", 42) + b"\x00" + struct.pack("<iH", 0, pan)
            + b"0000000TESTSN1".ljust(17, b"\x00")
        )
        data = {int.from_bytes(raw[:2], "little"): raw[2:]}
    return SimpleNamespace(
        address=address, name="MOBIUS", rssi=-70, time=time.monotonic(),
        manufacturer_data=data, service_uuids=[p.SERVICE_UUID],
    )


@pytest.fixture(autouse=True)
def no_dependencies():
    with patch("homeassistant.loader.Integration.dependencies", []):
        yield


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_lists_a_new_tank(hass):
    with patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: [_info("AA:AA", PAN)]):
        result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_a_tank_already_set_up_aborts_straight_away(hass):
    MockConfigEntry(domain=DOMAIN, data={CONF_PAN_ID: PAN}, unique_id=f"{PAN:04x}").add_to_hass(hass)

    async def scan(*_a, **_k):
        raise AssertionError("no scan: the tank in range is already known")

    with (
        patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: [_info("AA:AA", PAN)]),
        patch(f"{MOD}.bluetooth.async_process_advertisements", scan),
    ):
        result = await asyncio.wait_for(_start(hass), 1)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_configured"


async def test_scanning_shows_progress_until_a_tank_answers(hass):
    seen = [_info("AA:AA", None)]  # advertising, but no scan response yet
    answered = asyncio.Event()

    async def scan(*_a, **_k):
        await answered.wait()
        seen[0] = _info("AA:AA", PAN)
        return seen[0]

    with (
        patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: list(seen)),
        patch(f"{MOD}.bluetooth.async_process_advertisements", scan),
    ):
        result = await asyncio.wait_for(_start(hass), 1)
        assert result["type"] is FlowResultType.SHOW_PROGRESS, "the wizard opens while the scan runs"
        answered.set()
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PAN_ID: str(PAN)})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PAN_ID: PAN}


async def test_nothing_in_range_after_the_scan(hass):
    async def scan(*_a, **_k):
        raise TimeoutError

    with (
        patch(f"{MOD}.bluetooth.async_discovered_service_info", lambda *_a, **_k: [_info("AA:AA", None)]),
        patch(f"{MOD}.bluetooth.async_process_advertisements", scan),
    ):
        result = await _start(hass)
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
