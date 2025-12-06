"""Map interface with date range selection and marker drawing capabilities."""

from datetime import datetime

from loguru import logger
from nicegui import events, ui


def create_map_interface():
    """Create a beautiful interface with date range selection and interactive map."""
    # Header
    ui.label("Location & Date Selector").classes("text-3xl font-bold mb-6")

    # Main layout: Date picker and activity log on left, map in center
    with ui.row().classes("w-full gap-6"):
        # Left sidebar: Date picker and activity log
        date_picker, messages_column = _create_sidebar()

        # Map with draw controls
        _create_map(messages_column)

    return {"date_picker": date_picker}


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
        ui.label("Select Date (or Range)").classes("text-lg font-semibold mb-3")

        today = datetime.now().strftime("%Y-%m-%d")
        date_picker = ui.date(value=today).props("range")
        date_picker.classes("w-full")

        date_display = ui.label("").classes("text-sm text-blue-600 mt-3 font-medium")

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
        else:
            value_str = str(current_value)
            date_display.text = f"📅 {current_value}"
            message = f"📅 Date selected: {current_value}"

        # Log only if value has changed
        if value_str != last_logged["value"]:
            last_logged["value"] = value_str
            logger.info(message)
            add_message(message)

    # Poll for changes periodically
    ui.timer(0.5, check_date_change)


def _create_map(messages_column):
    """Create the map with drawing controls."""
    with ui.card().classes("flex-1"):
        ui.label("Mark Locations").classes("text-lg font-semibold mb-3")

        # Configure drawing tools
        draw_control = {
            "draw": {
                "polygon": True,
                "marker": True,
                "circlemarker": True,
                "polyline": True,
                "rectangle": True,
                "circle": False,
            },
            "edit": {
                "edit": True,
                "remove": True,
            },
        }

        # Create map centered on Leuven, Belgium
        m = ui.leaflet(center=(50.8798, 4.7005), zoom=13, draw_control=draw_control)
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

    m.on("draw:created", handle_draw)
    m.on("draw:edited", handle_edit)
    m.on("draw:deleted", handle_delete)


if __name__ in {"__main__", "__mp_main__"}:
    with ui.column().classes("w-full h-screen p-6"):
        create_map_interface()

    ui.run()
