#!/usr/bin/env python3
"""Interactive setup for Copernicus credentials.

Collects Copernicus username/password and optional static S3
credentials, then writes a `.env` file at the project root with the values.

Run: `python scripts/setup_credentials.py`
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ENV_KEYS = [
    "COPERNICUS_USERNAME",
    "COPERNICUS_PASSWORD",
    "COPERNICUS_S3_ACCESS_KEY",
    "COPERNICUS_S3_SECRET_KEY",
    "COPERNICUS_ENDPOINT",
]


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    default_s = "Y/n" if default else "y/N"
    while True:
        resp = input(f"{prompt} ({default_s}): ").strip().lower()
        if not resp:
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False


def write_env_file(path: Path, data: Dict[str, str]) -> None:
    # Ensure `src` is on sys.path so we can import the package when running
    # the script directly from the project root.
    project_src = PROJECT_ROOT / "src"
    if str(project_src) not in sys.path:
        sys.path.insert(0, str(project_src))

    from vresto.api.env_loader import write_env_file as _writer

    _writer(path, data)


def main() -> None:
    print("vresto credentials setup")
    env_path = PROJECT_ROOT / ".env"

    # If .env exists and already contains either username+password or the
    # official Copernicus S3 endpoint, skip interactive setup.
    try:
        project_src = PROJECT_ROOT / "src"
        if str(project_src) not in sys.path:
            sys.path.insert(0, str(project_src))

        from vresto.api.env_loader import parse_env_file

        if env_path.exists():
            existing = parse_env_file(env_path)
            has_user = bool(existing.get("COPERNICUS_USERNAME")) and bool(existing.get("COPERNICUS_PASSWORD"))
            endpoint = existing.get("COPERNICUS_S3_ENDPOINT") or existing.get("COPERNICUS_ENDPOINT")
            has_endpoint = endpoint == "https://eodata.dataspace.copernicus.eu"
            if has_user or has_endpoint:
                print("Found existing credentials or Copernicus S3 endpoint in .env — skipping setup.")
                return
    except Exception:
        # If parsing fails for any reason, continue to interactive setup.
        pass

    creds: Dict[str, str | None] = {k: None for k in ENV_KEYS}

    print("\nPlease enter Copernicus account credentials (username/password).")
    if prompt_yes_no("Do you want to provide username/password now?", default=True):
        username = input("COPERNICUS_USERNAME: ").strip()
        password = getpass.getpass("COPERNICUS_PASSWORD: ")
        if username:
            creds["COPERNICUS_USERNAME"] = username
        if password:
            creds["COPERNICUS_PASSWORD"] = password

    print("\n⚠️  IMPORTANT: Static S3 credentials (Highly Recommended)")
    print("   Without static S3 credentials, vresto will auto-generate temporary credentials")
    print("   with strict usage limits. These will be exhausted quickly with large downloads.")
    print("   To avoid quota restrictions, request permanent S3 credentials from:")
    print("   https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration")
    if prompt_yes_no("\nDo you want to provide static S3 credentials now?", default=False):
        access = input("COPERNICUS_S3_ACCESS_KEY: ").strip()
        secret = getpass.getpass("COPERNICUS_S3_SECRET_KEY: ")
        endpoint = input("COPERNICUS_ENDPOINT (optional, e.g. https://s3.example): ").strip()
        if access:
            creds["COPERNICUS_S3_ACCESS_KEY"] = access
        if secret:
            creds["COPERNICUS_S3_SECRET_KEY"] = secret
        if endpoint:
            creds["COPERNICUS_ENDPOINT"] = endpoint

    if not any(creds.values()):
        print("No credentials provided. Exiting without creating .env.")
        return

    if env_path.exists():
        print(f"Existing .env found at {env_path}")
        if not prompt_yes_no("Overwrite existing .env?", default=False):
            print("Aborted by user. No changes made.")
            return

    write_env_file(env_path, {k: v for k, v in creds.items() if v})
    print(f"Written .env to {env_path}")

    if prompt_yes_no("Run basic credentials check using scripts/check_credentials.py if available?", default=True):
        check_script = PROJECT_ROOT / "scripts" / "check_credentials.py"
        if check_script.exists():
            print("Running credentials check...")
            os.execvpe("python", ["python", str(check_script)], os.environ)
        else:
            print("No check script found at scripts/check_credentials.py. You can run your app now.")


if __name__ == "__main__":
    main()
