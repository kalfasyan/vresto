"""Sentinel Browser App - Interactive web interface for satellite product search and analysis.

This is the main entry point for the web interface. It can be run with:
    make app
    python src/vresto/ui/app.py
    vresto  (when installed as uv tool)
"""

import os

from nicegui import ui

from vresto.ui.map_interface import create_map_interface


@ui.page("/")
def index_page():
    """Create the main page UI."""
    with ui.column().classes("w-full h-screen p-6"):
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
