# Home Assistant EcoTech Mobius

Brought to you by [Fullstack Systems](https://fullstacksystems.com).

This custom integration talks directly to [EcoTech Marine](https://ecotechmarine.com/) Mobius devices (Radion lights, VorTech MP and Vectra pumps) over Bluetooth. It brings their status into Home Assistant as sensors and adds tank-wide Feed Mode, All Off and All On controls. Everything stays local: no EcoTech cloud account or phone is needed once the tank is set up in the Mobius app.

![Mobius dashboard in Home Assistant: tank controls with the Feed mode switch, active scene, VorTech MP40 flow, power, speed and temperatures, a Vectra return pump, Radion temperatures, device health, pump energy and supply voltage](https://raw.githubusercontent.com/denov/home-assistant-ecotech-mobius/main/docs/images/dashboard.png)

## Features

- Finds your tank automatically over Bluetooth (one Home Assistant entry per Mobius tank)
- Per device: mode (e.g. Reefcrest, Lagoon, Sync), error state, and what the device reports
  - Pumps: flow (GPH), motor power, motor speed, motor and driver temperature, input voltage
  - Lights: the brightness of each colour channel in % (Royal blue, Blue, Violet, UV, Cool white, Warm white, Green, Red, Moonlight, ...), so you can see the colour mix the light runs right now; LED, driver and module temperatures; input voltage
- Energy dashboard: an **Energy** sensor (kWh) for each pump, counted from its motor power. Input voltage sensors keep long-term statistics. Radion lights don't report power, so they have no energy sensor. A Vectra return pump usually uses the most.
- Per tank: active scene and scene time remaining
- A **Feed mode** switch: turn it on to start Feed mode, off to end it early. It's on while Feed mode runs, whether it was started from Home Assistant or the Mobius app.
- Buttons: **All off**, **All on** and **Resume schedule** (ends any scene and goes back to the schedule)
- Every command goes to all devices of the tank at once, like the scene tiles in the Mobius app
- Reads the whole tank through **one** Bluetooth connection: the device nearest the proxy relays to the others over the tank's own mesh, so a pump under the stand or a light out of range is still read
- Devices that drop out briefly keep their last reading; they go unavailable only after several failed reads in a row
- A device that has left the tank can be deleted from its device page
- Designed for HACS install

![Light colour channels in Home Assistant: one Radion's colour mix over a day, and the brightness of every channel of three Radion lights, colour coded](https://raw.githubusercontent.com/denov/home-assistant-ecotech-mobius/main/docs/images/lights.png)

![History in Home Assistant: pump flow and power over a day, pump energy per day for a week, and supply voltage of every device](https://raw.githubusercontent.com/denov/home-assistant-ecotech-mobius/main/docs/images/history.png)

Tested with VorTech MP40w G3 QD pumps, a Vectra L2 return pump and current-generation Radion lights. Other Mobius devices should work, since they share the same protocol. Please open an issue with debug logs if one doesn't.

## Requirements

- A tank already set up in the EcoTech Mobius app. The integration uses the tank the app created; it doesn't set up new devices.
- Home Assistant 2026.10 or newer.
- Bluetooth within range of at least one device of the tank. See below.

## Bluetooth connection

Mobius devices only speak Bluetooth Low Energy (the Mobius cloud and Wi-Fi aren't used), so Home Assistant needs a Bluetooth radio within a few metres of the tank. **One device in range is enough**: the integration reads the rest through the tank's mesh, the way the Mobius app does.

### Option 1: an ESPHome Bluetooth proxy (recommended)

An ESP32 board running [ESPHome](https://esphome.io/components/bluetooth_proxy.html) near the tank relays Bluetooth to Home Assistant over Wi-Fi. This works whatever machine Home Assistant runs on (including Docker or a VM without Bluetooth) and lets you put the radio right next to the tank.

```yaml
esphome:
  name: mobius-proxy

esp32:
  variant: esp32          # match your board (esp32s3, esp32c3, ...)
  framework:
    type: esp-idf

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome
    encryption:           # uses the api key; plaintext uploads are refused

logger:

esp32_ble_tracker:
  scan_parameters:
    active: true

bluetooth_proxy:
  active: true            # required: the integration connects to the devices

button:
  - platform: restart     # lets you restart the proxy from Home Assistant
    name: Restart
```

Flash it, add it to Home Assistant through the **ESPHome** integration, and the tank is offered under **Settings** → **Devices & services** shortly after.

Tips:
- Put the proxy within a few metres of the tank, with a clear line of sight to the lights and pumps where you can. Glass, water and metal stands weaken Bluetooth a lot.
- Give it a DHCP reservation rather than a fixed IP in the YAML, so it can't clash with another device on your network.
- A proxy handles 3 Bluetooth connections at once. The integration only ever uses one at a time, so a single proxy is enough for a tank. With several proxies, Home Assistant picks the one with the best signal for each device.

### Option 2: the Home Assistant host's own Bluetooth

A built-in or USB Bluetooth adapter on the Home Assistant machine works too, if the machine is close enough to the tank. Set it up with Home Assistant's [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/).

### Scanning

Mobius devices only say which tank they belong to (and their model and serial) when **actively** scanned. Home Assistant normally keeps ESPHome proxies passive, so the integration asks for active scans of Mobius devices itself; you don't need to change any setting. After a new proxy or a restart, a tank can take a few minutes to be fully found. Once it has, the integration remembers its devices, so restarts are quick.

### The Mobius app and Home Assistant

A Mobius device accepts **one Bluetooth connection at a time**. While the Mobius app on your phone is connected to the tank, Home Assistant can't read the device the app is using, and its commands may fail. The device keeps showing its last reading for a few minutes, then goes unavailable until the app lets go.

So when you've finished in the Mobius app, close it fully (swipe it away). Home Assistant reconnects on its next poll, within a minute.

### Troubleshooting

| What you see | What to check |
|---|---|
| The tank isn't offered | Is the proxy online in the ESPHome integration with `active: true`? Mobius devices appear as **MOBIUS** under **Settings** → **Bluetooth** → **Advertisement monitor**. Wait a few minutes for an active scan. |
| One device is unavailable | Is the Mobius app open? Is that device powered? Devices are read through the mesh, so it needn't be near the proxy, but it must be on the same tank in the Mobius app. |
| Everything is unavailable | Is the proxy online? Move it closer, or add a second proxy on the other side of the tank. If the proxy is online and still sees the devices (Advertisement monitor) but nothing connects, press its **Restart** button. |
| Commands fail with "Couldn't …" | The same as above: no device of the tank could be reached in time. Try again once the app is closed. |

## Confirm before All off

**All off** turns off every pump and light in the tank. To have the dashboard ask first, set the card's tap action to *Perform action* → `button.press` with **Require confirmation** on. In YAML:

```yaml
type: tile
entity: button.mobius_tank_405f_all_off   # use your entity ID
tap_action: &all_off
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.mobius_tank_405f_all_off
  confirmation:
    text: Turn every pump and light off?
icon_tap_action: *all_off
```

This only covers that card: the **Press** button in the entity's dialog, automations and voice commands don't ask.

## Installation

### Installation via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=denov&repository=home-assistant-ecotech-mobius&category=Integration)

1. Use the button above to open the repository in HACS. Until the integration is in the HACS default list, HACS offers to add it as a custom repository. You can also do that by hand: **HACS** → **⋮** → **Custom repositories**, paste `https://github.com/denov/home-assistant-ecotech-mobius` and choose type **Integration**.
1. Download **EcoTech Mobius** in HACS.
1. Restart Home Assistant.
1. Home Assistant offers the tank under **Settings** → **Devices & services** once it's in Bluetooth range, or set it up from the UI:

<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=mobius" target="_blank" rel="noreferrer noopener"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add Integration"></a>

### Manual Installation

1. Download `mobius.zip` from the [latest release](https://github.com/denov/home-assistant-ecotech-mobius/releases/latest).
2. Unzip it into `custom_components/mobius` in your Home Assistant configuration directory.
3. Restart Home Assistant.
4. Set up the integration using the UI.

## Reporting issues

1. Go to **Settings** → **Devices & services** and open **EcoTech Mobius**.
1. Choose **Enable debug logging**, wait a couple of minutes (or reproduce the problem), then choose **Disable debug logging** to download the log.
1. Attach the log to your [GitHub issue](https://github.com/denov/home-assistant-ecotech-mobius/issues), with your device models.

The debug log has every Bluetooth frame sent to and from your devices, which is usually what's needed to support a new model or firmware.

## How it works

The protocol was worked out from the Mobius Android app; see [`docs/`](docs/). `tools/mobius_cli.py` talks to devices through an ESPHome proxy without Home Assistant, which helps when testing a new device.

## License

This project is licensed under the **GNU Affero General Public License v3.0 or later**
(**AGPL-3.0-or-later**). See `LICENSE`.

### Commercial use

Commercial use is allowed under AGPL if you comply with its requirements (including
providing corresponding source to users who interact with the software over a network).

If you want to use this project without AGPL obligations, a **commercial license**
is available—see `COMMERCIAL_LICENSE.md`.

EcoTech Marine isn't affiliated with this project; see `NOTICE`.
