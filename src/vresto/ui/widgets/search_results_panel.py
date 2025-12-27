"""Search results panel widget for map-based searches.

Provides UI controls for collection, product level, cloud cover and max results,
and exposes a results display column plus a trigger callback that invokes the
provided `on_search` callback with a params dictionary.
"""

from typing import Callable, Tuple

from nicegui import ui

from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
)


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
        with ui.column().classes("w-full").style("max-width: 280px; flex-shrink: 0;"):
            with ui.card().classes("w-full p-3 shadow-sm rounded-lg"):
                ui.label("Search Filters").classes("font-medium mb-2")
                collection_select = ui.select(
                    options=["SENTINEL-2", "SENTINEL-3", "LANDSAT-8"],
                    value=self.default_collection,
                    label="Collection",
                ).classes("w-full")

                # Create product level select with dynamic options
                supported_levels = COLLECTION_PRODUCT_LEVELS.get(self.default_collection, [])
                level_options = supported_levels.copy()

                product_level_select = ui.select(
                    options=level_options,
                    value=level_options[0] if level_options else "L2A",
                    label="Product Level",
                ).classes("w-full")

                # Warning/info label for unsupported levels
                info_label = ui.label("").classes("text-xs text-gray-600 mt-1")

                def update_product_levels():
                    """Update product level options based on selected collection."""
                    selected_collection = collection_select.value
                    supported = COLLECTION_PRODUCT_LEVELS.get(selected_collection, [])

                    # Build level options
                    new_options = supported.copy()

                    # Update select options
                    product_level_select.options = new_options
                    if new_options:
                        product_level_select.value = new_options[0]

                    # Update info label based on selected collection
                    if selected_collection == "SENTINEL-2":
                        info_label.text = "✓ Full support for L1C & L2A"
                        info_label.classes(remove="text-orange-600 font-semibold")
                        info_label.classes(add="text-green-600")
                    elif selected_collection == "SENTINEL-3":
                        info_label.text = "⚠️ Sentinel-3 (beta) • Includes OLCI, SLSTR, SY products"
                        info_label.classes(remove="text-green-600")
                        info_label.classes(add="text-orange-600 font-semibold")
                    elif selected_collection == "LANDSAT-8":
                        info_label.text = "⚠️ Limited support: L0, L1GT, L1GS, L1TP, L2SP (beta)"
                        info_label.classes(remove="text-green-600")
                        info_label.classes(add="text-orange-600 font-semibold")

                # Update levels when collection changes
                collection_select.on_value_change(lambda: update_product_levels())

                max_cloud_input = ui.input(value=str(int(self.default_max_cloud)), label="Max Cloud Cover (%)").classes("w-full")
                max_results_input = ui.input(value=str(self.default_max_results), label="Max Results").classes("w-full")

                with ui.row().classes("items-center gap-2 mt-2"):
                    # attach async handler directly so UI context is preserved
                    self._loading_label = ui.label("")

                    async def _on_click_e():
                        await _trigger()

                    self._search_button = ui.button("🔎 Search", on_click=_on_click_e)

                # Initialize the info label
                update_product_levels()

            # Results display area - scrollable container for search results
            with ui.card().classes("w-full flex-1 mt-4 p-3 shadow-sm rounded-lg overflow-hidden"):
                self._results_display = ui.column().classes("w-full")
                # Apply scrollable styling to the results column
                self._results_display.style("max-height: 600px; overflow-y: auto;")

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
