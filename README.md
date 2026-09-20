# Andersen EV Chargepoint integration for Home Assistant
![Andersen Logo](/images/dark_logo.png)

## Status
![CI](https://github.com/HA-AndersenEV/hassio-andersen-ev/actions/workflows/ci.yml/badge.svg?branch=main)
![HA Quality Scale: Gold](https://img.shields.io/badge/HA_Quality_Scale-Gold-FFD700)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/HA-AndersenEV/hassio-andersen-ev)](https://github.com/HA-AndersenEV/hassio-andersen-ev/releases)
![Integration Usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.andersen_ev.total)

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Features
* Switch entities for each charging schedule allows enabling/disabling charge schedules.
* Lock entity to enable or disable the charge point.
* Provides services to:
  * Disable all charging schedules: `andersen_ev.disable_all_schedules`
  * Get detailed device information: `andersen_ev.get_device_info` (results displayed in UI)
  * Get detailed real-time device status: `andersen_ev.get_device_status` (results displayed in UI)
  * Reset RCM: `andersen_ev.reset_rcm`
* Live grid power sensors for those without smart meters.

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance.
2. Add this repository as a custom repository in HACS:
   - Navigate to HACS → Integrations → Menu (⋮) → Custom repositories
   - Add `https://github.com/HA-AndersenEV/hassio-andersen-ev` as a repository
   - Select `Integration` as the category
3. Click "Add"
4. Search for "Andersen EV" in HACS and install it
5. Restart Home Assistant
6. Add the integration via the Home Assistant UI (Settings → Devices & Services → Add Integration)
7. Search for "Andersen EV" and follow the configuration steps

### Testing beta releases

To receive beta pre-releases, open this repository in HACS and enable "Show beta versions" for it.
Beta builds are published on demand via the "Cut beta" workflow and are tagged `vX.Y.Z-beta.N`.
Stable users who leave "Show beta versions" disabled are unaffected.

### Manual Installation

1. Download the repository as a zip file and extract it.
2. Copy the `andersen_ev` folder to your Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Add the integration via the Home Assistant UI by providing your Andersen user account user name and password.

## Services
The integration provides the following services:

### disable_all_schedules
Disables all charging schedules for a specified device.

Example:
```yaml
service: andersen_ev.disable_all_schedules
data:
  device_id: "YOUR_DEVICE_ID"
```

### get_device_info
Retrieves detailed information about a device and displays the results directly in the Home Assistant UI. This service uses Home Assistant's new Action API that allows returning data to the user interface.

Example:
```yaml
service: andersen_ev.get_device_info
data:
  device_id: "YOUR_DEVICE_ID"
```

### get_device_status
Retrieves detailed real-time status of a device and displays the results directly in the Home Assistant UI. This provides more comprehensive status information than what is available through the sensors.

Example:
```yaml
service: andersen_ev.get_device_status
data:
  device_id: "YOUR_DEVICE_ID"
```

## Supported Devices

This integration supports **Andersen A2** chargepoints connected to an Andersen Konnect+ account.
Andersen currently makes one chargepoint product line, so there is nothing to select during setup:
the integration reads the model name reported by the live API automatically (falling back to a
generic "A2 (HW: ...)" hardware-revision label if the API doesn't report one), and shows it as the
device model in **Settings > Devices & Services**.

A single Andersen account can only be added once (the integration enforces one config entry per
install), but if that account has more than one charger, every charger it returns is set up as its
own device automatically - no per-charger configuration is needed.

## Supported Functionality

See [Features](#features) above for the full entity list and [Services](#services) for the
service calls. In short:
* A **lock** entity per charger to enable/disable charging (`userLock`/`userUnlock`).
* A **switch** entity per charging schedule slot, so each schedule can be turned on or off
  independently.
* **Sensors** for energy, cost, live grid/solar/charge power, voltage, temperature, fault code and
  connector state, sourced from the charger's historical and live status data.
* **Services** to disable all schedules, reset an RCM fault, and pull a one-off detailed device
  info/status snapshot into the UI.

## How data is updated

This is a cloud-only integration (`iot_class: cloud_polling`) - there is no local/LAN control of
the chargepoint. All communication goes through Andersen's Konnect+ cloud (AWS Cognito
authentication + a GraphQL API), so an internet connection and Andersen's cloud being up are both
required for entities to update or for services/lock actions to work.

A `DataUpdateCoordinator` polls the API every 60 seconds for all devices on the account. After a
lock/unlock, schedule change, or RCM reset, the integration waits briefly before requesting a
refresh, since the API needs a moment to apply the change before it's reflected in the polled
status.

If a poll fails, the integration keeps showing the last-known good data instead of immediately
marking entities unavailable, so a brief cloud hiccup doesn't blank your dashboard. Entities are
only marked unavailable once polling has failed for a device with no prior cached data.

## Known limitations

* **One Andersen account per Home Assistant instance.** The integration only allows a single
  config entry, so multiple separate Andersen accounts (e.g. two households sharing one HA
  instance) aren't supported side by side.
* **Fully cloud-dependent.** There's no local fallback - if Andersen's API or cloud is down, data
  stops updating and lock/schedule/service actions will fail until it recovers.
* **Cost sensors use the charger's own currency.** The cost sensors (`cost`, `grid_cost`,
  `solar_cost`, `surplus_cost`) report in whatever currency Andersen has configured for your
  charger. That can't be overridden from Home Assistant, so it won't follow your Home Assistant
  currency setting if the two differ.
* **Grid power sensors are a workaround, not a replacement for a smart meter integration.** They're
  intended for those without a smart meter connected to Home Assistant already (see
  [Features](#features)) - accuracy depends entirely on what the charger itself reports.
* **Fault codes are raw values.** The fault code sensor exposes whatever numeric/string code the
  charger reports, with no built-in lookup table translating codes to human-readable descriptions.

## Use cases

Beyond just seeing charger state in Home Assistant, the entities and services above are meant to
be driven by automations:
* **Tariff- or solar-aware scheduling** - toggle individual schedule switches on or off in
  response to an off-peak tariff window starting/ending, or based on forecast/live solar
  production, instead of managing schedules only from the Andersen app.
* **Presence- or security-based charging control** - use the lock entity in automations so
  charging only becomes available when, for example, someone is home or a specific door/gate is
  unlocked.
* **Solar-aware charging without a smart meter** - the live grid power sensors let you build
  solar-surplus charging automations even if you don't otherwise have a smart meter integration
  reporting home grid import/export.
* **On-demand status for dashboards and scripts** - `get_device_status`/`get_device_info` return
  data straight to the UI (or an automation/script), useful for a "check charger now" button or
  script rather than waiting on the next 60-second poll.

## Automation examples

Turn off a specific charging schedule when a peak-tariff sensor becomes active:

```yaml
automation:
  - alias: "Disable EV schedule during peak tariff"
    trigger:
      - platform: state
        entity_id: sensor.electricity_tariff_rate
        to: "peak"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.YOUR_SCHEDULE_1_SWITCH # e.g. switch.driveway_charger_schedule_1
```

Reset the RCM fault automatically when the fault code sensor reports a fault:

```yaml
automation:
  - alias: "Reset Andersen RCM on fault"
    trigger:
      - platform: state
        entity_id: sensor.YOUR_FAULT_CODE_SENSOR # e.g. sensor.driveway_charger_fault_code
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.state not in ['0', 'unknown', 'unavailable'] }}"
    action:
      - service: andersen_ev.reset_rcm
        data:
          device_id: "YOUR_DEVICE_ID"
```

Replace the placeholder entity IDs with your own - find them under **Settings > Devices &
Services > Andersen EV > (your charger)**.

## Troubleshooting

* **Credentials stopped working** - if Andersen invalidates your stored password, the integration
  raises a re-authentication request in **Settings > Devices & Services**; click it and re-enter
  your password to resume. You don't need to remove and re-add the integration.
* **Need to change just your password** - use **Reconfigure** on the integration entry (Settings
  > Devices & Services > Andersen EV > Configure) to update the stored password without removing
  and re-adding the integration.
* **"Andersen API response format has changed" repair notification** - if Andersen's cloud starts
  returning device data in a shape the integration doesn't recognise, a repair issue with this
  title is raised (falling back to cached data in the meantime). This usually means Andersen
  changed their API; check for an integration update, or open an issue if none is available.
* **Entities showing as unavailable** - this normally means recent polls have failed with no
  cached data to fall back on, most often because the Andersen cloud or your internet connection
  is down. Check Andersen's status/app first, then check the Home Assistant logs (see below).
* **Download diagnostics for a bug report** - from the integration entry's three-dot menu, choose
  **Download diagnostics**. Credentials and auth tokens are automatically redacted; the file
  includes the config entry, auth-state, and per-device status, which is far more useful in a bug
  report than a description alone.
* **Check the logs** - go to **Settings > System > Logs** and filter for `andersen_ev` to see
  polling, authentication, and service-call errors and warnings.

## Removal

1. In Home Assistant, go to **Settings > Devices & Services**.
2. Click the Andersen EV integration entry, then click **Delete**.
3. Restart Home Assistant.
4. (Optional) Uninstall via HACS: navigate to HACS > Integrations, find Andersen EV, and remove it.

## Contributing
Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the branch model, commit
conventions, and release process before opening a pull request.

## Development

### pre-commit

This repo ships a `.pre-commit-config.yaml` that runs the same ruff lint and
format checks as CI (pinned to the same ruff version), so issues are caught
locally before you push. To enable it:

```bash
pip install pre-commit   # or: pip install -r requirements-dev.txt
pre-commit install
```

The hooks then run automatically on `git commit`. To run them across the
integration source on demand:

```bash
pre-commit run --all-files
```

## Acknowledgements

 * [@iandbird](https://github.com/IanDBird/konnect) - uses the Konnect module as a baseline with my own modifications.
 * [@strobejb](https://github.com/strobejb/andersen-ev) - (indirectly) uses his GraphQL schema in development of API communication.
