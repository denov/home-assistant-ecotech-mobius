# 02 — Attributes, state and control

What the integration reads from and sends to a device. Transport and framing are in [01-ble-transport.md](01-ble-transport.md). Everything below was checked on the test tank (end of this file) unless it says otherwise.

## Test tank

Observed 2026-10-17 to 2026-10-18 through an ESPHome proxy. Tank panId `0x405f`, tank group 2:

- 2 × VorTech MP40w G3 QD (model 42), one of them the sync partner of the other
- 3 × Radion (model 195), PrimitiveType 1
- 1 × Vectra L2 (model 149), PrimitiveType 5. No proxy could hear it over BLE; it was reached through the mesh only.

## Attributes

An attribute has a u16 id and holds one or more elements. Each element is a fixed-size little-endian value.

| id | name | encoding / notes |
|---|---|---|
| 4 | Model | u16, the same model id as in the advert |
| 5 | Name | ASCII, NUL padded. The MP40s report "MP40"; the Radions report it blank |
| 8 | PrimitiveType | u8, the device kind: 1 light, 2 pump, 3 doser, 4 VorTech wave pump, 5 Vectra return pump. 1, 4 and 5 were seen; 2 and 3 weren't |
| 101 | PhysicalValues | i32 per element, element index = the measurement (below) |
| 104 | OperationState | u8: 2 scene running, 3 following the schedule |
| 105 | OperationMode | u8, the running program (below) |
| 107 | ErrorState | u16, 0 = no error. The names the integration shows for other values are `ERROR_STATES` in `sensor.py`; none were seen |
| 108 | GroupBitmap | u64, the device's groups (below) |
| 401 | CurrentScene | u16 scene id; 0 = none |
| 402 | SceneTimeout | u16 seconds, the length of the scene |
| 403 | SceneTimer | u16 seconds left |
| 901 | SupportedColorChannels | lights: one u8 channel id per element |
| 1001 | NetworkedThreadDevices | 28-byte elements, the mesh device list (below) |
| 1005 | MeshLocalAddresses | 16-byte IPv6 elements; element 0 is the device's own mesh-local address |
| 1500 | Intensity | lights: u16 per element, element index = channel id (below) |
| 1504 | MaxPower | lights: i32 per channel id (e.g. 45600 for cool white, 500 for moonlight). Not used |

`protocol.py` (`Attr`) names a few more ids that the integration doesn't read.

The integration reads ids 5, 8, 107, 108, 104, 105, 401, 403 and 402 together in **one** Get. PhysicalValues and, for lights, Intensity are read in separate Gets. A pump answers a Get of 1500 with status 7 (unsupported attribute), which fails the whole request, so it isn't put in the shared batch.

## PhysicalValues (101)

The answer comes back as **several blocks** of the same attribute, each with its own start index, e.g. MP40 indices 1–2, 4–7 and 10–11. Unreported indices are absent, not zero.

| index | value | unit | seen on |
|---|---|---|---|
| 1 | driver temperature | °C | MP40 (41), Radion (55–59) |
| 2 | motor temperature | °C | MP40 (45) |
| 3 | cluster temperature | °C | Radion (50–55) |
| 4 | motor power | W | MP40 (5–7), Vectra (127) |
| 5 | motor speed | RPM | MP40 (~1500), Vectra |
| 6 | battery voltage | V | Vectra (0: no battery backup attached) |
| 7 | input voltage | V | MP40 (31), Radion (34–35), Vectra |
| 8 | internal temperature | °C | |
| 11 | flow | GPH | MP40 (2300–2650); Vectra reported 0 |
| 14 | module temperature | °C | Radion (75–79) |
| 16 | second cluster temperature | °C | |

The Vectra also reported temperatures. Indices 8 and 16 weren't seen; their meaning and unit are assumed.

The sensors the integration creates are `PHYSICAL_SENSORS` in `const.py`.

## OperationMode (105)

Values seen:

| value | mode |
|---|---|
| 2 | Schedule (lights) |
| 32 | Constant speed (Vectra) |
| 34 | Reefcrest (MP40) |
| 37 | Sync slave (the MP40 paired to the other one) |
| 40 | Feed mode (both MP40s, during Feed mode) |
| 50 | see below |

A Radion reports **50** while it's the device the client is connected to over BLE. Read through the mesh at the same moment, it reports its real mode (2). This was seen whenever a light was read directly, and the next mesh read showed Schedule again. So the integration prefers a pump as the bridge (doc 03).

The integration names more modes (`OPERATION_MODES` in `const.py`). Unknown values show as `unknown_<n>`.

## Groups (108)

`GroupBitmap` is a u64. Bit 0 is always set. The **tank group** is `g` for the lowest set bit at position `g − 1` with `g` from 2 to 31. Bits 32–63 hold light sub-groups.

Seen: pumps `0x03` (group 2), lights `0x2000000000000003` (group 2, plus a light sub-group).

## Scenes

A scene is a temporary program that runs on the whole tank, then hands back to the schedule.

- A scene is **active** when `CurrentScene != 0` and `SceneTimeout > 0`. `SceneTimeout` read 3600 with no scene active, so `CurrentScene` alone decides.
- Time left is `SceneTimer` in seconds.

Scene ids the integration uses:

| id | scene |
|---|---|
| 1 | Feed mode |
| 3 | All off |
| 8 | All on |

Other ids are shown as `custom_<n>`.

## Control

Each command is a single **group broadcast**: one Set frame sent to the BLE-connected device, with the tank group in the top 6 bits of `rsv0` (`rsv0 = (rsv0 & 0x03) | group << 2`). The firmware passes it to every device in that group over the mesh. No CoAP relay is needed.

### Start a scene

Set `CurrentScene` (401) to one 4-byte element: `scene id u16 | 0 u16`.

Feed mode on the test tank, op 24, group 2:
```
payload = 91 01 | 00 | 01 | 04 | 01 00 00 00
          attr401 idx0 count1 len4  id=1, 0
rsv0    = 2 << 2 = 0x08
```

- The device answered status 0.
- On the next poll both MP40s were in OperationMode 40 (Feed mode) at ~800 GPH and 1 W, and the scene was active with ~10 min left. The device picks the length itself.
- The second MP40 was reached by the broadcast through the mesh. So one Set is enough for the whole tank.

The trailing u16 was always sent as 0. Whether another value changes the scene length hasn't been tested.

### Resume the schedule

Set `OperationState` (104) to `03`, as the same group broadcast:
```
payload = 68 00 | 00 | 01 | 01 | 03
```

The device answered status 0. On the next poll no scene was active and the MP40s were back on Reefcrest and Sync slave.

## Mesh device list (1001)

Read with start index 0 and count "all". The test tank's device list has 30 elements of 28 bytes; unused ones are all zero.

| bytes | field |
|---|---|
| 0–7 | interface id: the low 8 bytes of the device's mesh-local IPv6 |
| 8–9 | u16, varies per device (e.g. `0x1fb0`). Not used |
| 10–11 | always 0 so far |
| 12–13 | model (u16 LE) |
| 14–27 | serial, 14 ASCII characters |

- A device's mesh-local IPv6 is the first 8 bytes (the mesh prefix) of the connected device's own `MeshLocalAddresses[0]`, followed by the entry's interface id.
- **The list includes the connected device itself.** A relayed request to yourself gets no answer, so read the connected device directly.
- On the test tank, the list from one MP40 held all 6 devices, including the Vectra that no proxy could hear.
- Through one connection, the state batch plus PhysicalValues took 0.3–0.5 s per device. A direct BLE connection takes ~2 s per device.

## Light channels (901, 1500)

`SupportedColorChannels` lists the channel ids a light has. `Intensity` holds one u16 per channel, at **element index = channel id**, in tenths of a percent (1000 = 100 %).

Channel ids seen on the test tank's Radions:

| id | channel | | id | channel |
|---|---|---|---|---|
| 1 | Brightness (master dimmer, not a colour) | | 21 | UV |
| 16 | Cool white | | 22 | Warm white |
| 17 | Blue | | 23 | Violet |
| 18 | Royal blue | | 31 | Moonlight white |
| 19 | Green | | 32 | Moonlight blue |
| 20 | Red | | 100, 101 | not named by the integration |

The integration also names ids 24–30 and 33–38 for other Radion models (`CHANNELS` in `const.py`); those weren't seen.

At 11:30 local time: blues, UV and violet at 770 (77.0 %), whites, green and red at 128–129 (12.9 %). Moonlight read 0 in daytime.

## Open questions

1. The byte order of the source IPv6 in a `DF 1A` relay response (not needed so far).
2. Whether the trailing u16 of a `CurrentScene` set overrides the scene length.
3. What a Vectra reports as flow: index 11 read 0 on a running Vectra L2.
4. The 2 bytes after the status in a Set answer (`00 ffff`).
