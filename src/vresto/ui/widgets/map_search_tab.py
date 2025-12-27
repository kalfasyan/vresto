"""Map search tab widget combining map, date picker, and search controls."""

import asyncio
from typing import Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api import BoundingBox, CatalogSearch
from vresto.api.product_level_config import (
    COLLECTION_PRODUCT_LEVELS,
)
from vresto.ui.widgets.activity_log import ActivityLogWidget
from vresto.ui.widgets.date_picker import DatePickerWidget
from vresto.ui.widgets.map_widget import MapWidget
from vresto.ui.widgets.search_results_panel import SearchResultsPanelWidget


class MapSearchTab:
    """Encapsulates the Map Search tab with date picker, interactive map, and search controls.

    Usage:
        tab_widget = MapSearchTab(
            on_quicklook=lambda p, col: _show_quicklook(p, col),
            on_metadata=lambda p, col: _show_metadata(p, col),
        )
        tab_content = tab_widget.create()
    """

    def __init__(
        self,
        on_quicklook: Optional[Callable] = None,
        on_metadata: Optional[Callable] = None,
    ):
        """Initialize the Map Search tab.

        Args:
            on_quicklook: Callback(product, messages_column) for quicklook requests
            on_metadata: Callback(product, messages_column) for metadata requests
        """
        self.on_quicklook = on_quicklook or (lambda p, col: None)
        self.on_metadata = on_metadata or (lambda p, col: None)

        # State
        self.current_state = {
            "bbox": None,
            "date_range": {"from": "2020-01-01", "to": "2020-01-31"},
            "products": [],
        }

        # UI elements
        self.messages_column = None
        self.map_widget = None
        self.results_display = None
        self.date_picker = None

    def create(self):
        """Create and return the Map Search tab UI."""
        with ui.row().classes("w-full gap-6"):
            # Left sidebar: Date picker and activity log
            self._create_sidebar()

            # Map with draw controls
            map_widget_obj = MapWidget(
                center=(50.0, 10.0),
                zoom=5,
                on_bbox_update=lambda bbox: self.current_state.update({"bbox": bbox}),
            )
            self.map_widget = map_widget_obj.create(self.messages_column)

            # Right sidebar: Search controls and results
            search_panel = SearchResultsPanelWidget()
            self.results_display, trigger_search = search_panel.create(
                messages_column=self.messages_column,
                on_search=self._handle_search,
            )

        return {
            "messages_column": self.messages_column,
            "map": self.map_widget,
            "results": self.results_display,
            "state": self.current_state,
        }

    def _create_sidebar(self):
        """Create the left sidebar with date picker and activity log."""
        with ui.column().classes("w-80"):
            # Date picker with callback for date range updates
            picker_widget = DatePickerWidget(
                default_from="2020-01-01",
                default_to="2020-01-31",
                on_date_change=self._on_date_change,
            )
            self.date_picker, date_display = picker_widget.create()

            # Activity log
            activity_log = ActivityLogWidget(title="Activity Log")
            self.messages_column = activity_log.create()

        # Setup date monitoring with the now-existing messages_column
        picker_widget.setup_monitoring(self.date_picker, date_display, self.messages_column)

    def _on_date_change(self, start_date: str, end_date: str):
        """Handle date range changes from the date picker."""
        self.current_state["date_range"] = {"from": start_date, "to": end_date}

    async def _handle_search(self, params: dict):
        """Handle the search action.

        Args:
            params: Dict with keys: collection, product_level, max_cloud_cover, max_results
        """

        def add_message(text: str):
            """Add a message to the activity log."""
            if self.messages_column:
                with self.messages_column:
                    ui.label(text).classes("text-sm text-gray-700 break-words")

        # Validate inputs
        if self.current_state["bbox"] is None:
            ui.notify(
                "⚠️ Please drop a pin (or draw) a location on the map first",
                position="top",
                type="warning",
            )
            add_message("⚠️ Search failed: No location selected")
            return

        if self.current_state["date_range"] is None:
            ui.notify(
                "⚠️ Please select a date range",
                position="top",
                type="warning",
            )
            add_message("⚠️ Search failed: No date range selected")
            return

        # Extract parameters
        date_range = self.current_state["date_range"]
        start_date = date_range.get("from", "")
        end_date = date_range.get("to", start_date)

        collection = params.get("collection")
        product_level = params.get("product_level")
        max_cloud_cover = params.get("max_cloud_cover")
        max_results = params.get("max_results")

        # Validate product level support
        supported_levels = COLLECTION_PRODUCT_LEVELS.get(collection, [])

        if product_level not in supported_levels:
            warning_msg = f"⚠️ {collection} does not support product level: {product_level}. Supported levels: {', '.join(supported_levels)}"
            ui.notify(
                warning_msg,
                position="top",
                type="warning",
            )
            add_message(warning_msg)

        ui.notify(
            f"🔍 Searching {collection} products ({product_level})...",
            position="top",
            type="info",
        )
        add_message(f"🔍 Searching {collection} products ({product_level}) for {start_date} to {end_date}")

        # Clear results
        results_display = params.get("results_display")
        if results_display:
            results_display.clear()
            with results_display:
                ui.spinner(size="lg")
                ui.label("Searching...").classes("text-gray-600")

        await asyncio.sleep(0.1)

        try:
            # Perform search
            catalog = CatalogSearch()
            bbox = self.current_state["bbox"]

            # Convert bbox if needed
            try:
                if isinstance(bbox, (tuple, list)):
                    min_lon, min_lat, max_lon, max_lat = bbox
                    bbox = BoundingBox(west=min_lon, south=min_lat, east=max_lon, north=max_lat)
            except Exception:
                logger.exception("Failed to coerce bbox into BoundingBox")

            products = catalog.search_products(
                bbox=bbox,
                start_date=start_date,
                end_date=end_date,
                collection=collection,
                max_cloud_cover=max_cloud_cover if collection in ["SENTINEL-2", "SENTINEL-3"] else None,
                max_results=int(max_results),
                product_level=product_level,
            )

            # Filter by product level
            filtered_products = self._filter_by_level(products, product_level, collection)
            self.current_state["products"] = filtered_products

            # Display results
            if results_display:
                results_display.clear()

                if not filtered_products:
                    with results_display:
                        ui.label("No products found with selected level").classes("text-gray-500 italic")
                    ui.notify(
                        "No products found with selected level",
                        position="top",
                        type="warning",
                    )
                    add_message("❌ No products found with selected level")
                else:
                    with results_display:
                        ui.label(f"Found {len(filtered_products)} products (filtered from {len(products)} total)").classes("text-sm font-semibold text-green-600 mb-2")

                        for i, product in enumerate(filtered_products, 1):
                            self._create_product_card(results_display, i, product, self.messages_column)

                    ui.notify(
                        f"✅ Found {len(filtered_products)} products",
                        position="top",
                        type="positive",
                    )
                    add_message(f"✅ Found {len(filtered_products)} products (from {len(products)} total)")
                    logger.info(f"Search completed: {len(filtered_products)} products found (filtered from {len(products)})")

        except Exception as e:
            logger.error(f"Search failed: {e}")
            if results_display:
                results_display.clear()
                with results_display:
                    ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
            ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
            add_message(f"❌ Search error: {str(e)}")

    def _filter_by_level(self, products: list, level_filter: str, collection: str = "") -> list:
        """Filter products by processing level.

        Args:
            products: List of ProductInfo objects
            level_filter: "L1C", "L2A", etc.
            collection: Collection name (to handle different naming conventions)

        Returns:
            Filtered list of ProductInfo objects
        """
        # For Sentinel-3, skip client-side filtering due to different naming conventions
        # (e.g., S3A_OL_1_EFR vs L1)
        # Server-side filtering was skipped for Sentinel-3, so return all products as-is
        if collection == "SENTINEL-3":
            return products

        filtered = []
        for product in products:
            if level_filter in product.name:
                filtered.append(product)

        return filtered

    def _create_product_card(self, container, index: int, product, messages_column):
        """Create a product result card with quicklook/metadata buttons."""
        with container:
            with ui.card().classes("w-full p-3 bg-gray-50 shadow-sm rounded-md"):
                ui.label(f"{index}. {getattr(product, 'display_name', product.name)}").classes("text-xs font-mono break-all")
                ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600")
                ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600")
                if product.cloud_cover is not None:
                    ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600")

                # Buttons for quicklook and metadata
                with ui.row().classes("w-full gap-2 mt-2"):
                    ui.button(
                        "🖼️ Quicklook",
                        on_click=lambda p=product: self.on_quicklook(p, messages_column),
                    ).classes("text-xs flex-1")
                    ui.button(
                        "📋 Metadata",
                        on_click=lambda p=product: self.on_metadata(p, messages_column),
                    ).classes("text-xs flex-1")
