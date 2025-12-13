"""Map interface with date range selection and marker drawing capabilities."""

from datetime import datetime

from loguru import logger
from nicegui import events, ui

from vresto.api import BoundingBox, CatalogSearch
from vresto.products import ProductsManager

# Global state for current selection
# Default date range: January 2020 (whole month)
default_from = "2020-01-01"
default_to = "2020-01-31"
current_state = {
    "bbox": None,
    "date_range": {"from": default_from, "to": default_to},
    "products": [],
    "selected_product": None,
}


def create_map_interface():
    """Create a beautiful interface with date range selection and interactive map."""
    # Header
    ui.label("Sentinel Browser").classes("text-3xl font-bold mb-6")

    # Create tab headers
    with ui.tabs().classes("w-full") as tabs:
        map_tab = ui.tab("Map Search", icon="map")
        name_tab = ui.tab("Search by Name", icon="search")
        download_tab = ui.tab("Download Product", icon="download")
        local_tab = ui.tab("Product Analysis", icon="folder_open")

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

        with ui.tab_panel(local_tab):
            # Local downloaded products inspector
            _create_local_products_tab()

    return {
        "tabs": tabs,
        "messages_column": messages_column,
        "map": map_widget,
        "date_picker": date_picker,
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


def _create_activity_log():
    """Create the activity log panel."""
    with ui.card().classes("w-full flex-1"):
        ui.label("Activity Log").classes("text-lg font-semibold mb-3")

        with ui.scroll_area().classes("w-full h-96"):
            messages_column = ui.column().classes("w-full gap-2")

    return messages_column


def _create_date_picker():
    """Create the date picker component."""
    with ui.card().classes("w-full"):
        ui.label("Select date (or range)").classes("text-lg font-semibold mb-1")

        # Default to January 2020 (whole month) if not set
        date_from = current_state.get("date_range", {}).get("from", "2020-01-01")
        date_to = current_state.get("date_range", {}).get("to", "2020-01-31")

        # Set initial value as a dict for range mode
        date_picker = ui.date(value={"from": date_from, "to": date_to}).props("range")
        date_picker.classes("w-full")

        date_display = ui.label("").classes("text-sm text-blue-600 mt-3 font-medium")

    # Store initial date in global state
    current_state["date_range"] = {"from": date_from, "to": date_to}

    return date_picker, date_display


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


# date monitoring removed — dates are handled automatically from product names


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


def _create_local_products_tab():
    """Create a tab for inspecting already downloaded products locally."""
    import os
    from pathlib import Path

    from vresto.products.downloader import _BAND_RE

    with ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full gap-6"):
            # Left: folder selector and controls
            with ui.column().classes("w-80"):
                with ui.card().classes("w-full"):
                    ui.label("Downloaded Products").classes("text-lg font-semibold mb-3")

                    folder_input = ui.input(label="Download folder", value=str(Path.home() / "vresto_downloads")).classes("w-full mb-3")
                    browse_btn = ui.button("📂 Browse").classes("w-full mb-2")
                    scan_btn = ui.button("🔎 Scan folder").classes("w-full")

                    ui.label("Filter (substring)").classes("text-sm text-gray-600 mt-3")
                    filter_input = ui.input(placeholder="partial product name...").classes("w-full mb-2")

            # Middle: product list and bands
            with ui.column().classes("w-96"):
                with ui.card().classes("w-full flex-1"):
                    ui.label("Products").classes("text-lg font-semibold mb-3")
                    # Keep a lightweight dropdown to allow quick selection; list is still shown below
                    products_select = ui.select(options=[], label="Discovered products").classes("w-full mb-2")
                    with ui.scroll_area().classes("w-full h-72"):
                        products_column = ui.column().classes("w-full gap-2")

            # Right: preview and band selection
            with ui.column().classes("flex-1"):
                with ui.card().classes("w-full flex-1"):
                    ui.label("Preview & Bands").classes("text-lg font-semibold mb-3")
                    preview_area = ui.column().classes("w-full")

    # state holders
    scanned_products: dict = {}

    def add_activity(msg: str):
        with products_column:
            ui.label(msg).classes("text-sm text-gray-700 break-words")

    def _scan_folder():
        root = folder_input.value or ""
        root = os.path.expanduser(root)
        products_column.clear()
        scanned_products.clear()
        if not root or not os.path.exists(root):
            ui.notify("⚠️ Folder does not exist", position="top", type="warning")
            add_activity("⚠️ Scan failed: folder does not exist")
            return
        # show loading state
        scan_btn.enabled = False
        original_scan_text = getattr(scan_btn, "text", "🔎 Scan folder")
        scan_btn.text = "⏳ Scanning..."
        add_activity(f"🔎 Scanning folder: {root}")
        # discover .SAFE directories or directories containing IMG_DATA (recursive)
        found_set = set()
        for dirpath, dirnames, filenames in os.walk(root):
            # detect any .SAFE directories directly under current dir
            for d in list(dirnames):
                if d.endswith(".SAFE"):
                    found_set.add(os.path.join(dirpath, d))
            # detect IMG_DATA; product root often two levels up from IMG_DATA
            if "IMG_DATA" in dirnames:
                img_dir = os.path.join(dirpath, "IMG_DATA")
                product_root = os.path.abspath(os.path.join(img_dir, "..", ".."))
                # try to find nearest .SAFE ancestor
                cur = product_root
                found_safe = False
                while cur and cur != os.path.dirname(cur):
                    if cur.endswith(".SAFE"):
                        found_set.add(cur)
                        found_safe = True
                        break
                    cur = os.path.dirname(cur)
                if not found_safe:
                    found_set.add(product_root)

        found = sorted(found_set)

        # apply filter
        flt = (filter_input.value or "").strip().lower()
        if flt:
            found = [p for p in found if flt in os.path.basename(p).lower()]

        if not found:
            add_activity("ℹ️ No products found in folder")
            ui.notify("No products found", position="top", type="info")
            scan_btn.enabled = True
            scan_btn.text = original_scan_text
            return

        add_activity(f"✅ Found {len(found)} products")

        names = []
        for p in sorted(found):
            pname = os.path.basename(p)
            display_name = pname[:-5] if pname.upper().endswith(".SAFE") else pname
            names.append(display_name)
            # keep mapping from display name to full path; if duplicates arise, last wins
            scanned_products[display_name] = p

        # populate select and product cards
        products_select.options = names
        if names:
            products_select.value = names[0]
            # show cards as well (displaying friendly names without .SAFE)
            for name in names:
                p = scanned_products[name]
                with products_column:
                    with ui.card().classes("w-full p-2 bg-gray-50"):
                        ui.label(name).classes("text-xs font-mono break-all")
                        with ui.row().classes("w-full gap-2 mt-2"):
                            ui.button("🔍 Inspect", on_click=lambda pp=p: _inspect_local_product(pp)).classes("text-xs")
            # auto-inspect first
            _inspect_local_product(scanned_products[names[0]])

        # restore scan button
        scan_btn.enabled = True
        scan_btn.text = original_scan_text

    def _inspect_local_product(path: str):
        # clear preview area and show bands
        preview_area.clear()
        try:
            # find IMG_DATA prefix within SAFE structure if present
            img_root = None
            if path.endswith(".SAFE"):
                granule = os.path.join(path, "GRANULE")
                if os.path.isdir(granule):
                    for g in os.scandir(granule):
                        img = os.path.join(g.path, "IMG_DATA")
                        if os.path.isdir(img):
                            img_root = img
                            break
            else:
                # try to find IMG_DATA under product dir
                granule = os.path.join(path, "GRANULE")
                if os.path.isdir(granule):
                    for g in os.scandir(granule):
                        img = os.path.join(g.path, "IMG_DATA")
                        if os.path.isdir(img):
                            img_root = img
                            break

            if not img_root:
                # fallback: search recursively for any jp2 files
                candidates = []
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if f.lower().endswith(".jp2"):
                            candidates.append(os.path.join(root, f))
                if not candidates:
                    preview_area.add(ui.label("No image bands found locally").classes("text-sm text-gray-600"))
                    return
                img_root = os.path.dirname(candidates[0])

            # list files and extract bands
            bands_map = {}
            for root, dirs, files in os.walk(img_root):
                for f in files:
                    m = _BAND_RE.search(f)
                    if not m:
                        continue
                    band = m.group("band").upper()
                    res = int(m.group("res"))
                    bands_map.setdefault(band, set()).add(res)

            with preview_area:
                ui.label(f"Product: {os.path.basename(path)}").classes("text-sm font-semibold")
                ui.label(f"IMG_DATA: {img_root}").classes("text-xs text-gray-600 mb-2")

                # static, non-interactive band list (for clarity)
                ui.label("Available bands:").classes("text-sm text-gray-600 mt-1")
                # Make band list scrollable to save vertical space
                with ui.card().classes("w-full p-2 bg-gray-50 mb-2"):
                    with ui.scroll_area().classes("w-full max-h-40"):
                        for band, resset in sorted(bands_map.items()):
                            ui.label(f"- {band}: resolutions {sorted(resset)}").classes("text-xs font-mono")

                # interactive selectors for visualization
                # Make single-band selector narrower so it doesn't span full width
                single_band_select = ui.select(
                    options=sorted(bands_map.keys()),
                    label="Single band to preview",
                    value=sorted(bands_map.keys())[0] if bands_map else None,
                ).classes("w-48 mb-2")
                # Note about RGB composite: choose bands automatically for a natural-color composite
                ui.label("Note: 'RGB composite' composes three bands (e.g. B04,B03,B02) to create an approximate natural-color image.").classes("text-xs text-gray-600 mb-2")

                # Visualization controls: resolution selector and mode
                RES_NATIVE_LABEL = "Native (best available per band)"
                with ui.row().classes("w-full gap-2 mt-2 mb-2"):
                    resolution_select = ui.select(options=[RES_NATIVE_LABEL, "10", "20", "60"], value=RES_NATIVE_LABEL).classes("w-48")
                    mode_select = ui.select(options=["Single band", "RGB composite", "All bands"], value="Single band").classes("w-48")

                band_names = sorted(bands_map.keys())

                # helper to pick default RGB bands
                def _default_rgb():
                    for combo in [("B04", "B03", "B02"), ("B04", "B03", "B02")]:
                        if all(b in bands_map for b in combo):
                            return combo
                    # fallback to first three
                    return tuple(band_names[:3])

                ui.row().classes("w-full items-center mt-2")
                preview_btn = ui.button("▶️ Preview").classes("text-sm")
                preview_spinner = ui.spinner(size="sm").classes("ml-2 hidden")
                import asyncio

                # single preview display area (replace contents on each preview)
                preview_display = ui.column().classes("w-full mt-2")

                async def _show_preview():
                    # set loading state so user gets immediate feedback
                    original_text = getattr(preview_btn, "text", "▶️ Preview")
                    try:
                        preview_btn.text = "⏳ Previewing..."
                    except Exception:
                        pass
                    preview_btn.enabled = False
                    try:
                        preview_spinner.remove_class("hidden")
                    except Exception:
                        pass

                    # allow UI to render spinner/button text
                    try:
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass

                    try:
                        # determine desired bands
                        mode = mode_select.value
                        res_raw = resolution_select.value
                        resolution = "native" if res_raw == RES_NATIVE_LABEL else int(res_raw)
                        if mode == "RGB composite":
                            rgb_bands = _default_rgb()
                            _build_and_show_rgb(rgb_bands, img_root, resolution)
                        elif mode == "Single band":
                            band = single_band_select.value
                            if not band:
                                ui.notify("⚠️ No band selected for single-band preview", position="top", type="warning")
                            else:
                                _build_and_show_single(band, img_root, resolution)
                        else:  # All bands
                            all_bands = sorted(bands_map.keys())
                            if not all_bands:
                                ui.notify("⚠️ No bands available to show", position="top", type="warning")
                            else:
                                _build_and_show_all(all_bands, img_root, resolution)
                    finally:
                        # restore button and hide spinner
                        try:
                            preview_btn.text = original_text
                        except Exception:
                            pass
                        preview_btn.enabled = True
                        try:
                            preview_spinner.add_class("hidden")
                        except Exception:
                            pass

                preview_btn.on_click(lambda: asyncio.create_task(_show_preview()))

                def _build_and_show_rgb(bands_tuple, img_root_local, resolution_local):
                    # similar approach to earlier: use rasterio to read bands and compose
                    try:
                        import tempfile

                        import numpy as np

                        try:
                            import rasterio
                            from rasterio.enums import Resampling
                        except Exception:
                            ui.label("Rasterio not installed; cannot build RGB composite").classes("text-sm text-gray-600 mt-2")
                            return

                        # find files for requested bands and resolution (prefer exact resolution or native)
                        band_files = {}
                        for rootp, dirs, files in os.walk(img_root_local):
                            for f in files:
                                m = _BAND_RE.search(f)
                                if not m:
                                    continue
                                band = m.group("band").upper()
                                res = int(m.group("res"))
                                if band in bands_tuple:
                                    if resolution_local == "native" or res == int(resolution_local):
                                        band_files.setdefault(band, os.path.join(rootp, f))
                        # if resolution requested but missing for some band, try native
                        for b in bands_tuple:
                            if b not in band_files:
                                # find any available
                                for rootp, dirs, files in os.walk(img_root_local):
                                    for f in files:
                                        m = _BAND_RE.search(f)
                                        if not m:
                                            continue
                                        band = m.group("band").upper()
                                        if band == b:
                                            band_files[b] = os.path.join(rootp, f)
                                            break
                                    if b in band_files:
                                        break

                        if not all(b in band_files for b in bands_tuple):
                            ui.label("Requested bands not fully available locally").classes("text-sm text-gray-600 mt-2")
                            return

                        srcs = {b: rasterio.open(band_files[b]) for b in bands_tuple}
                        # choose reference by smallest pixel size
                        resolutions_map = {b: abs(s.transform.a) for b, s in srcs.items()}
                        ref_band = min(resolutions_map, key=resolutions_map.get)
                        ref = srcs[ref_band]
                        arrs = []
                        for b in bands_tuple:
                            s = srcs[b]
                            if s.width == ref.width and s.height == ref.height and s.transform == ref.transform:
                                data = s.read(1)
                            else:
                                data = s.read(1, out_shape=(ref.height, ref.width), resampling=Resampling.bilinear)
                            arrs.append(data)
                        rgb = np.stack(arrs, axis=-1)
                        p1 = np.percentile(rgb, 2)
                        p99 = np.percentile(rgb, 98)
                        rgb = (rgb - p1) / max((p99 - p1), 1e-6)
                        rgb = (np.clip(rgb, 0.0, 1.0) * 255).astype("uint8")
                        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        tmpf.close()
                        # Try several image writers: Pillow, imageio, matplotlib
                        wrote = False
                        try:
                            from PIL import Image

                            Image.fromarray(rgb).save(tmpf.name, quality=85)
                            wrote = True
                        except Exception:
                            try:
                                import imageio

                                imageio.imwrite(tmpf.name, rgb)
                                wrote = True
                            except Exception:
                                try:
                                    import matplotlib.pyplot as plt

                                    plt.imsave(tmpf.name, rgb)
                                    wrote = True
                                except Exception:
                                    wrote = False

                        # update single preview area
                        preview_display.clear()
                        with preview_display:
                            if wrote:
                                ui.image(source=tmpf.name).classes("w-full rounded-lg mt-2")
                            else:
                                ui.label("Cannot write preview image; install Pillow or imageio (e.g. `pip install Pillow imageio`)").classes("text-sm text-gray-600 mt-2")
                        # cleanup opened datasets
                        for s in srcs.values():
                            try:
                                s.close()
                            except Exception:
                                pass
                    except Exception as e:
                        logger.exception("Error building RGB: %s", e)
                        ui.label(f"Error building RGB preview: {e}").classes("text-sm text-red-600 mt-2")

                def _build_and_show_single(band, img_root_local, resolution_local):
                    """Render a single band using a viridis colormap and colorbar if possible."""
                    try:
                        import tempfile

                        import numpy as np

                        try:
                            import rasterio
                        except Exception:
                            preview_display.clear()
                            with preview_display:
                                ui.label("Rasterio not installed; cannot render band").classes("text-sm text-gray-600 mt-2")
                            return

                        # locate band file
                        band_file = None
                        for rootp, dirs, files in os.walk(img_root_local):
                            for f in files:
                                m = _BAND_RE.search(f)
                                if not m:
                                    continue
                                bname = m.group("band").upper()
                                if bname == band:
                                    band_file = os.path.join(rootp, f)
                                    break
                            if band_file:
                                break

                        if not band_file:
                            preview_display.clear()
                            with preview_display:
                                ui.label("Band file not found locally").classes("text-sm text-gray-600 mt-2")
                            return

                        s = rasterio.open(band_file)
                        data = s.read(1)

                        # scale to 0..1 using min/max
                        vmin = float(np.nanmin(data))
                        vmax = float(np.nanmax(data))
                        denom = vmax - vmin if (vmax - vmin) != 0 else 1.0
                        normalized = (data - vmin) / denom

                        # try plotly first for interactive heatmap
                        # if this is SCL, render with SCL palette and legend side-by-side using Plotly
                        if band and band.strip().upper() == "SCL":
                            logger.info("Rendering SCL single-band preview for band=%s", band)
                            scl_arr = data.astype("int")

                            # Try Plotly first
                            logger.info("Attempting Plotly SCL rendering...")
                            fig_scl = _scl_plotly_figure_from_array(scl_arr)
                            if fig_scl is not None:
                                logger.info("Plotly SCL rendering succeeded")
                                preview_display.clear()
                                with preview_display:
                                    legend_png = _scl_legend_image(box_width=48, box_height=20, pad=6)
                                    with ui.row().classes("w-full gap-2"):
                                        with ui.column().classes("flex-1"):
                                            ui.plotly(fig_scl).classes("w-full rounded-lg mt-2")
                                        with ui.column().classes("w-72"):
                                            if legend_png is not None:
                                                ui.image(source=legend_png).classes("w-full rounded-lg mt-2")
                                            else:
                                                _scl_legend_html_inline()
                                try:
                                    s.close()
                                except Exception:
                                    pass
                                return

                            # Fallback to PNG with palette mapping
                            logger.info("Plotly rendering returned None, attempting PNG fallback...")
                            try:
                                import tempfile

                                from PIL import Image

                                cmap, labels = _scl_palette_and_labels()
                                idx = np.clip(scl_arr, 0, len(cmap) - 1)
                                rgb = cmap[idx].astype("uint8")
                                tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                                tmpf.close()
                                Image.fromarray(rgb).save(tmpf.name, format="PNG")
                                logger.info("PNG fallback succeeded, writing to %s", tmpf.name)

                                preview_display.clear()
                                with preview_display:
                                    legend_png = _scl_legend_image(box_width=48, box_height=20, pad=6)
                                    with ui.row().classes("w-full gap-2"):
                                        with ui.column().classes("flex-1"):
                                            ui.image(source=tmpf.name).classes("w-full rounded-lg mt-2")
                                        with ui.column().classes("w-72"):
                                            if legend_png is not None:
                                                ui.image(source=legend_png).classes("w-full rounded-lg mt-2")
                                            else:
                                                _scl_legend_html_inline()
                                try:
                                    s.close()
                                except Exception:
                                    pass
                                return
                            except Exception as e:
                                logger.exception("SCL PNG fallback failed: %s", e)
                                pass

                        # try plotly first for interactive heatmap (non-SCL)
                        try:
                            import plotly.graph_objects as go

                            fig = go.Figure(go.Heatmap(z=normalized, colorscale="Viridis", colorbar=dict(title="scaled")))
                            # Preserve aspect ratio: size figure proportional to data shape and lock y-axis scale to x
                            rows, cols = normalized.shape if len(normalized.shape) == 2 else (normalized.shape[0], normalized.shape[1])
                            base_width = 700
                            min_h = 200
                            max_h = 900
                            try:
                                height = max(min_h, min(max_h, int(base_width * (rows / cols))))
                            except Exception:
                                height = 400
                            fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), width=base_width, height=height)
                            fig.update_yaxes(scaleanchor="x", scaleratio=1)
                            preview_display.clear()
                            with preview_display:
                                ui.plotly(fig).classes("w-full rounded-lg mt-2")
                                ui.label(f"renderer: plotly (interactive)  •  min={vmin:.3f} max={vmax:.3f}  •  shape={data.shape} dtype={data.dtype}").classes("text-xs text-gray-600 mt-1")
                            try:
                                s.close()
                            except Exception:
                                pass
                            return
                        except Exception:
                            pass

                        # fallback to static PNG (matplotlib or pillow/imageio)
                        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        tmpf.close()

                        wrote = False
                        renderer_used = "unknown"
                        try:
                            import matplotlib.pyplot as plt

                            plt.imsave(tmpf.name, normalized, cmap="viridis", vmin=0.0, vmax=1.0)
                            wrote = True
                            renderer_used = "matplotlib (viridis, imsave)"
                        except Exception:
                            try:
                                from PIL import Image

                                img = (np.clip(normalized, 0, 1) * 255).astype("uint8")
                                Image.fromarray(img).convert("L").save(tmpf.name, optimize=True)
                                wrote = True
                                renderer_used = "Pillow (grayscale)"
                            except Exception:
                                try:
                                    import imageio

                                    imageio.imwrite(tmpf.name, (np.clip(normalized, 0, 1) * 255).astype("uint8"))
                                    wrote = True
                                    renderer_used = "imageio (grayscale)"
                                except Exception:
                                    wrote = False

                        preview_display.clear()
                        with preview_display:
                            if wrote:
                                ui.image(source=tmpf.name).classes("w-full rounded-lg mt-2")
                                ui.row()
                                ui.label(f"renderer: {renderer_used}  •  min={vmin:.3f} max={vmax:.3f}  •  shape={data.shape} dtype={data.dtype}").classes("text-xs text-gray-600 mt-1")
                                ui.label(f"temp file: {tmpf.name}").classes("text-xs text-gray-500 mt-1")
                            else:
                                ui.label("Cannot write preview image; install matplotlib, plotly or Pillow (e.g. `pip install plotly matplotlib Pillow`)").classes("text-sm text-gray-600 mt-2")

                        try:
                            s.close()
                        except Exception:
                            pass

                    except Exception as e:
                        logger.exception("Error building single-band: %s", e)
                        preview_display.clear()
                        with preview_display:
                            ui.label(f"Error building band preview: {e}").classes("text-sm text-red-600 mt-2")

                def _build_and_show_all(bands_list, img_root_local, resolution_local):
                    """Create NxN grid of thumbnails for all bands and show as one image."""
                    try:
                        import math

                        import numpy as np

                        try:
                            import rasterio
                        except Exception:
                            ui.label("Rasterio not installed; cannot build band grid").classes("text-sm text-gray-600 mt-2")
                            return

                        thumbs = []
                        # limit number of bands to avoid huge images (cap at 64)
                        bands_list = bands_list[:64]
                        # keep mapping band -> original file path for re-opening (useful for SCL full render)
                        band_files_map: dict = {}
                        for band in bands_list:
                            band_file = None
                            for rootp, dirs, files in os.walk(img_root_local):
                                for f in files:
                                    m = _BAND_RE.search(f)
                                    if not m:
                                        continue
                                    b = m.group("band").upper()
                                    if b == band:
                                        band_file = os.path.join(rootp, f)
                                        break
                                if band_file:
                                    break
                            if not band_file:
                                # placeholder gray tile
                                thumbs.append(None)
                                continue
                            # remember original file for possible full-res reads later
                            band_files_map[band.upper()] = band_file
                            s = rasterio.open(band_file)
                            # downsample large bands to small thumb (e.g., 128x128)
                            from rasterio.enums import Resampling as _Res

                            # produce fixed-size square thumbnails to ensure uniform subplot sizing
                            target_h = 128
                            target_w = 128

                            # SCL is categorical: use nearest resampling and map classes to palette
                            if band.upper() == "SCL":
                                data_rs = s.read(1, out_shape=(target_h, target_w), resampling=_Res.nearest).astype("int")
                                cmap_scl, _labels = _scl_palette_and_labels()
                                # clip indices and map to RGB
                                idx = np.clip(data_rs, 0, len(cmap_scl) - 1)
                                tile_rgb = cmap_scl[idx]
                                tile_rgb = tile_rgb.astype("uint8")
                                thumbs.append(tile_rgb)
                            else:
                                data_rs = s.read(1, out_shape=(target_h, target_w), resampling=_Res.bilinear)
                                p1 = np.percentile(data_rs, 2)
                                p99 = np.percentile(data_rs, 98)
                                img = (np.clip((data_rs - p1) / max((p99 - p1), 1e-6), 0, 1) * 255).astype("uint8")
                                rgb = np.stack([img, img, img], axis=-1)
                                thumbs.append(rgb)
                            try:
                                s.close()
                            except Exception:
                                pass

                        # Helper: function to render a grid given a list of (band_name, tile) pairs
                        def render_grid(pairs, title_prefix=None):
                            import plotly.graph_objects as go
                            from plotly.subplots import make_subplots

                            n = len(pairs)
                            if n == 0:
                                return None

                            cols = int(math.ceil(math.sqrt(n)))
                            rows = int(math.ceil(n / cols))

                            titles = [p[0] for p in pairs]

                            col_w = [1.0 / cols] * cols
                            row_h = [1.0 / rows] * rows
                            fig = make_subplots(
                                rows=rows,
                                cols=cols,
                                subplot_titles=titles,
                                column_widths=col_w,
                                row_heights=row_h,
                                horizontal_spacing=0.01,
                                vertical_spacing=0.02,
                            )

                            for idx, (_name, t) in enumerate(pairs):
                                r = idx // cols + 1
                                c = idx % cols + 1
                                if t is None:
                                    tile = np.zeros((128, 128, 3), dtype="uint8") + 80
                                    trace = go.Image(z=tile)
                                else:
                                    if t.dtype != np.uint8:
                                        t_img = (np.clip(t, 0, 1) * 255).astype("uint8") if t.max() <= 1 else t.astype("uint8")
                                    else:
                                        t_img = t
                                    trace = go.Image(z=t_img)
                                fig.add_trace(trace, row=r, col=c)

                            fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
                            fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)

                            tile_px = 280
                            width = min(3000, cols * tile_px)
                            height = min(3000, rows * tile_px)
                            fig.update_layout(margin=dict(l=6, r=6, t=30, b=6), width=width, height=height, showlegend=False)
                            fig.update_xaxes(matches="x", showticklabels=False, showgrid=False, zeroline=False)
                            fig.update_yaxes(matches="y", showticklabels=False, showgrid=False, zeroline=False)
                            try:
                                for ann in fig.layout.annotations:
                                    ann.font.size = 12
                            except Exception:
                                pass

                            return fig

                        # Build mapping of band name -> thumbnail (preserve order)
                        pairs = []
                        for idx, band in enumerate(bands_list):
                            pairs.append((band, thumbs[idx] if idx < len(thumbs) else None))

                        # Prepare three separate groups for display (case-insensitive)
                        b_pairs = [(n, t) for (n, t) in pairs if n.upper().startswith("B")]
                        scl_pairs = [(n, t) for (n, t) in pairs if n.upper() == "SCL"]
                        special_pairs = [(n, t) for (n, t) in pairs if n.upper() in ("AOT", "TCI", "WVP")]

                        # Function to show a plotly figure or fallback image in preview_display
                        def show_fig_or_fallback(fig_obj):
                            preview_display.clear()
                            with preview_display:
                                if fig_obj is None:
                                    ui.label("No bands to display").classes("text-sm text-gray-600 mt-2")
                                else:
                                    try:
                                        ui.plotly(fig_obj).classes("w-full rounded-lg mt-2")
                                    except Exception:
                                        ui.label("Could not render interactive figure").classes("text-sm text-gray-600")

                        # Render and display the three requested figures in sequence (B-band grid, SCL, special group)
                        # Render all three groups into the preview area without clearing between them
                        preview_display.clear()
                        # 1) B* bands grid
                        try:
                            b_fig = render_grid(b_pairs) if b_pairs else None
                            with preview_display:
                                ui.label("B* Bands Grid").classes("text-sm font-semibold mb-1")
                                if b_fig is not None:
                                    try:
                                        ui.plotly(b_fig).classes("w-full rounded-lg mt-2")
                                    except Exception:
                                        ui.label("Could not render B* bands interactively").classes("text-sm text-gray-600 mt-2")
                                else:
                                    ui.label("No B* bands available").classes("text-sm text-gray-600 mt-2")
                        except Exception:
                            pass

                        # 2) SCL with custom colormap rendered as interactive Plotly image
                        try:
                            with preview_display:
                                ui.label("SCL (Scene Classification)").classes("text-sm font-semibold mt-4 mb-1")
                            if scl_pairs:
                                # Prefer reading the full SCL band from disk if available for accurate classes
                                scl_arr = None
                                scl_file = band_files_map.get("SCL") if "band_files_map" in locals() else None
                                if scl_file:
                                    try:
                                        s_full = rasterio.open(scl_file)
                                        scl_arr = s_full.read(1)
                                        try:
                                            s_full.close()
                                        except Exception:
                                            pass
                                    except Exception:
                                        scl_arr = None

                                # fallback: if no full file, attempt to derive class indices from thumbnail
                                if scl_arr is None:
                                    scl_tile = scl_pairs[0][1]
                                    if scl_tile is None:
                                        raise ValueError("SCL tile missing")
                                    # if thumbnail is RGB, we cannot reliably recover indices; try using first channel as proxy
                                    if getattr(scl_tile, "ndim", 0) == 3:
                                        scl_arr = scl_tile[..., 0]
                                    else:
                                        scl_arr = scl_tile

                                fig_scl = _scl_plotly_figure_from_array(scl_arr)
                                legend_png = _scl_legend_image(box_width=48, box_height=20, pad=6)
                                with preview_display:
                                    with ui.row().classes("w-full gap-2"):
                                        with ui.column().classes("flex-1"):
                                            if fig_scl is not None:
                                                try:
                                                    ui.plotly(fig_scl).classes("w-full rounded-lg mt-2")
                                                except Exception:
                                                    ui.label("Could not render SCL interactively").classes("text-sm text-gray-600 mt-2")
                                            else:
                                                ui.label("SCL rendering not available").classes("text-sm text-gray-600 mt-2")
                                        with ui.column().classes("w-72"):
                                            if legend_png is not None:
                                                ui.image(source=legend_png).classes("w-full rounded-lg mt-2")
                                            else:
                                                _scl_legend_html_inline()
                            else:
                                with preview_display:
                                    ui.label("SCL band not present in product").classes("text-sm text-gray-600 mt-2")
                        except Exception:
                            pass

                        # 3) AOT, TCI, WVP group
                        try:
                            special_fig = render_grid(special_pairs) if special_pairs else None
                            with preview_display:
                                ui.label("AOT / TCI / WVP").classes("text-sm font-semibold mt-4 mb-1")
                                if special_fig is not None:
                                    try:
                                        ui.plotly(special_fig).classes("w-full rounded-lg mt-2")
                                    except Exception:
                                        ui.label("Could not render special group interactively").classes("text-sm text-gray-600 mt-2")
                                else:
                                    ui.label("No AOT/TCI/WVP bands available").classes("text-sm text-gray-600 mt-2")
                        except Exception:
                            pass

                    except Exception as e:
                        logger.exception("Error building all-bands grid: %s", e)
                        ui.label(f"Error building band grid: {e}").classes("text-sm text-red-600 mt-2")

        except Exception as e:
            logger.error(f"Error inspecting local product: {e}")
            preview_area.clear()
            with preview_area:
                ui.label(f"Error: {e}").classes("text-sm text-red-600")

    # wire buttons
    scan_btn.on_click(lambda: _scan_folder())
    # NiceGUI doesn't provide native OS file picker here; set to home dir as a quick browse
    from pathlib import Path

    def _set_to_home():
        folder_input.value = str(Path.home())
        ui.notify(f"Set folder to {folder_input.value}", position="top", type="info")

    browse_btn.on_click(_set_to_home)

    # Wire dropdown change to auto-inspect the selected product (replaces the bottom button)
    def _on_products_select_change(e: dict):
        sel = e.value
        if not sel:
            return
        if sel not in scanned_products:
            ui.notify("⚠️ Selected product not found", position="top", type="warning")
            return
        _inspect_local_product(scanned_products[sel])

    try:
        products_select.on_change(_on_products_select_change)
    except Exception:
        # fallback for older nicegui versions
        pass


def _scl_colormap():
    """Return a matplotlib ListedColormap and BoundaryNorm for SCL values.

    Colors provided as RGB tuples (0-255) are normalized to 0-1.
    """
    try:
        from matplotlib.colors import BoundaryNorm, ListedColormap

        scl_colors = [
            (0, 0, 0),
            (255, 0, 0),
            (47, 47, 47),
            (100, 50, 0),
            (0, 160, 0),
            (255, 230, 90),
            (0, 0, 255),
            (128, 128, 128),
            (192, 192, 192),
            (255, 255, 255),
            (100, 200, 255),
            (255, 150, 255),
        ]
        scl_colors_norm = [(r / 255.0, g / 255.0, b / 255.0) for (r, g, b) in scl_colors]
        cmap = ListedColormap(scl_colors_norm)
        bounds = list(range(len(scl_colors) + 1))
        norm = BoundaryNorm(bounds, cmap.N)
        return cmap, norm
    except Exception:
        return None, None


def _scl_palette_and_labels():
    """Return SCL RGB palette (uint8) and labels list in class order 0..11."""
    labels = [
        "No Data (Missing data)",
        "Saturated or defective pixel",
        "Topographic casted shadows",
        "Cloud shadows",
        "Vegetation",
        "Not-vegetated",
        "Water",
        "Unclassified",
        "Cloud medium probability",
        "Cloud high probability",
        "Thin cirrus",
        "Snow or ice",
    ]
    colors = [
        (0, 0, 0),
        (255, 0, 0),
        (47, 47, 47),
        (100, 50, 0),
        (0, 160, 0),
        (255, 230, 90),
        (0, 0, 255),
        (128, 128, 128),
        (192, 192, 192),
        (255, 255, 255),
        (100, 200, 255),
        (255, 150, 255),
    ]
    import numpy as _np

    cmap = _np.array(colors, dtype=_np.uint8)
    return cmap, labels


def _scl_plotly_figure_from_array(scl_arr, max_width=900):
    """Create a Plotly Figure with the SCL array mapped to RGB colors."""
    try:
        import numpy as _np
        import plotly.graph_objects as go

        cmap, _labels = _scl_palette_and_labels()
        idx = _np.clip(scl_arr.astype("int"), 0, len(cmap) - 1)
        rgb = cmap[idx]
        rows, cols = rgb.shape[0], rgb.shape[1]
        fig = go.Figure(go.Image(z=rgb))
        width = min(max_width, cols)
        height = min(900, rows)
        fig.update_layout(margin=dict(l=6, r=6, t=6, b=6), width=width, height=height)
        return fig
    except Exception:
        return None


def _scl_legend_image(box_width: int = 40, box_height: int = 24, pad: int = 8, font_size: int = 12):
    """Create a vertical legend image (PNG) with color boxes next to labels and return temp file path.

    Falls back to None if Pillow is not available.
    """
    try:
        import tempfile

        from PIL import Image, ImageDraw, ImageFont

        cmap, labels = _scl_palette_and_labels()
        n = len(labels)

        # load default font
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        # measure max text width
        dummy = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy)
        max_text_w = 0
        for lab in labels:
            w, h = draw.textsize(lab, font=font)
            if w > max_text_w:
                max_text_w = w

        img_w = box_width + pad + max_text_w + pad * 2
        img_h = n * (box_height + pad) + pad
        img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        y = pad
        for i, lab in enumerate(labels):
            c = tuple(int(x) for x in cmap[i])
            # draw rectangle color box
            draw.rectangle([pad, y, pad + box_width, y + box_height], fill=c)
            # draw text to the right of box
            tx = pad + box_width + pad
            ty = y + max(0, (box_height - font.getsize(lab)[1]) // 2 if font else 0)
            draw.text((tx, ty), f"{i}: {lab}", fill=(0, 0, 0), font=font)
            y += box_height + pad

        tmpf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmpf.close()
        img.save(tmpf.name, format="PNG")
        return tmpf.name
    except Exception:
        return None


def _scl_legend_html(container):
    """Render an HTML/CSS legend inside given NiceGUI container for SCL classes.

    `container` should be a NiceGUI element context (e.g., a `with ui.column():` block).
    """
    cmap, labels = _scl_palette_and_labels()
    # vertical list: color swatch (fixed size) + label
    for i, lab in enumerate(labels):
        r, g, b = int(cmap[i][0]), int(cmap[i][1]), int(cmap[i][2])
        with container:
            with ui.row().classes("items-center gap-3"):
                ui.html(f"<div style='width:28px;height:20px;background:rgb({r},{g},{b});border:1px solid #666;'></div>", sanitize=False)
                ui.label(f"{i}: {lab}").classes("text-sm")


def _scl_legend_html_inline():
    """Render an HTML/CSS legend inline (within current UI context) for SCL classes."""
    cmap, labels = _scl_palette_and_labels()
    # vertical list: color swatch (fixed size) + label
    for i, lab in enumerate(labels):
        r, g, b = int(cmap[i][0]), int(cmap[i][1]), int(cmap[i][2])
        with ui.row().classes("items-center gap-3"):
            ui.html(f"<div style='width:28px;height:20px;background:rgb({r},{g},{b});border:1px solid #666;'></div>", sanitize=False)
            ui.label(f"{i}: {lab}").classes("text-sm")


def _create_name_search_sidebar():
    """Create the left sidebar for name-based search with filters."""
    with ui.column().classes("w-80"):
        # Product name search card
        with ui.card().classes("w-full"):
            ui.label("Search by Product Name").classes("text-lg font-semibold mb-3")

            # Product name input
            name_input = ui.input(
                label="Product Name",
                placeholder="e.g., S2A_MSIL2A_20201212T235129_...",
            ).classes("w-full mb-3")
            name_input.tooltip("Enter the full product name — everything needed is in the name")

            # Search button placed with the filters (single input only)
            search_button = ui.button("🔍 Search by Name").classes("w-full")
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

    # Parsed acquisition date (filled from product name when possible)
    parsed_acq_date = None

    # Only the product name is provided by the UI. Parse it for helpful filters.
    name_pattern = name_input.value.strip()
    # Default heuristics
    collection = None
    product_level = None
    max_results = 100

    # Try to parse product name fields using ProductName helper
    try:
        from vresto.products.product_name import ProductName

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
    ui.notify(f"🔍 Searching products for '{name_pattern}'...", position="top", type="info")
    add_message(f"🔍 Searching products for name: '{name_pattern}' (parsed collection={collection}, level={product_level})")

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
        # Perform name-based search using catalog API (server-side name filters)
        catalog = CatalogSearch()

        # Normalize pattern: remove wildcard characters, server side handles contains/eq
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

        # If the input looks like an exact product name, parsing above already attempted to extract date

        products = []
        try:
            if match_type == "eq":
                products = catalog.search_products_by_name(pattern, match_type="eq", max_results=max_results)
                if not products:
                    logger.info("Exact name search returned 0 results; trying exact with '.SAFE' suffix")
                    try:
                        products = catalog.search_products_by_name(f"{pattern}.SAFE", match_type="eq", max_results=max_results)
                    except Exception:
                        logger.exception("Exact '.SAFE' name search failed")

                if not products:
                    logger.info("Exact and '.SAFE' search returned 0 results; falling back to contains")
                    try:
                        products = catalog.search_products_by_name(pattern, match_type="contains", max_results=max(max_results, 100))
                    except Exception:
                        logger.exception("Fallback contains name search failed")
            else:
                products = catalog.search_products_by_name(pattern, match_type=match_type, max_results=max_results)
        except Exception:
            logger.exception("Name-based search failed; falling back to empty result list")

        # If we parsed an acquisition date, use it as a single-day start/end filter
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

        logger.info(f"Name search (server) returned {len(products)} products for pattern '{pattern}' (match_type tried={match_type})")

        # Apply client-side filters not supported by name API: date range and product level
        filtered_products: list = []
        filtered_out_examples: list[tuple[str, str]] = []  # (product_name, reason)
        for p in products:
            try:
                reason = None

                # Date filter
                if start_date:
                    try:
                        sensed = p.sensing_date
                        if sensed:
                            # p.sensing_date is formatted like 'YYYY-MM-DD HH:MM:SS'
                            dt = datetime.strptime(sensed, "%Y-%m-%d %H:%M:%S")
                            dt_date = dt.date()
                            sd = datetime.fromisoformat(start_date).date()
                            ed = datetime.fromisoformat(end_date).date() if end_date else sd
                            if not (sd <= dt_date <= ed):
                                reason = f"date {dt_date} outside {sd}–{ed}"
                    except Exception:
                        # If parsing fails, do not filter by date
                        pass

                # Product level filter (if parsed or apparent)
                if reason is None and product_level and product_level != "L1C + L2A":
                    try:
                        if product_level not in p.name:
                            reason = f"level not {product_level}"
                    except Exception:
                        pass

                if reason is None:
                    filtered_products.append(p)
                else:
                    if len(filtered_out_examples) < 5:
                        filtered_out_examples.append((p.name, reason))
            except Exception:
                logger.exception("Error while applying client-side filters; skipping product")

        # Display results
        results_display.clear()
        current_state["products"] = filtered_products

        # Inform user about server-return and client-side filtering
        with results_display:
            ui.label(f"Server returned {len(products)} products; {len(filtered_products)} match after client-side filters").classes("text-sm text-gray-600 mb-2")
            if filtered_out_examples:
                ui.label("Examples of filtered-out products:").classes("text-xs text-gray-500 mt-1")
                for name, reason in filtered_out_examples:
                    ui.label(f"- {name} ({reason})").classes("text-xs font-mono text-gray-500 break-all")

        if not filtered_products:
            with results_display:
                ui.label("No products found matching the criteria").classes("text-gray-500 italic mt-2")
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
