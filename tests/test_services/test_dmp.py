from datetime import datetime, timezone

import numpy as np

from vresto.services.dmp import (
    DMP_LEGEND,
    DMP_NODATA,
    DMP_OFFSET,
    DMP_SCALE,
    DMPOverlayResult,
    DMPService,
    raw_dmp_to_physical,
)


def test_raw_dmp_to_physical_uses_product_scale_and_offset():
    raw = np.array([0, 100, 5000, 15000], dtype=np.float32)
    result = raw_dmp_to_physical(raw)
    expected = raw * DMP_SCALE + DMP_OFFSET
    assert np.allclose(result, expected)


def test_dmp_legend_is_well_formed():
    assert DMP_LEGEND
    for _id, r, g, b, label in DMP_LEGEND:
        assert all(0 <= channel <= 255 for channel in (r, g, b))
        assert isinstance(label, str) and label


def test_dmp_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = DMPService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 300, "20230615")
    assert key.startswith("dmp_20230615_300m_")


def test_dmp_overlay_result_dataclass():
    dt = datetime(2023, 6, 15, tzinfo=timezone.utc)
    result = DMPOverlayResult("/tmp/dmp_rgba.tif", dt)
    assert result.colorized_path == "/tmp/dmp_rgba.tif"
    assert result.selected_datetime == dt


def test_dmp_colormap_lut_shape():
    lut = DMPService._colormap_lut()
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8


def test_dmp_nodata_is_negative_sentinel():
    # Flags (-1 Missing, -2 Sea) must fall outside the valid physical range.
    assert DMP_NODATA < 0
