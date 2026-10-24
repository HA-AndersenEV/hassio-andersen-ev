#!/usr/bin/env python3
"""Regenerate schema.graphql by introspecting the Andersen mobile GraphQL endpoint.

NOT a pytest test — run directly from the command line (from the
hassio-andersen-ev directory):

    python custom_components/andersen_ev/tests/manual_introspection_check.py \\
        --email you@example.com --password yourpassword

Or with environment variables:

    export ANDERSEN_EMAIL=you@example.com
    export ANDERSEN_PASSWORD=yourpassword
    python custom_components/andersen_ev/tests/manual_introspection_check.py

Writes the schema to custom_components/andersen_ev/schema.graphql unless
--output is given. Review the diff before committing: the integration's DSL
calls (e.g. setSolar) depend on the type names in it.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from graphql import build_client_schema, get_introspection_query, print_schema

# Ensure custom_components is on sys.path so "andersen_ev" is importable
# without loading Home Assistant.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from andersen_ev.konnect import const  # noqa: E402
from andersen_ev.konnect.client import KonnectClient  # noqa: E402

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "schema.graphql"


async def run(email: str, password: str, output: Path) -> None:
    """Authenticate, introspect and write the schema as SDL."""
    client = KonnectClient(email, password)
    await client.authenticate_user()
    transport = AIOHTTPTransport(url=const.GRAPHQL_URL, headers={"Authorization": f"Bearer {client.token}"})
    async with Client(transport=transport, fetch_schema_from_transport=False) as session:
        result = await session.execute(gql(get_introspection_query(descriptions=True)))
    schema = build_client_schema(result)
    output.write_text(print_schema(schema) + "\n")
    print(f"Wrote {len(schema.type_map)} types to {output}")


def main() -> None:
    """Parse arguments and run the introspection."""
    parser = argparse.ArgumentParser(description="Regenerate schema.graphql from the live API")
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
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="Where to write the schema")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Email and password are required (via flags or ANDERSEN_EMAIL / ANDERSEN_PASSWORD env vars)")

    asyncio.run(run(args.email, args.password, args.output))


if __name__ == "__main__":
    main()
