"""Sentinel Browser App - Interactive web interface for satellite product search and analysis.

This is the main entry point for the web interface. It can be run with:
    make app
    python src/vresto/ui/app.py
    vresto  (when installed as uv tool)
"""

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
    # Start the web server (blocks until interrupted)
    ui.run()


# Call ui.run() at module level for proper NiceGUI initialization
ui.run()
