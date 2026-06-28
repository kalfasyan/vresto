from datetime import datetime, timezone

import numpy as np

from vresto.services.swi import (
    SWI_LEGEND,
    SWI_OFFSET,
    SWI_SCALE,
    SWI_VALID_MAX_DN,
    SWIOverlayResult,
    SWIService,
    raw_swi_to_physical,
)


def test_raw_swi_to_physical_uses_product_scale_and_offset():
    raw = np.array([0, 50, 100, 200], dtype=np.float32)
    result = raw_swi_to_physical(raw)
    expected = raw * SWI_SCALE + SWI_OFFSET
    assert np.allclose(result, expected)


def test_swi_valid_dn_maps_to_zero_hundred_percent():
    assert raw_swi_to_physical(np.array([0]))[0] == 0.0
    assert raw_swi_to_physical(np.array([SWI_VALID_MAX_DN]))[0] == 100.0


def test_swi_legend_is_well_formed():
    assert SWI_LEGEND
    for _id, r, g, b, label in SWI_LEGEND:
        assert all(0 <= channel <= 255 for channel in (r, g, b))
        assert isinstance(label, str) and label


def test_swi_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = SWIService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 12500, "20230615")
    assert key.startswith("swi_20230615_12500m_")


def test_swi_overlay_result_dataclass():
    dt = datetime(2023, 6, 15, tzinfo=timezone.utc)
    result = SWIOverlayResult("/tmp/swi_rgba.tif", dt)
    assert result.colorized_path == "/tmp/swi_rgba.tif"
    assert result.selected_datetime == dt


def test_swi_colormap_lut_shape():
    lut = SWIService._colormap_lut()
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8
