# Andersen EV Chargepoint integration for Home Assistant

![Andersen Logo](/images/dark_logo.png)

## Status

![Tests](https://github.com/HA-AndersenEV/hassio-andersen-ev/actions/workflows/python-app.yml/badge.svg?branch=main)

### Beta 0.6.4

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
   - Add `https://github.com/lwsrbrts/hassio-andersen-ev` as a repository
   - Select `Integration` as the category
3. Click "Add"
4. Search for "Andersen EV" in HACS and install it
5. Restart Home Assistant
6. Add the integration via the Home Assistant UI (Settings → Devices & Services → Add Integration)
7. Search for "Andersen EV" and follow the configuration steps

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

## Future development
Frankly depends on whether or not I sell my house (with the charger).

## Changelog

### 0.6.4
* Resolve bug preventing use of the RCM reset service caused by incorrect method name call.

### 0.6.3
* First contribution from [@devachnid](https://github.com/devachnid) adds a service to perform an RCM reset.

### 0.6.2
* Another contribution from [@codeandr3w](https://github.com/codeandr3w) which adds a sensor to report the fault code of the charger.

### 0.6.1
* Fix for empty friendly name causing authentication issue on integration setup. Fixes [#6](https://github.com/lwsrbrts/hassio-andersen-ev/issues/6)

### 0.6.0
* First contribution from [@codeandr3w](https://github.com/codeandr3w) adds live grid power sensors.

### 0.5.2
* Bugfix missing model data in `lock.py`

### 0.5.1
* Add serial number to device
* Added icons to HA brands repo (and included here).

### 0.5.0
* Added switch entities to enable/disable individual charging schedules
* Improved schedule control to sync changes between Home Assistant and the mobile app
* Fixed state synchronization when toggling switches or making changes in the mobile app
* Added better error handling for API communications

### 0.4.2
* Improved model name handling by properly retrieving it from the API response
* Fixed issue with device model display in Home Assistant

### 0.4.1
* Added custom Material Design icons for all sensors
* Added service:
  * `get_device_status` - Retrieves detailed real-time device status with results displayed in UI - enables use of response variables.

### 0.4.0
* Added services:
  * `disable_all_schedules` - Disables all charging schedules for a charge point
  * `get_device_info` - Retrieves detailed device information with results displayed in UI - enables use of response variables.
* Removed redundant enable/disable charging services (use the lock entity instead)
* Changed power sensors to display in kilowatts (kW) to match API values

### 0.3.0
* Implemented automatic token refresh to fix the "No devices found" issue after 1 hour
  * This still uses a full authentication, which isn't ideal but refresh tokens just don't work. 🤷🏻‍♂️
* Added better error handling for authentication failures
* Improved logging for better troubleshooting

### 0.2.0
* Initial release

## Acknowledgements

 * [@iandbird](https://github.com/IanDBird/konnect) - uses the Konnect module as a baseline with my own modifications.
 * [@strobejb](https://github.com/strobejb/andersen-ev) - (indirectly) uses his GraphQL schema in development of API communication.
