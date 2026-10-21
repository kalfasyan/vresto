"""Pixel history panel: chart building, loading, date selection, scene streaming, and tab wiring."""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vresto.ui.widgets import pixel_history_panel as panel_mod
from vresto.ui.widgets.pixel_history_panel import (
    NDVI_KEY,
    HistoryPoint,
    HistoryVariable,
    PixelHistoryPanel,
    build_chart_options,
    history_variables,
    period_dates,
    series_to_csv,
    summarize,
)

NDVI = HistoryVariable(NDVI_KEY, "NDVI", "NDVI", -0.2, 1.0)
SWI = HistoryVariable("swi", "SWI", "% sat.", 0.0, 100.0, needs_cdse_s3=True)


def _when(day):
    return datetime(2025, 6, day, 10, 30, tzinfo=timezone.utc)


def _obs(day, ndvi, status="clear", tile="31UFS"):
    return SimpleNamespace(datetime=_when(day), ndvi=ndvi, status=status, tile_code=tile, product=MagicMock(name=f"product{day}"))


def _points():
    return [
        HistoryPoint(_when(1), 0.61, "clear", _obs(1, 0.61)),
        HistoryPoint(_when(6), 0.12, "cloud", _obs(6, 0.12, "cloud")),
        HistoryPoint(_when(11), None, "nodata", _obs(11, None, "nodata")),
        HistoryPoint(_when(16), 0.72, "clear", _obs(16, 0.72)),
    ]


# ── Pure helpers ────────────────────────────────────────────────────────────


def test_chart_separates_clear_and_flagged_and_skips_missing_values():
    options = build_chart_options(_points(), NDVI)
    clear, flagged = options["series"]
    assert [item[2] for item in clear["data"]] == [0, 3]
    assert [item[2] for item in flagged["data"]] == [1]
    assert clear["data"][0][0] == int(_when(1).timestamp() * 1000)
    assert (options["yAxis"]["min"], options["yAxis"]["max"]) == (-0.2, 1.0)
    assert "markLine" not in clear


def test_chart_marks_the_selected_date():
    clear = build_chart_options(_points(), NDVI, selected=1)["series"][0]
    assert clear["markLine"]["data"] == [{"xAxis": int(_when(6).timestamp() * 1000)}]


def test_summary_counts_clear_scenes_and_range():
    assert summarize(_points(), NDVI) == "4 scenes · 2 clear · NDVI 0.61 – 0.72"
    assert summarize([], NDVI) == "No data found for this point and period."
    overlay = [HistoryPoint(_when(1), 40.0, "clear"), HistoryPoint(_when(2), None, "nodata")]
    assert summarize(overlay, SWI) == "2 dates · 1 with data · % sat. 40.0 – 40.0"


def test_period_dates():
    assert period_dates("Selected dates", ("2025-01-01", "2025-01-31")) == ("2025-01-01", "2025-01-31")
    assert period_dates("Last 12 months", ("x", "y"), today=date(2025, 6, 30)) == ("2024-06-30", "2025-06-30")


def test_long_periods_read_more_scenes_up_to_a_cap():
    from vresto.ui.widgets.pixel_history_panel import max_scenes_for

    assert max_scenes_for("2025-06-01", "2025-06-30") == 150
    assert max_scenes_for("2023-06-30", "2025-06-30") == 365
    assert max_scenes_for("2020-01-01", "2025-06-30") == 500
    assert max_scenes_for("bad", "2025-06-30") == 150


def test_overlay_series_csv():
    csv = series_to_csv([HistoryPoint(_when(1), 40.0, "clear"), HistoryPoint(_when(2), None, "nodata")], SWI)
    assert csv.splitlines() == ["date,datetime_utc,swi", "2025-06-01,2025-06-01T10:30:00Z,40.0000", "2025-06-02,2025-06-02T10:30:00Z,"]


def test_history_variables_offer_ndvi_first_and_only_series_capable_overlays():
    import importlib

    variables = history_variables()
    assert variables[0].key == NDVI_KEY and not variables[0].needs_cdse_s3
    assert {v.key for v in variables[1:]} == set(panel_mod.OVERLAY_SERIES)
    for variable in variables[1:]:
        service = getattr(importlib.import_module(f"vresto.services.{variable.key}"), f"{variable.key}_service")
        assert service.supports_series and variable.needs_cdse_s3


# ── Panel behaviour ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_ui():
    with patch.object(panel_mod, "ui", MagicMock()) as mock:
        yield mock


def _panel(tile="31UFS", **kwargs):
    callbacks = {
        "get_date_range": lambda: ("2025-06-01", "2025-06-30"),
        "get_tile_code": lambda: tile,
        "on_pick_mode": MagicMock(),
        "on_show_scene": AsyncMock(),
        "on_point_loaded": MagicMock(),
    }
    callbacks.update(kwargs)
    panel = PixelHistoryPanel(**callbacks)
    panel.period = "Selected dates"
    return panel, callbacks


def test_create_builds_hidden_chart_and_slider(mock_ui):
    panel, _ = _panel()
    panel.create()
    mock_ui.echart.assert_called_once()
    assert mock_ui.echart.call_args.kwargs["on_point_click"] == panel._on_chart_click
    panel._slider.on.assert_called_once_with("change", panel._on_slider_release)
    panel._chart.set_visibility.assert_any_call(False)


def test_load_charts_the_streamed_tile_and_selects_the_last_clear_date(mock_ui):
    panel, callbacks = _panel()
    panel.create()
    observations = [_obs(1, 0.6), _obs(6, 0.7), _obs(11, 0.1, "cloud")]
    with patch("vresto.services.pixel_history.point_history", return_value=observations) as history:
        asyncio.run(panel.load(51.0, 4.43))

    history.assert_called_once_with(51.0, 4.43, "2025-06-01", "2025-06-30", tile_code="31UFS", max_scenes=150)
    assert [p.value for p in panel.points] == [0.6, 0.7, 0.1]
    assert panel.selected == 1  # the newest clear one, not the cloudy last scene
    callbacks["on_point_loaded"].assert_called_once_with(51.0, 4.43, "3 scenes · 2 clear · NDVI 0.60 – 0.70")
    panel._chart.set_visibility.assert_called_with(True)


def test_load_falls_back_to_any_tile_when_the_point_is_outside_the_streamed_one(mock_ui):
    panel, _ = _panel()
    with patch("vresto.services.pixel_history.point_history", side_effect=[[], [_obs(1, 0.5, tile="31UES")]]) as history:
        asyncio.run(panel.load(51.0, 4.43))
    assert history.call_args_list[1].kwargs == {"max_scenes": 150}
    assert len(panel.points) == 1


def test_overlay_variable_without_keys_explains_instead_of_loading(mock_ui):
    panel, callbacks = _panel()
    panel.create()
    panel.variable = panel.variables["swi"]
    with patch.object(panel_mod, "CopernicusConfig", return_value=MagicMock(has_static_s3_credentials=MagicMock(return_value=False))), patch.object(panel, "_fetch") as fetch:
        asyncio.run(panel.load(51.0, 4.43))
    fetch.assert_not_called()
    assert "needs CDSE S3 keys" in panel._status.set_text.call_args.args[0]
    callbacks["on_point_loaded"].assert_not_called()


def test_overlay_variable_reads_the_service_series(mock_ui):
    panel, _ = _panel()
    panel.variable = panel.variables["swi"]
    series = [SimpleNamespace(datetime=_when(1), value=40.0), SimpleNamespace(datetime=_when(2), value=None)]
    with (
        patch.object(panel_mod, "CopernicusConfig", return_value=MagicMock(has_static_s3_credentials=MagicMock(return_value=True))),
        patch("vresto.services.swi.swi_service.point_series", return_value=series) as point_series,
    ):
        asyncio.run(panel.load(51.0, 4.43))
    point_series.assert_called_once_with(51.0, 4.43, "2025-06-01", "2025-06-30")
    assert [(p.value, p.status) for p in panel.points] == [(40.0, "clear"), (None, "nodata")]


def test_a_superseded_load_is_dropped(mock_ui):
    panel, callbacks = _panel()

    def slow_fetch(*args):
        panel._request_id += 1  # a newer click arrives while this one is still loading
        return [HistoryPoint(_when(1), 0.5, "clear")]

    with patch.object(panel, "_fetch", side_effect=slow_fetch):
        asyncio.run(panel.load(51.0, 4.43))
    assert panel.points == []
    callbacks["on_point_loaded"].assert_not_called()


def test_chart_click_selects_and_streams_that_scene(mock_ui):
    panel, callbacks = _panel()
    panel.create()
    panel.points = _points()
    asyncio.run(panel._on_chart_click(SimpleNamespace(data=[0, 0.12, 1])))
    assert panel.selected == 1
    callbacks["on_show_scene"].assert_awaited_once_with(panel.points[1].scene)


def test_scene_streaming_keeps_only_the_newest_pending_pick(mock_ui):
    panel, _ = _panel()
    panel.points = _points()
    shown = []

    async def show(scene):
        shown.append(scene)
        if len(shown) == 1:  # two more picks arrive while the first scene streams
            for index in (1, 3):
                panel.select(index)
                await panel._show_selected_scene()

    panel._on_show_scene = show
    panel.select(0)
    asyncio.run(panel._show_selected_scene())
    assert shown == [panel.points[0].scene, panel.points[3].scene]


def test_follow_switch_off_stops_streaming(mock_ui):
    panel, callbacks = _panel()
    panel.create()
    panel.points = _points()
    panel._follow_switch.value = False
    panel.select(0)
    asyncio.run(panel._on_slider_release())
    callbacks["on_show_scene"].assert_not_awaited()


def test_set_picking_reports_changes_once(mock_ui):
    panel, callbacks = _panel()
    panel.create()
    panel._pick_switch.value = False
    panel.set_picking(True)
    panel.set_picking(True)
    callbacks["on_pick_mode"].assert_called_once_with(True)
    panel._pick_switch.set_value.assert_called_once_with(True)


def test_export_downloads_csv(mock_ui):
    panel, _ = _panel()
    panel.points = [HistoryPoint(_when(1), 40.0, "clear")]
    panel.variable = SWI
    panel.location = (51.0, 4.43)
    panel.export_csv()
    content, filename, media_type = mock_ui.download.content.call_args.args
    assert filename == "vresto_swi_51.0000_4.4300.csv" and media_type == "text/csv"
    assert content.startswith("date,datetime_utc,swi\n")


# ── Map tab wiring ──────────────────────────────────────────────────────────


@pytest.fixture
def tab():
    with patch("vresto.ui.widgets.map_search_tab.ui", MagicMock()), patch.object(panel_mod, "ui", MagicMock()):
        from vresto.ui.widgets.map_search_tab import MapSearchTab

        tab = MapSearchTab()
        tab.map_widget_obj = MagicMock()
        tab._pixel_history, _ = _panel(on_pick_mode=tab._on_pixel_history_mode, on_show_scene=tab._show_history_scene, on_point_loaded=tab._on_history_loaded)
        switch = MagicMock()
        switch.set_value.side_effect = lambda value: tab._on_inspect_toggle(SimpleNamespace(value=value))
        tab._inspect_switch = switch
        yield tab


def test_picking_routes_map_clicks_to_the_history(tab):
    tab._pixel_history.picking = True
    tab._pixel_history.load = AsyncMock()
    asyncio.run(tab._handle_map_click(51.0, 364.43))  # an unwrapped longitude from a panned map
    tab._pixel_history.load.assert_awaited_once()
    lat, lon = tab._pixel_history.load.call_args.args
    assert lat == 51.0 and lon == pytest.approx(4.43)
    tab.map_widget_obj.show_point_popup.assert_called_once()
    assert tab.map_widget_obj.show_point_popup.call_args.args[:2] == (51.0, 364.43)


def test_picking_and_inspecting_exclude_each_other(tab):
    tab._set_inspect_enabled(True)
    tab._pixel_history.set_picking(True)
    assert tab._pixel_history.picking and not tab._inspect_enabled
    tab.map_widget_obj.set_inspect_mode.assert_called_with(True)  # clicks still go to the tab

    tab._set_inspect_enabled(True)
    assert tab._inspect_enabled and not tab._pixel_history.picking
    tab.map_widget_obj.set_inspect_mode.assert_called_with(True)

    tab._set_inspect_enabled(False)
    tab.map_widget_obj.set_inspect_mode.assert_called_with(False)  # tile clicks are back


def test_history_scene_streams_without_refitting_the_map(tab):
    seen = {}

    async def commit(tile_code, product, previous):
        seen["fit"] = tab._fit_streamed_tile
        seen["args"] = (tile_code, product, previous)

    tab._streaming_tile_code = "31UFS"
    tab._commit_tile_selection = commit
    observation = _obs(1, 0.6, tile="31UFT")
    asyncio.run(tab._show_history_scene(observation))
    assert seen == {"fit": False, "args": ("31UFT", observation.product, "31UFS")}
    assert tab._fit_streamed_tile


def test_loaded_history_updates_the_popup_at_the_clicked_position(tab):
    tab._history_click = (51.0, 364.43)
    tab._on_history_loaded(51.0, 4.43, "3 scenes · 2 clear")
    lat, lon, html = tab.map_widget_obj.show_point_popup.call_args.args
    assert (lat, lon) == (51.0, 364.43)
    assert "3 scenes · 2 clear" in html and "4.43000" in html
