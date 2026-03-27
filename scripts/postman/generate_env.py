#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _values(**kwargs: str) -> list[dict[str, object]]:
    return [{"key": k, "value": v, "enabled": True} for k, v in kwargs.items()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate a Postman environment JSON for OAAS.")
    parser.add_argument("--name", default="OAAS Local", help="Environment name (default: OAAS Local)")
    parser.add_argument("--base-url", default="http://127.0.0.1:7090", help="API base URL (default: http://127.0.0.1:7090)")
    parser.add_argument("--realm", default="demo", help="Keycloak realm (default: demo)")
    parser.add_argument("--client", default="mobile", help="Client alias query param (default: mobile)")
    parser.add_argument("--tenant", default="demo", help="Tenant key for X-Tenant-ID header (default: demo)")
    parser.add_argument("--username", default="demo_super_admin", help="Login username (default: demo_super_admin)")
    parser.add_argument("--password", default="test123", help="Login password (default: test123)")
    parser.add_argument("--initialization-key", default="", help="Optional X-Initialization-Key value")
    args = parser.parse_args(argv)

    env = {
        "id": str(uuid.uuid4()),
        "name": args.name,
        "_postman_variable_scope": "environment",
        "_postman_exported_at": _utc_now_iso(),
        "_postman_exported_using": "scripts/postman/generate_env.py",
        "values": _values(
            base_url=args.base_url,
            realm=args.realm,
            client=args.client,
            tenant=args.tenant,
            username=args.username,
            password=args.password,
            initialization_key=args.initialization_key,
            access_token="",
            refresh_token="",
        ),
    }

    json.dump(env, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
