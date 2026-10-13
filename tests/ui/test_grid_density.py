"""Tests for colouring the MGRS grid by product count.

Covers the scripts MapWidget injects to restyle the grid, and the MapSearchTab logic that
decides when to count products and whether a result may still colour the map.
"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vresto.services.tile_density import STATS_SOURCE, TileDensity
from vresto.ui.widgets.map_search_tab import DENSITY_RATE_LIMITED_TEXT
from vresto.ui.widgets.map_widget import GRID_BASE_STYLE, GRID_DENSITY_BINS, MapWidget, grid_density_legend

BBOX = (4.3, 50.2, 4.4, 50.3)
DENSITY_PATCH = "vresto.ui.widgets.map_search_tab.count_products_per_tile"
DEBOUNCE_PATCH = "vresto.ui.widgets.map_search_tab.DENSITY_DEBOUNCE_S"


@pytest.fixture
def mock_ui():
    """Stub NiceGUI's ui in the two modules that draw the grid."""
    mock = MagicMock()
    with patch("vresto.ui.widgets.map_widget.ui", mock), patch("vresto.ui.widgets.map_search_tab.ui", mock):
        yield mock


def _map_widget() -> MapWidget:
    widget = MapWidget()
    widget._map = MagicMock(id=7)
    return widget


def _last_js(mock_ui) -> str:
    return mock_ui.run_javascript.call_args[0][0]


class TestMapWidgetGridDensity:
    def test_update_restyles_the_existing_layer_instead_of_redrawing_it(self, mock_ui):
        _map_widget().update_grid_density({"31UFS": {"count": 12, "min_cloud": 3.2}})

        js = _last_js(mock_ui)
        assert "window._vrestoGridLayer.eachLayer" in js
        assert "L.geoJSON" not in js
        assert "removeLayer" not in js

    def test_counts_and_bins_are_json_encoded_into_the_script(self, mock_ui):
        density = {"31UFS": {"count": 12, "min_cloud": 3.2}}

        _map_widget().update_grid_density(density, truncated=True)

        js = _last_js(mock_ui)
        assert f"const data = {json.dumps({'density': density, 'truncated': True})};" in js
        assert f"const bins = {json.dumps(GRID_DENSITY_BINS)};" in js

    def test_zero_counts_are_unknown_when_the_search_was_truncated(self, mock_ui):
        _map_widget().update_grid_density({}, truncated=True)

        assert "const certain = info.count > 0 || !data.truncated;" in _last_js(mock_ui)

    def test_a_new_grid_layer_starts_each_tile_in_the_flat_style(self, mock_ui):
        _map_widget().set_grid_layer({"type": "FeatureCollection", "features": []})

        assert f"layer._vrestoBaseStyle = {json.dumps(GRID_BASE_STYLE)};" in _last_js(mock_ui)

    def test_hover_restores_the_tile_base_style_not_the_flat_default(self, mock_ui):
        _map_widget().set_grid_layer({"type": "FeatureCollection", "features": []})

        js = _last_js(mock_ui)
        assert "layer.setStyle(window._vrestoTileStyle(layer))" in js
        assert "layer.setStyle({fillOpacity" not in js

    def test_highlight_and_clear_respect_the_base_style(self, mock_ui):
        widget = _map_widget()

        widget.highlight_tile("31UFS")
        assert "layer._vrestoHighlighted = true;" in _last_js(mock_ui)

        widget.clear_tile_highlight()
        js = _last_js(mock_ui)
        assert "layer._vrestoHighlighted = false;" in js
        assert "layer.setStyle(window._vrestoTileStyle(layer))" in js

    def test_clear_grid_density_returns_tiles_to_the_flat_style(self, mock_ui):
        _map_widget().clear_grid_density()

        js = _last_js(mock_ui)
        assert "layer._vrestoDensity = null;" in js
        assert f"layer._vrestoBaseStyle = {json.dumps(GRID_BASE_STYLE)};" in js

    def test_tooltip_wording_covers_counts_cloud_cover_and_the_click_hint(self, mock_ui):
        _map_widget().set_grid_layer({"type": "FeatureCollection", "features": []})

        js = _last_js(mock_ui)
        for fragment in ["no products in date range", "' products'", "' — click to stream'", "' · best '", "'% cloud'", "'≥'"]:
            assert fragment in js

    def test_density_calls_do_nothing_before_the_map_exists(self, mock_ui):
        widget = MapWidget()
        widget.update_grid_density({"31UFS": {"count": 1, "min_cloud": None}})
        widget.clear_grid_density()

        mock_ui.run_javascript.assert_not_called()

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_injected_scripts_are_valid_javascript(self, mock_ui, tmp_path):
        widget = _map_widget()
        widget.set_grid_layer({"type": "FeatureCollection", "features": []})
        widget.update_grid_density({"31UFS": {"count": 12, "min_cloud": 3.2}}, truncated=True)
        widget.highlight_tile("31UFS")
        widget.clear_tile_highlight()
        widget.clear_grid_density()

        assert mock_ui.run_javascript.call_count == 5
        for number, call in enumerate(mock_ui.run_javascript.call_args_list):
            script = tmp_path / f"grid_{number}.js"
            script.write_text(call.args[0], encoding="utf-8")
            result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr


def test_legend_labels_follow_the_density_bins():
    assert [label for label, _ in grid_density_legend()] == ["<3", "3–5", "6–8", "9–11", "12+"]


def _tab(grid_bbox=BBOX):
    from vresto.ui.widgets.map_search_tab import MapSearchTab

    tab = MapSearchTab()
    tab.map_widget_obj = MagicMock()
    tab.map_widget = MagicMock()
    tab._grid_bbox = grid_bbox
    tab.current_state["date_range"] = {"from": "2024-01-01", "to": "2024-01-31"}
    return tab


DENSITY = TileDensity(counts={"31UFS": 2}, min_cloud={"31UFS": 4.0}, truncated=False)


class TestStreamedTileStaysVisible:
    def test_streamed_tile_is_marked_and_the_grid_restyled(self, mock_ui):
        _map_widget().set_streamed_tile("31UFS")

        js = _last_js(mock_ui)
        assert 'window._vrestoStreamedCode = "31UFS";' in js
        assert "layer.setStyle(window._vrestoTileStyle(layer))" in js

    @pytest.mark.parametrize("method", ["update_grid_density", "clear_grid_density", "clear_tile_highlight"])
    def test_every_restyle_keeps_the_streamed_tile_unfilled(self, mock_ui, method):
        widget = _map_widget()
        args = ({"31UFS": {"count": 1, "min_cloud": None, "rate": 1.0}},) if method == "update_grid_density" else ()

        getattr(widget, method)(*args)

        js = _last_js(mock_ui)
        assert "window._vrestoTileStyle = function(layer)" in js
        assert "setStyle(window._vrestoTileStyle(layer))" in js
        assert "setStyle(layer._vrestoBaseStyle)" not in js

    def test_streaming_a_tile_marks_it_on_the_grid(self, mock_ui):
        tab = _tab()
        with (
            patch.object(tab, "_stream_product", return_value=True),
            patch.object(tab, "_update_tcd_overlay_availability"),
            patch.object(tab, "_refresh_lst_time_options"),
            patch.object(tab, "_reload_enabled_overlays"),
            patch.object(tab, "_set_overlay_controls_enabled"),
        ):
            asyncio.run(tab._commit_tile_selection("31UFS", MagicMock(), None))

        tab.map_widget_obj.set_streamed_tile.assert_called_once_with("31UFS")


class TestMapSearchTabGridDensity:
    def test_grid_is_coloured_with_the_counts_for_the_current_dates(self, mock_ui):
        tab = _tab()
        tab._grid_codes = ["31UFS", "31UFT"]
        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, return_value=DENSITY) as count:
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        count.assert_called_once_with(BBOX, "2024-01-01", "2024-01-31", tile_codes=["31UFS", "31UFT"])
        tab.map_widget_obj.update_grid_density.assert_called_once_with({"31UFS": {"count": 2, "min_cloud": 4.0, "rate": 2.0}}, truncated=False)

    def test_moving_the_map_records_the_drawn_tile_codes(self, mock_ui):
        tab = _tab()
        geojson = {"type": "FeatureCollection", "features": [{"properties": {"mgrs_code": "31UFS"}}, {"properties": {"mgrs_code": "31UFT"}}]}
        with (
            patch("vresto.ui.widgets.map_search_tab.compute_visible_tiles_geojson", return_value=geojson),
            patch.object(tab, "_schedule_density_refresh"),
        ):
            tab._handle_moveend(BBOX, 8)

        assert tab._grid_codes == ["31UFS", "31UFT"]

    def test_colour_bins_use_products_per_month(self, mock_ui):
        tab = _tab()
        two_months = TileDensity(counts={"31UFS": 18}, min_cloud={}, truncated=False, source=STATS_SOURCE, period="Sep-Oct 2026", months=2.0)

        tab._apply_density(two_months)

        payload = tab.map_widget_obj.update_grid_density.call_args[0][0]
        assert payload["31UFS"]["rate"] == 9.0

    def test_caption_names_the_months_counted_from_the_statistics(self, mock_ui):
        tab = _tab()
        tab._density_caption = MagicMock()
        monthly = TileDensity(counts={"31UFS": 9}, min_cloud={}, truncated=False, source=STATS_SOURCE, period="Sep-Oct 2026")

        tab._apply_density(monthly)

        caption = tab._density_caption.set_text.call_args[0][0]
        assert "Sep-Oct 2026" in caption
        assert "Whole months" in caption

    def test_request_superseded_while_waiting_never_searches(self, mock_ui):
        tab = _tab()
        stale = tab._density_generation
        tab._density_generation += 1  # the map moved again

        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, return_value=DENSITY) as count:
            asyncio.run(tab._refresh_density(stale, BBOX, MagicMock()))

        count.assert_not_called()
        tab.map_widget_obj.update_grid_density.assert_not_called()

    def test_result_is_dropped_when_a_newer_request_arrives_during_the_search(self, mock_ui):
        tab = _tab()

        def slow_search(*_args):
            tab._density_generation += 1  # a newer request is made while this search runs
            return DENSITY

        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, side_effect=slow_search):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        tab.map_widget_obj.update_grid_density.assert_not_called()

    def test_no_density_leaves_the_grid_plain(self, mock_ui):
        tab = _tab()
        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, return_value=None):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        tab.map_widget_obj.update_grid_density.assert_not_called()

    def test_a_failing_count_is_silent(self, mock_ui):
        tab = _tab()
        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, side_effect=RuntimeError("boom")):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))  # must not raise

        tab.map_widget_obj.update_grid_density.assert_not_called()

    def test_truncated_counts_show_the_partial_count_note(self, mock_ui):
        tab = _tab()
        tab._density_note = MagicMock()
        truncated = TileDensity(counts={"31UFS": 2000}, min_cloud={}, truncated=True)

        with patch(DEBOUNCE_PATCH, 0), patch(DENSITY_PATCH, return_value=truncated):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        tab.map_widget_obj.update_grid_density.assert_called_once_with({"31UFS": {"count": 2000, "min_cloud": None, "rate": 2000.0}}, truncated=True)
        tab._density_note.set_visibility.assert_called_with(True)

    def test_rate_limit_shows_a_busy_note_and_retries_after_the_pause(self, mock_ui):
        tab = _tab()
        tab._density_note = MagicMock()

        with (
            patch(DEBOUNCE_PATCH, 0),
            patch(DENSITY_PATCH, return_value=None),
            patch("vresto.ui.widgets.map_search_tab.rate_limited", return_value=True),
            patch("vresto.ui.widgets.map_search_tab.RATE_LIMIT_COOLDOWN_S", -1),
            patch.object(tab, "_schedule_density_refresh") as reschedule,
        ):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        tab.map_widget_obj.update_grid_density.assert_not_called()
        tab._density_note.set_text.assert_called_with(DENSITY_RATE_LIMITED_TEXT)
        tab._density_note.set_visibility.assert_called_with(True)
        reschedule.assert_called_once()

    def test_scheduling_hands_the_refresh_to_background_tasks(self, mock_ui):
        tab = _tab()
        with patch("vresto.ui.widgets.map_search_tab.background_tasks") as tasks:
            tab._schedule_density_refresh()

        tasks.create.assert_called_once()
        assert tasks.create.call_args.kwargs["name"] == "mgrs-density"
        tasks.create.call_args.args[0].close()  # the coroutine is not awaited in this test
        assert tab._density_generation == 1

    def test_nothing_is_scheduled_while_the_grid_is_hidden_or_density_is_off(self, mock_ui):
        with patch("vresto.ui.widgets.map_search_tab.background_tasks") as tasks:
            _tab(grid_bbox=None)._schedule_density_refresh()
            off = _tab()
            off._density_enabled = False
            off._schedule_density_refresh()

        tasks.create.assert_not_called()

    def test_date_picker_polls_recount_only_when_the_range_really_changes(self, mock_ui):
        tab = _tab()
        with patch.object(tab, "_schedule_density_refresh") as schedule:
            tab._on_date_change("2024-01-01", "2024-01-31")  # the range already in use
            tab._on_date_change("2024-02-01", "2024-02-29")  # a real change
            tab._on_date_change("2024-02-01", "2024-02-29")  # the next poll reports the same range

        assert schedule.call_count == 1
        assert tab.current_state["date_range"] == {"from": "2024-02-01", "to": "2024-02-29"}
        tab.map_widget_obj.clear_grid_density.assert_called_once()

    def test_map_move_redraws_the_grid_and_schedules_a_count(self, mock_ui):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab()
        tab.map_widget_obj = MagicMock()
        geojson = {"type": "FeatureCollection", "features": []}
        with patch("vresto.ui.widgets.map_search_tab.compute_visible_tiles_geojson", return_value=geojson), patch.object(tab, "_schedule_density_refresh") as schedule:
            tab._handle_moveend(BBOX, 7)

        tab.map_widget_obj.set_grid_layer.assert_called_once_with(geojson)
        assert tab._grid_bbox == BBOX
        schedule.assert_called_once_with()

    def test_a_view_without_tiles_hides_the_grid_and_its_counts(self, mock_ui):
        tab = _tab()
        generation = tab._density_generation
        with patch("vresto.ui.widgets.map_search_tab.compute_visible_tiles_geojson", return_value=None):
            tab._handle_moveend(BBOX, 8)

        tab.map_widget_obj.clear_grid_layer.assert_called_once()
        assert tab._grid_bbox is None
        assert tab._density_generation != generation

    def test_zooming_out_shows_grid_zones_instead_of_tiles(self, mock_ui):
        tab = _tab()
        zones = {"type": "FeatureCollection", "features": [{"properties": {"mgrs_code": "31U", "zone": True}}]}
        with (
            patch("vresto.ui.widgets.map_search_tab.compute_visible_zones_geojson", return_value=zones) as zone_grid,
            patch("vresto.ui.widgets.map_search_tab.compute_visible_tiles_geojson") as tile_grid,
            patch.object(tab, "_schedule_density_refresh"),
        ):
            tab._handle_moveend((-180.0, -80.0, 180.0, 84.0), 2)

        zone_grid.assert_called_once()
        tile_grid.assert_not_called()
        tab.map_widget_obj.set_grid_layer.assert_called_once_with(zones)
        assert tab._grid_zones is True
        assert tab._grid_codes == ["31U"]

    def test_grid_zones_are_counted_per_zone_and_averaged_per_tile(self, mock_ui):
        tab = _tab()
        tab._grid_zones = True
        tab._grid_codes = ["31U"]
        zone_density = TileDensity(counts={"31U": 120}, min_cloud={"31U": 1.0}, truncated=False, source=STATS_SOURCE, months=2.0, tiles={"31U": 10})

        with (
            patch(DEBOUNCE_PATCH, 0),
            patch("vresto.ui.widgets.map_search_tab.count_products_per_zone", return_value=zone_density) as per_zone,
            patch(DENSITY_PATCH) as per_tile,
        ):
            asyncio.run(tab._refresh_density(tab._density_generation, BBOX, MagicMock()))

        per_zone.assert_called_once_with("2024-01-01", "2024-01-31", ["31U"])
        per_tile.assert_not_called()
        payload = tab.map_widget_obj.update_grid_density.call_args[0][0]
        assert payload == {"31U": {"count": 120, "min_cloud": 1.0, "rate": 6.0, "tiles": 10}}  # 120 / 2 months / 10 tiles

    def test_switching_the_density_off_clears_the_colours_and_drops_pending_counts(self, mock_ui):
        tab = _tab()
        generation = tab._density_generation

        tab._toggle_density(SimpleNamespace(value=False))

        tab.map_widget_obj.clear_grid_density.assert_called_once()
        assert tab._density_enabled is False
        assert tab._density_generation != generation

    def test_switching_the_density_on_counts_the_visible_grid(self, mock_ui):
        tab = _tab()
        tab._density_enabled = False
        with patch.object(tab, "_schedule_density_refresh") as schedule:
            tab._toggle_density(SimpleNamespace(value=True))

        schedule.assert_called_once_with()

    def test_hiding_the_grid_hides_its_counts(self, mock_ui):
        tab = _tab()

        tab._toggle_grid(SimpleNamespace(value=False))

        tab.map_widget_obj.clear_grid_layer.assert_called_once()
        assert tab._grid_bbox is None

    @pytest.mark.parametrize(
        ("grid_bbox", "density_on", "expected"),
        [(BBOX, True, True), (None, True, False), (BBOX, False, False)],
        ids=["shown", "grid-hidden", "density-off"],
    )
    def test_legend_is_shown_only_while_tiles_are_coloured(self, mock_ui, grid_bbox, density_on, expected):
        tab = _tab(grid_bbox=grid_bbox)
        tab._density_enabled = density_on
        tab._density_legend = MagicMock()

        tab._sync_density_legend()

        tab._density_legend.set_visibility.assert_called_with(expected)


class TestTileRetry:
    def test_adding_a_tile_layer_retries_failed_tiles_with_backoff(self, mock_ui):
        widget = _map_widget()

        widget.add_tile_layer("http://127.0.0.1:9999/{z}/{x}/{y}.png", name="tci_31UFS")

        js = _last_js(mock_ui)
        assert "layer.on('tileerror'" in js
        assert "400 * Math.pow(2, attempt)" in js
        assert "map.on('layeradd'" in js  # layers added later are covered too
        assert "attempt >= 6" in js
