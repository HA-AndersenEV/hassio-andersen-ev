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
* **Charge now** and **Stop charging** buttons, matching the Konnect+ app (see
  [Charge now and Stop charging](#charge-now-and-stop-charging)).
* Solar charging controls matching the Konnect+ app's Power source screen (see
  [Solar charging controls](#solar-charging-controls)):
  * **Supplement with grid power** switch.
  * **Grid source power** number (kW).
  * **Charge outside schedule** switch.
  * **Boost** switch.
* **User max power** (kW) number to limit the charge rate, plus **user max current** (A), disabled by default.
* **Max system charge current** diagnostic sensor (the charger's configured maximum current, in A).
* Provides services to:
  * Disable all charging schedules: `andersen_ev.disable_all_schedules`
  * Get detailed device information: `andersen_ev.get_device_info` (results displayed in UI)
  * Get detailed real-time device status: `andersen_ev.get_device_status` (results displayed in UI)
  * Reset RCM: `andersen_ev.reset_rcm`
* Live grid power sensors for those without smart meters, and a solar power sensor for chargers with a solar CT clamp.
* Live updates: charger status is pushed to Home Assistant within seconds rather than polled.

See [ENTITIES.md](ENTITIES.md) for every entity, where its value comes from, and what it sends when changed.

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
There is nothing to select during setup: the integration identifies the charger from the live API
and fills in the model, hardware revision, firmware version and serial number on the device page in
**Settings > Devices & Services**.

The API reports an internal product name of its own (for the A2 it is "Thurlestone"), which is not
what the charger is sold as and is not shown in the Konnect+ app, so the integration translates the
known product ids to the name on the box. A charger whose product id we haven't seen before falls
back to showing the internal name, so it is still identifiable. Either way the raw product id is
kept as the model id on the device page.

A single Andersen account can only be added once (the integration enforces one config entry per
install), but if that account has more than one charger, every charger it returns is set up as its
own device automatically - no per-charger configuration is needed.

## Supported Functionality

See [Features](#features) above, [ENTITIES.md](ENTITIES.md) for the full entity list, and
[Services](#services) for the service calls. In short:
* A **lock** entity per charger to enable/disable charging (`userLock`/`userUnlock`).
* **Charge now** and **Stop charging** buttons per charger (`chargeNow` + `userForceStart`,
  and `userForceStop` + `stopChargeNow`).
* A **switch** entity per charging schedule slot, so each schedule can be turned on or off
  independently.
* **Solar charging** switches and a grid source power number, mirroring the app's Power source
  screen.
* **User max power** and **user max current** numbers to limit the charge rate, mirroring the app's
  power settings (`userAmpSet`).
* **Sensors** for energy, cost, live grid/solar/charge power, voltage, temperature, fault code and
  connector state, sourced from the charger's live status and its charge history.
* **Services** to disable all schedules, reset an RCM fault, and pull a one-off detailed device
  info/status snapshot into the UI.

## How data is updated

Charger status is pushed to Home Assistant within seconds, and a change you make shows once the
charger confirms it. The last-session energy and cost sensors are polled every minute. See
[DATA_UPDATES.md](DATA_UPDATES.md) for the details, including what happens if the connection
drops.

## Using the controls

### Lock states and pending commands

The charger takes a while to act on a lock or unlock command, and Andersen's API gives no reliable
confirmation that it did, so the lock entity only reports a change once the charger's own status
shows it. In the meantime:

* After you lock or unlock, the entity shows **`locking`** or **`unlocking`** until the charger
  confirms. That usually takes a few seconds, but has taken up to about 2.5 minutes.
* If nothing confirms the command within 5 minutes, the entity goes back to the state the charger
  reports and a warning is logged. A command sent while the charger is offline is queued by the
  cloud and applied when the charger reconnects, which can be much later.
* Pressing the same action again while it is pending does nothing.
* If the charger has not reported its lock state yet (for example just after Home Assistant starts),
  the entity shows **`unknown`**.

Because the lock passes through `locking` and `unlocking`, an automation trigger that uses `from:`
with a lock state, such as `from: locked` and `to: unlocked`, will not match. Trigger on `to:` alone,
or include the transitional state:

```yaml
triggers:
  - trigger: state
    entity_id: lock.my_charger_lock
    from: [locked, unlocking]
    to: unlocked
```

Also note that a condition such as "only if unlocked" is false while a command is pending.

### Charge now and Stop charging

These buttons send the same commands as the **Charge now** and **Stop charging** buttons on the
Konnect+ app's home screen:

* **Charge now** starts charging straight away, ignoring your schedules.
* **Stop charging** stops the current charge.

The connector and charge sensors change once the charger reports the new state, usually within a
few seconds.

The app only shows these buttons in some states, for example Charge now only when a car is
plugged in and the charger is not already charging, and neither when the charger is locked. The
integration always makes both buttons available, so pressing one when it doesn't apply may have no
effect. Andersen also hides them on firmware older than version 500, so they may not work on older
chargers.

**Stop charging and Charge outside schedule.** If **Charge outside schedule** is on and there is
enough solar to charge, pressing Stop charging has no effect and the charger keeps charging. Turn
Charge outside schedule off first, then press Stop charging.

### Max charge rate

These entities mirror the max power setting in the Konnect+ app, so you can lower the charge rate
from an automation, for example while other large loads are running:

| Entity | Type | What it does |
|---|---|---|
| User max power | Number (kW) | The highest power the charger will charge at, as shown in the app. |
| User max current | Number (A) | The same setting in amps, converted at 0.225 kW per amp. Disabled by default. |

Both entities control the same charger setting (`sysUserMaxChargeAmpSet`), so changing one updates
the other. User max current is disabled by default because it duplicates user max power. To use
it, enable it from the charger's device page. [Grid source power](#solar-charging-controls) is also worked out from this setting, so
its kW value changes when you change the max charge rate.

**Only four values are allowed.** Like the app, the charger only accepts **10, 16, 24 or 32 A**
(2.3, 3.6, 5.4 or 7.2 kW). It silently ignores anything else, so the integration rounds what you set
to the nearest allowed value. When a value is exactly halfway between two, it rounds down. For
example 20 A becomes 16 A and 5.0 kW becomes 24 A (5.4 kW).

**The installer limit still applies.** The value never goes above the charger's installed maximum
(max system charge current, `cfgChargeAmpMax`). On a charger installed at 20 A, the highest
setting is 16 A. Setting a value fails with an error if the installed maximum isn't known yet or is
below 10 A.

If no user max has been set, both entities show the installed maximum, as the app does. Both
entities show a new value once the charger reports it, usually within a couple of seconds.

### Solar charging controls

These entities mirror the **Power source** screen in the Konnect+ app:

| Entity | Type | What it does |
|---|---|---|
| Supplement with grid power | Switch | Lets the charger top up solar with grid power, up to the grid source power. |
| Grid source power | Number (kW) | The minimum charge rate during schedule times, made up from grid power when there isn't enough solar. For example, 3.6 kW with 2 kW of solar charges at 2 kW solar plus 1.6 kW grid. |
| Charge outside schedule | Switch | Allows solar charging outside your charging schedules. |
| Boost | Switch | Ignores the solar settings and charges at the maximum rate. Andersen limits a boost to about 2 hours, after which the solar settings resume. |
| Max system charge current | Sensor (A) | The charger's configured maximum current (`cfgChargeAmpMax`). Grid source power is calculated from it if no user max is set. |

**Grid source power and the API.** Andersen's API stores grid source power as a percentage of the
charger's maximum power, not in kW. The integration converts between the two:

* Maximum power is the [user max current](#max-charge-rate) × 0.225, rounded to the nearest
  0.1 kW, as the app does. If no user max is set, or the integration couldn't read it, max system
  charge current is used instead. For a 32 A charger with no user max this is 7.2 kW, which is also
  the highest grid source power you can set.
* Lowering the user max lowers the maximum, so the same percentage gives a lower kW value. For
  example, with a 16 A user max the maximum is 3.6 kW and 50% is 1.8 kW. The kW value shown for
  grid source power changes when the user max changes, even though the charger's setting doesn't.
* The kW value you set is converted to a whole percentage of that maximum, so the value read back
  can differ slightly from what you entered.

**Rules the integration follows.** These match what the app does, because the API doesn't apply
the settings reliably otherwise:

* Grid source power must be **0 or at least 1.4 kW**. The app says solar charging isn't permitted
  below 1.4 kW. A value between 0 and 1.4 kW is set to 0 and a warning is logged.
* Turning **Supplement with grid power off** also sets grid source power to 0.
* Turning **Supplement with grid power on** restores the grid source power you had before it was
  turned off. If there was no earlier value, or it was below 1.4 kW, it uses 1.4 kW. The earlier
  value is kept across Home Assistant restarts.
* Setting **grid source power** to 1.4 kW or more turns Supplement with grid power on, and
  setting it to 0 turns it off.

Unlike the app, the integration doesn't limit grid source power to 0.2 kW steps.

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
  [Features](#features)) - accuracy depends entirely on what the charger itself reports. System
  grid power is the whole house's import or export, measured by the charger's grid CT clamp.
* **Grid energy includes the whole house.** The session and last-session grid energy sensors count
  all grid import while the car was charging, not just the car's share, so they can exceed the
  energy delivered to the car. Andersen doesn't report the car's own grid share.
* **Solar sensors need a solar CT clamp.** Without one, Solar Power shows `unknown` and the solar
  energy sensors hold a stale value.
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
  data straight to the UI (or an automation/script), including fields that have no entity of their
  own.

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
* **Entities showing as unavailable** - most entities go unavailable when the live connection to
  Andersen's cloud has been down for 5 minutes; the log shows a "Live status subscription ... lost"
  warning when that starts. The energy and cost sensors go unavailable when the charge history
  can't be fetched. Both usually mean the Andersen cloud or your internet connection is down. Check
  Andersen's status/app first, then check the Home Assistant logs (see below).
* **Entities showing as unknown just after a restart** - they fill in with the charger's first
  live update, normally within a second. If they stay unknown, check the logs for subscription
  warnings.
* **Download diagnostics for a bug report** - from the integration entry's three-dot menu, choose
  **Download diagnostics**. Credentials and auth tokens are automatically redacted; the file
  includes the config entry, auth-state, per-device status and the live connection's state, which is far more useful in a bug
  report than a description alone.
* **Check the logs** - go to **Settings > System > Logs** and filter for `andersen_ev` to see
  live connection, polling, authentication, and service-call errors and warnings.

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
