"""Small helpers for reading boolean settings from environment variables."""

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str) -> bool:
    """Return True when the environment variable ``name`` is set to a true-like value (1, true, yes, on)."""
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def public_mode_enabled() -> bool:
    """Return True when vresto runs as a shared public website (``VRESTO_PUBLIC_MODE``).

    In public mode, credentials are kept per browser tab instead of in ``.env``, downloads are
    sent to the visitor's browser instead of the server's disk, tabs that browse the server's
    disk are hidden, and map tiles are served through the main web port.
    """
    return env_flag("VRESTO_PUBLIC_MODE")


def tile_proxy_enabled() -> bool:
    """Return True when map tiles should be served through the main web port (``VRESTO_TILE_PROXY``, on in public mode)."""
    return env_flag("VRESTO_TILE_PROXY") or public_mode_enabled()
