"""Runtime helpers for launching the NiceGUI UI."""

import os
import sys

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def should_auto_open_browser() -> bool:
    """Return whether the UI should try to open a browser automatically."""
    override = os.getenv("VRESTO_UI_SHOW_BROWSER")
    if override:
        normalized = override.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False

    return not sys.platform.startswith("linux")
