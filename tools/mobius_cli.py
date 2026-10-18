# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talk to Mobius devices through an ESPHome Bluetooth proxy (no HA needed).

    python tools/mobius_cli.py --proxy 192.168.1.50 --key <api key> scan
    python tools/mobius_cli.py --proxy ... read AA:BB:CC:DD:EE:FF
    python tools/mobius_cli.py --proxy ... feed AA:BB:CC:DD:EE:FF
    python tools/mobius_cli.py --proxy ... resume AA:BB:CC:DD:EE:FF

Add -v for frame-level hex logs. The proxy can also be set with MOBIUS_PROXY / MOBIUS_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys
import types

# Load protocol.py and client.py (HA-free) as package "mobius" without running the
# integration's __init__.py, which imports Home Assistant.
_package = types.ModuleType("mobius")
_package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "mobius")]
sys.modules.setdefault("mobius", _package)

from bleak_esphome import APIConnectionManager  # noqa: E402
import habluetooth  # noqa: E402
from habluetooth import BluetoothManager, HaBleakClientWrapper  # noqa: E402

from mobius import protocol as p  # noqa: E402
from mobius.client import MobiusClient  # noqa: E402

READ_ATTRS = [
    p.Attr.NAME,
    p.Attr.MODEL,
    p.Attr.PRIMITIVE_TYPE,
    p.Attr.ERROR_STATE,
    p.Attr.GROUP_BITMAP,
    p.Attr.OPERATION_STATE,
    p.Attr.OPERATION_MODE,
    p.Attr.CURRENT_SCENE,
    p.Attr.SCENE_TIMER,
    p.Attr.SCENE_TIMEOUT,
]


class CliBluetoothManager(BluetoothManager):
    """HA's subclass routes adverts to integrations; here the history is enough."""

    def _discover_service_info(self, service_info) -> None:
        pass


async def setup_proxy(host: str, key: str | None, scan_seconds: float) -> BluetoothManager:
    logging.getLogger("habluetooth.manager").setLevel(logging.CRITICAL)  # no local adapter in WSL
    manager = CliBluetoothManager()
    habluetooth.set_manager(manager)
    await manager.async_setup()
    conn = APIConnectionManager({"address": host, "noise_psk": key})
    await asyncio.wait_for(conn.start(), 20)
    print(f"connected to proxy {host}, scanning {scan_seconds:.0f}s…", file=sys.stderr)
    await asyncio.sleep(scan_seconds)
    return manager


def mobius_adverts(manager: BluetoothManager):
    for info in manager.async_discovered_service_info(connectable=False):
        adv = p.parse_advert(info.manufacturer_data)
        if adv is not None:
            yield info, adv


async def cmd_scan(manager: BluetoothManager, _args) -> None:
    found = list(mobius_adverts(manager))
    if not found:
        print("no Mobius adverts seen. Raw manufacturer data of everything nearby:")
        for info in manager.async_discovered_service_info(connectable=False):
            md = {f"0x{k:04x}": v.hex() for k, v in info.manufacturer_data.items()}
            print(f"  {info.address} {info.name!r} rssi={info.rssi} {md} {info.service_uuids}")
        return
    for info, adv in found:
        print(
            f"{info.address}  {adv.model:<24} serial={adv.serial} panId=0x{adv.pan_id & 0xFFFF:04x}"
            f" legacy={adv.legacy} error={adv.has_error} rssi={info.rssi} name={info.name!r}"
            f" connectable={info.connectable}"
        )
        print(f"    raw={adv.raw.hex()}")


async def connect(manager: BluetoothManager, address: str) -> tuple[HaBleakClientWrapper, MobiusClient]:
    device = manager.async_ble_device_from_address(address.upper(), True)
    if device is None:
        raise SystemExit(f"{address} not seen as connectable by the proxy")
    ble = HaBleakClientWrapper(device)
    await ble.connect(timeout=30)
    print(f"connected to {address}, mtu={ble.mtu_size}", file=sys.stderr)
    client = MobiusClient(ble)
    await client.start()
    print(f"opcodes={client.opcodes.hex()} wide={client.wide}", file=sys.stderr)
    return ble, client


async def cmd_read(manager: BluetoothManager, args) -> None:
    ble, client = await connect(manager, args.address)
    try:
        for attr in READ_ATTRS:
            try:
                values = await client.get_attrs([attr])
            except (p.ProtocolError, TimeoutError) as err:
                print(f"{attr.name:<20} ERROR {err}")
                continue
            for v in values.values():
                print(f"{attr.name:<20} idx={v.index} {[e.hex() for e in v.elements]}")
        try:
            values = await client.get_attrs([(p.Attr.PHYSICAL_VALUES, 0, 0xFFFF)])
            for v in values.values():
                for i, e in sorted(v.items.items()):
                    label = p.PhysicalValue(i).name if i in p.PhysicalValue._value2member_map_ else i
                    print(f"PHYSICAL[{label}] = {p.i32(e) if len(e) >= 4 else e.hex()}")
        except (p.ProtocolError, TimeoutError) as err:
            print(f"PHYSICAL_VALUES ERROR {err}")
        print(await client.scene_state())
    finally:
        await ble.disconnect()


async def cmd_scene(manager: BluetoothManager, args) -> None:
    ble, client = await connect(manager, args.address)
    try:
        group = await client.tank_group()
        print(f"tank group {group}")
        if args.cmd == "resume":
            await client.exit_scene(group)
        else:
            scene = {"feed": p.SceneId.FEED_MODE, "all-off": p.SceneId.ALL_OFF, "all-on": p.SceneId.ALL_ON}
            await client.start_scene(scene[args.cmd], group)
        await asyncio.sleep(1)
        print(await client.scene_state())
    finally:
        await ble.disconnect()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", default=os.environ.get("MOBIUS_PROXY"), help="ESPHome proxy host/IP")
    ap.add_argument("--key", default=os.environ.get("MOBIUS_KEY"), help="ESPHome API encryption key")
    ap.add_argument("--scan", type=float, default=8, help="seconds to collect adverts")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    for name in ("read", "feed", "all-off", "all-on", "resume"):
        sub.add_parser(name).add_argument("address")
    args = ap.parse_args()
    if not args.proxy:
        ap.error("--proxy (or MOBIUS_PROXY) is required")

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    if args.verbose:
        logging.getLogger("custom_components.mobius").setLevel(logging.DEBUG)

    manager = await setup_proxy(args.proxy, args.key, args.scan)
    handler = {"scan": cmd_scan, "read": cmd_read}.get(args.cmd, cmd_scene)
    await handler(manager, args)


if __name__ == "__main__":
    asyncio.run(main())
