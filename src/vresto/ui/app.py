"""Sentinel Browser App - Interactive web interface for satellite product search and analysis.

This is the main entry point for the web interface. It can be run with:
    make app
    python src/vresto/ui/app.py
    vresto app (if configured)
"""

import os

from nicegui import ui

from vresto.ui.map_interface import create_map_interface
from vresto.ui.widgets.credentials_menu import CredentialsMenu


@ui.page("/")
def index_page():
    """Create the main page UI."""
    # Global styling
    ui.add_head_html("""
        <style>
            :root {
                --nicegui-default-padding: 0.5rem;
                --nicegui-default-gap: 0.5rem;
            }
            body {
                background-color: #f8fafc; /* slate-50 */
            }
        </style>
    """)

    # Header
    with ui.header(elevated=True).classes("bg-blue-grey-10 text-white h-16 px-4 flex items-center gap-4 border-b border-slate-700"):
        # Menu Button
        with ui.button(on_click=lambda: drawer.toggle()).props("flat color=white round dense icon=menu"):
            ui.tooltip("Toggle Settings")

        # Logo / Title
        with ui.row().classes("items-center gap-3"):
            ui.icon("public", size="md").classes("text-orange-3")
            ui.label("Sentinel Browser").classes("text-xl font-bold tracking-wide text-white")

        ui.space()

        # Header Actions (Right Side)
        with ui.row().classes("items-center gap-2"):

            def show_help():
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label("Help & Resources").classes("text-xl font-bold text-slate-800")
                    ui.label("Sentinel Browser v0.1").classes("text-xs text-slate-500 mb-4")

                    ui.label("This application allows you to search, view, and download Sentinel-1/2/3/5P products from the Copernicus Data Space Ecosystem.").classes("text-sm text-slate-600 mb-4")

                    ui.label("Useful Links:").classes("font-semibold text-slate-700")
                    with ui.column().classes("gap-1 ml-2 mb-4"):
                        ui.link("Project Documentation", "https://kalfasyan.github.io/vresto/").classes("text-sm text-blue-600 no-underline hover:underline").props("target=_blank")
                        ui.link("Copernicus Data Space", "https://dataspace.copernicus.eu/").classes("text-sm text-blue-600 no-underline hover:underline").props("target=_blank")
                        ui.link("Report an Issue", "https://github.com/kalfasyan/vresto/issues").classes("text-sm text-blue-600 no-underline hover:underline").props("target=_blank")

                    with ui.row().classes("w-full justify-end"):
                        ui.button("Close", on_click=dialog.close).props("outline")
                dialog.open()

            with ui.button(on_click=show_help).props("flat color=white round dense icon=help_outline"):
                ui.tooltip("Help & Documentation")

    # Left Drawer
    with ui.left_drawer(value=False).classes("bg-white border-r border-slate-200 w-80 shadow-lg") as drawer:
        with ui.column().classes("w-full h-full p-4 gap-4"):
            ui.label("Settings").classes("text-xl font-bold text-slate-800 tracking-tight")
            ui.separator().classes("bg-slate-200")

            # Credentials Menu Widget
            credentials_menu = CredentialsMenu()
            credentials_menu.create()

            ui.space()
            ui.label("Sentinel Browser v0.1").classes("text-xs text-slate-400 self-center")

    # Main Content Area
    # We use a column wrapper to ensure proper spacing from the header and consistent margins
    with ui.column().classes("w-full p-4 gap-4"):
        create_map_interface()


def main():
    """Main entry point for the Sentinel Browser web interface.

    This function is called when the script is executed directly.
    """
    # Get port and host from environment variables
    port = int(os.getenv("NICEGUI_WEBSERVER_PORT", 8080))
    host = os.getenv("NICEGUI_WEBSERVER_HOST", "0.0.0.0")

    # Start the web server
    ui.run(
        title="Sentinel Browser",
        host=host,
        port=port,
        favicon="🛰️",
        dark=False,  # Explicitly light mode as requested
        reload=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
