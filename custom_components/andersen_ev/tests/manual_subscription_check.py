#!/usr/bin/env python3
"""Manual check for the deviceStatusUpdated GraphQL subscription.

NOT a pytest test — run directly from the command line (from the
hassio-andersen-ev directory):

    python custom_components/andersen_ev/tests/manual_subscription_check.py \\
        --email you@example.com --password yourpassword

Or with environment variables:

    export ANDERSEN_EMAIL=you@example.com
    export ANDERSEN_PASSWORD=yourpassword
    python custom_components/andersen_ev/tests/manual_subscription_check.py

Prints the first payload in full, then only the fields that change, for
--duration seconds. --start-with-invalid-token sends a bad token first to
exercise the refresh-and-retry path.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time

# Ensure custom_components is on sys.path so "andersen_ev" is importable
# without loading Home Assistant.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from andersen_ev.konnect.client import KonnectClient  # noqa: E402
from andersen_ev.konnect.exceptions import AndersenError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s  %(message)s")
logging.getLogger("andersen_ev").setLevel(logging.DEBUG)
_LOGGER = logging.getLogger("subscription_check")


def _changes(old: dict, new: dict) -> dict:
    """Return the fields of new whose values differ from old."""
    return {key: {"from": old.get(key), "to": value} for key, value in new.items() if old.get(key) != value}


async def run(email: str, password: str, target_device_id: str | None, duration: int, invalid_token: bool) -> bool:
    """Authenticate, subscribe and print updates. Returns True if at least one payload arrived."""
    client = KonnectClient(email, password)
    await client.authenticate_user()
    devices = await client.getDevices()
    if not devices:
        _LOGGER.error("No devices found for this account")
        return False
    device = next((d for d in devices if d.device_id == target_device_id), devices[0])
    _LOGGER.info("Subscribing for %s (%s) for %ss", device.friendly_name, device.device_id, duration)

    if invalid_token:
        client.token = "not-a-valid-token"

    start = time.monotonic()
    last: dict = {}
    count = 0

    async def consume() -> None:
        nonlocal last, count
        async for payload in device.subscription.updates():
            count += 1
            elapsed = time.monotonic() - start
            if not last:
                print(f"\n+{elapsed:.0f}s first payload:\n{json.dumps(payload, indent=2)}")
            elif changed := _changes(last, payload):
                print(f"+{elapsed:.0f}s changed: {json.dumps(changed)}")
            else:
                print(f"+{elapsed:.0f}s no changes")
            last = payload

    try:
        await asyncio.wait_for(consume(), duration)
    except TimeoutError:
        pass
    except AndersenError as err:
        _LOGGER.error("Subscription failed after %.0fs: %r", time.monotonic() - start, err)
    finally:
        await device.close()

    _LOGGER.info("Received %d payload(s) in %.0fs", count, time.monotonic() - start)
    return count > 0


def main() -> None:
    """Parse arguments and run the subscription check."""
    parser = argparse.ArgumentParser(description="Andersen EV manual subscription check")
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
    parser.add_argument("--duration", type=int, default=180, help="Seconds to listen for (default 180)")
    parser.add_argument(
        "--start-with-invalid-token",
        action="store_true",
        help="Send an invalid token first to exercise the refresh-and-retry path",
    )
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password are required (via flags or ANDERSEN_EMAIL / ANDERSEN_PASSWORD env vars)")

    passed = asyncio.run(run(args.email, args.password, args.device_id, args.duration, args.start_with_invalid_token))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
