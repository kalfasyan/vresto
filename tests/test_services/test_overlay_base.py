"""Shared overlay helpers: windowed reads, colouring, overviews and cache keys."""

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from vresto.services.overlay_base import (
    build_overlay_overviews,
    colormap_lut,
    paint_classes,
    ramp_rgba,
    read_window_at_resolution,
    reference_id,
)


def _lonlat_raster(path, width=40, height=40, bounds=(4.0, 50.0, 5.0, 51.0)):
    data = np.arange(width * height, dtype="float32").reshape(height, width)
    with rasterio.open(path, "w", driver="GTiff", dtype="float32", count=1, width=width, height=height, crs="EPSG:4326", transform=from_bounds(*bounds, width, height)) as dst:
        dst.write(data, 1)
    return str(path)


def test_read_window_returns_none_when_bbox_misses_raster(tmp_path):
    with rasterio.open(_lonlat_raster(tmp_path / "src.tif")) as src:
        assert read_window_at_resolution(src, 10.0, 10.0, 11.0, 11.0, 1000) is None


def test_read_window_decimates_to_target_resolution_and_snaps_transform(tmp_path):
    with rasterio.open(_lonlat_raster(tmp_path / "src.tif")) as src:
        # Half a degree of latitude is ~55 km; at 11 km per pixel that is ~5 rows.
        data, transform = read_window_at_resolution(src, 4.0, 50.5, 4.5, 51.0, 11_000)
    assert data.shape[0] == 5
    assert transform.c == 4.0 and transform.f == 51.0


def test_read_window_never_upsamples_beyond_native_grid(tmp_path):
    with rasterio.open(_lonlat_raster(tmp_path / "src.tif")) as src:
        data, _ = read_window_at_resolution(src, 4.0, 50.0, 5.0, 51.0, 1)
    assert data.shape == (40, 40)


def test_ramp_rgba_clips_to_range_and_hides_invalid_pixels():
    values = np.array([[0.0, 50.0, 100.0, 500.0]], dtype="float32")
    valid = np.array([[True, True, True, False]])
    lut = colormap_lut("viridis")
    r, g, b, a = ramp_rgba(values, valid, lut, 0.0, 100.0)
    assert a.tolist() == [[255, 255, 255, 0]]
    assert (r[0, 0], g[0, 0], b[0, 0]) == tuple(lut[0])
    assert (r[0, 2], g[0, 2], b[0, 2]) == tuple(lut[255])
    assert (r[0, 3], g[0, 3], b[0, 3]) == (0, 0, 0)


def test_paint_classes_colours_only_known_values():
    values = np.array([[0, 70, 251]], dtype="uint8")
    r, g, b, matched = paint_classes(values, {0: (1, 2, 3), 70: (4, 5, 6)})
    assert matched.tolist() == [[True, True, False]]
    assert np.stack([r, g, b], axis=-1)[0].tolist() == [[1, 2, 3], [4, 5, 6], [0, 0, 0]]


def test_colormap_lut_shape():
    lut = colormap_lut("YlGnBu")
    assert lut.shape == (256, 3) and lut.dtype == np.uint8


def test_reference_id_is_short_and_stable():
    assert reference_id("/a/ref.tif") == reference_id("/a/ref.tif")
    assert reference_id("/a/ref.tif") != reference_id("/b/ref.tif")
    assert len(reference_id("/a/ref.tif")) == 8


def test_build_overlay_overviews_records_resampling(tmp_path):
    path = tmp_path / "ov.tif"
    with rasterio.open(path, "w", driver="GTiff", dtype="uint8", count=1, width=64, height=64, crs="EPSG:4326", transform=from_bounds(0, 0, 1, 1, 64, 64)) as dst:
        dst.write(np.zeros((64, 64), dtype="uint8"), 1)
    build_overlay_overviews(str(path), "nearest")
    with rasterio.open(path) as ds:
        assert ds.overviews(1) == [2, 4, 8, 16, 32]
        assert ds.tags(ns="rio_overview")["resampling"] == "nearest"


def test_build_overlay_overviews_ignores_unreadable_paths(tmp_path):
    build_overlay_overviews(str(tmp_path / "missing.tif"))


def _pipeline_inputs(tmp_path, dtype, values):
    """A 256 px UTM reference tile and a lon/lat source COG cycling through ``values`` pixel by pixel."""
    ref = tmp_path / "ref.tif"
    with rasterio.open(
        ref,
        "w",
        driver="GTiff",
        dtype="uint8",
        count=1,
        width=256,
        height=256,
        crs="EPSG:32631",
        tiled=True,
        blockxsize=256,
        blockysize=256,
        transform=from_bounds(600000, 5650000, 610240, 5660240, 256, 256),
    ) as dst:
        dst.write(np.zeros((256, 256), dtype="uint8"), 1)
    src = tmp_path / "src.tif"
    pattern = np.array(values, dtype=dtype)[np.add.outer(np.arange(60), np.arange(60)) % len(values)]
    with rasterio.open(src, "w", driver="GTiff", dtype=dtype, count=1, width=60, height=60, crs="EPSG:4326", transform=from_bounds(4.0, 50.7, 5.2, 51.3, 60, 60)) as dst:
        dst.write(pattern, 1)
    return str(ref), str(src)


def _run_pipeline(service, ref, src):
    from contextlib import nullcontext
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import patch

    asset = SimpleNamespace(href=src, item_datetime=datetime(2023, 6, 15, tzinfo=timezone.utc))
    with patch.object(type(service), "_s3_env", lambda self: nullcontext()), patch("vresto.api.stac_assets.find_closest_stac_asset", return_value=asset) as find:
        result = service.get_colorized_result(ref, 100, "20230615")
    assert find.call_args.args[0] == service.collection_id
    with rasterio.open(result.aligned_path) as aligned, rasterio.open(result.colorized_path) as rgba:
        return result, aligned.read(1), aligned.nodata, rgba.read()


def test_ramp_service_decodes_aligns_and_hides_nodata(tmp_path):
    from vresto.services.swi import SWI_NODATA, SWIService

    ref, src = _pipeline_inputs(tmp_path, "uint8", [0, 100, 200, 255])
    result, aligned, nodata, rgba = _run_pipeline(SWIService(cache_root=tmp_path / "cache"), ref, src)

    assert nodata == SWI_NODATA
    valid = aligned != SWI_NODATA
    assert valid.any() and (~valid).any()
    assert aligned[valid].min() >= 0.0 and aligned[valid].max() <= 100.0
    assert (rgba[3] == np.where(valid, 255, 0)).all()
    assert result.selected_datetime.year == 2023


def test_class_service_colours_only_listed_classes(tmp_path):
    from vresto.services.wb import WB_CLASSES, WBService

    ref, src = _pipeline_inputs(tmp_path, "uint8", [0, 70, 251, 255])
    _, aligned, _, rgba = _run_pipeline(WBService(cache_root=tmp_path / "cache"), ref, src)

    assert set(np.unique(aligned)) <= {0, 70, 251, 255}
    coloured = np.isin(aligned, list(WB_CLASSES))
    assert coloured.any() and (~coloured).any()
    assert (rgba[3] == np.where(coloured, 255, 0)).all()


def test_service_skips_lookup_outside_coverage(tmp_path):
    from unittest.mock import patch

    from vresto.services.ssm import SSMService

    ref, _ = _pipeline_inputs(tmp_path, "uint8", [0])
    with patch("vresto.services.ssm.ssm_has_coverage", return_value=False), patch("vresto.api.stac_assets.find_closest_stac_asset") as find:
        assert SSMService(cache_root=tmp_path / "cache").get_aligned_result(ref, 1000, "20230615") is None
    find.assert_not_called()
