# SPDX-License-Identifier: AGPL-3.0-or-later
"""Polls every device of one Mobius tank over BLE and sends tank-wide scene commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import protocol as p
from .client import MobiusClient, SceneState
from .const import CONF_DEVICES, LOCAL_NAME, SCAN_INTERVAL_SECONDS, STALE_ADVERT_SECONDS

_LOGGER = logging.getLogger(__name__)

# one batched read per device; PhysicalValues is read on its own (sparse, device specific)
STATE_ATTRS = [
    p.Attr.NAME,
    p.Attr.PRIMITIVE_TYPE,
    p.Attr.ERROR_STATE,
    p.Attr.GROUP_BITMAP,
    p.Attr.OPERATION_STATE,
    p.Attr.OPERATION_MODE,
    p.Attr.CURRENT_SCENE,
    p.Attr.SCENE_TIMER,
    p.Attr.SCENE_TIMEOUT,
]

# entities go unavailable after this many failed reads in a row (like ha-apex)
UNAVAILABLE_AFTER_FAILURES = 3

# how long the first poll waits for scan responses before giving up (HA retries setup)
FIRST_ADVERT_WAIT_SECONDS = 40

# connection failures in a row (poll or command) after which the radio path counts as down
ABORT_AFTER_CONNECT_FAILURES = 2

type Seen = bluetooth.BluetoothServiceInfoBleak

CONNECT_ERRORS = (BleakError, TimeoutError, p.ProtocolError, EOFError)


@dataclass
class DeviceState:
    """What one poll read from one device."""

    serial: str
    address: str
    advert: p.Advert
    name: str = ""
    primitive: int = 0
    error_state: int = 0
    group: int = 0
    operation_state: int | None = None
    operation_mode: int | None = None
    scene: SceneState | None = None
    physical: dict[int, int] = field(default_factory=dict)
    # lights: VisualID -> intensity in tenths of a percent (1000 = 100 %)
    intensity: dict[int, int] = field(default_factory=dict)
    # time.monotonic() of the read; a reading kept through failed polls keeps its old time
    read_at: float = 0.0

    @property
    def is_light(self) -> bool:
        return self.primitive == 1  # VisualV1


@dataclass
class TankData:
    devices: dict[str, DeviceState]

    @property
    def scene(self) -> SceneState | None:
        """The tank's scene: scenes are broadcast, so the longest-running one wins."""
        scenes = [d.scene for d in self.devices.values() if d.scene is not None]
        active = [s for s in scenes if s.active]
        if active:
            return max(active, key=lambda s: s.timer)
        return scenes[0] if scenes else None

    @property
    def group(self) -> int:
        return next((d.group for d in self.devices.values() if d.group), 0)


def is_mobius(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    return info.name == LOCAL_NAME or p.SERVICE_UUID in info.service_uuids


class MobiusCoordinator(DataUpdateCoordinator[TankData]):
    """One tank (one Thread mesh, identified by panId)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, pan_id: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Mobius tank {pan_id:04x}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.pan_id = pan_id
        # one BLE connection at a time: a poll takes it per device read, so a command waits
        # for at most one read; a command holds it for all its attempts
        self._lock = asyncio.Lock()
        # failed connects in a row, polls and commands alike; reset by any successful connect
        self._connect_failures = 0
        # address -> GetOpCodes result, so later connections skip that round trip
        self._opcodes: dict[str, bytes] = {}
        # serial -> failed reads in a row; the last reading stands in until UNAVAILABLE_AFTER_FAILURES
        self._failures: dict[str, int] = {}
        # serial -> last advert, so devices keep their entities while briefly unreachable
        self.known: dict[str, p.Advert] = {}
        # address -> this tank's device there. Model, serial and panId are only in scan
        # responses (active scans), so the map is kept in the entry to survive restarts.
        self.adverts: dict[str, p.Advert] = {
            addr: p.Advert(model_id, serial, pan_id, False)
            for addr, (model_id, serial) in entry.data.get(CONF_DEVICES, {}).items()
        }
        self._active_scans: dict[str, Callable[[], None]] = {}

    @callback
    def _track_addresses(self) -> None:
        """Ask for active scans of every Mobius address, so scan responses keep arriving.

        HA runs ESPHome proxies in AUTO mode: passive, with active windows only for
        addresses an integration registered an ACTIVE callback for.
        """
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if not is_mobius(info) or info.address in self._active_scans or self._other_tank(info):
                continue
            unsub = bluetooth.async_register_callback(
                self.hass,
                self._on_advert,
                bluetooth.BluetoothCallbackMatcher(address=info.address),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
            self._active_scans[info.address] = unsub
            # also runs when setup fails, unlike async_shutdown
            self.config_entry.async_on_unload(unsub)

    @callback
    def _on_advert(self, info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange) -> None:
        if self._other_tank(info) and (unsub := self._active_scans.pop(info.address, None)):
            unsub()  # a neighbour's tank: stop keeping the proxy busy with it
            return
        self._remember(info)

    def _other_tank(self, info: bluetooth.BluetoothServiceInfoBleak) -> bool:
        adv = p.parse_advert(info.manufacturer_data)
        return adv is not None and adv.pan_id != self.pan_id

    @callback
    def _remember(self, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        """Add a scan response from this tank to the device map, saving it when it changes."""
        adv = p.parse_advert(info.manufacturer_data)
        if adv is None or adv.pan_id != self.pan_id or self.adverts.get(info.address) == adv:
            return
        self.adverts[info.address] = adv
        entry = self.config_entry
        devices = {addr: [a.model_id, a.serial] for addr, a in self.adverts.items()}
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICES: devices})

    @callback
    def advertised(self) -> dict[str, tuple[bluetooth.BluetoothServiceInfoBleak, p.Advert]]:
        """This tank's connectable devices seen recently, by serial (strongest address wins)."""
        now = time.monotonic()
        found: dict[str, tuple[bluetooth.BluetoothServiceInfoBleak, p.Advert]] = {}
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if not is_mobius(info) or now - info.time > STALE_ADVERT_SECONDS:
                continue
            self._remember(info)
            if (adv := self.adverts.get(info.address)) is None:
                continue
            prev = found.get(adv.serial)
            if prev is None or info.rssi > prev[0].rssi:
                found[adv.serial] = (info, adv)
        return found

    async def _with_client(
        self, info: bluetooth.BluetoothServiceInfoBleak, action: Callable[[MobiusClient], Awaitable[Any]]
    ) -> Any:
        """One connection to one device; the caller holds self._lock."""
        ble_device = bluetooth.async_ble_device_from_address(self.hass, info.address, connectable=True)
        if ble_device is None:
            raise BleakError(f"{info.address} is not connectable right now")
        # one attempt: a device that's down costs ~40 s per try, and the next poll retries
        try:
            ble = await establish_connection(BleakClientWithServiceCache, ble_device, info.address, max_attempts=1)
        except CONNECT_ERRORS:
            self._connect_failures += 1
            raise
        self._connect_failures = 0
        try:
            client = MobiusClient(ble)
            await client.start(self._opcodes.get(info.address))
            self._opcodes[info.address] = client.opcodes
            return await action(client)
        finally:
            try:
                await ble.disconnect()
            except BleakError as err:
                _LOGGER.debug("%s: disconnect failed: %s", info.address, err)

    def _path_down(self, tried: bool) -> bool:
        """Stop trying once something was tried and the last connects all failed."""
        return tried and self._connect_failures >= ABORT_AFTER_CONNECT_FAILURES

    async def _read_device(
        self, client: MobiusClient, address: str, adv: p.Advert, target: bytes | None = None
    ) -> DeviceState:
        """Read one device: the connected one, or (with `target`) one the connected device relays to."""
        state = DeviceState(adv.serial, address, adv, read_at=time.monotonic())
        values = await _get_tolerant(client, STATE_ATTRS, target)
        if (v := values.get(p.Attr.NAME)) is not None:
            state.name = p.text(v.first)
        if (v := values.get(p.Attr.PRIMITIVE_TYPE)) is not None:
            state.primitive = p.u8(v.first)
        if (v := values.get(p.Attr.ERROR_STATE)) is not None:
            state.error_state = p.u16(v.first)
        if (v := values.get(p.Attr.GROUP_BITMAP)) is not None:
            state.group = p.tank_group(p.u64(v.first))
        if (v := values.get(p.Attr.OPERATION_STATE)) is not None:
            state.operation_state = p.u8(v.first)
        if (v := values.get(p.Attr.OPERATION_MODE)) is not None:
            state.operation_mode = p.u16(v.first) if len(v.first) >= 2 else p.u8(v.first)
        scene_attrs = (p.Attr.CURRENT_SCENE, p.Attr.SCENE_TIMER, p.Attr.SCENE_TIMEOUT)
        if all(a in values for a in scene_attrs):
            state.scene = SceneState(*(p.u16(values[a].first) for a in scene_attrs))
        try:
            pv = await client.get_attrs([(p.Attr.PHYSICAL_VALUES, 0, 0xFFFF)], target_ipv6=target)
        except p.ProtocolError as err:
            _LOGGER.debug("%s: no physical values (%s)", adv.serial, err)
        else:
            if (v := pv.get(p.Attr.PHYSICAL_VALUES)) is not None:
                state.physical = {i: p.i32(b) for i, b in v.items.items() if len(b) >= 4}
        if state.is_light:  # pumps reject Intensity, so it stays out of the shared batch
            try:
                iv = await client.get_attrs([(p.Attr.INTENSITY, 0, 0xFFFF)], target_ipv6=target)
            except p.ProtocolError as err:
                _LOGGER.debug("%s: no intensity (%s)", adv.serial, err)
            else:
                if (v := iv.get(p.Attr.INTENSITY)) is not None:
                    state.intensity = {i: p.u16(b) for i, b in v.items.items() if len(b) >= 2}
        return state

    async def _read_tank(
        self, client: MobiusClient, info: Seen, adv: p.Advert, seen: dict[str, tuple[Seen, p.Advert]],
        devices: dict[str, DeviceState],
    ) -> None:
        """Read the bridge directly, then every other mesh device through its relay (~0.5 s each vs ~2 s direct)."""
        devices[adv.serial] = await self._read_device(client, info.address, adv)
        try:
            mesh = await client.mesh_devices()
        except (p.ProtocolError, TimeoutError) as err:
            _LOGGER.debug("%s: no mesh list (%s)", adv.serial, err)
            return
        for member in mesh:
            if member.serial == adv.serial:
                continue  # a relay to yourself never answers
            known = self.known.setdefault(member.serial, p.Advert(member.model_id, member.serial, self.pan_id, False))
            address = seen[member.serial][0].address if member.serial in seen else ""
            try:
                devices[member.serial] = await self._read_device(client, address, known, member.ipv6)
            except (p.ProtocolError, TimeoutError) as err:
                _LOGGER.debug("%s: relayed read failed: %s", member.serial, err)

    async def _async_update_data(self) -> TankData:
        self._track_addresses()
        seen = self.advertised()
        if not seen and self.data is None:
            # first poll after a restart: wait for an active scan to deliver scan responses
            for _ in range(FIRST_ADVERT_WAIT_SECONDS // 2):
                await asyncio.sleep(2)
                seen = self.advertised()
                if seen:
                    break
        for serial, (_info, adv) in seen.items():
            self.known[serial] = adv
        if not seen:
            raise UpdateFailed("No Mobius device of this tank is advertising")

        last = self.data.devices if self.data else {}
        devices: dict[str, DeviceState] = {}
        errors: dict[str, str] = {}
        tried = False
        # mesh first: one connection to the strongest device that takes one
        for info, adv in _strongest(seen):
            if self._path_down(tried):
                break
            tried = True
            try:
                async with self._lock:
                    await self._with_client(
                        info, lambda c, info=info, adv=adv: self._read_tank(c, info, adv, seen, devices)
                    )
                break
            except CONNECT_ERRORS as err:
                errors[adv.serial] = f"{info.address}: {err}"
        # then direct connections to advertising devices the mesh didn't deliver
        for serial, (info, adv) in seen.items():
            if serial in devices or self._path_down(tried):
                continue
            tried = True
            try:
                async with self._lock:
                    devices[serial] = await self._with_client(
                        info, lambda c, info=info, adv=adv: self._read_device(c, info.address, adv)
                    )
            except CONNECT_ERRORS as err:
                errors[serial] = f"{info.address}: {err}"
        for serial in devices:
            self._failures[serial] = 0
        for serial in (seen.keys() | last.keys()) - devices.keys():
            why = errors.get(serial) or ("skipped: the last connects all failed" if serial in seen else "not seen")
            self._keep_last(serial, last, devices, why)
        if not devices:
            raise UpdateFailed(f"Couldn't read any of {len(seen)} advertising Mobius devices")
        return TankData(devices)

    def _keep_last(self, serial: str, last: dict[str, DeviceState], devices: dict[str, DeviceState], why: str) -> None:
        """Count a failed read; the last reading stands in until UNAVAILABLE_AFTER_FAILURES in a row."""
        failures = self._failures[serial] = self._failures.get(serial, 0) + 1
        _LOGGER.debug("%s: read failed (%d in a row): %s", serial, failures, why)
        if failures < UNAVAILABLE_AFTER_FAILURES and serial in last:
            devices[serial] = last[serial]

    async def async_tank_command(self, what: str, action: Callable[[MobiusClient, int], Awaitable[None]]) -> None:
        """Send a tank-wide broadcast through the strongest connectable device, else the next."""
        seen = self.advertised()
        if not seen:
            raise HomeAssistantError(f"Can't {what}: no device of this tank is in Bluetooth range")
        last_err: Exception | None = None
        sent = False
        async with self._lock:  # for all attempts, so poll reads can't slip in between
            for info, _adv in _strongest(seen):
                if self._path_down(last_err is not None):
                    break
                try:
                    await self._with_client(info, lambda c: _broadcast(c, action, self.data))
                except CONNECT_ERRORS as err:
                    _LOGGER.debug("%s via %s failed: %s", what, info.address, err)
                    last_err = err
                else:
                    sent = True
                    break
        if not sent:
            raise HomeAssistantError(f"Couldn't {what}: {last_err}") from last_err
        await self.async_request_refresh()


def _strongest(seen: dict[str, tuple[Seen, p.Advert]]) -> list[tuple[Seen, p.Advert]]:
    """The two devices with the best signal: who to connect to for the whole tank."""
    return sorted(seen.values(), key=lambda s: s[0].rssi, reverse=True)[:2]


async def _broadcast(client: MobiusClient, action, data: TankData | None) -> None:
    group = (data.group if data else 0) or await client.tank_group()
    await action(client, group)


async def _get_tolerant(client: MobiusClient, attrs: list[int], target: bytes | None) -> dict[int, p.AttrValue]:
    """One batched read; if the device rejects the batch, read the attributes one by one."""
    try:
        return await client.get_attrs(attrs, target_ipv6=target)
    except p.ProtocolError as err:
        _LOGGER.debug("Batched read failed (%s); reading one by one", err)
    values: dict[int, p.AttrValue] = {}
    for attr in attrs:
        try:
            values.update(await client.get_attrs([attr], target_ipv6=target))
        except p.ProtocolError:
            continue
    return values
