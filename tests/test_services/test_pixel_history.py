"""Pixel history: Earth Search point search, per-scene pixel reads, NDVI, CSV, and overlay series."""

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from vresto.api.stac_assets import STACAssetMatch
from vresto.services import pixel_history as ph

# A point inside the synthetic UTM 31N rasters below (600-610 km E, 5650-5660 km N).
LAT, LON = 51.0, 4.43


def _band(path, value, dtype="uint16", nodata=0):
    with rasterio.open(path, "w", driver="GTiff", dtype=dtype, count=1, width=32, height=32, crs="EPSG:32631", transform=from_bounds(600000, 5650000, 610240, 5660240, 32, 32), nodata=nodata) as dst:
        dst.write(np.full((32, 32), value, dtype=dtype), 1)
    return str(path)


def _item(tmp_path, item_id, when, red=500, nir=3500, scl=4, tile="31UFS", cloud=12.0, offset=None):
    folder = tmp_path / item_id
    folder.mkdir()
    bands = [{"scale": 0.0001, **({"offset": offset} if offset is not None else {})}]
    return {
        "id": item_id,
        "properties": {"datetime": when, "grid:code": f"MGRS-{tile}", "eo:cloud_cover": cloud},
        "assets": {
            "red": {"href": _band(folder / "red.tif", red), "raster:bands": bands},
            "nir": {"href": _band(folder / "nir.tif", nir), "raster:bands": bands},
            "scl": {"href": _band(folder / "scl.tif", scl, dtype="uint8", nodata=None)},
            "visual": {"href": "https://example.com/visual.tif"},
        },
    }


def test_classify_scl_groups_codes():
    assert [ph.classify_scl(code) for code in (4, 5, 6, 8, 9, 10, 3, 11, 0, 1, None)] == [
        "clear",
        "clear",
        "clear",
        "cloud",
        "cloud",
        "cloud",
        "shadow",
        "snow",
        "nodata",
        "nodata",
        "nodata",
    ]


def test_compute_ndvi():
    assert ph.compute_ndvi(0.05, 0.35) == pytest.approx(0.75)
    assert ph.compute_ndvi(None, 0.3) is None
    assert ph.compute_ndvi(0.0, 0.0) is None


def test_item_tile_from_grid_code_or_mgrs_properties():
    assert ph._item_tile({"properties": {"grid:code": "MGRS-31UFS"}}) == "31UFS"
    assert ph._item_tile({"properties": {"mgrs:utm_zone": 5, "mgrs:latitude_band": "V", "mgrs:grid_square": "MK"}}) == "05VMK"


def test_search_sends_point_and_keeps_the_most_common_tile(tmp_path):
    items = [
        {"id": "b", "properties": {"datetime": "2025-06-10T10:00:00Z", "grid:code": "MGRS-31UFS"}},
        {"id": "x", "properties": {"datetime": "2025-06-11T10:00:00Z", "grid:code": "MGRS-31UES"}},
        {"id": "a", "properties": {"datetime": "2025-06-01T10:00:00Z", "grid:code": "MGRS-31UFS"}},
    ]
    with patch.object(ph.public_s2, "_fetch_items", return_value=items) as fetch:
        result = ph.search_point_scenes(LAT, LON, "2025-06-01", "2025-06-30")

    params, limit = fetch.call_args.args
    assert params["intersects"] == {"type": "Point", "coordinates": [LON, LAT]}
    assert params["datetime"] == "2025-06-01T00:00:00Z/2025-06-30T23:59:59Z"
    assert "grid:code" not in params["query"]
    assert limit == ph.DEFAULT_MAX_SCENES
    assert [item["id"] for item in result] == ["a", "b"]  # oldest first, the 31UES scene dropped


def test_search_filters_on_the_requested_tile():
    with patch.object(ph.public_s2, "_fetch_items", return_value=[]) as fetch:
        ph.search_point_scenes(LAT, LON, "2025-06-01", "2025-06-30", tile_code="T31UFS", max_cloud=40)
    query = fetch.call_args.args[0]["query"]
    assert query == {"eo:cloud_cover": {"lte": 40}, "grid:code": {"eq": "MGRS-31UFS"}}


def test_search_failure_returns_nothing():
    with patch.object(ph.public_s2, "_fetch_items", side_effect=TimeoutError("slow")):
        assert ph.search_point_scenes(LAT, LON, "2025-06-01", "2025-06-30") == []


def test_read_observation_computes_ndvi_from_scaled_reflectance(tmp_path):
    obs = ph.read_observation(_item(tmp_path, "s1", "2025-06-01T10:30:00Z", offset=-0.1, red=1500, nir=4500), LAT, LON)

    assert obs.status == "clear" and obs.clear
    assert obs.red == pytest.approx(0.05) and obs.nir == pytest.approx(0.35)
    assert obs.ndvi == pytest.approx(0.75)
    assert obs.scl == 4 and obs.tile_code == "31UFS" and obs.scene_cloud_cover == 12.0
    assert obs.datetime == datetime(2025, 6, 1, 10, 30, tzinfo=timezone.utc)
    assert obs.product is not None and obs.product.source == "earth-search"


def test_read_observation_flags_clouds_but_keeps_their_values(tmp_path):
    obs = ph.read_observation(_item(tmp_path, "s1", "2025-06-01T10:30:00Z", scl=9), LAT, LON)
    assert obs.status == "cloud" and not obs.clear
    assert obs.ndvi is not None


def test_read_observation_skips_bands_where_scl_has_no_data(tmp_path):
    item = _item(tmp_path, "s1", "2025-06-01T10:30:00Z", scl=0)
    with patch.object(ph, "_reflectance") as reflectance:
        obs = ph.read_observation(item, LAT, LON)
    assert obs.status == "nodata" and obs.ndvi is None
    reflectance.assert_not_called()


def test_read_observation_outside_the_scene_is_nodata(tmp_path):
    obs = ph.read_observation(_item(tmp_path, "s1", "2025-06-01T10:30:00Z"), 45.0, 10.0)
    assert obs.status == "nodata"


def test_read_observation_reports_unreadable_files(tmp_path):
    item = _item(tmp_path, "s1", "2025-06-01T10:30:00Z")
    item["assets"]["scl"]["href"] = str(tmp_path / "missing.tif")
    assert ph.read_observation(item, LAT, LON).status == "error"


def test_point_history_reads_every_scene(tmp_path):
    items = [_item(tmp_path, f"s{i}", f"2025-06-{i + 1:02d}T10:30:00Z", scl=scl) for i, scl in enumerate((4, 8, 5))]
    with patch.object(ph.public_s2, "_fetch_items", return_value=items):
        series = ph.point_history(LAT, LON, "2025-06-01", "2025-06-30")
    assert [obs.status for obs in series] == ["clear", "cloud", "clear"]
    assert [obs.date for obs in series] == ["2025-06-01", "2025-06-02", "2025-06-03"]


def test_observations_to_csv(tmp_path):
    obs = ph.read_observation(_item(tmp_path, "s1", "2025-06-01T10:30:00Z"), LAT, LON)
    lines = ph.observations_to_csv([obs]).splitlines()
    assert lines[0] == ",".join(ph.CSV_COLUMNS)
    assert lines[1].startswith("2025-06-01,2025-06-01T10:30:00Z,31UFS,clear,0.75,0.05,0.35,4,12.0,s1")


# ── Overlay series ──────────────────────────────────────────────────────────


def _lonlat_cog(path, value, dtype="uint8"):
    with rasterio.open(path, "w", driver="GTiff", dtype=dtype, count=1, width=10, height=10, crs="EPSG:4326", transform=from_bounds(4.0, 50.5, 5.0, 51.5, 10, 10)) as dst:
        dst.write(np.full((10, 10), value, dtype=dtype), 1)
    return str(path)


def _series(service, tmp_path, values):
    assets = [STACAssetMatch(f"item{i}", datetime(2025, 6, i + 1, tzinfo=timezone.utc), service.asset_key, _lonlat_cog(tmp_path / f"{i}.tif", v)) for i, v in enumerate(values)]
    with patch.object(type(service), "_s3_env", lambda self: nullcontext()), patch("vresto.api.stac_assets.find_stac_assets_between", return_value=assets) as find:
        series = service.point_series(LAT, LON, "2025-06-01", "2025-06-30")
    return series, find


def test_overlay_series_decodes_values_and_masks_nodata(tmp_path):
    from vresto.services.swi import SWIService

    service = SWIService(cache_root=tmp_path / "cache")
    series, find = _series(service, tmp_path, [100, 255, 200])

    assert [point.value for point in series] == [50.0, None, 100.0]
    assert [point.item_id for point in series] == ["item0", "item1", "item2"]
    collection, bbox, start, end, asset_key = find.call_args.args
    assert collection == service.collection_id and asset_key == service.asset_key
    assert bbox[0] < LON < bbox[2] and bbox[1] < LAT < bbox[3]
    assert (start.date().isoformat(), end.hour) == ("2025-06-01", 23)


def test_fapar_series_is_in_physical_units(tmp_path):
    from vresto.services.fapar import FAPARService

    series, _ = _series(FAPARService(cache_root=tmp_path / "cache"), tmp_path, [125, 254])
    assert series[0].value == pytest.approx(0.5)
    assert series[1].value is None  # a flag DN, not a fraction


def test_categorical_overlays_have_no_series(tmp_path):
    from vresto.services.wb import WBService

    with patch("vresto.api.stac_assets.find_stac_assets_between") as find:
        assert WBService(cache_root=tmp_path / "cache").point_series(LAT, LON, "2025-06-01", "2025-06-30") == []
    find.assert_not_called()
