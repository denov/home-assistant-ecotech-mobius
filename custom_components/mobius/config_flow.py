# SPDX-License-Identifier: AGPL-3.0-or-later
"""Set up one Mobius tank (one mesh network) from Bluetooth discovery or a list of tanks in range."""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
import voluptuous as vol

from . import protocol as p
from .const import CONF_PAN_ID, DOMAIN
from .coordinator import is_mobius

ADVERT_TIMEOUT_SECONDS = 60


def _tanks_in_range(hass) -> dict[int, list[p.Advert]]:
    tanks: dict[int, dict[str, p.Advert]] = {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=True):
        if not is_mobius(info):
            continue
        adv = p.parse_advert(info.manufacturer_data)
        if adv is not None and adv.pan_id not in (0, 0xFFFF):
            tanks.setdefault(adv.pan_id, {})[adv.serial] = adv
    return {pan: list(devs.values()) for pan, devs in tanks.items()}


def _title(pan_id: int) -> str:
    return f"Mobius tank {pan_id:04X}"


def _unique_id(pan_id: int) -> str:
    return f"{pan_id:04x}"


def _summary(devices: list[p.Advert]) -> str:
    return ", ".join(sorted(f"{d.model} ({d.serial[-4:]})" for d in devices))


class MobiusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._pan_id: int | None = None

    async def async_step_bluetooth(self, discovery_info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        adv = p.parse_advert(discovery_info.manufacturer_data)
        if adv is None:
            # passive adverts carry only the service UUID; the panId is in the scan response
            try:
                info = await bluetooth.async_process_advertisements(
                    self.hass,
                    lambda i: p.parse_advert(i.manufacturer_data) is not None,
                    bluetooth.BluetoothCallbackMatcher(address=discovery_info.address),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                    ADVERT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return self.async_abort(reason="no_scan_response")
            adv = p.parse_advert(info.manufacturer_data)
        if adv is None or adv.pan_id in (0, 0xFFFF):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(_unique_id(adv.pan_id))
        self._abort_if_unique_id_configured()
        self._pan_id = adv.pan_id
        self.context["title_placeholders"] = {"name": _title(adv.pan_id)}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._pan_id is not None
        if user_input is not None:
            return self.async_create_entry(title=_title(self._pan_id), data={CONF_PAN_ID: self._pan_id})
        devices = _tanks_in_range(self.hass).get(self._pan_id, [])
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": _title(self._pan_id), "devices": _summary(devices) or "-"},
        )

    async def _async_active_scan(self) -> dict[int, list[p.Advert]]:
        """Seen Mobius devices without a scan response yet: ask for an active scan of one."""
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if not is_mobius(info):
                continue
            try:
                await bluetooth.async_process_advertisements(
                    self.hass,
                    lambda i: p.parse_advert(i.manufacturer_data) is not None,
                    bluetooth.BluetoothCallbackMatcher(address=info.address),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                    ADVERT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return {}
            return _tanks_in_range(self.hass)
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        configured = {e.unique_id for e in self._async_current_entries()}

        def new(found: dict[int, list[p.Advert]]) -> dict[int, list[p.Advert]]:
            return {pan: devs for pan, devs in found.items() if _unique_id(pan) not in configured}

        tanks = new(_tanks_in_range(self.hass))
        if user_input is not None:
            pan_id = int(user_input[CONF_PAN_ID])
            await self.async_set_unique_id(_unique_id(pan_id))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=_title(pan_id), data={CONF_PAN_ID: pan_id})
        if not tanks:
            tanks = new(await self._async_active_scan())
        if not tanks:
            return self.async_abort(reason="no_devices_found")
        options = {str(pan): f"{_title(pan)}: {_summary(devs)}" for pan, devs in tanks.items()}
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_PAN_ID): vol.In(options)})
        )
