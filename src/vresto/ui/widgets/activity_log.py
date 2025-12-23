"""Activity log widget for displaying messages in a scrollable area."""

from nicegui import ui


class ActivityLogWidget:
    """Encapsulates activity log display with message history.

    Usage:
        activity_log = ActivityLogWidget(title="Activity Log")
        messages_column = activity_log.create()
        activity_log.add_message("✅ Task completed")
    """

    def __init__(self, title: str = "Activity Log"):
        self.title = title
        self.messages_column = None

    def create(self):
        """Create and return the activity log UI (messages column).

        Returns the `ui.column()` container that will hold message labels.
        """
        with ui.card().classes("w-full flex-1 p-3 shadow-sm rounded-lg"):
            ui.label(self.title).classes("text-lg font-semibold mb-3")

            with ui.scroll_area().classes("w-full h-96"):
                self.messages_column = ui.column().classes("w-full gap-2")

        return self.messages_column

    def add_message(self, text: str):
        """Add a message to the activity log.

        If the UI hasn't been created yet, this is a no-op.
        """
        if self.messages_column is None:
            return
        with self.messages_column:
            ui.label(text).classes("text-sm text-gray-700 break-words")
