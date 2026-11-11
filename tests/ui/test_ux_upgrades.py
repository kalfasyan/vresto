"""Tests for searching the map view, result footprints, shareable links, the overlay picker and date comparison."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vresto.api import ProductInfo
from vresto.ui.footprints import footprint_geometry, footprints_feature_collection, sort_products
from vresto.ui.url_state import SharedView, parse_shared_view

from .patching import patch_map_search_ui

POLYGON = [[[3.0, 50.0], [4.0, 50.0], [4.0, 51.0], [3.0, 51.0], [3.0, 50.0]]]


def _product(day: str, cloud=None, footprint=None) -> ProductInfo:
    return ProductInfo(
        id=day,
        name=f"S2B_MSIL2A_{day}T104619_N0511_R051_T31UES_{day}T120000.SAFE",
        collection="SENTINEL-2",
        sensing_date=f"{day[:4]}-{day[4:6]}-{day[6:]}T10:46:19",
        size_mb=800.0,
        cloud_cover=cloud,
        footprint=footprint,
    )


@pytest.fixture
def mock_ui():
    mock = MagicMock()
    with patch_map_search_ui(mock), patch("vresto.ui.widgets.map_widget.ui", mock):
        yield mock


class TestSharedView:
    def test_reads_every_part_of_a_link(self):
        view = parse_shared_view(
            {"ll": "50.85,4.35", "z": "9", "from": "2025-06-01", "to": "2025-06-30", "tile": "31UES", "date": "20250612", "overlay": "ndvi"},
            ["ndvi", "worldcover"],
        )
        assert view == SharedView(center=(50.85, 4.35), zoom=9, date_from="2025-06-01", date_to="2025-06-30", tile="31UES", scene_date="20250612", overlay="ndvi")

    @pytest.mark.parametrize(
        "params",
        [
            {"ll": "95,4"},
            {"ll": "abc"},
            {"z": "40"},
            {"from": "2025-13-01", "to": "nope"},
            {"tile": "ZZZZZ"},
            {"date": "20250612"},  # a scene date means nothing without its tile
            {"overlay": "<script>"},
        ],
    )
    def test_drops_values_that_do_not_parse(self, params):
        assert parse_shared_view(params, ["ndvi"]) == SharedView()

    def test_swaps_reversed_dates_and_widens_a_single_date_to_a_day(self):
        assert parse_shared_view({"from": "2025-06-30", "to": "2025-06-01"}).date_from == "2025-06-01"
        view = parse_shared_view({"to": "2025-06-12"})
        assert (view.date_from, view.date_to) == ("2025-06-12", "2025-06-12")

    def test_accepts_tile_codes_as_product_names_spell_them(self):
        assert parse_shared_view({"tile": "t31ues"}).tile == "31UES"


class TestFootprints:
    def test_geometry_from_stac_and_odata(self):
        stac = {"type": "Polygon", "coordinates": POLYGON}
        assert footprint_geometry(stac) is stac
        assert footprint_geometry(POLYGON) == {"type": "Polygon", "coordinates": POLYGON}
        assert footprint_geometry([POLYGON]) == {"type": "MultiPolygon", "coordinates": [POLYGON]}
        assert footprint_geometry(None) is None
        assert footprint_geometry("POLYGON((...))") is None

    def test_features_keep_the_list_numbers_and_skip_products_without_a_footprint(self):
        products = [_product("20250601", 12.4, POLYGON), _product("20250602"), _product("20250603", None, POLYGON)]
        features = footprints_feature_collection(products)["features"]
        assert [f["properties"]["index"] for f in features] == [1, 3]
        assert features[0]["properties"]["label"] == "1. 2025-06-01 · ☁️ 12%"
        assert features[1]["properties"]["label"] == "3. 2025-06-03"

    def test_sorting(self):
        a, b, c = _product("20250601", 30.0), _product("20250610", None), _product("20250605", 5.0)
        assert sort_products([a, b, c], "newest") == [b, c, a]
        assert sort_products([a, b, c], "oldest") == [a, c, b]
        assert sort_products([a, b, c], "cloud") == [c, a, b]  # unknown cloud cover last


class TestSearchTheMapView:
    def _tab(self):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab()
        tab.map_widget_obj = MagicMock()
        return tab

    def test_without_a_drawn_area_the_visible_map_is_searched(self, mock_ui):
        tab = self._tab()
        tab.map_widget_obj.get_view_bbox = AsyncMock(return_value=(3.0, 50.0, 5.0, 51.0))
        products = [_product("20250601", 10.0, POLYGON)]
        catalog = MagicMock()
        catalog.search_products.return_value = products
        with patch("vresto.ui.widgets.map_search_tab.CatalogSearch", return_value=catalog), patch("vresto.ui.widgets.map_search_tab.get_shared_auth"):
            asyncio.run(tab._handle_search({"collection": "SENTINEL-2", "product_level": "L2A", "max_cloud_cover": 30, "max_results": 10, "results_display": MagicMock()}))

        bbox = catalog.search_products.call_args.kwargs["bbox"]
        assert (bbox.west, bbox.south, bbox.east, bbox.north) == (3.0, 50.0, 5.0, 51.0)
        drawn = tab.map_widget_obj.set_footprints.call_args.args[0]
        assert [f["properties"]["index"] for f in drawn["features"]] == [1]

    def test_an_unreadable_view_warns_instead_of_searching(self, mock_ui):
        tab = self._tab()
        tab.map_widget_obj.get_view_bbox = AsyncMock(return_value=None)
        with patch("vresto.ui.widgets.map_search_tab.CatalogSearch") as catalog:
            asyncio.run(tab._handle_search({"collection": "SENTINEL-2", "product_level": "L2A", "results_display": MagicMock()}))
        catalog.assert_not_called()
        assert mock_ui.notify.call_args.kwargs["type"] == "warning"

    def test_a_drawn_area_still_wins(self, mock_ui):
        tab = self._tab()
        tab.current_state["bbox"] = (4.0, 50.5, 4.1, 50.6)
        tab.map_widget_obj.get_view_bbox = AsyncMock()
        catalog = MagicMock()
        catalog.search_products.return_value = []
        with patch("vresto.ui.widgets.map_search_tab.CatalogSearch", return_value=catalog), patch("vresto.ui.widgets.map_search_tab.get_shared_auth"):
            asyncio.run(tab._handle_search({"collection": "SENTINEL-2", "product_level": "L2A", "max_results": 5, "results_display": MagicMock()}))
        tab.map_widget_obj.get_view_bbox.assert_not_awaited()
        assert catalog.search_products.call_args.kwargs["bbox"].west == 4.0
        tab.map_widget_obj.clear_footprints.assert_called_once()


class TestSharedLinkRestore:
    def test_a_link_sets_the_dates_and_the_map_view(self, mock_ui):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab(shared_view=SharedView(center=(51.0, 4.0), zoom=9, date_from="2025-06-01", date_to="2025-06-30", overlay="ndvi"))
        assert tab.current_state["date_range"] == {"from": "2025-06-01", "to": "2025-06-30"}
        assert tab._pending_overlay == "ndvi"
        with patch("vresto.ui.widgets.map_search_tab.MapWidget") as map_widget:
            tab.create()
        assert map_widget.call_args.kwargs["center"] == (51.0, 4.0)
        assert map_widget.call_args.kwargs["zoom"] == 9

    def test_the_linked_scene_is_streamed_keeping_the_linked_view(self, mock_ui):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab(shared_view=SharedView(center=(51.0, 4.0), tile="31UES", scene_date="20250612"))
        wanted = _product("20250612")
        tab._get_tile_candidates = AsyncMock(return_value=[_product("20250622"), wanted])
        fits = []

        async def commit(tile, product, previous):
            fits.append(tab._fit_streamed_tile)
            assert (tile, product) == ("31UES", wanted)

        tab._commit_tile_selection = commit
        tab._handle_tile_click = AsyncMock()
        asyncio.run(tab._restore_shared_view())
        assert fits == [False]
        assert tab._fit_streamed_tile is True
        tab._handle_tile_click.assert_not_awaited()

    def test_a_scene_no_longer_listed_opens_the_tile_as_a_click_would(self, mock_ui):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab(shared_view=SharedView(tile="31UES", scene_date="20240101"))
        tab._get_tile_candidates = AsyncMock(return_value=[_product("20250622")])
        tab._handle_tile_click = AsyncMock()
        asyncio.run(tab._restore_shared_view())
        tab._handle_tile_click.assert_awaited_once_with("31UES")


class TestOverlayPicker:
    def _tab(self):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab()
        tab._overlay_switches = {"ndvi": MagicMock(value=False), "worldcover": MagicMock(value=False)}
        tab._overlay_section_rows = {"ndvi": MagicMock(), "worldcover": MagicMock()}
        tab._overlay_select = MagicMock(value="ndvi")
        tab._overlay_status_label = MagicMock()
        return tab

    def test_picking_before_a_tile_waits_for_the_first_tile(self, mock_ui):
        tab = self._tab()
        tab._on_overlay_pick(SimpleNamespace(value="ndvi"))
        assert tab._pending_overlay == "ndvi"
        tab._overlay_switches["ndvi"].set_value.assert_not_called()
        tab._overlay_section_rows["ndvi"].set_visibility.assert_called_with(True)
        tab._overlay_section_rows["worldcover"].set_visibility.assert_called_with(False)

        tab._streaming_tile_code = "31UES"
        tab._activate_pending_overlay()
        tab._overlay_switches["ndvi"].set_value.assert_called_once_with(True)
        assert tab._pending_overlay is None

    def test_picking_with_a_tile_turns_the_overlay_on(self, mock_ui):
        tab = self._tab()
        tab._streaming_tile_code = "31UES"
        tab._on_overlay_pick(SimpleNamespace(value="worldcover"))
        tab._overlay_switches["worldcover"].set_value.assert_called_once_with(True)

    def test_none_turns_the_active_overlay_off(self, mock_ui):
        tab = self._tab()
        tab._streaming_tile_code = "31UES"
        tab._active_overlay = "ndvi"
        tab._on_overlay_pick(SimpleNamespace(value=""))
        tab._overlay_switches["ndvi"].set_value.assert_called_once_with(False)

    def test_the_active_overlay_goes_into_the_link(self, mock_ui):
        tab = self._tab()
        tab._sync_overlay_sections("worldcover")
        tab._overlay_select.set_value.assert_called_once_with("worldcover")
        assert 'window.vrestoUrl.set({"overlay": "worldcover"})' in mock_ui.run_javascript.call_args.args[0]


class TestCompareDates:
    def _tab(self):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab()
        tab.map_widget_obj = MagicMock()
        tab._streaming_tile_code = "31UES"
        tab._streaming_date = "20250612"
        tab._compare_stop_button = MagicMock()
        return tab

    def test_the_second_date_is_shown_left_of_the_divider(self, mock_ui):
        tab = self._tab()
        tab._fetch_tci = AsyncMock(return_value="/cache/b.tif")
        with patch("vresto.services.tiles.tile_pool.get_or_create", return_value="http://tiles/{z}/{x}/{y}"):
            asyncio.run(tab._start_compare(_product("20250622")))
        tab.map_widget_obj.add_tile_layer.assert_called_once_with("http://tiles/{z}/{x}/{y}", name="compare_31UES")
        tab.map_widget_obj.enable_swipe.assert_called_once_with("compare_31UES", "2025-06-22", "2025-06-12")
        assert tab._compare_layer == "compare_31UES"

    def test_stopping_removes_the_layer_and_divider(self, mock_ui):
        tab = self._tab()
        tab._compare_layer = "compare_31UES"
        with patch("vresto.services.tiles.tile_pool.remove") as remove:
            tab._stop_compare()
        remove.assert_called_once_with("compare_31UES")
        tab.map_widget_obj.disable_swipe.assert_called_once()
        tab.map_widget_obj.remove_tile_layer.assert_called_once_with("compare_31UES")
        assert tab._compare_layer is None

    def test_a_failed_stream_adds_nothing(self, mock_ui):
        tab = self._tab()
        tab._fetch_tci = AsyncMock(return_value=None)
        asyncio.run(tab._start_compare(_product("20250622")))
        tab.map_widget_obj.add_tile_layer.assert_not_called()
        tab.map_widget_obj.enable_swipe.assert_not_called()
