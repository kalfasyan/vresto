"""Map interface with date range selection and marker drawing capabilities."""

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

    # Main layout: Date picker and activity log on left, map in center, results on right
    with ui.row().classes("w-full gap-6"):
        # Left sidebar: Date picker and activity log
        date_picker, messages_column = _create_sidebar()

        # Map with draw controls
        m = _create_map(messages_column)

        # Right sidebar: Search controls and results
        results_column = _create_results_panel(messages_column)

    return {"date_picker": date_picker, "map": m, "results": results_column}


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
        ui.label("Mark Locations").classes("text-lg font-semibold mb-3")

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
            search_button = ui.button("🔍 Search Products", on_click=lambda: _perform_search(messages_column, results_display, collection_select.value, product_level_select.value, cloud_cover_input.value, max_results_input.value))
            search_button.classes("w-full")
            search_button.props("color=primary")

        # Results display
        with ui.card().classes("w-full flex-1 mt-4"):
            ui.label("Results").classes("text-lg font-semibold mb-3")
            with ui.scroll_area().classes("w-full h-96"):
                results_display = ui.column().classes("w-full gap-2")

    return results_display


async def _perform_search(messages_column, results_display, collection: str, product_level: str, max_cloud_cover: float, max_results: int):
    """Perform catalog search with current state.

    Args:
        messages_column: UI column for activity log messages
        results_display: UI column for displaying results
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
        return

    if current_state["date_range"] is None:
        ui.notify("⚠️ Please select a date range", position="top", type="warning")
        add_message("⚠️ Search failed: No date range selected")
        return

    # Extract date range
    date_range = current_state["date_range"]
    start_date = date_range.get("from", "")
    end_date = date_range.get("to", start_date)

    # Show loading message
    ui.notify(f"🔍 Searching {collection} products ({product_level})...", position="top", type="info")
    add_message(f"🔍 Searching {collection} products ({product_level}) for {start_date} to {end_date}")

    # Clear previous results
    results_display.clear()
    with results_display:
        ui.spinner(size="lg")
        ui.label("Searching...").classes("text-gray-600")

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

    except Exception as e:
        logger.error(f"Search failed: {e}")
        results_display.clear()
        with results_display:
            ui.label(f"Error: {str(e)}").classes("text-red-600 text-sm")
        ui.notify(f"❌ Search failed: {str(e)}", position="top", type="negative")
        add_message(f"❌ Search error: {str(e)}")


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


if __name__ in {"__main__", "__mp_main__"}:
    with ui.column().classes("w-full h-screen p-6"):
        create_map_interface()

    ui.run()
