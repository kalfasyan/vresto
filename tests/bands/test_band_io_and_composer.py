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
