#!/usr/bin/env python3
"""Manual integration test for Andersen EV GraphQL calls.

NOT a pytest test — run directly from the command line:

    python -m custom_components.andersen_ev.tests.integration_test \\
        --email you@example.com --password yourpassword

Or with environment variables:

    export ANDERSEN_EMAIL=you@example.com
    export ANDERSEN_PASSWORD=yourpassword
    python -m custom_components.andersen_ev.tests.integration_test

Optionally pass --device-id <id> to target a specific device.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Ensure the repo root is on sys.path so "andersen_ev" is importable
# when run from the hassio-andersen-ev directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from andersen_ev.konnect.client import KonnectClient  # noqa: E402

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
_LOGGER = logging.getLogger("integration_test")

# Reduce noise from libraries unless you want full debug
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

# Delay (seconds) after a write before re-reading, giving the API
# time to apply the change.
WRITE_READ_DELAY_S = 3


def _pp(label: str, data: object) -> None:
    """Pretty-print a result block."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print("=" * 60)
    print(json.dumps(data, indent=2, default=str))


async def run(email: str, password: str, target_device_id: str | None) -> bool:
    """Authenticate, discover devices, and exercise every GraphQL call.

    Returns True if all checks passed, False if any failed.
    """
    failures: list[str] = []

    # -- authenticate ------------------------------------------------------
    _LOGGER.info("Authenticating as %s ...", email)
    client = KonnectClient(email, password)
    await client.authenticate_user()
    _LOGGER.info("Authenticated — token expires in ~%ss", client.token_expires_in)

    # -- discover devices --------------------------------------------------
    _LOGGER.info("Fetching device list ...")
    devices = await client.getDevices()
    if not devices:
        _LOGGER.error("No devices found for this account")
        return False

    for dev in devices:
        _pp(
            f"Device: {dev.friendly_name} ({dev.device_id})",
            {
                "device_id": dev.device_id,
                "friendly_name": dev.friendly_name,
                "user_lock": dev.user_lock,
            },
        )

    # Pick the device to test against
    device = devices[0]
    if target_device_id:
        for d in devices:
            if d.device_id == target_device_id:
                device = d
                break
        else:
            _LOGGER.warning(
                "Device %s not found, falling back to %s",
                target_device_id,
                device.device_id,
            )

    _LOGGER.info("Testing against device: %s (%s)", device.friendly_name, device.device_id)

    # -- test calls --------------------------------------------------------

    # 1. Device status
    _LOGGER.info("Calling get_detailed_device_status ...")
    status = await device.get_detailed_device_status()
    _pp("Device Status", status)

    # 2. Device info
    _LOGGER.info("Calling get_device_info ...")
    info = await device.get_device_info()
    _pp("Device Info", info)

    # 3. Last charge
    _LOGGER.info("Calling get_last_charge ...")
    charge = await device.get_last_charge()
    _pp("Last Charge", charge)

    # 4. Get solar settings (re-fetches device status)
    _LOGGER.info("Fetching solar settings ...")
    solar = await device.get_solar()
    _pp("Solar Settings (from deviceStatus)", solar)

    # 5. Solar round-trip test:
    #    - save original solar settings from device status
    #    - write modified values via setSolar
    #    - re-fetch status and verify the changes took effect
    #    - restore the original values
    #    - re-fetch status and verify they are restored
    if solar and any(v is not None for v in solar.values()):
        original = dict(solar)
        _pp("Original solar settings (saved)", original)

        # Build modified values — flip booleans, nudge the percentage
        modified_boost = not original.get("boost", False)
        modified_charge_always = not original.get("solarChargeAlways", False)
        orig_pct = original.get("solarMaxGridChargePercent", 50)
        modified_pct = 25 if orig_pct != 25 else 75

        _LOGGER.info(
            "Writing MODIFIED solar settings: boost=%s, charge_always=%s, max_grid_charge_percent=%s",
            modified_boost,
            modified_charge_always,
            modified_pct,
        )
        ok = await device.set_solar(
            boost=modified_boost,
            charge_always=modified_charge_always,
            max_grid_charge_percent=modified_pct,
        )
        _pp("set_solar (modify) result", {"success": ok})

        if not ok:
            failures.append("set_solar (modify) returned failure")
            _LOGGER.error("set_solar FAILED — skipping verify/restore")
        else:
            _LOGGER.info("Waiting %ss for API to apply changes ...", WRITE_READ_DELAY_S)
            await asyncio.sleep(WRITE_READ_DELAY_S)

            _LOGGER.info("Re-fetching device status to verify changes ...")
            after_modify = await device.get_solar()
            _pp("Solar Settings (after modify)", after_modify)

            # Verify modified values took effect
            expected_modify = {
                "boost": modified_boost,
                "solarChargeAlways": modified_charge_always,
                "solarMaxGridChargePercent": modified_pct,
            }
            mismatches = {
                k: {"expected": expected_modify[k], "actual": after_modify.get(k)}
                for k in expected_modify
                if after_modify.get(k) != expected_modify[k]
            }
            if mismatches:
                failures.append(f"MODIFY mismatches: {mismatches}")
                _LOGGER.error("MODIFY FAILED — mismatches: %s", mismatches)
            else:
                _LOGGER.info("MODIFY VERIFIED — all solar values match expected")

            # Restore original values
            _LOGGER.info("Restoring original solar settings ...")
            ok = await device.set_solar(
                boost=original.get("boost"),
                charge_always=original.get("solarChargeAlways"),
                max_grid_charge_percent=original.get("solarMaxGridChargePercent"),
            )
            _pp("set_solar (restore) result", {"success": ok})

            if not ok:
                failures.append("set_solar (restore) returned failure")
                _LOGGER.error("set_solar RESTORE FAILED — values may still be modified!")
            else:
                _LOGGER.info("Waiting %ss for API to apply changes ...", WRITE_READ_DELAY_S)
                await asyncio.sleep(WRITE_READ_DELAY_S)

                _LOGGER.info("Re-fetching device status to verify restore ...")
                after_restore = await device.get_solar()
                _pp("Solar Settings (after restore)", after_restore)

                # Verify original values are back
                restore_mismatches = {
                    k: {"expected": original[k], "actual": after_restore.get(k)}
                    for k in original
                    if after_restore.get(k) != original[k]
                }
                if restore_mismatches:
                    failures.append(f"RESTORE mismatches: {restore_mismatches}")
                    _LOGGER.error("RESTORE FAILED — mismatches: %s", restore_mismatches)
                else:
                    _LOGGER.info("RESTORE VERIFIED — all solar values back to original")
    else:
        _LOGGER.warning("Skipping set_solar — no solar data in device status")

    # -- cleanup -----------------------------------------------------------
    await device.close()

    if failures:
        _LOGGER.error("RESULT: FAIL — %d failure(s):", len(failures))
        for f in failures:
            _LOGGER.error("  • %s", f)
        return False

    _LOGGER.info("RESULT: PASS — all calls completed, all checks passed")
    return True


def main() -> None:
    """Parse arguments and run the integration test."""
    parser = argparse.ArgumentParser(description="Andersen EV manual integration test")
    parser.add_argument(
        "--email",
        default=os.environ.get("ANDERSEN_EMAIL"),
        help="Account email (or ANDERSEN_EMAIL env var)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("ANDERSEN_PASSWORD"),
        help="Account password (or ANDERSEN_PASSWORD env var)",
    )
    parser.add_argument("--device-id", default=None, help="Target a specific device ID")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password are required (via flags or ANDERSEN_EMAIL / ANDERSEN_PASSWORD env vars)")

    passed = asyncio.run(run(args.email, args.password, args.device_id))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
