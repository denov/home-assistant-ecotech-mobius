# 03 — Home Assistant integration behaviour

What `custom_components/mobius` does, built on the protocol in [01](01-ble-transport.md) and [02](02-attributes-and-control.md). Tests in `tests/` are derived from this spec; when behaviour changes, this file changes first.

## Discovery and setup

- The manifest matches adverts by the Mobius service UUID (`01ff0100-…`) on connectable devices. The local name `MOBIUS` is also accepted.
- Model, serial and panId are only in **scan responses**, so they need active scans. HA runs ESPHome proxies passively, so the integration asks for active scans itself: it registers an ACTIVE callback per Mobius address.
  - It asks only for addresses that belong to this tank, or that it can't place yet. Once a scan response shows an address belongs to another tank, its active scan is dropped. A neighbour's tank doesn't keep our proxy busy.
- One config entry per tank. Its unique id is the panId as 4 hex digits.
  - A discovery flow waits up to 60 s for a scan response, then aborts with `no_scan_response`.
  - The user flow lists the tanks found, and runs an active scan if none are found yet.
- The entry stores `devices`, a map of address → `[model id, serial]`. It is updated whenever a scan response adds or changes an address. After a restart, passive adverts are then enough to know which device is at which address.

## Devices and entities

- A **tank** device is identified by `tank_<panId>`. Every pump and light hangs off it through `via_device`. The tank device holds:
  - the `scene` sensor (`feed_mode`, `all_off`, `all_on`, … or `none`),
  - `scene_remaining` (duration; 0 when no scene is active),
  - the `feed_mode` binary sensor,
  - the buttons `feed_mode`, `all_off`, `all_on` and `resume`.
- Each pump or light is a device, identified by its serial and named by its own name, or else its model, plus the last 4 characters of the serial. Its entities are:
  - `mode` (OperationMode name; unknown values show as `unknown_<n>`, with `raw` as an attribute). Its icon shows the device type, using Material Design icons that ship with HA (no EcoTech artwork):
    - lights (PrimitiveType 1): `mdi:light-flood-down`,
    - VorTech wave pumps (4): `mdi:waves`,
    - Vectra and other return pumps (2, 5): `mdi:pump`,
    - dosers (3): `mdi:beaker-outline`,
    - anything else: `mdi:help-circle-outline`.
  - `operation_state` and `error` (diagnostic),
  - a `problem` binary sensor,
  - one sensor per supported PhysicalValues element the device reports (see `PHYSICAL_SENSORS`).
  - **Lights:** one `channel_<name>` sensor per colour channel the light reports in `Intensity` (e.g. Royal Blue, UV, Cool White), in % with one decimal. Brightness and unknown ids get no sensor. These sensors show the colour mix the light is running right now.
  - **Battery voltage** is created only once it reads above 0 V. Pumps without a battery backup attached report 0 V.
  - Devices that report motor power also get an `energy` sensor (below).
- Entities are added when a poll first reports their device or value. They are never removed while the entry is loaded.
- **Removing a device:** a pump or light that the last poll didn't report (e.g. it left the tank) can be deleted from its device page. One that's still reported can't be deleted, because it would come straight back. The tank device can't be deleted either.

## Polling

- The tank is polled every 60 s. Reading a device means one batched state read and one PhysicalValues read; lights (PrimitiveType 1) also get one `Intensity` read. If the device rejects the batch, the state attributes are read one by one.
- **Mesh first, then direct.** Relayed reads take ~0.5 s per device and a direct connection ~2 s (measured, doc 02), so the mesh is the default.
  1. **Bridge.** Connect to the strongest advertising device of the tank; if that fails, try the next strongest. Read the bridge itself directly.
     - **Pumps bridge before lights.** A light read over a direct connection reports OperationMode 50 instead of its real mode (doc 02), so lights count as 10 dB weaker when ranking. A pump within 10 dB of the strongest light is picked first. A light still bridges when no pump is close.
     - A light that bridges anyway keeps the mode of its last reading (from the mesh) instead of 50. With no earlier reading, its mode is unknown.
  2. **Mesh.** In the same connection, read the mesh list (`NetworkedThreadDevices`). Read every other listed device through the relay. This reaches devices no proxy can hear, such as a return pump under the stand.
  3. **Direct fallback.** A device whose relayed read failed, but which advertised in the last 300 s, gets its own direct connection after the bridge is released.
- Relayed requests wait 5 s, with one retry. A device that's off the mesh can't stall a poll for long.
- Devices learned from the mesh list get entities like any other device. Their model and serial come from the list.
- A device seen at several addresses is reached at the one with the strongest RSSI.
- The first poll after a restart waits up to 40 s for adverts before failing. HA then retries setup.
- **Keep the last reading.** A device that fails to read, or isn't seen, keeps its last reading. After **3** failed polls in a row its entities go unavailable. The next successful read brings them back.
- **Fail fast.** When **2 connection attempts in a row** fail, anywhere in the integration (poll or command), the integration treats the radio path (proxy or adapter) as down. The current poll then stops making connections: the devices it hasn't read count as failed reads, as above. Every poll still makes at least one attempt, so recovery is noticed on the next poll.
- **One connection at a time.** The integration holds one BLE connection at a time, under a lock.
  - A poll takes the lock per connection (the bridge, then each direct fallback), so a command waits for at most one of them.
  - Each attempt is a single connect; there's no retry inside a poll.
  - A failed disconnect doesn't lose the reading.
  - The GetOpCodes result is cached per device after the first connection, so later connections skip that round trip.

## Commands (buttons)

- A button sends one tank-wide broadcast through one connected device. The firmware relays it over the Thread mesh to the rest of the tank.
  - Feed mode, All off and All on set `CurrentScene` with the tank group in `rsv0`.
  - Resume schedule sets `OperationState = 3` (Schedule).
  - The tank group comes from the last poll, or is read from the device if the integration has none yet.
- The command tries the device with the strongest signal first, then the next strongest. It stops after 2 connection failures in a row, counting failures from polls just before it. So with a radio path that is down, a press makes one attempt.
- **A command goes ahead of polling.** It waits for at most the one read that is already under way. It then holds the connection lock for all of its attempts, so poll reads can't run in between.
- Failure raises `HomeAssistantError("Couldn't <what>: <reason>")`, which the UI shows.
- Success triggers a refresh, so the scene sensors update.
- Buttons are always available, because they don't depend on the last poll.

## Energy

- `energy` (kWh, `total_increasing`) integrates motor power over time, between fresh readings only. A kept reading isn't counted twice.
  - Gaps longer than 1 hour (e.g. HA was stopped) add nothing.
  - The total is restored after a restart.
- Voltage and power sensors use the `measurement` state class, so they get long-term statistics.

## Known gaps

- Model 195's product name isn't known, so it shows as "Radion (model 195)".
- A Radion read over a direct connection reports OperationMode 50 instead of its real mode (docs/02). Pumps are preferred as the bridge, and a bridging light keeps its last mode (see Polling). Only on a tank with no pump in range, right after setup, is its mode unknown.
- Vectra pumps are read-tested through the mesh only (a Vectra L2). Whether a Vectra reports flow is unknown: GallonsPerHour read 0.
