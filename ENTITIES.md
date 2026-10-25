# Andersen EV entities

Every entity the integration creates for each charger, where its value comes from, and what it sends when you change it.

## How values are read

| Source type | What it is | When it updates |
| --- | --- | --- |
| **GraphQL subscription** | `deviceStatusUpdated` on `wss://graphql-mobile.andersen-ev.com/graphql`. The charger's live status, pushed by Andersen's cloud | As soon as the charger reports a change: typically every few seconds while charging, and every few seconds to 5 minutes while idle. A change made from Home Assistant usually appears within 1–3 seconds |
| **GraphQL query** | A request to `https://graphql-mobile.andersen-ev.com` | Polled every 60 seconds, or once at startup, as noted per entity |
| **HTTP GET** | Andersen's mobile REST API | Polled every 60 seconds |

Entities read from the subscription show **unknown** until the first live update after Home Assistant starts (setup waits up to 5 seconds for it). They become **unavailable** once the subscription has been disconnected for 5 minutes, and recover on the next update.

Changes made from Home Assistant are not shown straight away. The entity changes when the charger reports the new value through the subscription.

Unique IDs are `<device id>_<suffix>`, using the suffix in the Entity column. Entity IDs follow your charger's name, for example `sensor.home_charger_connector`.

## Sensors

| Entity | Type | Data source type | Data source field | Update field | Notes |
| --- | --- | --- | --- | --- | --- |
| Connector (`connector`) | Sensor, enum | GraphQL subscription | `evseState` | — | 1 Ready, 2 Connected, 3 Charging, 4 Error, 254 Sleeping, 255 Disabled. Anything else, including the 0 seen briefly while a charge starts, shows `unknown` |
| Charge Power (`charge_power`) | Sensor, power (W) | GraphQL subscription | `chargeStatus.chargePower` | — | Charger output for the current session. Updates several times as a session starts, then every 40–90 seconds, and once at the end. Keeps the last session's value between sessions |
| Max Charge Power (`charge_power_max`) | Sensor, power (kW) | GraphQL subscription | `chargeStatus.chargePowerMax` | — | Highest charge power in the current session |
| Solar Power (`solar_power`) | Sensor, power (W) | GraphQL subscription | `sysSolarPower` (kW, shown in W) | — | Reading from the charger's solar CT clamp. Unknown when no solar CT is fitted (`sysSolarCT` false). Enabled by default only if a solar CT is fitted when the entity is first created; if the first live update hasn't arrived within 5 seconds it starts disabled, and can be enabled by hand |
| Current Session Energy (`current_charge_energy`) | Sensor, energy (kWh), total | GraphQL subscription | `chargeStatus.chargeEnergyTotal` | — | Energy delivered to the car this session. Starts a new metering cycle at each session start |
| Current Session Solar Energy (`current_solar_energy`) | Sensor, energy (kWh), total | GraphQL subscription | `chargeStatus.solarEnergyTotal` | — | Needs a solar CT; otherwise holds a stale value |
| Current Session Grid Energy (`current_grid_energy`) | Sensor, energy (kWh), total | GraphQL subscription | `chargeStatus.gridEnergyTotal` | — | All grid import during the session, **including the rest of the house**, not only the car's share |
| Session Start Time (`session_start`) | Sensor, timestamp | GraphQL subscription | `chargeStatus.start` | — | Start of the current or last session |
| System Grid Power (`sys_grid_power`) | Sensor, power (kW) | GraphQL subscription | `sysGridPower` | — | Whole-house grid import (+) or export (−), from the charger's grid CT clamp. While charging it only refreshes with the session fields |
| System Temperature (`sys_temperature`) | Sensor, temperature (°C) | GraphQL subscription | `sysTemperature` | — | |
| System Voltage (`sys_voltage`) | Sensor, voltage (V) | GraphQL subscription | `sysVoltageC` | — | |
| Fault Code (`sys_fault_code`) | Sensor, diagnostic | GraphQL subscription | `sysFaultCode` | — | Raw bitfield; 0 means no fault. 262144 is "waiting for solar" (not a fault). Bit names are in `engineering.md` |
| System Grid Energy Delta (`sys_grid_energy_delta`) | Sensor, energy (kWh), diagnostic | GraphQL subscription | `sysGridEnergyDelta` | — | Disabled by default |
| Max system charge current (`max_system_charge_current`) | Sensor, current (A), diagnostic | GraphQL subscription | `cfgChargeAmpMax` | — | The installer's maximum current. Limits user max current and grid source power |
| Total Energy (`energy`) | Sensor, energy (kWh), total | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].chargeEnergyTotal` | — | Last completed session of at least 0.5 kWh |
| Grid Energy (`grid_energy`) | Sensor, energy (kWh), total | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].gridEnergyTotal` | — | Last completed session. Includes the rest of the house's grid import, so it can exceed Total Energy |
| Solar Energy (`solar_energy`) | Sensor, energy (kWh), total | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].solarEnergyTotal` | — | Last completed session |
| Surplus Energy (`surplus_energy`) | Sensor, energy (kWh), total | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].surplusUsedEnergyTotal` | — | Last completed session |
| Total Cost (`cost`) | Sensor, monetary | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].chargeCostTotal` | — | Currency from `getDevice.deviceInfo.currency`, read once at startup; GBP until known |
| Grid Cost (`grid_cost`) | Sensor, monetary | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].gridCostTotal` | — | As Total Cost |
| Solar Cost (`solar_cost`) | Sensor, monetary | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].solarCostTotal` | — | As Total Cost |
| Surplus Cost (`surplus_cost`) | Sensor, monetary | GraphQL query, every 60 s | `getDeviceCalculatedChargeLogs[0].surplusUsedCostTotal` | — | As Total Cost |

## Controls

| Entity | Type | Data source type | Data source field | Update field | Notes |
| --- | --- | --- | --- | --- | --- |
| Lock (`lock`) | Lock | GraphQL subscription | `sysUserLock` | `runAEVCommand` `userLock` / `userUnlock` | Shows `locking` / `unlocking` until the charger confirms, usually within seconds. Reverts with a warning if not confirmed within 5 minutes |
| Charge now (`charge_now`) | Button | — | — | `runAEVCommand` `chargeNow`, then `userForceStart` | Starts charging now, ignoring schedules |
| Stop charging (`stop_charging`) | Button | — | — | `runAEVCommand` `userForceStop`, then `stopChargeNow` | Has no effect while Charge outside schedule is on and solar is available |
| Schedule {n} (`schedule_{index}`) | Switch, config | GraphQL subscription | `scheduleSlotsArray[n].enabled` | `setSchedules` with the slot as `sch{n}` | One switch per slot, created at startup from the `getDevice` query. The slot's name from the app (`deviceInfo.schedule{n}Name`) is in the `schedule_name` attribute |
| Boost (`solar_boost`) | Switch | GraphQL subscription | `solarOverride` | `setSolar(override)` | Ignores solar settings and charges at full rate while a charge is in progress. It doesn't start a charge. Andersen ends a boost after about 2 hours |
| Supplement with grid power (`solar_solarChargeAlways`) | Switch | GraphQL subscription | `solarChargeAlways` | `setSolar(chargeAlways, maxGridChargePercent)` | Off also sends 0%. On restores the previous percentage, at least 1.4 kW; the previous value survives restarts |
| Charge outside schedule (`solar_solarChargeOutsideSchedules`) | Switch | GraphQL subscription | `chargeOutsideSchedules` | `setSolar(chargeOutsideSchedules)` | Allows solar charging outside schedules. `sysScheduleLock` is not this setting: it means a schedule is currently blocking charging |
| Grid source power (`grid_source_power`) | Number, power (kW), step 0.1 | GraphQL subscription | `solarMaxGridChargePercent`, with `sysUserMaxChargeAmpSet` and `cfgChargeAmpMax` | `setSolar(chargeAlways, maxGridChargePercent)` | Stored by Andersen as a percentage of maximum power (user max current × 0.225 kW, or the installer limit if no user max is set). Must be 0 or at least 1.4 kW: values in between are set to 0 with a warning. 1.4 kW or more also turns Supplement with grid power on; 0 turns it off |
| User max power (`user_max_power`) | Number, power (kW), step 0.1 | GraphQL subscription | `sysUserMaxChargeAmpSet` (range from `cfgChargeAmpMax`) | `runAEVCommand` `userAmpSet` `{"userSetChargeAmpMax": <A>}` | Only 10, 16, 24 or 32 A (2.3, 3.6, 5.4, 7.2 kW) up to the installer limit; other values round to the nearest, halfway rounds down. Shows the installer limit when no user max is set |
| User max current (`user_max_current`) | Number, current (A), step 1 | GraphQL subscription | `sysUserMaxChargeAmpSet` (range from `cfgChargeAmpMax`) | `runAEVCommand` `userAmpSet` `{"userSetChargeAmpMax": <A>}` | Same setting as User max power. Disabled by default |

## Device information

| Shown as | Data source type | Data source field | Notes |
| --- | --- | --- | --- |
| Model | GraphQL subscription | `sysProductId`, falling back to `sysProductName` | Product id 30 is shown as A2. Read when the entities are created |
| Model ID | GraphQL subscription | `sysProductId` | |
| Hardware version | GraphQL subscription | `sysHwVersion` | |
| Firmware version | GraphQL subscription | `sysFwVersion` | |
| Serial number | GraphQL subscription | `konnectSerial` | |

## Actions

| Action | Sends | Notes |
| --- | --- | --- |
| `andersen_ev.disable_all_schedules` | GraphQL mutation `setAllSchedulesDisabled` | The schedule switches follow when the charger reports it |
| `andersen_ev.reset_rcm` | `runAEVCommand` `rcmReset` | Clears an RCM trip; the fault code follows |
| `andersen_ev.get_device_info` | GraphQL query `getDevice` | One-off device information, returned to the caller |
| `andersen_ev.get_device_status` | GraphQL query `getDeviceStatus` | One-off snapshot of the full status, including fields without an entity |

## Background requests

| Request | Type | When | Used for |
| --- | --- | --- | --- |
| `deviceStatusUpdated` | GraphQL subscription | Kept open; reconnects after 5 s, backing off to 5 minutes | Every entity marked GraphQL subscription above |
| `getDevices` | HTTP GET | Every 60 s | List of chargers on the account, and their names |
| `getDeviceCalculatedChargeLogs` | GraphQL query | Every 60 s | Last-session energy and cost sensors |
| `getDevice` | HTTP GET | Every 60 s | Fetched for future use; no entity reads it |
| `getDevice` | GraphQL query | Once at startup | Schedule slots and names, and the cost currency |
