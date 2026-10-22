#!/usr/bin/env python3
"""Manual check for the Andersen EV REST getDevice endpoint.

NOT a pytest test — run directly from the command line (from the
hassio-andersen-ev directory):

    python custom_components/andersen_ev/tests/manual_get_device_check.py \\
        --email you@example.com --password yourpassword

Or with environment variables:

    export ANDERSEN_EMAIL=you@example.com
    export ANDERSEN_PASSWORD=yourpassword
    python custom_components/andersen_ev/tests/manual_get_device_check.py

Flow:
    1. Authenticate via Cognito (KonnectClient) to get a Bearer IdToken.
    2. GET {base}/getDevices to discover device IDs.
    3. GET {base}/getDevice?deviceId=<id> and dump the full response,
       highlighting data.userDevice.info / data.userDevice.status as read
       by the mobile app.

The production base URL is tried first; staging is only tried if prod fails
(or always, with --also-staging).
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import requests

# Ensure custom_components is on sys.path so "andersen_ev" is importable
# without loading Home Assistant.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from andersen_ev.konnect.client import KonnectClient  # noqa: E402

PROD_BASE_URL = "https://mobile.andersen-ev.com/api"
STAGING_BASE_URL = "https://mobile-staging.andersen-ev.com/api"
REQUEST_TIMEOUT_S = 30

# (section, key) pairs under data.userDevice to log individually at the end.
KEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("info", "ChargeOutsideSchedules"),
    ("status", "evseState"),
    ("status", "sysUserMaxChargeAmpSet"),
    ("status", "evseChargeAmpCurrentLimit"),
    ("status", "cfgChargeAmpMaxDSO"),
    ("status", "sysProductName"),
    ("status", "cfgChargeAmpMin"),
    ("status", "cfgChargeAmpMax"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
_LOGGER = logging.getLogger("get_device_check")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)


def _pp(label: str, data: object) -> None:
    """Pretty-print a result block."""
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print("=" * 60)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(data)


def _get(url: str, token: str, params: dict[str, str] | None = None) -> requests.Response:
    """Authenticated GET against the mobile REST API."""
    return requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_S,
    )


def _body(response: requests.Response) -> object:
    """Return the JSON body if possible, otherwise the raw text."""
    try:
        return response.json()
    except ValueError:
        return response.text


def fetch_device_ids(base_url: str, token: str) -> list[str]:
    """Call getDevices on the given environment and return the device IDs."""
    url = f"{base_url}/getDevices"
    _LOGGER.info("GET %s", url)
    response = _get(url, token)
    body = _body(response)
    _pp(f"getDevices [{response.status_code}] {url}", body)
    if response.status_code != 200 or not isinstance(body, dict):
        return []
    # Prod returns flat entries ({"id": ...}); staging nests them ({"device": {"id": ...}}).
    ids = []
    for entry in body.get("devices", []):
        device = entry.get("device", entry)
        if "id" in device:
            ids.append(str(device["id"]))
    return ids


def fetch_device(base_url: str, token: str, device_id: str) -> tuple[bool, dict | None]:
    """Call getDevice for one device and dump the response.

    Returns (ok, userDevice): ok is True if the call returned HTTP 200 with a
    JSON body; userDevice is the parsed data.userDevice object if present.
    """
    url = f"{base_url}/getDevice"
    _LOGGER.info("GET %s?deviceId=%s", url, device_id)
    try:
        response = _get(url, token, params={"deviceId": device_id})
    except requests.RequestException as err:
        _LOGGER.error("Request to %s failed: %s", url, err)
        return False, None

    body = _body(response)
    _pp(f"getDevice [{response.status_code}] {response.url}", body)

    if response.status_code != 200 or not isinstance(body, dict):
        _LOGGER.error("getDevice failed on %s (HTTP %s)", base_url, response.status_code)
        return False, None

    # The app reads data.userDevice; tolerate the key being at the top level too.
    user_device = (body.get("data") or body).get("userDevice")
    if not isinstance(user_device, dict):
        _LOGGER.warning("Response has no data.userDevice — see full body above")
        return True, None

    _LOGGER.info("userDevice keys: %s", sorted(user_device))
    for section in ("info", "status"):
        if section in user_device:
            _pp(f"userDevice.{section}", user_device[section])
        else:
            _LOGGER.warning("userDevice has no '%s' object", section)
    return True, user_device


def log_key_fields(env_name: str, device_id: str, user_device: dict) -> None:
    """Log each of KEY_FIELDS on its own line."""
    _LOGGER.info("---- Key fields: %s device %s ----", env_name, device_id)
    for section, key in KEY_FIELDS:
        section_data = user_device.get(section)
        if not isinstance(section_data, dict):
            _LOGGER.warning("device.%s.%s = <no '%s' object>", section, key, section)
        elif key not in section_data:
            _LOGGER.warning("device.%s.%s = <missing>", section, key)
        else:
            _LOGGER.info("device.%s.%s = %r", section, key, section_data[key])


async def run(
    email: str,
    password: str,
    target_device_id: str | None,
    also_staging: bool,
    out_file: str | None,
) -> bool:
    """Authenticate, discover devices and call getDevice. Returns True on success."""
    _LOGGER.info("Authenticating as %s ...", email)
    client = KonnectClient(email, password)
    await client.authenticate_user()
    if (token := client.token) is None:
        _LOGGER.error("Authentication returned no token")
        return False
    _LOGGER.info("Authenticated — token expires in ~%ss", client.token_expires_in)

    environments = [("prod", PROD_BASE_URL), ("staging", STAGING_BASE_URL)]
    results: dict[str, bool] = {}
    devices: list[tuple[str, str, dict]] = []

    for env_name, base_url in environments:
        if env_name == "staging" and results.get("prod") and not also_staging:
            break

        _LOGGER.info("------------------------")
        _LOGGER.info("---- Environment: %s (%s) ----", env_name, base_url)
        _LOGGER.info("------------------------")

        try:
            device_ids = await asyncio.to_thread(fetch_device_ids, base_url, token)
        except requests.RequestException as err:
            _LOGGER.error("getDevices on %s failed: %s", env_name, err)
            device_ids = []

        if target_device_id:
            device_ids = [target_device_id]
        if not device_ids:
            _LOGGER.error("No device IDs available for %s", env_name)
            results[env_name] = False
            continue

        ok = True
        for device_id in device_ids:
            device_ok, user_device = await asyncio.to_thread(fetch_device, base_url, token, device_id)
            ok &= device_ok
            if user_device is not None:
                devices.append((env_name, device_id, user_device))
        results[env_name] = ok

        if out_file and ok:
            # Re-fetch the first device raw so the file holds exactly what the API returned.
            response = await asyncio.to_thread(_get, f"{base_url}/getDevice", token, {"deviceId": device_ids[0]})
            path = out_file if env_name == "prod" else f"{out_file}.{env_name}"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(_body(response), fh, indent=2, default=str)
            _LOGGER.info("Saved raw getDevice response to %s", path)

    _pp("Summary", {env: "OK" if ok else "FAILED" for env, ok in results.items()})
    for env_name, device_id, user_device in devices:
        log_key_fields(env_name, device_id, user_device)
    return any(results.values())


def main() -> None:
    """Parse arguments and run the check."""
    parser = argparse.ArgumentParser(description="Andersen EV getDevice REST check")
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
    parser.add_argument("--device-id", default=None, help="Skip discovery and query this device ID")
    parser.add_argument(
        "--also-staging",
        action="store_true",
        help="Query staging even if prod succeeds",
    )
    parser.add_argument("--out", default=None, help="Write the raw getDevice JSON to this file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password are required (via flags or ANDERSEN_EMAIL / ANDERSEN_PASSWORD env vars)")
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    passed = asyncio.run(run(args.email, args.password, args.device_id, args.also_staging, args.out))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
