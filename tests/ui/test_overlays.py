"""Tests for the declarative overlay registry and the generic overlay load pipeline."""

import asyncio
import importlib
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from vresto.services.sampling import sample_raster
from vresto.ui.overlays import (
    FLOAT_NODATA,
    OVERLAY_NAMES,
    OVERLAY_REGISTRY,
    WB_START_DATE,
    FetchedOverlay,
    OverlayRequest,
    OverlaySpec,
    _precheck_ssm,
    _precheck_tcd,
    _precheck_wb,
    build_overlay_legend_html,
)


def _request(**overrides) -> OverlayRequest:
    values = {"ref_path": "/tmp/ref.tif", "tile_code": "31UFS", "date": "20200126", "timestamp": "20200126125129"}
    values.update(overrides)
    return OverlayRequest(**values)


def _spec(name: str) -> OverlaySpec:
    return next(spec for spec in OVERLAY_REGISTRY if spec.name == name)


def _legend_table(spec: OverlaySpec):
    module_name, _, attribute = spec.legend_classes.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


@contextmanager
def _path_service(name, colorized="/tmp/x_rgba.tif", aligned="/tmp/x.tif"):
    """Patch a path-returning service's colourise and aligned getters; yields both mocks."""
    base = f"vresto.services.{name}.{name}_service"
    with patch(f"{base}.get_colorized_{name}_path", return_value=colorized) as colorized_call, patch(f"{base}.get_aligned_{name}_path", return_value=aligned) as aligned_call:
        yield colorized_call, aligned_call


class TestRegistry:
    """Invariants every registry entry must satisfy, so a new overlay cannot be half-wired."""

    def test_names_and_layer_prefixes_are_unique(self):
        assert len(set(OVERLAY_NAMES)) == len(OVERLAY_NAMES)
        prefixes = [spec.layer_prefix for spec in OVERLAY_REGISTRY]
        assert len(set(prefixes)) == len(prefixes)

    def test_categories_are_contiguous(self):
        """The sidebar prints a heading whenever the category changes, so each must appear once."""
        categories = [spec.category for spec in OVERLAY_REGISTRY]
        collapsed = [c for i, c in enumerate(categories) if i == 0 or c != categories[i - 1]]
        assert len(collapsed) == len(set(collapsed))

    @pytest.mark.parametrize("spec", OVERLAY_REGISTRY, ids=lambda s: s.name)
    def test_spec_is_complete(self, spec):
        assert callable(spec.fetch)
        assert spec.label and spec.emoji and spec.layer_prefix and spec.info
        assert 0.2 <= spec.opacity <= 1.0
        assert spec.legend_type in ("discrete", "continuous")
        if spec.precheck is not None:
            assert callable(spec.precheck)

    @pytest.mark.parametrize("spec", OVERLAY_REGISTRY, ids=lambda s: s.name)
    def test_spec_is_inspectable(self, spec):
        """The point inspector needs to turn a raw pixel into text and say how coarse the source is."""
        assert callable(spec.decode)
        assert spec.native_resolution_m > 0

    @pytest.mark.parametrize("spec", [s for s in OVERLAY_REGISTRY if s.legend_type == "discrete"], ids=lambda s: s.name)
    def test_class_decoder_reads_the_same_table_as_the_legend(self, spec):
        """``decode`` repeats the legend path, so check the two cannot drift apart."""
        for class_id, *_, label in _legend_table(spec):
            assert spec.decode(class_id) == label

    def test_float_nodata_matches_every_float_service(self):
        from vresto.services import ba, dem, dmp, lst, ssm, swi

        sentinels = {dem.DEM_NODATA, dmp.DMP_NODATA, lst.LST_CELSIUS_NODATA, ssm.SSM_NODATA, swi.SWI_NODATA, ba.BA_NODATA}
        assert sentinels == {FLOAT_NODATA}

    @pytest.mark.parametrize("spec", OVERLAY_REGISTRY, ids=lambda s: s.name)
    def test_legend_renders(self, spec):
        html = build_overlay_legend_html(spec)
        assert (spec.legend_title or spec.title) in html

    @pytest.mark.parametrize("spec", [s for s in OVERLAY_REGISTRY if s.legend_type == "continuous"], ids=lambda s: s.name)
    def test_continuous_legend_has_a_ramp(self, spec):
        assert len(spec.legend_stops) >= 2
        assert spec.vmin < spec.vmax

    @pytest.mark.parametrize("spec", [s for s in OVERLAY_REGISTRY if s.legend_type == "discrete"], ids=lambda s: s.name)
    def test_discrete_legend_class_table_resolves(self, spec):
        import importlib

        module_name, _, attribute = spec.legend_classes.partition(":")
        table = getattr(importlib.import_module(module_name), attribute)
        assert table and all(len(row) == 5 for row in table)


class TestLegend:
    def test_suffix_is_appended_in_parentheses(self):
        assert "Tree Cover Density (2020)" in build_overlay_legend_html(_spec("tcd"), "2020")

    def test_legend_title_overrides_title(self):
        html = build_overlay_legend_html(_spec("dmp"), "2020-01-26")
        assert "Dry Matter Productivity (2020-01-26)" in html
        assert "Dry Matter Prod." not in html

    def test_no_suffix_means_no_parentheses(self):
        assert "DEM terrain (" not in build_overlay_legend_html(_spec("dem"))


class TestFetchers:
    def test_path_fetcher_returns_none_when_service_returns_none(self):
        with _path_service("worldcover", colorized=None) as (_, aligned):
            assert _spec("worldcover").fetch(_request()) is None

        aligned.assert_not_called()

    def test_worldcover_uses_ref_path_and_fixed_year(self):
        with _path_service("worldcover", "/tmp/wc.tif", "/tmp/wc_aligned.tif") as (colorized, _):
            fetched = _spec("worldcover").fetch(_request())

        colorized.assert_called_once_with("/tmp/ref.tif", 20, "2021")
        assert fetched == FetchedOverlay("/tmp/wc.tif", aligned_path="/tmp/wc_aligned.tif")

    @pytest.mark.parametrize("name", ["worldcover", "lcm", "tcd", "dem", "lc100", "ndvi"])
    def test_path_overlays_report_the_aligned_raster_not_the_rgba(self, name):
        with _path_service(name, "/tmp/x_rgba.tif", "/tmp/x.tif") as (colorized, aligned):
            fetched = _spec(name).fetch(_request(setting="2020"))

        assert (fetched.colorized_path, fetched.aligned_path) == ("/tmp/x_rgba.tif", "/tmp/x.tif")
        # Same arguments as the colourise call, so the service's pre-network cache check hits.
        assert aligned.call_args == colorized.call_args

    def test_year_overlays_pass_the_selected_year_and_show_it_in_the_legend(self):
        with _path_service("tcd", "/tmp/tcd.tif") as (tcd, _), _path_service("lc100", "/tmp/lc.tif") as (lc100, _):
            tcd_fetched = _spec("tcd").fetch(_request(setting="2020"))
            lc100_fetched = _spec("lc100").fetch(_request(setting="2017"))

        tcd.assert_called_once_with("/tmp/ref.tif", 20, "2020")
        lc100.assert_called_once_with("/tmp/ref.tif", 20, "2017")
        assert tcd_fetched.legend_suffix == "2020"
        assert lc100_fetched.legend_suffix == "2017"

    def test_ndvi_legend_suffix_is_the_dekad(self):
        with _path_service("ndvi", "/tmp/ndvi.tif") as (colorized, _):
            fetched = _spec("ndvi").fetch(_request())

        colorized.assert_called_once_with("/tmp/ref.tif", 1000, "20200126")
        assert fetched.legend_suffix == "01-21"

    def test_lst_reports_the_selected_scene_as_detail(self):
        result = SimpleNamespace(colorized_path="/tmp/lst_rgba.tif", aligned_path="/tmp/lst.tif", selected_datetime=datetime(2020, 1, 26, 12, 0, tzinfo=timezone.utc))
        with patch("vresto.services.lst.lst_service.get_colorized_lst_result", return_value=result) as call:
            fetched = _spec("lst").fetch(_request(setting="20200126120000"))

        call.assert_called_once_with("/tmp/ref.tif", 3000, "20200126120000")
        assert fetched.detail == fetched.legend_suffix == "2020-01-26 13:00 CET"
        assert (fetched.colorized_path, fetched.aligned_path) == ("/tmp/lst_rgba.tif", "/tmp/lst.tif")

    def test_fapar_legend_suffix_is_the_streamed_date(self):
        result = SimpleNamespace(colorized_path="/tmp/fapar_rgba.tif", aligned_path="/tmp/fapar.tif")
        with patch("vresto.services.fapar.fapar_service.get_colorized_fapar_result", return_value=result):
            fetched = _spec("fapar").fetch(_request())

        assert fetched.legend_suffix == "20200126"
        assert (fetched.colorized_path, fetched.aligned_path) == ("/tmp/fapar_rgba.tif", "/tmp/fapar.tif")

    @pytest.mark.parametrize(
        ("name", "module", "method", "resolution"),
        [
            ("dmp", "dmp", "get_colorized_dmp_result", 300),
            ("ssm", "ssm", "get_colorized_ssm_result", 1000),
            ("swi", "swi", "get_colorized_swi_result", 12500),
            ("ba", "ba", "get_colorized_ba_result", 300),
            ("wb", "wb", "get_colorized_wb_result", 100),
        ],
    )
    def test_stac_overlays_snap_to_the_product_date(self, name, module, method, resolution):
        result = SimpleNamespace(colorized_path="/tmp/x_rgba.tif", aligned_path="/tmp/x.tif", selected_datetime=datetime(2020, 1, 21, tzinfo=timezone.utc))
        with patch(f"vresto.services.{module}.{name}_service.{method}", return_value=result) as call:
            fetched = _spec(name).fetch(_request())

        call.assert_called_once_with("/tmp/ref.tif", resolution, "20200126")
        assert fetched == FetchedOverlay("/tmp/x_rgba.tif", legend_suffix="2020-01-21", aligned_path="/tmp/x.tif")

    def test_stac_overlay_returns_none_without_a_result(self):
        with patch("vresto.services.ba.ba_service.get_colorized_ba_result", return_value=None):
            assert _spec("ba").fetch(_request()) is None


class TestDecoders:
    """Raw pixel -> display text, including every flag value a service leaves undrawn."""

    @pytest.mark.parametrize(
        ("name", "raw", "expected"),
        [
            # Class overlays: the legend label, and None for codes the service renders transparent.
            ("worldcover", 40, "Cropland"),
            ("worldcover", 40.0, "Cropland"),
            ("worldcover", 0, None),
            ("worldcover", 255, None),
            ("lcm", 254, "Unclassifiable"),
            ("lcm", 255, None),
            ("lc100", 40, "Cropland"),
            ("lc100", 200, "Open sea"),
            ("lc100", 0, None),
            ("lc100", 255, None),
            ("tcd", 60, "60%"),
            ("tcd", 35, None),
            ("tcd", 255, None),
            # Water bodies: "No water" is information, "no data" (251) is not.
            ("wb", 70, "Water"),
            ("wb", 0, "Sea"),
            ("wb", 255, "No water"),
            ("wb", 251, None),
            # Terrain.
            ("dem", 123.4, "123 m"),
            ("dem", 0.0, "0 m"),
            ("dem", -3.6, "-4 m"),
            ("dem", -9999.0, None),
            # Scaled DN overlays: valid up to DN 250, flags above (254 sea, 255 no data).
            ("ndvi", 0, "-0.08"),
            ("ndvi", 19, "0.00"),
            ("ndvi", 165, "0.58"),
            ("ndvi", 250, "0.92"),
            ("ndvi", 251, None),
            ("ndvi", 254, None),
            ("ndvi", 255, None),
            ("fapar", 0, "0.00"),
            ("fapar", 125, "0.50"),
            ("fapar", 235, "0.94"),
            ("fapar", 250, "1.00"),
            ("fapar", 251, None),
            ("fapar", 254, None),
            ("fapar", 255, None),
            # Float overlays that already hold physical values.
            ("dmp", 52.34, "52.3 kg/ha/day"),
            ("dmp", -9999.0, None),
            ("lst", 14.26, "14.3 °C"),
            ("lst", -0.04, "0.0 °C"),
            ("lst", -9999.0, None),
            ("ssm", 41.0, "41 % sat."),
            ("ssm", 100.0, "100 % sat."),
            ("ssm", -9999.0, None),
            ("swi", 41.0, "41 % sat."),
            ("swi", -9999.0, None),
            ("ba", 214.0, "Burned, day of year 214"),
            ("ba", 1.0, "Burned, day of year 1"),
            ("ba", 366.0, "Burned, day of year 366"),
            ("ba", 0.0, None),
            ("ba", 367.0, None),
            ("ba", -9999.0, None),
        ],
    )
    def test_decode(self, name, raw, expected):
        assert _spec(name).decode(raw) == expected

    def test_burned_area_words_a_pixel_without_a_burn_differently(self):
        """The aligned BA raster has no "unburned" value (it is nodata), so "no data" would mislead."""
        assert "burned area" in _spec("ba").nodata_text.lower()
        assert [s.name for s in OVERLAY_REGISTRY if s.nodata_text] == ["ba"]

    def test_a_sampled_value_decodes_end_to_end(self, tmp_path):
        """sample_raster on an aligned-style raster feeds straight into the spec's decoder."""
        path = str(tmp_path / "ndvi_aligned.tif")
        # Three 0.01 degree pixels with their centres at lon 4.005, 4.015, 4.025 and lat 50.995.
        with rasterio.open(path, "w", driver="GTiff", height=1, width=3, count=1, dtype="uint8", crs="EPSG:4326", transform=from_origin(4.0, 51.0, 0.01, 0.01), nodata=255) as dst:
            dst.write(np.array([[165, 254, 255]], dtype="uint8"), 1)

        def decoded(col):
            sample = sample_raster(path, 50.995, 4.005 + 0.01 * col)
            return sample.status, _spec("ndvi").decode(sample.raw) if sample.status == "ok" else None

        assert decoded(0) == ("ok", "0.58")
        assert decoded(1) == ("ok", None)  # sea is a flag value, not masked by the file's nodata
        assert decoded(2) == ("nodata", None)


class TestPrechecks:
    def test_tcd_blocks_only_when_tile_is_unavailable(self):
        assert _precheck_tcd(_request(tile_available=True)) is None
        assert "unavailable" in _precheck_tcd(_request(tile_available=False))

    def test_wb_blocks_dates_before_the_product_starts(self):
        assert _precheck_wb(_request(date="20200930")) is not None
        assert "2020-09-30" in _precheck_wb(_request(date="20200930"))
        assert _precheck_wb(_request(date=WB_START_DATE)) is None
        assert _precheck_wb(_request(date="")) is None

    def _fake_rasterio(self):
        ref = MagicMock(crs="EPSG:32631", bounds=(0, 0, 1, 1))
        opened = MagicMock(__enter__=MagicMock(return_value=ref), __exit__=MagicMock(return_value=False))
        return {"rasterio": SimpleNamespace(open=MagicMock(return_value=opened)), "rasterio.warp": SimpleNamespace(transform_bounds=MagicMock(return_value=(100.0, 10.0, 101.0, 11.0)))}

    @pytest.mark.parametrize(("covered", "blocked"), [(True, False), (False, True)])
    def test_ssm_blocks_tiles_outside_europe(self, covered, blocked):
        with patch.dict(sys.modules, self._fake_rasterio()), patch("vresto.services.ssm.ssm_has_coverage", return_value=covered):
            reason = _precheck_ssm(_request())

        assert (reason is not None) is blocked

    def test_ssm_does_not_block_when_the_coverage_check_itself_fails(self):
        broken = {"rasterio": SimpleNamespace(open=MagicMock(side_effect=OSError("unreadable")))}
        with patch.dict(sys.modules, {**self._fake_rasterio(), **broken}):
            assert _precheck_ssm(_request()) is None


@pytest.fixture
def tab():
    """A MapSearchTab with a streamed tile, a mocked map and a mocked ``ui``."""
    from vresto.ui.widgets.map_search_tab import MapSearchTab

    with patch("vresto.ui.widgets.map_search_tab.ui") as mock_ui:
        widget = MapSearchTab()
        widget._streaming_tile_code = "31UFS"
        widget._streaming_date = "20200126"
        widget._streaming_timestamp = "20200126125129"
        widget.map_widget_obj = MagicMock()
        widget._overlay_status_label = MagicMock()
        widget._add_message = MagicMock()
        widget.mock_ui = mock_ui
        yield widget


def _with_fetch(tab, name, fetch):
    """Swap one overlay's fetcher for a stub (specs are frozen, so replace the whole spec)."""
    tab._overlay_specs[name] = replace(tab._overlay_specs[name], fetch=fetch)


def _load(tab, name, url="http://tiles"):
    with (
        patch("vresto.ui.widgets.map_search_tab.sentinel_stream_service.find_any_cached_tci", return_value="/tmp/ref.tif") as find_ref,
        patch("vresto.ui.widgets.map_search_tab.tile_pool.get_or_create", return_value=url) as pool,
    ):
        asyncio.run(tab._load_overlay(name))
    return find_ref, pool


class TestLoadPipeline:
    def test_success_adds_layer_with_configured_opacity_and_legend(self, tab):
        fetch = MagicMock(return_value=FetchedOverlay("/tmp/dem.tif", legend_suffix="x"))
        _with_fetch(tab, "dem", fetch)
        tab._overlay_opacity_by_name["dem"] = 0.4

        find_ref, pool = _load(tab, "dem")

        find_ref.assert_called_once_with("31UFS", "20200126")
        request = fetch.call_args[0][0]
        assert (request.ref_path, request.tile_code, request.date, request.timestamp) == ("/tmp/ref.tif", "31UFS", "20200126", "20200126125129")
        pool.assert_called_once_with("dem_31UFS", "/tmp/dem.tif")
        tab.map_widget_obj.add_tile_layer.assert_called_once_with("http://tiles", name="dem_31UFS", opacity=0.4)
        tab.map_widget_obj.set_legend.assert_called_once()
        assert "DEM terrain (x)" in tab.map_widget_obj.set_legend.call_args[0][0]
        assert tab._overlay_layer_urls["dem"] == "http://tiles"

    def test_year_setting_reaches_the_fetcher(self, tab):
        fetch = MagicMock(return_value=FetchedOverlay("/tmp/tcd.tif", legend_suffix="2018"))
        _with_fetch(tab, "tcd", fetch)
        tab._tcd_year = "2018"

        _load(tab, "tcd")

        assert fetch.call_args[0][0].setting == "2018"

    def test_fetch_failure_reports_and_leaves_no_layer(self, tab):
        _with_fetch(tab, "dem", MagicMock(return_value=None))
        tab._overlay_layer_urls["dem"] = "stale"

        _, pool = _load(tab, "dem")

        pool.assert_not_called()
        tab.map_widget_obj.add_tile_layer.assert_not_called()
        tab.map_widget_obj.set_legend.assert_not_called()
        assert "dem" not in tab._overlay_layer_urls
        tab._add_message.assert_called_with("❌ DEM overlay failed for 31UFS")

    def test_missing_tile_server_url_counts_as_failure(self, tab):
        _with_fetch(tab, "dem", MagicMock(return_value=FetchedOverlay("/tmp/dem.tif")))

        _load(tab, "dem", url=None)

        tab.map_widget_obj.add_tile_layer.assert_not_called()
        tab._add_message.assert_called_with("❌ DEM overlay failed for 31UFS")

    def test_requires_a_streamed_tci(self, tab):
        fetch = MagicMock()
        _with_fetch(tab, "dem", fetch)

        with patch("vresto.ui.widgets.map_search_tab.sentinel_stream_service.find_any_cached_tci", return_value=None):
            asyncio.run(tab._load_overlay("dem"))

        fetch.assert_not_called()
        tab._add_message.assert_called_with("⚠️ Stream TCI first before enabling overlays")

    def test_noop_without_a_streamed_tile(self, tab):
        fetch = MagicMock()
        _with_fetch(tab, "dem", fetch)
        tab._streaming_tile_code = None

        asyncio.run(tab._load_overlay("dem"))

        fetch.assert_not_called()

    def test_detail_is_surfaced_in_log_and_status(self, tab):
        _with_fetch(tab, "lst", MagicMock(return_value=FetchedOverlay("/tmp/lst.tif", legend_suffix="13:00", detail="13:00")))

        _load(tab, "lst")

        assert tab._lst_selected_timestamp_label == "13:00"
        assert "(13:00)" in tab._overlay_status_label.set_text.call_args[0][0]
        assert "(13:00, " in tab._add_message.call_args[0][0]

    def test_failed_lst_load_clears_the_selected_label(self, tab):
        _with_fetch(tab, "lst", MagicMock(return_value=None))
        tab._lst_selected_timestamp_label = "stale"

        _load(tab, "lst")

        assert tab._lst_selected_timestamp_label is None

    def test_lst_defaults_to_the_streamed_timestamp_unless_user_picked_one(self, tab):
        fetch = MagicMock(return_value=None)
        _with_fetch(tab, "lst", fetch)

        _load(tab, "lst")
        assert fetch.call_args[0][0].setting == "20200126125129"

        tab._lst_user_selected_timestamp = "20200126130000"
        _load(tab, "lst")
        assert fetch.call_args[0][0].setting == "20200126130000"

    def test_precheck_rejection_skips_fetch_and_switches_overlay_off(self, tab):
        fetch = MagicMock()
        _with_fetch(tab, "wb", fetch)
        tab._streaming_date = "20190101"
        switch = MagicMock()
        tab._overlay_switches["wb"] = switch
        tab._set_overlay_flag("wb", True)
        tab._active_overlay = "wb"
        tab._clear_overlay_legend = MagicMock()
        tab._sync_overlay_sections = MagicMock()

        _load(tab, "wb")

        fetch.assert_not_called()
        switch.set_value.assert_called_once_with(False)
        assert tab._get_enabled_overlay() is None
        assert tab._active_overlay is None
        tab._clear_overlay_legend.assert_called_once()
        tab._sync_overlay_sections.assert_called_once_with(None)
        assert "October 2020" in tab._add_message.call_args[0][0]

    def test_switching_off_an_inactive_overlay_leaves_the_active_one_alone(self, tab):
        tab._active_overlay = "dem"
        tab._overlay_switches["wb"] = MagicMock()

        tab._switch_off_overlay("wb")

        assert tab._active_overlay == "dem"

    def test_tile_availability_feeds_the_tcd_precheck(self, tab):
        fetch = MagicMock()
        _with_fetch(tab, "tcd", fetch)
        tab._overlay_tile_available["tcd"] = False
        tab._overlay_switches["tcd"] = MagicMock()

        _load(tab, "tcd")

        fetch.assert_not_called()
        assert "unavailable" in tab._add_message.call_args[0][0]
