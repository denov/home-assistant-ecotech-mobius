# SPDX-License-Identifier: AGPL-3.0-or-later
"""Async request/response client for one BLE-connected Mobius device.

Works with any connected bleak client (plain bleak, habluetooth, HA). Requests to
other devices in the same mesh go through the connected device as CoAP relays.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
import logging
import struct
from typing import Any

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

DIRECT_TIMEOUT = 5.0
# relayed reads answer in ~0.5 s (docs/02); the app waits 20 s, too long for a poll
COAP_TIMEOUT = 5.0


@dataclass(slots=True)
class SceneState:
    scene_id: int
    timer: int
    timeout: int

    @property
    def active(self) -> bool:
        return self.timeout > 0 and self.scene_id != p.SceneId.EMPTY

    @property
    def name(self) -> str:
        try:
            return p.SceneId(self.scene_id).name.lower() if self.active else "none"
        except ValueError:
            return f"custom_{self.scene_id}"


class MobiusClient:
    """Speak the C2/FSCI protocol over a connected bleak client."""

    def __init__(self, bleak_client: Any) -> None:
        self._ble = bleak_client
        self._rx = bytearray()
        self._pending: dict[tuple[int, int, int], asyncio.Future[p.Frame]] = {}
        self._coap_pending: dict[int, asyncio.Future[p.Frame]] = {}
        self._msg_ids = itertools.cycle(range(1, 20000))
        self._tokens = itertools.count(101)
        self._write_lock = asyncio.Lock()
        self.wide = True
        self.opcodes: bytes = b""
        self.indications: asyncio.Queue[p.Frame] = asyncio.Queue(maxsize=100)

    @property
    def mtu(self) -> int:
        size = getattr(self._ble, "mtu_size", 23) or 23
        return max(20, size - 3)

    async def start(self, opcodes: bytes | None = None) -> None:
        """Enable notifications and pick the opcode variants; pass `opcodes` from an earlier connection to skip asking."""
        await self._ble.start_notify(p.RX_DATA_UUID, self._on_rx_data)
        await self._ble.start_notify(p.RX_FINAL_UUID, self._on_rx_final)
        if opcodes is not None:
            self.opcodes = opcodes
        else:
            try:
                self.opcodes = await self.get_opcodes()
            except (TimeoutError, p.ProtocolError):
                _LOGGER.debug("GetOpCodes failed; falling back to v1 attribute opcodes")
                self.opcodes = b""
        self.wide = p.Op.GET_ATTR_V2 in self.opcodes and p.Op.SET_ATTR_V2 in self.opcodes

    # --- notifications ------------------------------------------------------

    def _on_rx_data(self, _char: Any, data: bytearray) -> None:
        self._rx += data

    def _on_rx_final(self, _char: Any, data: bytearray) -> None:
        self._rx += data
        raw, self._rx = bytes(self._rx), bytearray()
        try:
            frame = p.Frame.decode(raw)
        except p.ProtocolError as err:
            _LOGGER.debug("Dropping frame: %s", err)
            return
        _LOGGER.debug("<- %s", raw.hex())
        self._dispatch(frame)

    def _dispatch(self, frame: p.Frame) -> None:
        if frame.group == p.OpGroup.C2CI_CONFIRM and frame.op == p.Op.COAP_INDICATION:
            try:
                ind = p.coap_unwrap(frame.payload)
                inner = p.Frame.decode(ind.inner)
            except p.ProtocolError as err:
                _LOGGER.debug("Bad CoAP indication: %s", err)
                return
            fut = self._coap_pending.pop(ind.token, None)
            if fut and not fut.done():
                fut.set_result(inner)
            else:
                self._queue_indication(inner)
            return
        if frame.group == p.OpGroup.C2CI_CONFIRM and frame.op == p.Op.COAP_REQUEST:
            return  # local ack that the relay was queued; the answer comes as 0x1A
        fut = self._pending.pop((frame.group, frame.op, frame.msg_id), None)
        if fut and not fut.done():
            fut.set_result(frame)
        else:
            self._queue_indication(frame)

    def _queue_indication(self, frame: p.Frame) -> None:
        try:
            self.indications.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    # --- sending ------------------------------------------------------------

    async def _write(self, data: bytes) -> None:
        _LOGGER.debug("-> %s", data.hex())
        async with self._write_lock:
            parts = p.chunk(data, self.mtu)
            for part in parts[:-1]:
                await self._ble.write_gatt_char(p.TX_DATA_UUID, part, response=False)
            await self._ble.write_gatt_char(p.TX_FINAL_UUID, parts[-1], response=False)

    async def request(
        self,
        op: int,
        payload: bytes = b"",
        *,
        group: int | None = None,
        target_ipv6: bytes | None = None,
        timeout: float | None = None,
    ) -> p.Frame:
        """Send a C2CI request and wait for its confirm."""
        frame = p.Frame(p.OpGroup.C2CI_REQUEST, op, next(self._msg_ids), payload)
        if group is not None:
            frame.set_group(group)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[p.Frame] = loop.create_future()

        if target_ipv6 is None:
            key = (p.OpGroup.C2CI_CONFIRM, op, frame.msg_id)
            self._pending[key] = fut
            data = frame.encode()
            wait = timeout or DIRECT_TIMEOUT
            cleanup = lambda: self._pending.pop(key, None)  # noqa: E731
        else:
            token = next(self._tokens)
            self._coap_pending[token] = fut
            outer = p.Frame(
                p.OpGroup.C2CI_REQUEST,
                p.Op.COAP_REQUEST,
                next(self._msg_ids),
                p.coap_wrap(target_ipv6, token, frame.encode()),
            )
            data = outer.encode()
            wait = timeout or COAP_TIMEOUT
            cleanup = lambda: self._coap_pending.pop(token, None)  # noqa: E731

        try:
            # One retry, like BaseConnection.requestTimeout.
            for attempt in range(2):
                await self._write(data)
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), wait)
                except TimeoutError:
                    if attempt:
                        raise
                    _LOGGER.debug("Retrying %s", data.hex())
            raise TimeoutError  # unreachable
        finally:
            cleanup()

    # --- operations -----------------------------------------------------------

    async def get_opcodes(self) -> bytes:
        frame = await self.request(p.Op.GET_OPCODES)
        return p.parse_opcodes(frame.payload)

    async def get_attrs(
        self, attrs: list[int | tuple[int, int, int]], *, target_ipv6: bytes | None = None
    ) -> dict[int, p.AttrValue]:
        spec = [a if isinstance(a, tuple) else (a, 0, 1) for a in attrs]
        op = p.Op.GET_ATTR_V2 if self.wide else p.Op.GET_ATTR_V1
        frame = await self.request(op, p.get_attrs_payload(spec, self.wide), target_ipv6=target_ipv6)
        status, values = p.parse_get_response(frame.payload, self.wide)
        if status != p.Status.SUCCESS:
            raise p.ProtocolError(f"get {spec} failed: {_status(status)}")
        return values

    async def set_attrs(
        self,
        attrs: list[tuple[int, int, list[bytes]]],
        *,
        group: int | None = None,
        target_ipv6: bytes | None = None,
    ) -> None:
        op = p.Op.SET_ATTR_V2 if self.wide else p.Op.SET_ATTR_V1
        frame = await self.request(
            op, p.set_attrs_payload(attrs, self.wide), group=group, target_ipv6=target_ipv6
        )
        if not frame.payload or frame.payload[0] != p.Status.SUCCESS:
            status = frame.payload[0] if frame.payload else -1
            raise p.ProtocolError(f"set failed: {_status(status)}")

    async def tank_group(self) -> int:
        values = await self.get_attrs([p.Attr.GROUP_BITMAP])
        return p.tank_group(p.u64(values[p.Attr.GROUP_BITMAP].elements[0]))

    async def mesh_devices(self) -> list[p.MeshDevice]:
        """The devices in this device's Thread mesh, itself included."""
        values = await self.get_attrs([p.Attr.MESH_LOCAL_ADDRESSES, (p.Attr.NETWORKED_THREAD_DEVICES, 0, 0xFFFF)])
        own = values[p.Attr.MESH_LOCAL_ADDRESSES].first
        return p.parse_mesh_devices(values[p.Attr.NETWORKED_THREAD_DEVICES], own)

    async def scene_state(self, *, target_ipv6: bytes | None = None) -> SceneState:
        v = await self.get_attrs(
            [p.Attr.CURRENT_SCENE, p.Attr.SCENE_TIMER, p.Attr.SCENE_TIMEOUT], target_ipv6=target_ipv6
        )
        return SceneState(
            p.u16(v[p.Attr.CURRENT_SCENE].elements[0]),
            p.u16(v[p.Attr.SCENE_TIMER].elements[0]),
            p.u16(v[p.Attr.SCENE_TIMEOUT].elements[0]),
        )

    async def start_scene(self, scene_id: int, group: int) -> None:
        """Tank-wide scene broadcast (SceneCollectionView.onItemClick)."""
        await self.set_attrs(
            [(p.Attr.CURRENT_SCENE, 0, [struct.pack("<HH", scene_id, 0)])], group=group
        )

    async def exit_scene(self, group: int) -> None:
        """Back to schedule for the whole tank (SceneCollectionView.exitSceneBroadcast)."""
        await self.set_attrs(
            [(p.Attr.OPERATION_STATE, 0, [bytes([p.OperationState.SCHEDULE])])], group=group
        )


def _status(status: int) -> str:
    try:
        return p.Status(status).name
    except ValueError:
        return str(status)
