"""Patch helpers shared by the UI tests."""

from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

# MapSearchTab and the mixins it is built from each import NiceGUI's ``ui``.
MAP_SEARCH_UI_MODULES = ("map_search_tab", "map_search_overlays", "map_search_density", "map_search_tiles")


@contextmanager
def patch_map_search_ui(mock=None):
    """Replace ``ui`` with ``mock`` (a fresh MagicMock by default) in every Map Search tab module."""
    mock = MagicMock() if mock is None else mock
    with ExitStack() as stack:
        for module in MAP_SEARCH_UI_MODULES:
            stack.enter_context(patch(f"vresto.ui.widgets.{module}.ui", mock))
        yield mock
