import os
from pathlib import Path

import numpy as np
import pytest

from vresto.bands import BandComposer, find_band_file, scan_img_data


def test_scan_img_data_and_find(tmp_path: Path):
    # create dummy files matching the BAND_RE pattern
    d = tmp_path / "IMG_DATA"
    d.mkdir()
    f1 = d / "TILE_B04_10m.jp2"
    f2 = d / "TILE_B04_60m.jp2"
    f3 = d / "TILE_SCL_60m.jp2"
    f1.write_text("")
    f2.write_text("")
    f3.write_text("")

    bands = scan_img_data(str(tmp_path))
    assert "B04" in bands
    assert 10 in bands.get("B04", set())
    assert 60 in bands.get("B04", set())

    p = find_band_file(str(tmp_path), "B04", preferred_resolution=60)
    assert p is not None and p.endswith("60m.jp2")


def test_composer_save_array_as_png():
    composer = BandComposer()
    arr = (np.random.rand(128, 128, 3) * 255).astype("uint8")
    tmp = composer.save_array_as_png(arr)
    assert os.path.exists(tmp)
    # cleanup
    try:
        os.remove(tmp)
    except Exception:
        pass


def test_composer_build_rgb_preview_skipped_if_no_rasterio(monkeypatch):
    # If rasterio not installed, reading functions should raise
    composer = BandComposer()
    if composer._rasterio is None:
        with pytest.raises(RuntimeError):
            composer.read_band_preview("nonexistent.jp2")
    else:
        pytest.skip("rasterio available; skip negative test")


# ---------------------------------------------------------------------------
# Tests below exercise the rasterio-dependent paths in BandComposer.
# ---------------------------------------------------------------------------
rasterio = pytest.importorskip("rasterio")


def _write_tiff(path: Path, data: np.ndarray) -> None:
    """Write a tiny single-band GeoTIFF for composer tests."""
    h, w = data.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype=data.dtype,
    ) as dst:
        dst.write(data, 1)


def test_composer_read_band_preview_full_resolution(tmp_path: Path):
    arr = (np.random.rand(32, 48) * 255).astype("uint8")
    p = tmp_path / "band.tif"
    _write_tiff(p, arr)

    composer = BandComposer()
    out = composer.read_band_preview(str(p))

    assert out.shape == (32, 48)
    assert out.dtype == arr.dtype


def test_composer_read_band_preview_with_out_shape_resamples(tmp_path: Path):
    arr = (np.random.rand(64, 64) * 255).astype("uint8")
    p = tmp_path / "band.tif"
    _write_tiff(p, arr)

    composer = BandComposer()
    out = composer.read_band_preview(str(p), out_shape=(16, 16))

    assert out.shape == (16, 16)


def test_composer_build_rgb_preview_stacks_three_bands(tmp_path: Path):
    paths = []
    for i, label in enumerate(("r", "g", "b")):
        arr = np.full((20, 30), (i + 1) * 50, dtype="uint8")
        p = tmp_path / f"{label}.tif"
        _write_tiff(p, arr)
        paths.append(str(p))

    composer = BandComposer()
    rgb = composer.build_rgb_preview(paths)

    assert rgb.shape == (20, 30, 3)
    assert rgb.dtype == np.uint8
    # After per-band percentile stretch the three channels should still be
    # ordered by their input intensities (R < G < B in our synthetic data).
    assert rgb[..., 0].mean() <= rgb[..., 1].mean() <= rgb[..., 2].mean()


def test_composer_build_rgb_preview_rejects_wrong_count(tmp_path: Path):
    arr = np.zeros((8, 8), dtype="uint8")
    p = tmp_path / "only.tif"
    _write_tiff(p, arr)

    composer = BandComposer()
    with pytest.raises(ValueError, match="three file paths"):
        composer.build_rgb_preview([str(p), str(p)])


def test_composer_build_rgb_preview_propagates_read_errors(tmp_path: Path):
    valid = tmp_path / "ok.tif"
    _write_tiff(valid, np.zeros((4, 4), dtype="uint8"))

    composer = BandComposer()
    with pytest.raises(Exception):
        composer.build_rgb_preview([str(valid), str(valid), str(tmp_path / "missing.tif")])
