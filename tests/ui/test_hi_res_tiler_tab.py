from unittest.mock import MagicMock, patch

import pytest

from vresto.ui.widgets.hi_res_tiler_tab import HiResTilerTab


@pytest.fixture
def hi_res_tiler_tab():
    tab = HiResTilerTab()
    # Mock UI elements that are created during .create()
    tab.map_widget_obj = MagicMock()
    tab.map_container = MagicMock()
    tab.products_column = MagicMock()
    tab.product_search_input = MagicMock()
    tab.product_search_input.value = ""
    tab.resolution_selector = MagicMock()
    tab.bands_container = MagicMock()
    return tab


def test_initial_state(hi_res_tiler_tab):
    assert hi_res_tiler_tab.selected_product_name is None
    assert hi_res_tiler_tab.current_img_root is None
    assert hi_res_tiler_tab.scanned_products == {}
    assert hi_res_tiler_tab.available_bands == {}
    assert hi_res_tiler_tab.selected_bands == []


def test_filter_products(hi_res_tiler_tab):
    hi_res_tiler_tab.scanned_products = {"product1.SAFE": "/path/to/product1.SAFE", "product2.SAFE": "/path/to/product2.SAFE"}
    hi_res_tiler_tab.product_search_input.value = "product1"

    with patch("vresto.ui.widgets.hi_res_tiler_tab.ui.card"), patch("vresto.ui.widgets.hi_res_tiler_tab.ui.label"), patch("vresto.ui.widgets.hi_res_tiler_tab.ui.row"), patch("vresto.ui.widgets.hi_res_tiler_tab.ui.button"):
        hi_res_tiler_tab._filter_products()

    # Check if products_column.clear() was called
    hi_res_tiler_tab.products_column.clear.assert_called()


def test_clear_map(hi_res_tiler_tab):
    hi_res_tiler_tab.selected_bands = ["B02", "B03"]
    hi_res_tiler_tab._clear_map()

    hi_res_tiler_tab.map_widget_obj.clear_tile_layers.assert_called_once()
    assert hi_res_tiler_tab.selected_bands == []


@patch("vresto.ui.widgets.hi_res_tiler_tab.Path")
@patch("vresto.ui.widgets.hi_res_tiler_tab.os.path.exists")
@patch("vresto.ui.widgets.hi_res_tiler_tab.os.walk")
def test_scan_downloads(mock_walk, mock_exists, mock_path, hi_res_tiler_tab):
    mock_exists.return_value = True
    mock_walk.return_value = [
        ("/home/user/vresto_downloads", ["P1.SAFE", "other"], []),
    ]

    hi_res_tiler_tab._scan_downloads()

    # Now .SAFE is stripped from names in scanned_products
    assert "P1" in hi_res_tiler_tab.scanned_products
    assert hi_res_tiler_tab.scanned_products["P1"] == "/home/user/vresto_downloads/P1.SAFE"


def test_on_band_toggle(hi_res_tiler_tab):
    import asyncio

    # Mock ui.context.client and ui.timer
    with patch("vresto.ui.widgets.hi_res_tiler_tab.ui.context") as mock_context, patch("vresto.ui.widgets.hi_res_tiler_tab.ui.timer") as mock_timer:
        mock_context.client = MagicMock()

        # Toggle on
        asyncio.run(hi_res_tiler_tab._on_band_toggle("B02", True))
        assert "B02" in hi_res_tiler_tab.selected_bands
        # Should now be calling via timer
        mock_timer.assert_called()

        # Toggle off
        mock_timer.reset_mock()
        asyncio.run(hi_res_tiler_tab._on_band_toggle("B02", False))
        assert "B02" not in hi_res_tiler_tab.selected_bands
        mock_timer.assert_called()


def test_zoom_to_product_no_bounds(hi_res_tiler_tab):
    with patch("vresto.ui.widgets.hi_res_tiler_tab.ui.notify") as mock_notify:
        hi_res_tiler_tab._zoom_to_product()
        mock_notify.assert_called_with("No product selected or bounds unknown", type="warning")


def test_zoom_to_product_with_bounds(hi_res_tiler_tab):
    hi_res_tiler_tab._product_bounds = (10, 20, 30, 40)
    with patch.object(hi_res_tiler_tab, "_apply_zoom_to_bounds") as mock_apply:
        hi_res_tiler_tab._zoom_to_product()
        mock_apply.assert_called_with((10, 20, 30, 40))
