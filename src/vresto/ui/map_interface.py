"""Map interface with date range selection and marker drawing capabilities."""

from datetime import datetime

from loguru import logger
from nicegui import events, ui

from vresto.api import BoundingBox, CatalogSearch
from vresto.products import ProductsManager

# Global state for current selection
current_state = {"bbox": None, "date_range": None, "products": [], "selected_product": None}


def create_map_interface():
    """Create a beautiful interface with date range selection and interactive map."""
    # Header
    ui.label("Sentinel Browser").classes("text-3xl font-bold mb-6")

    # Create tab headers
    with ui.tabs().classes("w-full") as tabs:
        map_tab = ui.tab("Map Search", icon="map")
        name_tab = ui.tab("Search by Name", icon="search")
        download_tab = ui.tab("Download Product", icon="download")

    # Create tab content panels with full separation
    # We'll capture key UI components so callers/tests can inspect them
    date_picker = None
    messages_column = None
    map_widget = None
    results_display = None
    name_search_filters = None
    name_results_display = None

    with ui.tab_panels(tabs, value=map_tab).classes("w-full"):
        with ui.tab_panel(map_tab):
            # Map search tab content
            with ui.row().classes("w-full gap-6"):
                # Left sidebar: Date picker and activity log
                date_picker, messages_column = _create_sidebar()

                # Map with draw controls
                map_widget = _create_map(messages_column)

                # Right sidebar: Search controls and results
                results_display = _create_results_panel(messages_column)

        with ui.tab_panel(name_tab):
            # Name search tab content
            with ui.row().classes("w-full gap-6"):
                # Left sidebar with search filters
                name_search_filters = _create_name_search_sidebar()

                # Results panel
                name_results_display = _create_name_search_results_panel(name_search_filters)

        with ui.tab_panel(download_tab):
            # Download product tab content
            _create_download_tab()

    return {
        "tabs": tabs,
        "date_picker": date_picker,
        "messages_column": messages_column,
        "map": map_widget,
        "results_display": results_display,
        "name_search_filters": name_search_filters,
        "name_results_display": name_results_display,
    }


def _create_sidebar():
    """Create the left sidebar with date picker and activity log."""
    with ui.column().classes("w-80"):
        # Date picker card
        date_picker, date_display = _create_date_picker()

        # Activity log card
        messages_column = _create_activity_log()

    # Set up date monitoring
    _setup_date_monitoring(date_picker, date_display, messages_column)

    return date_picker, messages_column


def _create_date_picker():
    """Create the date picker component."""
    with ui.card().classes("w-full"):
        ui.label("Select date (or range)").classes("text-lg font-semibold mb-1")

        # Default to July 2020 (whole month)
        date_from = "2020-07-01"
        date_to = "2020-07-31"

        # Set initial value as a dict for range mode
        date_picker = ui.date(value={"from": date_from, "to": date_to}).props("range")
        date_picker.classes("w-full")

        date_display = ui.label("").classes("text-sm text-blue-600 mt-3 font-medium")

    # Store initial date in global state
    current_state["date_range"] = {"from": date_from, "to": date_to}

    return date_picker, date_display


def _create_activity_log():
    """Create the activity log panel."""
    with ui.card().classes("w-full flex-1"):
        ui.label("Activity Log").classes("text-lg font-semibold mb-3")

        with ui.scroll_area().classes("w-full h-96"):
            messages_column = ui.column().classes("w-full gap-2")

    return messages_column


def _setup_date_monitoring(date_picker, date_display, messages_column):
    """Set up monitoring and logging for date changes."""
    last_logged = {"value": None}

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    def check_date_change():
        """Check if date has changed and log it."""
        current_value = date_picker.value

        # Format date for display and comparison
        if isinstance(current_value, dict):
            value_str = f"{current_value.get('from', '')}-{current_value.get('to', '')}"
            start = current_value.get("from", "")
            end = current_value.get("to", "")
            date_display.text = f"📅 {start} to {end}"
            message = f"📅 Date range selected: {start} to {end}"
            # Update global state
            current_state["date_range"] = current_value
        else:
            value_str = str(current_value)
            date_display.text = f"📅 {current_value}"
            message = f"📅 Date selected: {current_value}"
            # Update global state
            current_state["date_range"] = {"from": current_value, "to": current_value}

        # Log only if value has changed
        if value_str != last_logged["value"]:
            last_logged["value"] = value_str
            logger.info(message)
            add_message(message)

    # Initialize date display immediately
    check_date_change()

    # Poll for changes periodically
    ui.timer(0.5, check_date_change)


def _create_map(messages_column):
    """Create the map with drawing controls."""
    with ui.card().classes("flex-1"):
        ui.label("Mark the location").classes("text-lg font-semibold mb-3")

        # Configure drawing tools
        draw_control = {
            "draw": {
                "marker": True,
            },
            "edit": {
                "edit": True,
                "remove": True,
            },
        }

        # Create map centered on Stockholm, Sweden
        m = ui.leaflet(center=(59.3293, 18.0686), zoom=13, draw_control=draw_control)
        m.classes("w-full h-screen rounded-lg")

        # Set up event handlers
        _setup_map_handlers(m, messages_column)

    return m


def _setup_map_handlers(m, messages_column):
    """Set up event handlers for map drawing actions."""

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    def handle_draw(e: events.GenericEventArguments):
        """Handle drawing creation events."""
        layer_type = e.args["layerType"]
        coords = e.args["layer"].get("_latlng") or e.args["layer"].get("_latlngs")
        message = f"✅ Drawn {layer_type} at {coords}"
        logger.info(f"Drawn {layer_type} at {coords}")
        add_message(message)
        ui.notify(f"Marked a {layer_type}", position="top", type="positive")

        # Update global state with bounding box from drawn shape
        _update_bbox_from_layer(e.args["layer"], layer_type)

    def handle_edit():
        """Handle drawing edit events."""
        message = "✏️ Edit completed"
        logger.info("Edit completed")
        add_message(message)
        ui.notify("Locations updated", position="top", type="info")

    def handle_delete():
        """Handle drawing deletion events."""
        message = "🗑️ Marker deleted"
        logger.info("Marker deleted")
        add_message(message)
        ui.notify("Marker removed", position="top", type="warning")
        # Clear bbox from state
        current_state["bbox"] = None

    m.on("draw:created", handle_draw)
    m.on("draw:edited", handle_edit)
    m.on("draw:deleted", handle_delete)


def _create_results_panel(messages_column):
    """Create the results panel with search controls."""
    with ui.column().classes("w-96"):
        with ui.card().classes("w-full"):
            ui.label("Search Products").classes("text-lg font-semibold mb-3")

            # Collection selector
            collection_select = ui.select(
                label="Satellite Collection",
                options=["SENTINEL-2", "SENTINEL-1", "SENTINEL-3", "SENTINEL-5P"],
                value="SENTINEL-2",
            ).classes("w-full mb-3")

            # Product level filter (for Sentinel-2)
            product_level_select = ui.select(
                label="Product Level",
                options=["L1C", "L2A", "L1C + L2A"],
                value="L2A",
            ).classes("w-full mb-3")

            # Cloud cover filter (for optical sensors)
            cloud_cover_input = ui.number(label="Max Cloud Cover (%)", value=30, min=0, max=100, step=5).classes("w-full mb-3")

            # Max results
            max_results_input = ui.number(label="Max Results", value=10, min=1, max=100, step=5).classes("w-full mb-3")

            # Search button
            search_button = ui.button("🔍 Search Products")
            search_button.classes("w-full")
            search_button.props("color=primary")

            # Loading indicator label
            loading_label = ui.label("").classes("text-sm text-blue-600 mt-2 font-medium")

            async def perform_search_wrapper():
                await _perform_search(messages_column, results_display, search_button, loading_label, collection_select.value, product_level_select.value, cloud_cover_input.value, max_results_input.value)

            search_button.on_click(perform_search_wrapper)

        # Results display
        with ui.card().classes("w-full flex-1 mt-4"):
            ui.label("Results").classes("text-lg font-semibold mb-3")
            with ui.scroll_area().classes("w-full h-96"):
                results_display = ui.column().classes("w-full gap-2")

    return results_display


async def _perform_search(messages_column, results_display, search_button, loading_label, collection: str, product_level: str, max_cloud_cover: float, max_results: int):
    """Perform catalog search with current state.

    Args:
        messages_column: UI column for activity log messages
        results_display: UI column for displaying results
        search_button: Search button element for disabling during search
        loading_label: Loading label element for showing search status
        collection: Satellite collection (e.g., "SENTINEL-2")
        product_level: Product processing level ("L1C", "L2A", or "L1C + L2A")
        max_cloud_cover: Maximum cloud cover percentage
        max_results: Maximum number of results to return
    """

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    def filter_products_by_level(products: list, level_filter: str) -> list:
        """Filter products by processing level.

        Args:
            products: List of ProductInfo objects
            level_filter: "L1C", "L2A", or "L1C + L2A"

        Returns:
            Filtered list of ProductInfo objects
        """
        if level_filter == "L1C + L2A":
            return products  # Keep all

        filtered = []
        for product in products:
            if level_filter in product.name:  # Check if L1C or L2A is in product name
                filtered.append(product)

        return filtered

    # Validate that we have necessary data
    if current_state["bbox"] is None:
        ui.notify("⚠️ Please drop a pin (or draw) a location on the map first", position="top", type="warning")
        add_message("⚠️ Search failed: No location selected")
        loading_label.text = ""
        return

    if current_state["date_range"] is None:
        ui.notify("⚠️ Please select a date range", position="top", type="warning")
        add_message("⚠️ Search failed: No date range selected")
        loading_label.text = ""
        return

    # Extract date range
    date_range = current_state["date_range"]
    start_date = date_range.get("from", "")
    end_date = date_range.get("to", start_date)

    # Show loading message and disable button
    ui.notify(f"🔍 Searching {collection} products ({product_level})...", position="top", type="info")
    add_message(f"🔍 Searching {collection} products ({product_level}) for {start_date} to {end_date}")

    # Disable search button and show loading state
    search_button.enabled = False
    original_text = search_button.text
    search_button.text = "⏳ Searching..."
    loading_label.text = "⏳ Searching..."

    # Clear previous results
    results_display.clear()
    with results_display:
        ui.spinner(size="lg")
        ui.label("Searching...").classes("text-gray-600")

    # Allow UI to render before starting the blocking search
    import asyncio

    await asyncio.sleep(0.1)

    try:
        # Perform search
        catalog = CatalogSearch()
        bbox = current_state["bbox"]

        products = catalog.search_products(
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
            collection=collection,
            max_cloud_cover=max_cloud_cover if collection in ["SENTINEL-2", "SENTINEL-3"] else None,
            max_results=int(max_results),
            product_level=product_level if product_level != "L1C + L2A" else None,
        )

        # Filter by product level if needed
        filtered_products = filter_products_by_level(products, product_level)

        # Display results
        results_display.clear()
        current_state["products"] = filtered_products

        if not filtered_products:
            with results_display:
                ui.label("No products found with selected level").classes("text-gray-500 italic")
            ui.notify("No products found with selected level", position="top", type="warning")
            add_message("❌ No products found with selected level")
        else:
            with results_display:
                ui.label(f"Found {len(filtered_products)} products (filtered from {len(products)} total)").classes("text-sm font-semibold text-green-600 mb-2")

                for i, product in enumerate(filtered_products, 1):
                    with ui.card().classes("w-full p-2 bg-gray-50"):
                        ui.label(f"{i}. {product.name}").classes("text-xs font-mono break-all")
                        ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600")
                        ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600")
                        if product.cloud_cover is not None:
                            ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600")

                        # Buttons for quicklook and metadata
                        with ui.row().classes("w-full gap-2 mt-2"):
                            ui.button(
                                "🖼️ Quicklook",
                                on_click=lambda p=product: _show_product_quicklook(p, messages_column),
                            ).classes("text-xs flex-1")
                            ui.button(
                                "📋 Metadata",
                                on_click=lambda p=product: _show_product_metadata(p, messages_column),
                            ).classes("text-xs flex-1")

            ui.notify(f"✅ Found {len(filtered_products)} products", position="top", type="positive")
            add_message(f"✅ Found {len(filtered_products)} products (from {len(products)} total)")
            logger.info(f"Search completed: {len(filtered_products)} products found (filtered from {len(products)})")

            # Re-enable search button and clear loading label
            search_button.enabled = True
            search_button.text = original_text
            loading_label.text = ""

    except Exception as e:
        logger.error(f"Search failed: {e}")
        results_display.clear()
        with results_display:
            ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
        ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
        add_message(f"❌ Search error: {str(e)}")

        # Re-enable search button and clear loading label
        search_button.enabled = True
        search_button.text = original_text
        loading_label.text = ""
    finally:
        # Ensure the search button and loading label are always reset
        try:
            search_button.enabled = True
            search_button.text = original_text
            loading_label.text = ""
        except Exception:
            # Defensive: ignore UI update errors
            pass


async def _show_product_quicklook(product, messages_column):
    """Show quicklook image for a product."""

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    try:
        ui.notify("📥 Downloading quicklook...", position="top", type="info")
        add_message(f"📥 Downloading quicklook for {product.name}")

        # Initialize products manager and download quicklook
        manager = ProductsManager()
        quicklook = manager.get_quicklook(product)

        if quicklook:
            # Show quicklook in a dialog
            with ui.dialog() as dialog:
                with ui.card():
                    ui.label(f"Quicklook: {product.name}").classes("text-lg font-semibold mb-3")
                    ui.label(f"Sensing Date: {product.sensing_date}").classes("text-sm text-gray-600 mb-3")

                    # Display image
                    base64_image = quicklook.get_base64()
                    ui.image(source=f"data:image/jpeg;base64,{base64_image}").classes("w-full rounded-lg")

                    with ui.row().classes("w-full gap-2 mt-4"):
                        ui.button("Close", on_click=dialog.close).classes("flex-1")

            dialog.open()
            ui.notify("✅ Quicklook loaded", position="top", type="positive")
            add_message(f"✅ Quicklook loaded for {product.name}")
        else:
            ui.notify("❌ Could not load quicklook", position="top", type="negative")
            add_message(f"❌ Quicklook not available for {product.name}")

    except Exception as e:
        logger.error(f"Error loading quicklook: {e}")
        ui.notify(f"❌ Error: {str(e)}", position="top", type="negative")
        add_message(f"❌ Quicklook error: {str(e)}")


async def _show_product_metadata(product, messages_column):
    """Show metadata for a product."""

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    try:
        ui.notify("📥 Downloading metadata...", position="top", type="info")
        add_message(f"📥 Downloading metadata for {product.name}")

        # Initialize products manager and download metadata
        manager = ProductsManager()
        metadata = manager.get_metadata(product)

        if metadata:
            # Show metadata in a dialog with scrollable XML
            with ui.dialog() as dialog:
                with ui.card():
                    ui.label(f"Metadata: {product.name}").classes("text-lg font-semibold mb-3")
                    ui.label("File: MTD_MSIL2A.xml").classes("text-sm text-gray-600 mb-3")

                    # Display metadata in a scrollable area
                    with ui.scroll_area().classes("w-full h-96"):
                        ui.code(metadata.metadata_xml, language="xml").classes("w-full text-xs")

                    with ui.row().classes("w-full gap-2 mt-4"):
                        ui.button("Close", on_click=dialog.close).classes("flex-1")

            dialog.open()
            ui.notify("✅ Metadata loaded", position="top", type="positive")
            add_message(f"✅ Metadata loaded for {product.name}")
        else:
            ui.notify("❌ Could not load metadata", position="top", type="negative")
            add_message(f"❌ Metadata not available for {product.name}")

    except Exception as e:
        logger.error(f"Error loading metadata: {e}")
        ui.notify(f"❌ Error: {str(e)}", position="top", type="negative")
        add_message(f"❌ Metadata error: {str(e)}")


def _update_bbox_from_layer(layer: dict, layer_type: str):
    """Extract bounding box from drawn layer and update global state."""
    try:
        if layer_type == "marker":
            # For a single marker, create a small bbox around it
            latlng = layer.get("_latlng", {})
            lat = latlng.get("lat")
            lng = latlng.get("lng")
            if lat is not None and lng is not None:
                # Create a ~1km bbox around the point
                delta = 0.01  # roughly 1km
                current_state["bbox"] = BoundingBox(west=lng - delta, south=lat - delta, east=lng + delta, north=lat + delta)
                logger.info(f"Updated bbox from marker: {current_state['bbox']}")
        # Add support for other shapes (rectangle, polygon) as needed
    except Exception as e:
        logger.error(f"Error extracting bbox from layer: {e}")


def _create_name_search_sidebar():
    """Create the left sidebar for name-based search with filters."""
    with ui.column().classes("w-80"):
        # Product name search card
        with ui.card().classes("w-full"):
            ui.label("Search by Product Name").classes("text-lg font-semibold mb-3")

            # Product name input
            name_input = ui.input(label="Product Name Pattern", placeholder="e.g., S2A_MSIL2A").classes("w-full mb-3")
            name_input.tooltip("Enter a product name or pattern (supports wildcards)")

            # Collection selector
            collection_select = ui.select(
                label="Satellite Collection",
                options=["SENTINEL-2", "SENTINEL-1", "SENTINEL-3", "SENTINEL-5P"],
                value="SENTINEL-2",
            ).classes("w-full mb-3")

            # Product level filter (for Sentinel-2)
            product_level_select = ui.select(
                label="Product Level",
                options=["L1C", "L2A", "L1C + L2A"],
                value="L2A",
            ).classes("w-full mb-3")

            # Date range filter
            with ui.expansion("📅 Date Range (Optional)").classes("w-full mb-3"):
                # Default to January 2020 (one-month range)
                date_from = "2020-01-01"
                date_to = "2020-01-31"
                date_picker_name = ui.date(value={"from": date_from, "to": date_to}).props("range")
                date_picker_name.classes("w-full")

            # Cloud cover filter
            cloud_cover_input = ui.number(label="Max Cloud Cover (%)", value=30, min=0, max=100, step=5).classes("w-full mb-3")

            # Max results
            max_results_input = ui.number(label="Max Results", value=10, min=1, max=100, step=5).classes("w-full mb-3")

            # Search button placed with the filters (so it's not isolated in its own card)
            search_button = ui.button("🔍 Search by Name")
            search_button.classes("w-full")
            search_button.props("color=primary")

            # Loading indicator label
            loading_label = ui.label("").classes("text-sm text-blue-600 mt-2 font-medium")

        # Activity log card
        with ui.card().classes("w-full flex-1 mt-4"):
            ui.label("Activity Log").classes("text-lg font-semibold mb-3")
            with ui.scroll_area().classes("w-full h-96"):
                messages_column_name = ui.column().classes("w-full gap-2")

    return {
        "name_input": name_input,
        "collection_select": collection_select,
        "product_level_select": product_level_select,
        "date_picker": date_picker_name,
        "cloud_cover_input": cloud_cover_input,
        "max_results_input": max_results_input,
        "search_button": search_button,
        "loading_label": loading_label,
        "messages_column": messages_column_name,
    }


def _create_name_search_results_panel(filters):
    """Create the results panel for name-based search."""
    with ui.column().classes("flex-1"):
        # Note: the search button is provided by the filters sidebar; here we only show results
        search_button = filters.get("search_button")
        loading_label = filters.get("loading_label")

        # Results display
        with ui.card().classes("w-full flex-1 mt-4"):
            ui.label("Results").classes("text-lg font-semibold mb-3")
            with ui.scroll_area().classes("w-full h-96"):
                results_display = ui.column().classes("w-full gap-2")

        # Set up button click handler after results_display is defined
        async def perform_name_search_wrapper():
            await _perform_name_search(
                filters["messages_column"],
                results_display,
                search_button,
                loading_label,
                filters["name_input"],
                filters["collection_select"],
                filters["product_level_select"],
                filters["date_picker"],
                filters["cloud_cover_input"],
                filters["max_results_input"],
            )

        # Wire up the search button provided by the sidebar filters
        if search_button is not None:
            search_button.on_click(perform_name_search_wrapper)

    return results_display


def _create_download_tab():
    """Create the download tab UI which accepts a product name, fetches available bands,
    allows selecting bands and resolution, and triggers downloads via ProductsManager.
    """
    from pathlib import Path

    from vresto.products import ProductsManager

    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full gap-6"):
            # Left column: product input and controls
            with ui.column().classes("w-80"):
                with ui.card().classes("w-full"):
                    ui.label("Download Product").classes("text-lg font-semibold mb-3")

                    product_input = ui.input(label="Product name or S3 path", placeholder="S2A_MSIL2A_... or s3://.../PRODUCT.SAFE").classes("w-full mb-3")

                    fetch_button = ui.button("📥 Fetch bands").classes("w-full mb-2")
                    fetch_button.props("color=primary")

                    # Resolution selector is above the bands so user chooses resolution first
                    ui.label("Band resolution to download:").classes("text-sm text-gray-600 mb-2")
                    # Resolution selector: show friendly labels, map to internal values at download time
                    RES_NATIVE_LABEL = "Native (best available per band)"
                    resolution_select = ui.select(options=[RES_NATIVE_LABEL, "10", "20", "60"], value=RES_NATIVE_LABEL).classes("w-full mb-3")

                    ui.label("Native selects each band's best available (smallest) native resolution. Choosing 10/20/60 requires that exact resolution for every selected band.").classes("text-xs text-gray-600 mb-2")

                    ui.label("Available bands").classes("text-sm text-gray-600 mb-2")
                    # Selection helpers
                    with ui.row().classes("w-full gap-2 mb-2"):
                        select_all_btn = ui.button("Select All").classes("text-sm")
                        deselect_all_btn = ui.button("Deselect All").classes("text-sm")
                        select_10m_btn = ui.button("Select all 10m bands").classes("text-sm")
                        select_20m_btn = ui.button("Select all 20m bands").classes("text-sm")
                        select_60m_btn = ui.button("Select all 60m bands").classes("text-sm")

                    bands_container = ui.column().classes("w-full gap-1")
                    created_checkboxes: list = []

                    def _select_all(val: bool):
                        for c in created_checkboxes:
                            try:
                                c.set_value(val)
                            except Exception:
                                try:
                                    c.value = val
                                except Exception:
                                    pass

                    def _select_by_res(res_target: int, val: bool = True):
                        for c in created_checkboxes:
                            try:
                                res_list = getattr(c, "resolutions", [])
                                if res_target in res_list:
                                    c.set_value(val)
                            except Exception:
                                try:
                                    if res_target in getattr(c, "resolutions", []):
                                        c.value = val
                                except Exception:
                                    pass

                    select_all_btn.on_click(lambda: _select_all(True))
                    deselect_all_btn.on_click(lambda: _select_all(False))
                    select_10m_btn.on_click(lambda: _select_by_res(10, True))
                    select_20m_btn.on_click(lambda: _select_by_res(20, True))
                    select_60m_btn.on_click(lambda: _select_by_res(60, True))

                    dest_input = ui.input(label="Destination directory", value=str(Path.home() / "vresto_downloads")).classes("w-full mt-3 mb-3")

                    download_button = ui.button("⬇️ Download selected").classes("w-full")
                    download_button.props("color=primary")
                    # Progress UI for downloads: circular progress with a small textual counter
                    # hide the built-in numeric value; we'll show a formatted percentage below
                    progress = ui.circular_progress(value=0.0, max=1.0, size="lg", show_value=False).classes("m-auto mt-2")
                    progress_label = ui.label("").classes("text-sm text-gray-600 mt-1")

            # Right column: activity log and results
            with ui.column().classes("flex-1"):
                with ui.card().classes("w-full flex-1"):
                    ui.label("Activity Log").classes("text-lg font-semibold mb-3")
                    with ui.scroll_area().classes("w-full h-96"):
                        activity_column = ui.column().classes("w-full gap-2")

    # Handlers
    def add_activity(msg: str):
        with activity_column:
            ui.label(msg).classes("text-sm text-gray-700 break-words")

    async def handle_fetch():
        product = product_input.value.strip() if product_input.value else ""
        if not product:
            ui.notify("⚠️ Enter a product name or S3 path first", position="top", type="warning")
            add_activity("⚠️ Fetch failed: no product provided")
            return

        add_activity(f"🔎 Resolving bands for: {product}")
        try:
            mgr = ProductsManager()
            # ProductsManager uses ProductDownloader internally; get the mapper via ProductDownloader
            # We'll ask ProductDownloader.list_available_bands via ProductsManager by constructing s3 path
            s3_path = mgr._construct_s3_path_from_name(product)

            # Use ProductDownloader directly to list bands
            from vresto.products.downloader import ProductDownloader

            pd = ProductDownloader(s3_client=mgr.s3_client)
            bands_map = pd.list_available_bands(s3_path)

            bands_container.clear()
            created_checkboxes.clear()
            if not bands_map:
                add_activity("ℹ️ No band files found for this product (or product not found)")
                ui.notify("No bands found", position="top", type="warning")
                return

            for band, res_set in sorted(bands_map.items()):
                # show band with its available resolutions inside the bands_container
                with bands_container:
                    cb = ui.checkbox(f"{band} (available: {sorted(res_set)})")
                cb.band_name = band
                cb.resolutions = sorted(res_set)
                created_checkboxes.append(cb)

            add_activity(f"✅ Found bands: {', '.join(sorted(bands_map.keys()))}")
            ui.notify("Bands fetched", position="top", type="positive")

        except Exception as e:
            add_activity(f"❌ Error fetching bands: {e}")
            ui.notify(f"Error: {e}", position="top", type="negative")

    async def handle_download():
        product = product_input.value.strip() if product_input.value else ""
        if not product:
            ui.notify("⚠️ Enter a product name or S3 path first", position="top", type="warning")
            add_activity("⚠️ Download failed: no product provided")
            return

        # Read selected checkboxes from the created_checkboxes list
        selected_bands = [getattr(c, "band_name", None) for c in created_checkboxes if getattr(c, "value", False)]
        if not selected_bands:
            ui.notify("⚠️ Select at least one band to download", position="top", type="warning")
            add_activity("⚠️ Download failed: no bands selected")
            return

        # Map display from select to internal resolution: 'native' or int
        raw_res = resolution_select.value
        if raw_res == "Native (best available per band)":
            resolution = "native"
        else:
            try:
                resolution = int(raw_res)
            except Exception:
                resolution = "native"
        # resample option removed; downloads are fetched at requested resolution if available
        dest_dir = dest_input.value or str(Path.home() / "vresto_downloads")

        add_activity(f"⬇️ Starting download for {product}: bands={selected_bands}, resolution={resolution}")

        try:
            mgr = ProductsManager()
            from vresto.products.downloader import ProductDownloader, _parse_s3_uri

            pd = ProductDownloader(s3_client=mgr.s3_client)

            # Resolve product S3 prefix and build keys for requested bands at chosen resolution
            s3_path = mgr._construct_s3_path_from_name(product)
            try:
                keys = pd.build_keys_for_bands(s3_path, selected_bands, resolution)
            except Exception as e:
                add_activity(f"❌ Could not build keys for bands/resolution: {e}")
                ui.notify(f"Failed: {e}", position="top", type="negative")
                return

            total = len(keys)
            # initialize progress
            try:
                progress.set_value(0.0)
            except Exception:
                progress.value = 0.0
            progress_label.text = f"0.0% (0 / {total})"
            add_activity(f"⬇️ Downloading {total} files to {dest_dir}")

            import asyncio

            downloaded = []
            for i, s3uri in enumerate(keys, start=1):
                try:
                    bucket, key = _parse_s3_uri(s3uri)
                    # preserve s3 structure locally
                    dest = Path(dest_dir) / key
                    path = await asyncio.to_thread(pd._download_one, s3uri, dest, False)
                    downloaded.append(path)
                    # update circular progress (value between 0 and 1)
                    frac = float(i) / float(total) if total else 1.0
                    try:
                        progress.set_value(frac)
                    except Exception:
                        progress.value = frac
                    progress_label.text = f"{frac * 100:.1f}% ({i} / {total})"
                    add_activity(f"✅ Downloaded {Path(path).name}")
                except Exception as ex:
                    add_activity(f"❌ Failed to download {s3uri}: {ex}")
                    # continue downloading remaining files

            add_activity(f"✅ Download completed: {len(downloaded)} of {total} files")
            ui.notify(f"Download finished: {len(downloaded)} files", position="top", type="positive")
        except Exception as e:
            add_activity(f"❌ Download error: {e}")
            ui.notify(f"Download failed: {e}", position="top", type="negative")

    # NiceGUI accepts async handlers directly
    fetch_button.on_click(handle_fetch)
    download_button.on_click(handle_download)


async def _perform_name_search(
    messages_column,
    results_display,
    search_button,
    loading_label,
    name_input,
    collection_select,
    product_level_select,
    date_picker,
    cloud_cover_input,
    max_results_input,
):
    """Perform product name search.

    Args:
        messages_column: UI column for activity log messages
        results_display: UI column for displaying results
        search_button: Search button element for disabling during search
        loading_label: Loading label element for showing search status
        name_input: Input field with product name pattern
        collection_select: Selected satellite collection
        product_level_select: Selected product level
        date_picker: Date range picker
        cloud_cover_input: Max cloud cover value
        max_results_input: Max results value
    """

    def add_message(text: str):
        """Add a message to the activity log."""
        with messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")

    def filter_products_by_level(products: list, level_filter: str) -> list:
        """Filter products by processing level."""
        if level_filter == "L1C + L2A":
            return products

        filtered = []
        for product in products:
            if level_filter in product.name:
                filtered.append(product)

        return filtered

    # Validate that we have a product name
    if not name_input.value or not name_input.value.strip():
        ui.notify("⚠️ Please enter a product name or pattern", position="top", type="warning")
        add_message("⚠️ Search failed: No product name entered")
        return

    # Extract date range
    date_range = date_picker.value
    if isinstance(date_range, dict):
        start_date = date_range.get("from", "")
        end_date = date_range.get("to", start_date)
    else:
        start_date = ""
        end_date = ""

    # Warn user if the requested range is large (>= 6 months)
    try:
        if start_date and end_date:
            dt_start = datetime.fromisoformat(start_date)
            dt_end = datetime.fromisoformat(end_date)
            # compute months difference roughly
            months = (dt_end.year - dt_start.year) * 12 + (dt_end.month - dt_start.month)
            if months >= 6:
                # Build an explicit dialog and await the user's choice. Using ui.dialog
                # per NiceGUI docs ensures consistent behaviour across versions.
                message = f"You've requested a date range of {months} months. This can be slow and may lose connection. Continue?\n\nFor long searches consider using the programmatic API (examples/search_by_name_example.py)"

                with ui.dialog().props("persistent") as confirm_dialog, ui.card():
                    ui.label(message).classes("break-words")
                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("No", on_click=lambda: confirm_dialog.submit(False)).classes("text-sm")
                        ui.button("Yes", on_click=lambda: confirm_dialog.submit(True)).props("color=primary").classes("text-sm")

                confirmed = await confirm_dialog
                if not confirmed:
                    add_message("ℹ️ Name search cancelled by user due to large date range")
                    return
    except Exception:
        # If parsing dates fails, ignore and continue (validation elsewhere will handle it)
        pass

    collection = collection_select.value
    product_level = product_level_select.value
    max_cloud_cover = cloud_cover_input.value
    max_results = int(max_results_input.value)
    name_pattern = name_input.value.strip()

    # Show loading message and disable button
    ui.notify(f"🔍 Searching for products matching '{name_pattern}'...", position="top", type="info")
    add_message(f"🔍 Searching {collection} products for name: '{name_pattern}'")

    # Disable search button and show loading state
    search_button.enabled = False
    original_text = search_button.text
    search_button.text = "⏳ Searching..."
    loading_label.text = "⏳ Searching..."

    # Clear previous results
    results_display.clear()
    with results_display:
        ui.spinner(size="lg")
        ui.label("Searching...").classes("text-gray-600")

    # Allow UI to render before starting the blocking search
    import asyncio

    await asyncio.sleep(0.1)

    try:
        # Perform search using catalog API
        catalog = CatalogSearch()

        # Build search parameters
        search_params = {
            "collection": collection,
            "max_results": max_results,
        }

        # Add date range if provided
        if start_date:
            search_params["start_date"] = start_date
            search_params["end_date"] = end_date if end_date else start_date

        # Add cloud cover filter if applicable
        if collection in ["SENTINEL-2", "SENTINEL-3"]:
            search_params["max_cloud_cover"] = max_cloud_cover

        # For name search, we need a dummy bbox (search by name doesn't use spatial query)
        # Using a large bbox covering most of the world
        dummy_bbox = BoundingBox(west=-180, south=-90, east=180, north=90)
        search_params["bbox"] = dummy_bbox
        # Pass product_level to server-side filter unless user requested both
        if product_level and product_level != "L1C + L2A":
            search_params["product_level"] = product_level

        # Perform search
        products = catalog.search_products(**search_params)

        # Filter by product name pattern (case-insensitive)
        name_pattern_lower = name_pattern.lower()
        if not isinstance(products, list):
            logger.warning(f"Expected list of products from catalog.search_products(), got {type(products)}")
            products = []

        filtered_by_name = []
        for p in products:
            try:
                if name_pattern_lower in p.name.lower():
                    filtered_by_name.append(p)
            except Exception:
                logger.exception("Error while filtering product by name; skipping product")

        logger.info(f"Name search: server returned {len(products)} products, {len(filtered_by_name)} match name pattern '{name_pattern}'")

        # Filter by product level if needed
        filtered_products = filter_products_by_level(filtered_by_name, product_level)

        # Display results
        results_display.clear()
        current_state["products"] = filtered_products

        if not filtered_products:
            with results_display:
                ui.label("No products found matching the criteria").classes("text-gray-500 italic")
            ui.notify("No products found", position="top", type="warning")
            add_message("❌ No products found matching the search criteria")
        else:
            with results_display:
                ui.label(f"Found {len(filtered_products)} products").classes("text-sm font-semibold text-green-600 mb-2")

                import asyncio

                for i, product in enumerate(filtered_products, 1):
                    with ui.card().classes("w-full p-2 bg-gray-50"):
                        ui.label(f"{i}. {product.name}").classes("text-xs font-mono break-all")
                        ui.label(f"📅 {product.sensing_date}").classes("text-xs text-gray-600")
                        ui.label(f"💾 {product.size_mb:.1f} MB").classes("text-xs text-gray-600")
                        if product.cloud_cover is not None:
                            ui.label(f"☁️ {product.cloud_cover:.1f}%").classes("text-xs text-gray-600")

                        # Buttons for quicklook and metadata
                        with ui.row().classes("w-full gap-2 mt-2"):
                            ui.button(
                                "🖼️ Quicklook",
                                on_click=lambda p=product: _show_product_quicklook(p, messages_column),
                            ).classes("text-xs flex-1")
                            ui.button(
                                "📋 Metadata",
                                on_click=lambda p=product: _show_product_metadata(p, messages_column),
                            ).classes("text-xs flex-1")

                    # Yield to the event loop periodically to avoid blocking the NiceGUI websocket
                    if i % 20 == 0:
                        await asyncio.sleep(0)

            ui.notify(f"✅ Found {len(filtered_products)} products", position="top", type="positive")
            add_message(f"✅ Found {len(filtered_products)} products matching '{name_pattern}'")
            logger.info(f"Name search completed: {len(filtered_products)} products found")

            # Re-enable search button and clear loading label
            search_button.enabled = True
            search_button.text = original_text
            loading_label.text = ""

    except Exception as e:
        logger.error(f"Name search failed: {e}")
        results_display.clear()
        with results_display:
            ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
        ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
        add_message(f"❌ Search error: {str(e)}")

        # Re-enable search button and clear loading label
        search_button.enabled = True
        search_button.text = original_text
        loading_label.text = ""
    finally:
        # Ensure the search button and loading label are always reset
        try:
            search_button.enabled = True
            search_button.text = original_text
            loading_label.text = ""
        except Exception:
            pass


if __name__ in {"__main__", "__mp_main__"}:
    with ui.column().classes("w-full h-screen p-6"):
        create_map_interface()

    ui.run()
