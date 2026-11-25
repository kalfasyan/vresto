"""Map interface - orchestrates tab widgets for product search, download, and analysis.

The interface provides a tabbed UI for:
1. Map Search - search by location and date range
2. Name Search - search by product name pattern
3. Download - fetch and download product bands
4. Product Analysis - inspect downloaded products locally
"""

import threading
import uuid

from nicegui import ui

from vresto.services.tiles import tile_pool
from vresto.ui.public_mode import VisitorCredentials
from vresto.ui.url_state import SharedView, parse_shared_view
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


def read_shared_view(params) -> SharedView:
    """Read the view a shared link carries from its query parameters (see :mod:`vresto.ui.url_state`)."""
    from vresto.ui.overlays import OVERLAY_NAMES

    return parse_shared_view(params, OVERLAY_NAMES)


def create_map_interface(visitor: VisitorCredentials | None = None, shared_view: SharedView | None = None):
    """Create the main tabbed interface orchestrating all tab widgets.

    Args:
        visitor: In public mode, this visitor's in-memory credentials. The tabs then use them
            instead of the machine's, downloads go to the browser, and the tabs that read the
            server's disk (Product Analysis, Hi-Res Tiler) are left out.
        shared_view: The view a shared link opened, restored in the Map Search tab.

    Returns a dict with references to tab content for testing/inspection.
    """
    public = visitor is not None

    # Create tab headers
    with ui.tabs().props('appearance="underline"').classes("w-full mb-2") as tabs:
        map_tab = ui.tab("Map Search", icon="map")
        name_tab = ui.tab("Search by Name", icon="search")
        download_tab = ui.tab("Download Product", icon="download")
        if not public:
            analysis_tab = ui.tab("Product Analysis", icon="folder_open")
            viewer_tab = ui.tab("Hi-Res Tiler", icon="visibility")

    if public:
        # Each visitor gets their own viewer (their credentials) and their own tile pool names.
        viewer = ProductViewerWidget(visitor=visitor)
        pool_namespace = f"visitor_{uuid.uuid4().hex[:12]}_"

        async def on_quicklook(product, messages_column):
            await viewer.show_quicklook(product, messages_column)

        async def on_metadata(product, messages_column):
            await viewer.show_metadata(product, messages_column)

        _on_visitor_leave(visitor, pool_namespace)
    else:
        on_quicklook, on_metadata, pool_namespace = _show_product_quicklook, _show_product_metadata, ""

    # Built before the search tabs so their Download buttons can hand products to it.
    download_widget = DownloadTab(visitor=visitor)

    async def _open_in_download_tab(product):
        tabs.set_value(download_tab)
        await download_widget.load_product(product.name)

    # Tab content panels
    with ui.tab_panels(tabs, value=map_tab).classes("w-full"):
        with ui.tab_panel(map_tab):
            # Map Search tab - instantiate the widget and render it
            map_search_widget = MapSearchTab(
                on_quicklook=on_quicklook,
                on_metadata=on_metadata,
                on_download=_open_in_download_tab,
                pool_namespace=pool_namespace,
                shared_view=shared_view,
            )
            map_search_content = map_search_widget.create()

        with ui.tab_panel(name_tab):
            # Name Search tab
            name_search_widget = NameSearchTab(
                on_quicklook=on_quicklook,
                on_metadata=on_metadata,
                on_download=_open_in_download_tab,
            )
            name_search_content = name_search_widget.create()

        with ui.tab_panel(download_tab):
            # Download tab
            download_content = download_widget.create()

        analysis_content = viewer_content = None
        if not public:
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


def _on_visitor_leave(visitor: VisitorCredentials, pool_namespace: str) -> None:
    """Forget a visitor's credentials and stop their tile servers when their tab closes."""
    try:
        client = ui.context.client
    except Exception:  # noqa: BLE001 - outside a page (e.g. in tests) there is nothing to hook
        return

    def _cleanup():
        visitor.clear()
        threading.Thread(target=tile_pool.remove_prefix, args=(pool_namespace,), name="vresto-visitor-cleanup", daemon=True).start()

    client.on_delete(_cleanup)
