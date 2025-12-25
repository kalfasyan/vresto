"""Sentinel Browser App - Interactive web interface for satellite product search and analysis.

This is the main entry point for the web interface. It can be run with:
    make app
    python src/vresto/ui/app.py
    vresto  (when installed as uv tool)
"""

import os

from nicegui import ui

from vresto.ui.map_interface import create_map_interface
from vresto.ui.widgets.credentials_menu import CredentialsMenu


@ui.page("/")
def index_page():
    """Create the main page UI."""
    # Create a left drawer for the credentials menu
    with ui.left_drawer().classes("bg-gray-50 p-4") as drawer:
        drawer.value = False  # Keep drawer closed by default
        ui.label("Settings").classes("text-lg font-bold mb-4")
        credentials_menu = CredentialsMenu()
        credentials_menu.create()

    # Main header with hamburger menu button
    with ui.header().classes("bg-blue-500 shadow-md p-4"):
        with ui.row().classes("w-full items-center gap-4"):
            menu_button = ui.button(icon="menu", on_click=drawer.toggle)
            menu_button.props("outline").classes("text-white text-2xl border-white")
            ui.label("Sentinel Browser").classes("text-2xl font-bold flex-1 text-white")

    # Main content area
    with ui.column().classes("w-full p-6"):
        create_map_interface()


def main():
    """Main entry point for the Sentinel Browser web interface.

    This function is called when the vresto command is executed or when running directly.
    It sets up the UI and starts the web server.
    """
    # Get port and host from environment variables
    port = int(os.getenv("NICEGUI_WEBSERVER_PORT", 8080))
    host = os.getenv("NICEGUI_WEBSERVER_HOST", "0.0.0.0")

    # Start the web server (blocks until interrupted)
    ui.run(host=host, port=port)


# Call ui.run() at module level for proper NiceGUI initialization
port = int(os.getenv("NICEGUI_WEBSERVER_PORT", 8080))
host = os.getenv("NICEGUI_WEBSERVER_HOST", "0.0.0.0")
ui.run(host=host, port=port)
