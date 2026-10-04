"""The temporal overlay services must hand back the aligned raster next to the colourised one.

The point inspector samples the aligned raster, and re-deriving its path would repeat the
network STAC search that precedes these services' cache check. So the result has to carry it
on both return paths: right after colourising, and when the RGBA raster is already cached.
"""

import importlib
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

SELECTED = datetime(2020, 1, 21, tzinfo=timezone.utc)

# module, service class, aligned-result method, colourised-result method, aligned dtype, nodata, valid pixel values
CASES = [
    ("dmp", "DMPService", "get_aligned_dmp_result", "get_colorized_dmp_result", "float32", -9999.0, [0.0, 52.3, 150.0]),
    ("fapar", "FAPARService", "get_aligned_fapar_result", "get_colorized_fapar_result", "uint8", 255, [0, 100, 235]),
    ("lst", "LSTService", "get_aligned_lst_result", "get_colorized_lst_result", "float32", -9999.0, [-12.5, 14.3, 41.0]),
    ("ssm", "SSMService", "get_aligned_ssm_result", "get_colorized_ssm_result", "float32", -9999.0, [0.0, 41.0, 100.0]),
    ("swi", "SWIService", "get_aligned_swi_result", "get_colorized_swi_result", "float32", -9999.0, [0.0, 41.0, 100.0]),
    ("wb", "WBService", "get_aligned_wb_result", "get_colorized_wb_result", "uint8", 251, [0, 70, 255]),
    ("ba", "BAService", "get_aligned_ba_result", "get_colorized_ba_result", "float32", -9999.0, [1.0, 214.0, 366.0]),
]


def _write_aligned(path, dtype, nodata, values):
    """Write a small aligned-style raster: valid pixels, one nodata pixel, 16x16 so overviews can be built."""
    data = np.full((16, 16), nodata, dtype=dtype)
    data[0, : len(values)] = values
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=16,
        width=16,
        count=1,
        dtype=dtype,
        crs="EPSG:32631",
        transform=from_origin(500000.0, 5600000.0, 60.0, 60.0),
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return str(path)


@pytest.mark.parametrize(("module", "service_cls", "aligned_method", "colorized_method", "dtype", "nodata", "values"), CASES, ids=[case[0] for case in CASES])
def test_colorized_result_carries_the_aligned_raster(tmp_path, module, service_cls, aligned_method, colorized_method, dtype, nodata, values):
    service = getattr(importlib.import_module(f"vresto.services.{module}"), service_cls)(cache_root=tmp_path / "cache")
    aligned = _write_aligned(tmp_path / "aligned_source.tif", dtype, nodata, values)

    with patch.object(service, aligned_method, return_value=(aligned, SELECTED)) as aligned_call:
        built = getattr(service, colorized_method)("/tmp/ref.tif", 300, "20200126")
        cached = getattr(service, colorized_method)("/tmp/ref.tif", 300, "20200126")

    assert aligned_call.call_count == 2
    for result in (built, cached):
        assert result.aligned_path == aligned
        assert result.colorized_path.endswith("_rgba.tif")
        assert result.selected_datetime == SELECTED
    assert built.colorized_path == cached.colorized_path
