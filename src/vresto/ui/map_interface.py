"""Map interface - orchestrates tab widgets for product search, download, and analysis.

The interface provides a tabbed UI for:
1. Map Search - search by location and date range
2. Name Search - search by product name pattern
3. Download - fetch and download product bands
4. Product Analysis - inspect downloaded products locally
"""

from nicegui import ui

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
