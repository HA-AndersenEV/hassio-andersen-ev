# How Andersen EV data is updated

This is a cloud-only integration (`iot_class: cloud_push`) - there is no local/LAN control of
the chargepoint. All communication goes through Andersen's Konnect+ cloud (AWS Cognito
authentication, a GraphQL API and a REST API), so an internet connection and Andersen's cloud
being up are both required for entities to update or for services/lock actions to work.

**Live status is pushed.** The integration keeps a GraphQL subscription open to Andersen's cloud
for each charger, and the charger's status arrives as soon as it changes: every few seconds while
charging, and anywhere from a few seconds to a few minutes while idle. Almost every entity reads
from it: the lock, switches, numbers, buttons, device information, and every sensor except the
last-session energy and cost sensors.

* After Home Assistant starts, these entities show `unknown` until the first live update, which
  normally arrives within a second.
* Changes you make from Home Assistant are shown once the charger reports them, usually within
  1-3 seconds. Nothing is assumed in the meantime, so the entity never shows a value the charger
  hasn't confirmed.
* If the connection drops, the integration reconnects after 5 seconds, backing off to every 5
  minutes. One warning is logged when it is lost and one message when it is restored. Entities keep
  their last values until the connection has been down for 5 minutes, then become unavailable
  until it returns.
* If the subscription refuses the login even after refreshing it, the integration stops retrying
  and asks you to sign in again (re-authentication).

**Some data is still polled**, every 60 seconds, because the subscription doesn't carry it:

| Polled | Used for |
|---|---|
| Charge history (GraphQL `getDeviceCalculatedChargeLogs`) | The last-session energy and cost sensors: Total, Grid, Solar and Surplus Energy, and Total, Grid, Solar and Surplus Cost |
| Charger list (REST `getDevices`) | Finding chargers added to or removed from the account |
| Device settings (REST `getDevice`) | Fetched, but not currently used by any entity |

The schedule names and the cost currency are read once at startup.

See [ENTITIES.md](ENTITIES.md) for the source of each entity's value.

## Reading the debug log

With debug logging enabled for `custom_components.andersen_ev`, each source logs its first
response in full and after that only the fields that changed. Live updates show the old and new
values; REST responses show only the new value:

```
deviceStatusUpdated for Home Charger (initial): {'id': '…', 'evseState': 255, …}
deviceStatusUpdated for Home Charger: evseState 2 -> 3, sysTime -> '2026-10-24T12:40:34+01:00'
REST getDevice 1234567890: userDevice.status.sysUserMaxChargeAmpSet -> 32
```

Timestamps always show only their new value. Nested fields are named with dots, such as
`chargeStatus.chargePower`. A response with no changes logs nothing.
