"""Map interface - orchestrates tab widgets for product search, download, and analysis.

The interface provides a tabbed UI for:
1. Map Search - search by location and date range
2. Name Search - search by product name pattern
3. Download - fetch and download product bands
4. Product Analysis - inspect downloaded products locally
"""

import os
from pathlib import Path

from nicegui import ui

from vresto.ui.widgets.credentials_menu import CredentialsMenu
from vresto.ui.widgets.download_tab import DownloadTab
from vresto.ui.widgets.hi_res_tiler_tab import HiResTilerTab
from vresto.ui.widgets.map_search_tab import MapSearchTab
from vresto.ui.widgets.name_search_tab import NameSearchTab
from vresto.ui.widgets.product_analysis_tab import ProductAnalysisTab
from vresto.ui.widgets.product_viewer import ProductViewerWidget

# Lazy-initialized product viewer instance
_product_viewer = None


def _get_product_viewer():
    """Get or create the shared product viewer instance."""
    global _product_viewer
    if _product_viewer is None:
        _product_viewer = ProductViewerWidget()
    return _product_viewer


async def _show_product_quicklook(product, messages_column):
    """Show quicklook image for a product using ProductViewerWidget."""
    viewer = _get_product_viewer()
    await viewer.show_quicklook(product, messages_column)


async def _show_product_metadata(product, messages_column):
    """Show metadata for a product using ProductViewerWidget."""
    viewer = _get_product_viewer()
    await viewer.show_metadata(product, messages_column)


def create_map_interface():
    """Create the main tabbed interface orchestrating all tab widgets.

    Returns a dict with references to tab content for testing/inspection.
    """
    # Create tab headers
    with ui.tabs().props('appearance="underline"').classes("w-full mb-2") as tabs:
        map_tab = ui.tab("Map Search", icon="map")
        name_tab = ui.tab("Search by Name", icon="search")
        download_tab = ui.tab("Download Product", icon="download")
        analysis_tab = ui.tab("Product Analysis", icon="folder_open")
        viewer_tab = ui.tab("Hi-Res Tiler", icon="visibility")

    # Tab content panels
    with ui.tab_panels(tabs, value=map_tab).classes("w-full"):
        with ui.tab_panel(map_tab):
            # Map Search tab - instantiate the widget and render it
            map_search_widget = MapSearchTab(
                on_quicklook=_show_product_quicklook,
                on_metadata=_show_product_metadata,
            )
            map_search_content = map_search_widget.create()

        with ui.tab_panel(name_tab):
            # Name Search tab
            name_search_widget = NameSearchTab(
                on_quicklook=_show_product_quicklook,
                on_metadata=_show_product_metadata,
            )
            name_search_content = name_search_widget.create()

        with ui.tab_panel(download_tab):
            # Download tab
            download_widget = DownloadTab()
            download_content = download_widget.create()

        with ui.tab_panel(analysis_tab):
            # Product Analysis tab
            analysis_widget = ProductAnalysisTab()
            analysis_content = analysis_widget.create()

        with ui.tab_panel(viewer_tab):
            # Hi-Res Tiler tab
            viewer_widget = HiResTilerTab()
            viewer_content = viewer_widget.create()

    return {
        "tabs": tabs,
        "map_search": map_search_content,
        "name_search": name_search_content,
        "download": download_content,
        "analysis": analysis_content,
        "hi_res_tiler": viewer_content,
    }


@ui.page("/")
def index_page():
    """Standalone page for running this module directly."""
    _STATIC_DIR = Path(__file__).parent / "static"
    dark_mode = ui.dark_mode()

    css_file = _STATIC_DIR / "style.css"
    if css_file.exists():
        ui.add_head_html(f"<style>{css_file.read_text()}</style>")

    with ui.header(elevated=True).classes("bg-slate-900 text-white h-16 px-4 flex items-center gap-4 border-b border-slate-700"):
        with ui.button(on_click=lambda: drawer.toggle()).props("flat color=white round dense icon=menu"):
            ui.tooltip("Toggle Settings")

        with ui.row().classes("items-center gap-3"):
            ui.icon("public", size="md").classes("text-orange-3")
            ui.label("Sentinel Browser").classes("text-xl font-bold tracking-wide text-white")

        ui.space()

        with ui.row().classes("items-center gap-2"):

            def show_help():
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label("Help & Resources").classes("text-xl font-bold text-slate-800 dark:text-slate-100")
                    ui.label("Sentinel Browser v0.1").classes("text-xs text-slate-500 dark:text-slate-400 mb-4")
                    ui.label("This application allows you to search, view, and download Sentinel-1/2/3/5P products from the Copernicus Data Space Ecosystem.").classes("text-sm text-slate-600 dark:text-slate-300 mb-4")
                    ui.label("Useful Links:").classes("font-semibold text-slate-700 dark:text-slate-200")
                    with ui.column().classes("gap-1 ml-2 mb-4"):
                        ui.link("Project Documentation", "https://kalfasyan.github.io/vresto/").classes("text-sm text-blue-600 dark:text-sky-400 no-underline hover:underline").props("target=_blank")
                        ui.link("Copernicus Data Space", "https://dataspace.copernicus.eu/").classes("text-sm text-blue-600 dark:text-sky-400 no-underline hover:underline").props("target=_blank")
                        ui.link("Report an Issue", "https://github.com/kalfasyan/vresto/issues").classes("text-sm text-blue-600 dark:text-sky-400 no-underline hover:underline").props("target=_blank")
                    with ui.row().classes("w-full justify-end"):
                        ui.button("Close", on_click=dialog.close).props("outline")
                dialog.open()

            with ui.button(on_click=dark_mode.toggle).props("flat color=white round dense icon=brightness_4"):
                ui.tooltip("Toggle Dark Mode")

            with ui.button(on_click=show_help).props("flat color=white round dense icon=help_outline"):
                ui.tooltip("Help & Documentation")

    with ui.left_drawer(value=False).classes("dark:bg-slate-900 border-r border-slate-200 w-80 shadow-lg") as drawer:
        with ui.column().classes("w-full h-full p-4 gap-4"):
            ui.label("Settings").classes("text-xl font-bold text-slate-800 tracking-tight dark:text-slate-100")
            ui.separator().classes("bg-slate-200 dark:bg-slate-700")
            credentials_menu = CredentialsMenu()
            credentials_menu.create()
            ui.space()
            ui.label("Sentinel Browser v0.1").classes("text-xs text-slate-400 self-center")

    with ui.column().classes("w-full p-4 gap-4"):
        create_map_interface()


def main():
    """Run the map interface as a standalone NiceGUI app."""
    port = int(os.getenv("NICEGUI_WEBSERVER_PORT", 8080))
    host = os.getenv("NICEGUI_WEBSERVER_HOST", "0.0.0.0")

    ui.run(
        title="Sentinel Browser",
        host=host,
        port=port,
        favicon="🛰️",
        dark=None,
        reload=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
