# Andersen EV app: API findings from `index.android.bundle`

Notes from reverse engineering the Andersen Android app's React Native bundle

## Index

1. [How the bundle was analysed](#1-how-the-bundle-was-analysed)
   - [Repeating the analysis in future](#repeating-the-analysis-in-future)
2. [Endpoints](#2-endpoints-important)
3. [`runAEVCommand`](#3-runaevcommand)
   - [Known `functionName` values](#known-functionname-values)
4. [Charge outside schedule](#4-charge-outside-schedule)
   - [What the UI reads for the current value](#what-the-ui-reads-for-the-current-value)
5. [`override` (Boost)](#5-override-boost)
   - [When Boost is visible](#when-boost-is-visible)
6. [`chargeAlways` and `maxGridChargePercent`](#6-chargealways-and-maxgridchargepercent-supplement-with-grid-power)
   - [UI behaviour on the Power source screen](#ui-behaviour-on-the-power-source-screen)
   - [Implied rules](#implied-rules)
7. [Converting the slider (kW) to `maxGridChargePercent`](#7-converting-the-slider-kw-to-maxgridchargepercent)
   - [Where `sysUserMaxChargeAmpSet` comes from](#where-sysusermaxchargeampset-comes-from)
8. ["Charge now" and "Stop charging" visibility](#8-charge-now-and-stop-charging-visibility)
9. [Other API surface found in the bundle](#9-other-api-surface-found-in-the-bundle)
   - [GraphQL mutations](#graphql-mutations)
   - [GraphQL queries and subscriptions](#graphql-queries-and-subscriptions)
   - [Useful status and info fields](#useful-status-and-info-fields)
10. [Replicating the charger status](#10-replicating-the-charger-status-the-badge-on-the-home-screen)
    - [Inputs](#inputs)
    - [`evseState` values](#evsestate-values)
    - [`sysFaultCode` bit values](#sysfaultcode-bit-values)
    - [Order of checks](#order-of-checks)
    - [Badge text and colour](#badge-text-and-colour)
    - [Practical shortcut](#practical-shortcut)

---

## 1. How the bundle was analysed

- `index.android.bundle` is **Hermes bytecode** (v96, then v98 in the newer build), not plain JavaScript. A normal text search only finds some strings.
- Disassembled with [`hermes-dec`](https://github.com/P1sec/hermes-dec) (`hbc-disassembler`) to a `.hasm` text file (about 140 to 170 MB).
- v98 is not formally supported by `hermes-dec`, so some opcodes may be mis-decoded. String literals are reliable.
- Hermes v98 packs and suffix-shares its string table, so some strings are not labelled in the disassembly but still exist in the raw bytes. `grep -a` on the raw bundle finds them.
- GraphQL documents are stored as string literals, so they can be read directly.
- Command names such as `userUnlock` are passed as string arguments to helper functions. They don't appear next to the query text, which is why they were hard to find by searching.

### Repeating the analysis in future

To get a fresh copy of the bundle after an app update:

- Install adb and jadx-github
- Open a command prompt in windows
- Connect phone to PC in USB debug mode
- Run `adb shell pm path com.andersen_ev.android`
- Run adb pull on the resulting path from above e.g. `adb pull /data/app/xxxx/com.andersen_ev.android-xxx/base.apk ./andersen-base.apk`
- Open the downloaded file in jadx-gui, navigate to assets, export `index.android.bundle`

Then disassemble the exported bundle with `hermes-dec` as described above and compare the GraphQL documents, endpoints and config values with this document.

---

## 2. Endpoints (important)

| Purpose | URL |
|---|---|
| **GraphQL (production)** | `https://graphql-mobile.andersen-ev.com` |
| GraphQL (non-production default) | `graphql-staging.andersen-ev.com` |
| Mobile REST API | `https://mobile-staging.andersen-ev.com/api` |
| Portal REST API | `https://portal.andersen-ev.com/api` |

- **Use `graphql-mobile`.** `https://graphql.andersen-ev.com` is a different deployment with a different schema. On it, `setSolar` rejects `chargeOutsideSchedules` with `Unknown argument "chargeOutsideSchedules" on field "setSolar" of type "Mutation"`. Switching to `graphql-mobile` fixed that.
- **The mobile API is only named "staging".** The bundle has a single config block, the one where `graphqlUrl` is the production `graphql-mobile` host. It sets `mobileApiUrl` to the `mobile-staging` host. No separate production mobile host appears anywhere. We've been using https://mobile.andersen-ev.com/api for the getDevice call but this provides a lot less info, whilst the staging endpoint provides info and status data not otherwise available, for example device.info.ChargeOutsideSchedules which isn't in the graphql device call


---

## 3. `runAEVCommand`

```graphql
mutation runAEVCommand(
  $deviceId: ID!
  $functionName: String!
  $params: String
) {
  runAEVCommand(
    deviceId: $deviceId
    functionName: $functionName
    params: $params
  ) {
    return_value
  }
}
```

`params` is a **JSON-encoded string** (the app builds it with `JSON.stringify`).

### Known `functionName` values

The app has a wrapper hook (`useRunAEVCommand`) exposing these:

| App wrapper | `functionName` | `params` |
|---|---|---|
| `chargeNow` | `chargeNow` | none |
| `forceStop` | `userForceStop` | none |
| `stopChargeNow` | `stopChargeNow` | none |
| `forceStart` | `userForceStart` | none |
| `skipScheduleRandom` | `ScheduleRandomSkip` | `{"enable": true}` |
| (lock toggle) | `userLock` / `userUnlock` | none |
| `setUserPowerMax` | `userAmpSet` | `{"userSetChargeAmpMax": <amps>}` |
| `setLightSettings` | `lightSettings` | `{"status": …, "amenity": …, "motion": …, "socket": …}` |
| `setMotorLock` | `motorLockSet` | `{"motorLocked": <bool>}` |

The same names exist in the v98 bundle (checked in the raw bytes).

Notes:

- Pressing **Stop charging** sends `userForceStop` and then `stopChargeNow`.
- Pressing **Charge now** sends `chargeNow`, then `userForceStart` once that returns, and refreshes the device every 30 seconds. `userForceStart` clears the force stop left by **Stop charging**; without it the charger will not start again after a stop.

This means we should be able to replicate the chargeNow and stop charging buttons now.

---

## 4. "Charge outside schedule"

The toggle on the **Power source** screen (`DevicePowerSourceScreen`) uses a different mutation, `setSolar`:

```graphql
mutation setSolar(
  $deviceId: ID!
  $override: Boolean
  $chargeAlways: Boolean
  $maxGridChargePercent: Int
  $chargeOutsideSchedules: Boolean
) {
  setSolar(
    deviceId: $deviceId
    override: $override
    chargeAlways: $chargeAlways
    maxGridChargePercent: $maxGridChargePercent
    chargeOutsideSchedules: $chargeOutsideSchedules
  ) {
    return_value
  }
}
```
The Power source screen's Save button sends:

```
{ deviceId, chargeAlways, maxGridChargePercent: round(sliderKw / maxPower * 100), chargeOutsideSchedules }
```

- The current setting is `chargeOutsideSchedules`, on `DeviceStatus` (query and `deviceStatusUpdated` subscription) and `DeviceInfo`. It matches the app's `info.ChargeOutsideSchedules`.
- `sysScheduleLock` is **not** the setting. It means a schedule is currently blocking charging: it went `false` when Charge now was pressed and back to `true` when charging stopped, while "Charge outside schedule" stayed off (tested 24 Oct 2026).
- The other arguments are nullable, but we need to confirm behaviour still
- chargeOutsideSchedules only works on the `graphql-mobile` host (section 2).

### What the UI reads for the current value

- The Power source screen initialises its toggle with `useState(device.info.ChargeOutsideSchedules)`. The capital `C` is real. The "unsaved changes" check compares the toggle against the same field.
- **That field does not come from GraphQL.** The `getDevice` GraphQL query never requests it, and `sysScheduleLock` is not in that query either. `sysScheduleLock` appears only in the `deviceStatusUpdated` subscription and in the app's charge-state helpers (`getChargeStatus`, `calculateChargeState`).
- The app loads the device through the REST client instead: `GET {mobileApiUrl}/getDevice?deviceId=<id>` with `Authorization: Bearer <token>`. The app reads `data.userDevice` from the response. This object carries `info` and `status`, and `info` has PascalCase fields including `ChargeOutsideSchedules`.
- The app never writes `ChargeOutsideSchedules` locally. After a save it calls `refetch`, so the new value comes back through that REST call.
- Other REST paths on the same base: `/getUserDevices`, `/getDeviceChargeLogs?deviceId=`, `/getSmartTariffInfo?deviceId=`, `/regenerateSmartSchedules?deviceId=`.

I've been unable to replicate the getDevice?deviceId=<id> call, however all of the info
is available via getDevices when using the staging endpoint

---

## 5. `override` (Boost)

`override` is the **Boost** button. The handler calls:

```
setSolar({ deviceId, override: !deviceStatus.solarOverride })
```

- It toggles the `solarOverride` status field. The button reads "Cancel boost" while `solarOverride` is true.
- The UI text says boosting overrides solar settings and charges at the maximum rate for up to 2 hours, after which solar settings resume. That 2-hour limit is not in the app code, so it is enforced server-side or on the device.

### When Boost is visible

The boost button appears on the second screen of hte home page (swipe left), but only whilst charging.
The Boost block (including the "remaining" timer bar) only renders when **both** are true:

1. `chargeState === 'CHARGING'`
2. `solarConfig.advanced || solarConfig.basic`, derived from `status.cfgCTConfig`:

| `cfgCTConfig` | advanced | basic | adaptiveFuse |
|---|---|---|---|
| `CT_CONFIG_THREE_PHASE_A4_SOLAR1CT_GRID1CT` | yes | no | yes |
| `CT_CONFIG_THREE_PHASE_A4_SOLAR1CT_NOGRID` | yes | no | no |
| `CT_CONFIG_THREE_PHASE_A4_SOLAR3CT_NOGRID` | yes | no | no |
| `CT_CONFIG_THREE_PHASE_A4_NOSOLAR_GRID3CT_BASIC` | no | yes | yes |
| `CT_CONFIG_SINGLE_PHASE_SOLARWIRED_NOGRID` | yes | no | no |
| `CT_CONFIG_SINGLE_PHASE_SOLARWIRED_GRIDWIRED` | yes | no | yes |
| `CT_CONFIG_SINGLE_PHASE_NOSOLAR_GRIDWIRED` | no | no | yes |
| `CT_CONFIG_SINGLE_PHASE_NOSOLAR_GRIDWIRED_BASIC` | no | yes | yes |
| anything else / unset | no | no | no |

If you can't see Boost, check that the charger is actively charging and look at `deviceStatus.cfgCTConfig`.


---

## 6. `chargeAlways` and `maxGridChargePercent` ("Supplement with grid power")

### UI behaviour on the Power source screen

| UI action | What the handler does |
|---|---|
| Toggle "Supplement with grid power" **on** | sets `chargeAlways = true` and sets the slider to **1.4 kW** if it's below that |
| Toggle it **off** | sets `chargeAlways = false` and sets the slider to **0** |
| Move the slider | forces `chargeAlways = true` if it was off, then sets the slider value |
| Toggle "Charge outside schedule" | only changes `chargeOutsideSchedules` |

- The slider has `minimumValue: 1.4` and `step: 0.2` (kW). The screen says solar charging is not permitted below 1.4 kW "due to regulations".
- UI help text: the supplement toggle makes the vehicle charge from both solar and grid, and the slider sets the **minimum vehicle charge rate during schedule times**. Example from the app: a 3.6 kW slider setting with 2 kW of solar means 2 kW solar plus 1.6 kW grid.


### Implied rules

The app only ever sends two kinds of pair:

- **On:** `chargeAlways: true` with a percent of at least `round(1.4 / maxPower × 100)`. That is **19** on a 7.2 kW charger.
- **Off:** `chargeAlways: false` with `maxGridChargePercent: 0`.

Observed behaviour that fits this:

- `chargeAlways: true` with percent `0` does nothing. The app never sends it, and its "unsaved changes" check treats that combination as a mismatch.
- With a non-zero percent you can't switch `chargeAlways` off. Same mismatch in reverse.

The server-side behaviour looks like "supplementing is on only if the percent is above 0", with `chargeAlways` mostly redundant. **This is an inference**; the bundle doesn't show the server rules. To turn it off, send `chargeAlways: false` and `maxGridChargePercent: 0` together.

---

## 7. Converting the slider (kW) to `maxGridChargePercent`

The app calculates `maxPower` in kW from device status:

```
amps     = status.sysUserMaxChargeAmpSet ?? status.cfgChargeAmpMax ?? 0
maxPower = round(amps × 9 / 40, 1 decimal)        // = amps × 0.225
```

At 32 A this gives 7.2 kW.

```
save:  maxGridChargePercent = Math.round(sliderKw / maxPower × 100)
load:  sliderKw = round(solarMaxGridChargePercent / 100 × maxPower, 2 decimals)
```

- The reference is the **user-set maximum** (`sysUserMaxChargeAmpSet`), not the hardware maximum. If the max power is lowered in the app, percentages are relative to the lower figure.
- It is **not** `evseChargeAmpCurrentLimit`, which is the dynamic limit.
- For an integration: read `sysUserMaxChargeAmpSet × 0.225` live rather than hardcoding 7.2, and fall back to `cfgChargeAmpMax` (or 32 A) if it's null.
- I did not confirm what the slider's maximum is. The code only shows the minimum and step. It is most likely `maxPower`.

### Where `sysUserMaxChargeAmpSet` comes from

- **Read:** it is in the `deviceStatus` selection of the `getDevice` GraphQL query and in the `deviceStatusUpdated` subscription, so you get the initial value and live changes over GraphQL.
- **Write:** the app's `setUserPowerMax` helper sends `runAEVCommand` with `functionName: "userAmpSet"` and `params: {"userSetChargeAmpMax": <amps>}`. The charger then reports the new value back as `sysUserMaxChargeAmpSet` (inferred; the bundle can't show the firmware side).
- **Fallback in the UI:** the Power Settings screen shows `sysUserMaxChargeAmpSet` and falls back to `cfgChargeAmpMax` when it is null or 0.
- **`cfgChargeAmpMax`:** this is the installer-configured hardware limit. It is not in the `getDevice` GraphQL query text, so it must arrive in the status object through the REST `getDevice` call (not confirmed). The app builds it alongside `cfgAFAmpMax` and `cfgAFEnable` in what looks like the installer setup flow, probably over Bluetooth (transport not confirmed).

---

## 8. "Charge now" and "Stop charging" visibility

The Home screen (`ChargerOverview`) computes five flags: `chargeNow`, `oldChargeNow`, `stopCharging`, `lockCharger` and `socketLock`.

- **Firmware check.** If `status.sysFwVersion` (null becomes 0) is below `fwVersionForAdvancedUse` (**500**), the old flow applies: `oldChargeNow = (chargeState === 'SCHEDULED')`, and the new Charge now and Stop buttons are hidden.
- **Newer firmware, Charge now.** `chargeNow` is shown when the car is plugged in (`isPluggedIn`) and either:
  - `chargeState` is `SCHEDULED`, `WONT_CHARGE` or `PAUSED`, or
  - `sysForceStop` is true and `chargeState` is not `CHARGING`.
- **Newer firmware, Stop.** `stopCharging = (chargeState === 'CHARGING')`.
- **Overrides:**
  - `chargeState === 'LOCKED'` hides Charge now and Stop.
  - A third-party integration (any integration type other than "none") hides them and shows "To control your charging, please use your … app".
- **Lock button.** `lockCharger = (chargeState !== 'OFFLINE')`.

`chargeState` is derived from the raw status by the app. See section 10.

---

## 9. Other API surface found in the bundle

### GraphQL mutations

`runAEVCommand`, `setSolar`, `setSchedules`, `setAllSchedulesDisabled`, `setScheduleNames`, `upsertDeviceInfo`, `upsertAccount`, `claimDeviceByName`, `unclaimDevice`, `shareChargePoint`, `acceptDeviceShare`, `editDeviceShare`, `removeShare`, `createChargeRate`, `updateChargeRate`, `deleteChargeRate`, `registerMobileDevice`, `unregisterMobileDevice`

### GraphQL queries and subscriptions

`getDevice`, `getCurrentUserDevices` (id, name, `deviceInfo { id friendlyName }`, `deviceStatus { id cfgCTConfig sysFwVersion }`), `getDeviceCalculatedChargeLogs`, `getDeviceChargeRates`, `getDeviceIdBySerial`, `getCalculatedPowerLogsBinned`, `searchAddress`; subscription `deviceStatusUpdated`.

### Useful status and info fields

- **Status (`deviceStatus`):** `sysUserLock`, `sysScheduleLock`, `solarOverride`, `solarChargeAlways`, `solarMaxGridChargePercent`, `sysForceStop`, `sysPlugged`, `evseState`, `evseChargeAmpCurrentLimit`, `sysUserMaxChargeAmpSet`, `cfgCTConfig`, `sysFwVersion`, `sysSchEnabled`, `sysSch0`–`sysSch9`, `sysKaluzaSch0`–`sysKaluzaSch9`, `scheduleSlotsArray`.
- **Fields in the `deviceStatusUpdated` subscription:** includes `sysScheduleLock`, `sysUserLock`, `solarOverride`, `solarChargeAlways`, `solarMaxGridChargePercent`, `sysForceStop`, `sysUserMaxChargeAmpSet`, `evseChargeAmpCurrentLimit` and `cfgCTConfig`.
- **Info (`deviceInfo`, GraphQL):** `userLock`, `solarChargeAlways`, `amenityLight`, `motionLight`, `statusLight`, plus the schedule names and codes. It does not include `ChargeOutsideSchedules`.
- **Info (REST `getDevice`):** PascalCase fields including `ChargeOutsideSchedules`, `KaluzaLinked`, `KaluzaSchedules`, `KrakenLinked`, `LastCheckedOctopus`, `NotifiedDeviceOfflineAt`.
---

## 10. Replicating the charger status (the badge on the Home screen)

The charger does not send a ready-made state. The app works it out on the phone in `getChargeStatus` (wrapped by `calculateChargeState`) from the raw status. These findings come from the v98 decompilation, so the order of checks is my best reading rather than certain.

### Inputs

| Input | Source |
|---|---|
| `evseState`, `sysUserLock`, `sysScheduleLock`, `online`, `sysFaultCode`, `sysFwVersion`, `sysSch0`–`sysSch9`, `sysKaluzaSch0`–`sysKaluzaSch9` | GraphQL `deviceStatus` (initial read and live via `deviceStatusUpdated`) |
| `lastEventAge` | seconds since the last event: `round((now - lastEvent) / 1000)`, calculated by `calculateChargeState` |
| `KaluzaLinked`, `KaluzaSchedules`, `LastCheckedOctopus`, `NotifiedDeviceOfflineAt` | REST `info` (PascalCase) |

### `evseState` values

| Value | Name | State returned |
|---|---|---|
| 0 | `EVSE_STATE_STARTING` | `STARTING` |
| 1 | `EVSE_STATE_NOT_CONNECTED` | `STANDBY` (badge says "UNPLUGGED") |
| 2 | `EVSE_STATE_CONNECTED` | `CONNECTED` |
| 3 | `EVSE_STATE_CHARGING` | `CHARGING` (or `FAULT` if the RCM-trip bit is set) |
| 14 | `EVSE_STATE_OPEN_RELAY` | `FAULT` (falls through the checks) |
| 255 | `EVSE_STATE_DISABLED` | schedule and fault checks below |
| anything else | unknown | `FAULT` |

### `sysFaultCode` bit values

`sysFaultCode` is a bitfield (undefined is treated as 0). Names are as they appear in the app:

| Bit | Name | Bit | Name |
|---|---|---|---|
| 1 | `FAULT_CODE_RCM_TRIP` | 16384 | `FAULT_OVERTEMP` |
| 2 | `FAULT_CODE_RTC_LOST_POWER` | 32768 | `FAULT_I2C_PEN` |
| 4 | `FAULT_CODE_RTC` | 65536 | `FAULT_CODE_SPI_GPIO` |
| 8 | `FAULT_CODE_I2C_SPI` | 131072 | `FAULT_CODE_SUPPLY_VOLTS_FAULT` |
| 16 | `FAULT_CODE_I2C_GPIO` | 262144 | `FAULT_SOLAR_CHARGE_CURRENT_LOW` |
| 32 | `FAULT_CODE_EARTH_CT_OP` | 524288 | `FAULT_NO_WIFI_CREDENTIALS` |
| 64 | `FAULT_CODE_EARTH_CT_ERROR` | 1048576 | `FAULT_CODE_PEN_INHIBIT` |
| 128 | `FAULT_CODE_ENERGY_METER` | 2097152 | `FAULT_CODE_OCP_TRIP` |
| 256 | `FAULT_CODE_EVSE_COMMS` | 4194304 | `FAULT_CODE_CHARGE_CURRENT_TOO_LOW_AF` |
| 512 | `FAULT_CODE_EVSE_VENT_REQUIRED` | 67108864 | `FAULT_CODE_STUCK_CONTACT` |
| 1024 | `FAULT_CODE_EVSE_DIODE_CHECK` | 134217728 | `FAULT_CODE_OPEN_CONTACT` |
| 2048 | `FAULT_CODE_EVSE_OTHER` | | |
| 4096 | `FAULT_CODE_TIME_CORRUPT` | | |
| 8192 | `FAULT_NO_CLOUD_CONNECTION` | | |

### Order of checks

1. **Offline.** Returns `OFFLINE` if `isOffline` is set, if `online` is false, or if `lastEventAge` is greater than a firmware-dependent threshold. The threshold is **900 s** for `sysFwVersion` in 516–599, 611–699 and 706–899, and **300 s** (`offlineDurationInSeconds`) otherwise.
2. **Locked.** `sysUserLock === true` returns `LOCKED`.
3. **Hard faults.** Any of `FAULT_CODE_STUCK_CONTACT`, `FAULT_CODE_OPEN_CONTACT` or `FAULT_OVERTEMP` returns `FAULT`.
4. **`evseState`.** As in the table above. Only `EVSE_STATE_DISABLED` continues.
5. **Disabled branch.**
   1. If any of `FAULT_CODE_RCM_TRIP`, `FAULT_CODE_OCP_TRIP`, `FAULT_CODE_SUPPLY_VOLTS_FAULT`, `FAULT_CODE_PEN_INHIBIT`, `FAULT_CODE_EARTH_CT_OP` or `FAULT_CODE_EARTH_CT_ERROR` is set, return `FAULT`.
   2. If `sysScheduleLock === true` and any `sysSchN` string ends in `'1'`, return `SCHEDULED`.
   3. Smart-tariff (Kaluza/Octopus) handling can return `SCHEDULED` or `WONT_CHARGE`. It uses `sysKaluzaSch0`–`9`, `KaluzaSchedules` (a JSON string), the weekday codes monday `02`, tuesday `04`, wednesday `08` and so on, and a comparison with today and yesterday. **Not fully decoded.** The UI text for `WONT_CHARGE` is "No smart schedules set" and for `SCHEDULED` is "Your charge point is due to start charging…" (with a randomised delay if one applies).
   4. If `FAULT_SOLAR_CHARGE_CURRENT_LOW` or `FAULT_CODE_CHARGE_CURRENT_TOO_LOW_AF` is set, return `PAUSED`.
   5. Otherwise return `DISABLED`.

`REDUCED_CHARGE` exists in the state list but `getChargeStatus` never returns it. It is probably set elsewhere (the UI text is "Charge rate reduced by vehicle").

### Badge text and colour

| State | Text | Colour |
|---|---|---|
| `CHARGING` | CHARGING | green |
| `CONNECTED` | CONNECTED | green |
| `STANDBY` | UNPLUGGED | grey |
| `LOCKED` | LOCKED | light blue |
| `SCHEDULED` | SCHEDULED | orange |
| `PAUSED` | WAITING (plus FOR SOLAR when solar applies) | orange |
| `FAULT` | ERROR | red |
| `OFFLINE` | OFFLINE (text path not fully traced) | grey |

A red STOPPED badge also exists. I did not find what triggers it. The `PAUSED` tooltip reads "Waiting for solar energy".

### Practical shortcut

For a Home Assistant sensor, the GraphQL fields are enough for most states: `OFFLINE`, `LOCKED`, `FAULT`, `STANDBY`, `CONNECTED`, `CHARGING` and "waiting for solar" (`evseState` 255 with a solar-low fault bit set). Only the `SCHEDULED` versus `WONT_CHARGE` split in smart-tariff mode needs the REST fields and the undecoded branch.