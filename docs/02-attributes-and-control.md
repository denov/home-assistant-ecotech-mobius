# 02 — Attributes, state and control (HA v1 scope)

Transport and framing are covered in [01-ble-transport.md](01-ble-transport.md). Paths are relative to `~/app-decompiled/sources/com/`.

## Opcode discovery

`GetOpCodes`: `DE 29`, empty payload (`c2/comm/requests/GetOpCodesRequest.java`).
Response data: `status u8 | ?? u16 (opCodeVersion?) | count u16 | count × opcode u8`.
If the list contains `0x1C`/`0x1D`, use the 2-byte get/set variants; otherwise use `0x17`/`0x18`.

## Attribute ids (`c2/comm/M.java:137`, enum `C2Attribute`)

Some constants in the decompile are aliased to unrelated library constants (e.g. `TypedValues.CycleType.TYPE_CURVE_FIT`). The numbering is sequential, so their real values are clear. Excerpt relevant to HA:

| id | name | encoding / notes |
|---|---|---|
| 0 | AttributeTableVersion | u8 |
| 1 | FirmwareVersion | element index = `M.FirmwareType` (OS, Bootloader …) |
| 2 | HardwareRevision | element index = `M.HardwareInfo` |
| 3 | SerialNumber | |
| 4 | Model | u16 → `M.Model` |
| 5 | Name | ASCII, NUL padded |
| 8 | PrimitiveType | u8 → VisualV1(1), PumpV1(2), DoseV1(3), VorTechV1(4), VectraV1(5)… |
| 14 | SupportedAttributesCRC | u16 |
| 101 | PhysicalValues | array of i32, index = `M.PhysicalValues` (below) |
| 102 | PowerState | TODO |
| 104 | OperationState | u8: OOB 0, LiveDemo 1, **Scene 2**, **Schedule 3** |
| 105 | OperationMode | u8: pump modes, e.g. ConstantSpeed 32, Lagoon 33, Reefcrest 34, … BatteryBackup 39, **FeedMode 40** … |
| 107 | ErrorState | u16 → `M.ErrorState` (0 NoError, 2 Temperature, 3 Stall, …) |
| 108 | GroupBitmap | u64; bit0 always set; lowest set bit in positions 2..31 = tank group; 32..63 = light sub-group |
| 400 | ConfiguredScenes | array of scene records (below) |
| 401 | CurrentScene | u16 scene id (set: 4 bytes `id u16, 0 u16`) |
| 402 | SceneTimeout | u16 seconds, total length of the active scene |
| 403 | SceneTimer | u16 seconds remaining |
| 700 | MotorSpeed | pumps |
| 702 | FeedModeMaxSpeed | pumps |
| 802 | FeedModeReturnDelay | pumps |
| 1001 | NetworkedThreadDevices | list of devices in the mesh (TODO: decode) |
| 1005 | MeshLocalAddresses | 16-byte IPv6 elements |
| 1500 | Intensity | lights |
| 1511 | PuckTemperature | lights |

`PhysicalValues` element indices (`M.PhysicalValues`): 1 DriverTemperature, 2 MotorTemperature, 3 ClusterTemperature, 4 MotorPower, 5 MotorRPM, 6 BatteryVoltage, 7 InputVoltage, 8 InternalTemperature, 9 FanSpeed, 11 GallonsPerHour, 12 SupplyCurrent, 14 ModuleTemperature, 15 MotorSpinning, 16 Cluster2Temperature. Each value is an i32 LE (`c2/comm/model/MotorPower.java`). **Units are unconfirmed** and need checking against a capture.

## Scenes (`c2/comm/model/Scene.java`, `Scenes.java`, `M.SceneID`)

Built-in scene ids: EmptyScene 0, **FeedMode 1**, BatteryBackup 2, **AllOff 3**, ColorCycle 4, Disco 5, Thunderstorm 6, CloudCover 7, **AllOn 8**, All50 9. Custom scenes use other ids.

Scene record (one `ConfiguredScenes` element): `id u16 | timeout u16 (seconds) | name[16] NUL-padded | primitive-specific data`.

Reading the active scene (`Scenes.parseRequest` / constructor lambda): get `CurrentScene`, `SceneTimer`, `SceneTimeout` (plus `LiveDemoScene`/`LiveDemoTimer`/`LiveDemoTimeout` for the demo).
- active ⇔ `SceneTimeout > 0` and `CurrentScene != 0`
- remaining = `SceneTimer` s; elapsed = `SceneTimeout − SceneTimer`

## Control

All three v1 controls are tank-wide **group broadcasts**: one Set frame sent to the BLE-connected device with the tank group in the reserved bits. The firmware forwards it to every device in that group (`c2development/mobius/dashboard/views/SceneCollectionView.java:~560-595`, `c2/comm/Comm.java:304 broadcast`). No CoAP relay is needed.

`setGroup(g)`: `rsv0 = (rsv0 & 0x03) | (g << 2)` (g < 64). The group comes from `GroupBitmap` (see above).

### Start a scene (Feed Mode / All Off / All On)
`SetC2Attr` `CurrentScene` with one 4-byte element `struct.pack("<HH", scene_id, 0)`, group = tank group.

Example with op 29, scene FeedMode (1), group 2, msgId 7:
```
payload = 91 01 | 00 00 | 01 00 | 04 00 | 01 00 00 00
          attr401 idx0   count1  len4    id=1, 0
frame   = 02 DE 1D 07 00 08 00 0C 00 <payload> <crc16 LE>
                          ^^ rsv0 = 2<<2 = 0x08
```

The duration is the scene's configured `timeout` (from its `ConfiguredScenes` record). The trailing u16 is always 0 in the app and is probably an override duration (unverified).

### Stop the active scene / return to schedule (`exitSceneBroadcast`, `SceneCollectionView.java:986`)
`SetC2Attr` `OperationState` = `[0x03]` (Schedule), same group broadcast.

### Response
Every device answers with a `DF 1D`/`DF 18` confirm whose data begins with a status byte (`responses/FsciConfirmResponse.java`): 0 Success, 1 Failed, 4 NotPermitted, 7 UnsupportedAttribute, 9 InvalidValue, 17 Busy, …

## Reading other devices in the tank

Send the Get frame inside a CoAP relay (`DE 19`) to the target's mesh-local IPv6; the answer comes back as `DF 1A` (doc 01). Every device advertises over BLE as well, so a device's identity (model, serial, panId) is known from its advertisement. Its IPv6 is available from the device itself (`MeshLocalAddresses`) once connected; mapping the others still needs `NetworkedThreadDevices` decoded (TODO). The fallback is to connect to each device directly over BLE in turn.

## Open questions → resolve with an HCI snoop capture
1. The `DF 1A` source IPv6 byte order.
2. `PhysicalValues` units (W? mW? RPM raw?).
3. Whether the second u16 in the `CurrentScene` set is a duration override.
4. `NetworkedThreadDevices` element layout.

Resolved: the passcode (`PINCode` 109, u16) is an **app-side** gate only. The app reads it from the device and compares it locally (`c2/comm/model/PIN.java`, `c2development/mobius/settings/passcode/*`). Nothing is sent to unlock Set requests. The `superuser` reserved bit is only used for the serial-fixer and update-channel tools.

## Verified on real hardware (2026-10-17, through an ESPHome proxy)

Tank panId `0x405f`, group 2: 2 × VorTech MP40w G3 QD (model 42), 3 × model 195 (Radion, primitive VisualV1; the app knows it only by a codename).

- **Adverts:** the passive advert has only flags, the 128-bit service UUID `01ff0100-…` and the name `MOBIUS`. **Manufacturer data (model/serial/panId) is only in the scan response**, so it needs active scanning. HA runs ESPHome proxies in AUTO (passive) mode, so the integration registers ACTIVE callbacks per address and caches the last parsed advert.
- **GetOpCodes (`DE 29`) answers status `0xFF`** on both models, so devices use the 1-byte get/set variants (`0x17`/`0x18`). Those work.
- **Batched gets work** (several attributes in one `0x17`).
- **PhysicalValues returns several sparse blocks** in one response, e.g. MP40 indices 1–2, 4–7, 10–11. Units: °C, W, RPM, V, GPH.
  - MP40: driver 41 °C, motor 45 °C, 5–7 W, ~1500 RPM, input 31 V, ~2300–2650 GPH.
  - Radion: driver 55–59 °C, clusters 50–55 °C, module 75–79 °C, input 34–35 V.
- **OperationMode:** MP40s report 34 (Reefcrest) and 37 (SyncSlave, the paired pump). Radions report **50**, which is not in `M.OperationMode` (open question).
- **Scenes:** `SceneTimeout` reads 3600 even with no scene active (`CurrentScene` = 0). Activity = `CurrentScene != 0 && SceneTimeout > 0`.
- The light's `Name` attribute was blank; the pumps' was "MP40".
- **GroupBitmap:** light `0x2000000000000003` (group 2 + sub-group 62), pump `0x03` (group 2).
- The Vectra wasn't advertising (likely powered off).
