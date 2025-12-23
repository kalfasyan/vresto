"""Search results panel widget for map-based searches.

Provides UI controls for collection, product level, cloud cover and max results,
and exposes a results display column plus a trigger callback that invokes the
provided `on_search` callback with a params dictionary.
"""

from typing import Callable, Tuple

from nicegui import ui


class SearchResultsPanelWidget:
    """Encapsulates search controls and results display panel."""

    def __init__(self) -> None:
        # default values mirrored from map_interface
        self.default_collection = "SENTINEL-2"
        self.default_product_level = "L2A"
        self.default_max_cloud = 30.0
        self.default_max_results = 100

        # holders for UI elements
        self._search_button = None
        self._loading_label = None
        self._results_display = None

    def create(self, messages_column, on_search: Callable[[dict], None]) -> Tuple[ui.element, Callable[[], None]]:
        """Create and return (results_display, trigger_search_callback).

        Args:
            messages_column: UI column for logging messages (used by the widget for small messages)
            on_search: Callback to invoke when search is triggered. It will be called
                       with a single dict argument containing: collection, product_level,
                       max_cloud_cover, max_results and messages_column and results_display.

        Returns:
            Tuple of (results_display column, trigger_search callback)
        """
        with ui.column().classes("w-96"):
            with ui.card().classes("w-full p-3 shadow-sm rounded-lg"):
                ui.label("Search Filters").classes("font-medium mb-2")
                collection_select = ui.select(
                    options=["SENTINEL-2", "SENTINEL-3", "LANDSAT-8"],
                    value=self.default_collection,
                    label="Collection",
                )
                product_level_select = ui.select(
                    options=["L1C", "L2A", "L1C + L2A"],
                    value=self.default_product_level,
                    label="Product Level",
                )
                max_cloud_input = ui.input(value=str(int(self.default_max_cloud)), label="Max Cloud Cover (%)")
                max_results_input = ui.input(value=str(self.default_max_results), label="Max Results")

                with ui.row().classes("items-center gap-2 mt-2"):
                    # attach async handler directly so UI context is preserved
                    self._loading_label = ui.label("")

                    async def _on_click_e():
                        await _trigger()

                    self._search_button = ui.button("🔎 Search", on_click=_on_click_e)

            # Results display area
            with ui.card().classes("w-full flex-1 mt-4 p-3 shadow-sm rounded-lg"):
                self._results_display = ui.column()

        async def _trigger():
            # prepare params dict
            try:
                max_cloud = float(max_cloud_input.value) if max_cloud_input.value else None
            except Exception:
                max_cloud = None

            try:
                max_results = int(max_results_input.value) if max_results_input.value else None
            except Exception:
                max_results = None

            params = {
                "collection": collection_select.value,
                "product_level": product_level_select.value,
                "max_cloud_cover": max_cloud,
                "max_results": max_results,
                # expose UI handles so the search implementation can optionally update state
                "messages_column": messages_column,
                "results_display": self._results_display,
                # provide access to button/label for compatibility
                "_search_button": self._search_button,
                "_loading_label": self._loading_label,
            }

            # set loading state on the button while callback runs (callback may be async)
            try:
                self._search_button.enabled = False
                orig_text = getattr(self._search_button, "text", "🔎 Search")
                self._search_button.text = "⏳ Searching..."
                self._loading_label.text = "⏳ Searching..."
            except Exception:
                orig_text = None

            # Call the provided on_search. It may be async or sync. Await it here
            try:
                result = on_search(params)
                if hasattr(result, "__await__"):
                    await result
            finally:
                try:
                    self._search_button.enabled = True
                    if orig_text is not None:
                        self._search_button.text = orig_text
                    self._loading_label.text = ""
                except Exception:
                    pass

        return self._results_display, _trigger
