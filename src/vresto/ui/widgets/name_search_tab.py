"""Name-based product search tab widget."""

import asyncio
from datetime import datetime
from typing import Callable, Optional

from loguru import logger
from nicegui import ui

from vresto.api import CatalogSearch
from vresto.products.product_name import ProductName
from vresto.ui.widgets.activity_log import ActivityLogWidget


class NameSearchTab:
    """Encapsulates the Name Search tab for finding products by name pattern.

    Usage:
        tab_widget = NameSearchTab(
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
        """Initialize the Name Search tab.

        Args:
            on_quicklook: Callback(product, messages_column) for quicklook requests
            on_metadata: Callback(product, messages_column) for metadata requests
        """
        self.on_quicklook = on_quicklook or (lambda p, col: None)
        self.on_metadata = on_metadata or (lambda p, col: None)

        # State
        self.current_state = {
            "products": [],
        }

        # UI elements
        self.messages_column = None
        self.results_display = None
        self.search_button = None
        self.loading_label = None
        self.name_input = None
        self.max_results_input = None

    def create(self):
        """Create and return the Name Search tab UI."""
        with ui.row().classes("w-full gap-6"):
            # Left sidebar: search filters
            self._create_sidebar()

            # Right area: results display
            self._create_results_panel()

        return {
            "messages_column": self.messages_column,
            "results": self.results_display,
            "state": self.current_state,
        }

    def _create_sidebar(self):
        """Create the left sidebar with product name input and filters."""
        with ui.column().classes("w-80"):
            # Product name search card
            with ui.card().classes("w-full"):
                ui.label("Search by Product Name").classes("text-lg font-semibold mb-3")

                # Product name input
                self.name_input = ui.input(
                    label="Product Name",
                    placeholder="e.g., S2A_MSIL2A_20201212T235129_...",
                ).classes("w-full mb-3")
                self.name_input.tooltip("Enter the full product name — everything needed is in the name")

                # Search button
                async def _on_search_click():
                    await self._perform_search()

                self.search_button = ui.button("🔍 Search by Name", on_click=_on_search_click).classes("w-full")
                self.search_button.props("color=primary")

                # Loading indicator label
                self.loading_label = ui.label("").classes("text-sm text-blue-600 mt-2 font-medium")

                # Max results for name search (user-configurable)
                self.max_results_input = ui.number(label="Max Results", value=100, min=1, max=500, step=10).classes("w-full mt-2 mb-1")
                self.max_results_input.tooltip("Maximum number of results returned by the server for name searches")

            # Activity log card
            activity_log = ActivityLogWidget(title="Activity Log")
            self.messages_column = activity_log.create()

    def _create_results_panel(self):
        """Create the results panel for name-based search."""
        with ui.column().classes("flex-1"):
            with ui.card().classes("w-full flex-1 mt-4"):
                ui.label("Results").classes("text-lg font-semibold mb-3")
                with ui.scroll_area().classes("w-full h-96"):
                    self.results_display = ui.column().classes("w-full gap-2")

    async def _perform_search(self):
        """Perform product name search."""

        def add_message(text: str):
            """Add a message to the activity log."""
            if self.messages_column:
                with self.messages_column:
                    ui.label(text).classes("text-sm text-gray-700 break-words")

        # Validate that we have a product name
        if not self.name_input.value or not self.name_input.value.strip():
            ui.notify(
                "⚠️ Please enter a product name or pattern",
                position="top",
                type="warning",
            )
            add_message("⚠️ Search failed: No product name entered")
            return

        # Respect max_results input
        try:
            max_results = int(self.max_results_input.value)
        except Exception:
            max_results = 100

        # Heuristic: detect overly-generic patterns and warn
        name_trim = self.name_input.value.strip()
        generic = False
        try:
            if len(name_trim) < 6:
                generic = True
            if name_trim.upper().endswith("_") and name_trim.upper().startswith(("S2A_", "S2B_", "S1A_", "S1B_")):
                generic = True
            if name_trim.upper() in ("S2A", "S2B", "S2", "S1", "S1A", "S1B"):
                generic = True
        except Exception:
            generic = False

        if generic:
            ui.notify(
                "⚠️ This looks like a very generic product pattern — results may be large.",
                position="top",
                type="warning",
            )
            add_message("⚠️ Detected generic name search; limiting results to avoid UI timeout")
            max_results = min(max_results, 200)

        # Try to parse product name for helpful filters
        name_pattern = self.name_input.value.strip()
        collection = None
        product_level = None
        parsed_acq_date = None

        try:
            pn = ProductName(name_pattern)
            product_level = pn.product_level
            # Guess collection from product type
            if pn.product_type == "S2":
                collection = "SENTINEL-2"
            elif pn.product_type == "S1":
                collection = "SENTINEL-1"
            elif pn.product_type == "S5P":
                collection = "SENTINEL-5P"

            if pn.acquisition_datetime and len(pn.acquisition_datetime) >= 8:
                parsed_acq_date = pn.acquisition_datetime[:8]
        except Exception:
            pn = None

        # Show loading message and disable button
        ui.notify(
            f"🔍 Searching products for '{name_pattern}' (using OData for performance)...",
            position="top",
            type="info",
        )
        add_message(f"🔍 Searching products for name: '{name_pattern}' (parsed collection={collection}, level={product_level})")

        # Disable search button and show loading state
        self.search_button.enabled = False
        original_text = self.search_button.text
        self.search_button.text = "⏳ Searching..."
        self.loading_label.text = "⏳ Searching..."

        # Clear previous results
        self.results_display.clear()
        with self.results_display:
            ui.spinner(size="lg")
            ui.label("Searching...").classes("text-gray-600")

        # Allow UI to render before starting the blocking search
        await asyncio.sleep(0.1)

        try:
            # Perform name-based search using catalog API
            catalog = CatalogSearch()

            # Normalize pattern: remove wildcard characters
            raw_pattern = name_pattern.strip()
            pattern = raw_pattern.replace("*", "")

            # Heuristic: use exact match when the provided string looks like a full product name
            looks_exact = False
            try:
                if len(pattern) > 30 and ("MSIL" in pattern or "_MSI" in pattern) and "T" in pattern:
                    looks_exact = True
            except Exception:
                looks_exact = False

            match_type = "eq" if looks_exact else "contains"

            products = []
            try:
                if match_type == "eq":
                    products = catalog.search_products_by_name(pattern, match_type="eq", max_results=max_results)
                    if not products:
                        logger.info("Exact name search returned 0 results; trying exact with '.SAFE' suffix")
                        try:
                            products = catalog.search_products_by_name(
                                f"{pattern}.SAFE",
                                match_type="eq",
                                max_results=max_results,
                            )
                        except Exception:
                            logger.exception("Exact '.SAFE' name search failed")

                    if not products:
                        logger.info("Exact and '.SAFE' search returned 0 results; falling back to contains")
                        try:
                            products = catalog.search_products_by_name(
                                pattern,
                                match_type="contains",
                                max_results=max(max_results, 100),
                            )
                        except Exception:
                            logger.exception("Fallback contains name search failed")
                else:
                    products = catalog.search_products_by_name(pattern, match_type=match_type, max_results=max_results)
            except Exception:
                logger.exception("Name-based search failed; falling back to empty result list")

            # If we parsed an acquisition date, use it as a single-day filter
            start_date = ""
            end_date = ""
            if parsed_acq_date:
                try:
                    sd = f"{parsed_acq_date[0:4]}-{parsed_acq_date[4:6]}-{parsed_acq_date[6:8]}"
                    start_date = sd
                    end_date = sd
                    add_message(f"ℹ️ Using date from product name: {sd}")
                except Exception:
                    start_date = ""
                    end_date = ""

            logger.info(f"Name search (server) returned {len(products)} products for pattern '{pattern}' (match_type={match_type})")

            # If server returned a lot of results, warn the user
            SERVER_TOO_MANY = 500
            if len(products) > SERVER_TOO_MANY:
                ui.notify(
                    f"⚠️ Server returned {len(products)} products — this may be slow. Showing first {max_results}.",
                    position="top",
                    type="warning",
                )
                add_message(f"⚠️ Server returned {len(products)} products; truncated to first {max_results} for UI responsiveness")
                products = products[:max_results]

            # Apply client-side filters
            filtered_products = self._apply_client_filters(products, start_date, end_date, product_level)
            self.current_state["products"] = filtered_products

            # Display results
            self.results_display.clear()

            # Inform user about server-return and client-side filtering
            with self.results_display:
                ui.label(f"Server returned {len(products)} products; {len(filtered_products)} match after client-side filters").classes("text-sm text-gray-600 mb-2")

            if not filtered_products:
                with self.results_display:
                    ui.label("No products found matching the criteria").classes("text-gray-500 italic mt-2")
                ui.notify("No products found", position="top", type="warning")
                add_message("❌ No products found matching the search criteria")
            else:
                with self.results_display:
                    ui.label(f"Found {len(filtered_products)} products").classes("text-sm font-semibold text-green-600 mb-2")

                    for i, product in enumerate(filtered_products, 1):
                        self._create_product_card(self.results_display, i, product, self.messages_column)
                        # Yield to event loop periodically
                        if i % 20 == 0:
                            await asyncio.sleep(0)

                ui.notify(
                    f"✅ Found {len(filtered_products)} products",
                    position="top",
                    type="positive",
                )
                add_message(f"✅ Found {len(filtered_products)} products matching '{name_pattern}'")
                logger.info(f"Name search completed: {len(filtered_products)} products found")

        except Exception as e:
            logger.error(f"Name search failed: {e}")
            self.results_display.clear()
            with self.results_display:
                ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
            ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
            add_message(f"❌ Search error: {str(e)}")

        finally:
            # Ensure the search button and loading label are always reset
            try:
                self.search_button.enabled = True
                self.search_button.text = original_text
                self.loading_label.text = ""
            except Exception:
                pass

    def _apply_client_filters(self, products: list, start_date: str, end_date: str, product_level: Optional[str]) -> list:
        """Apply client-side filters to products.

        Args:
            products: List of ProductInfo objects from server
            start_date: Start date for filtering (YYYY-MM-DD)
            end_date: End date for filtering (YYYY-MM-DD)
            product_level: Product level to filter by

        Returns:
            Filtered list of products
        """
        filtered_products = []

        for p in products:
            try:
                reason = None

                # Date filter
                if start_date:
                    try:
                        sensed = p.sensing_date
                        if sensed:
                            dt = datetime.strptime(sensed, "%Y-%m-%d %H:%M:%S")
                            dt_date = dt.date()
                            sd = datetime.fromisoformat(start_date).date()
                            ed = datetime.fromisoformat(end_date).date() if end_date else sd
                            if not (sd <= dt_date <= ed):
                                reason = f"date {dt_date} outside {sd}–{ed}"
                    except Exception:
                        pass

                # Product level filter
                if reason is None and product_level:
                    try:
                        if product_level not in p.name:
                            reason = f"level not {product_level}"
                    except Exception:
                        pass

                if reason is None:
                    filtered_products.append(p)
            except Exception:
                logger.exception("Error while applying client-side filters; skipping product")

        return filtered_products

    def _create_product_card(self, container, index: int, product, messages_column):
        """Create a product result card with quicklook/metadata buttons."""
        with container:
            with ui.card().classes("w-full p-2 bg-gray-50"):
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
