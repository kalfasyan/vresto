"""Date picker widget extracted from map_interface.

Provides DatePickerWidget which encapsulates creation and monitoring
of a NiceGUI date range picker and activity logging.
"""

from typing import Callable, Optional

from loguru import logger
from nicegui import ui


class DatePickerWidget:
    """Encapsulate date picker UI and monitoring.

    The widget creates the date picker and a small display label.
    Supports optional callback when date range changes.

    Args:
        default_from: default start date (YYYY-MM-DD)
        default_to: default end date (YYYY-MM-DD)
        on_message: optional callback invoked when an activity message is added
        on_date_change: optional callback invoked with (from_date, to_date) when range changes
    """

    def __init__(
        self,
        default_from: str = "2020-01-01",
        default_to: str = "2020-01-31",
        on_message: Optional[Callable[[str], None]] = None,
        on_date_change: Optional[Callable[[str, str], None]] = None,
    ):
        self.default_from = default_from
        self.default_to = default_to
        self.on_message = on_message
        self.on_date_change = on_date_change

    def create(self):
        """Create the date picker UI elements.

        Returns:
            tuple: (date_picker, date_display)
        """
        with ui.card().classes("w-full p-3 shadow-sm rounded-lg"):
            ui.label("Select date (or range)").classes("text-lg font-semibold mb-1")

            date_picker = ui.date(value={"from": self.default_from, "to": self.default_to}).props("range")
            date_picker.classes("w-full")

            date_display = ui.label("").classes("text-sm text-blue-600 mt-3 font-medium")

        return date_picker, date_display

    def setup_monitoring(self, date_picker, date_display, messages_column):
        """Set up polling to monitor date changes and log activity.

        Uses callbacks instead of global state to communicate date changes.
        """
        last_logged = {"value": None}

        def add_message(text: str):
            with messages_column:
                ui.label(text).classes("text-sm text-gray-700 break-words")
            if self.on_message:
                try:
                    self.on_message(text)
                except Exception:
                    logger.exception("Error in DatePickerWidget on_message callback")

        def check_date_change():
            current_value = date_picker.value

            if isinstance(current_value, dict):
                value_str = f"{current_value.get('from', '')}-{current_value.get('to', '')}"
                start = current_value.get("from", "")
                end = current_value.get("to", "")
                date_display.text = f"📅 {start} to {end}"
                message = f"📅 Date range selected: {start} to {end}"
                # Invoke callback if provided
                if self.on_date_change:
                    try:
                        self.on_date_change(start, end)
                    except Exception:
                        logger.exception("Error in DatePickerWidget on_date_change callback")
            else:
                value_str = str(current_value)
                date_display.text = f"📅 {current_value}"
                message = f"📅 Date selected: {current_value}"
                if self.on_date_change:
                    try:
                        self.on_date_change(current_value, current_value)
                    except Exception:
                        logger.exception("Error in DatePickerWidget on_date_change callback")

            if value_str != last_logged["value"]:
                last_logged["value"] = value_str
                logger.info(message)
                add_message(message)

        # Initialize display and start polling
        check_date_change()
        ui.timer(0.5, check_date_change)
