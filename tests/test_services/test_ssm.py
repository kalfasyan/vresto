from datetime import datetime, timezone

import numpy as np

from vresto.services.ssm import (
    SSM_LEGEND,
    SSM_OFFSET,
    SSM_SCALE,
    SSM_VALID_MAX_DN,
    SSMOverlayResult,
    SSMService,
    raw_ssm_to_physical,
    ssm_has_coverage,
)


def test_raw_ssm_to_physical_uses_product_scale_and_offset():
    raw = np.array([0, 50, 100, 200], dtype=np.float32)
    result = raw_ssm_to_physical(raw)
    expected = raw * SSM_SCALE + SSM_OFFSET
    assert np.allclose(result, expected)


def test_ssm_valid_dn_maps_to_zero_hundred_percent():
    assert raw_ssm_to_physical(np.array([0]))[0] == 0.0
    assert raw_ssm_to_physical(np.array([SSM_VALID_MAX_DN]))[0] == 100.0


def test_ssm_has_coverage_inside_and_outside_europe():
    assert ssm_has_coverage(4.0, 50.0, 5.0, 51.0)  # Belgium
    assert not ssm_has_coverage(20.0, -5.0, 21.0, -4.0)  # Central Africa
    assert not ssm_has_coverage(-120.0, 40.0, -119.0, 41.0)  # California


def test_ssm_legend_is_well_formed():
    assert SSM_LEGEND
    for _id, r, g, b, label in SSM_LEGEND:
        assert all(0 <= channel <= 255 for channel in (r, g, b))
        assert isinstance(label, str) and label


def test_ssm_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = SSMService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 1000, "20230615")
    assert key.startswith("ssm_20230615_1000m_")


def test_ssm_overlay_result_dataclass():
    dt = datetime(2023, 6, 15, tzinfo=timezone.utc)
    result = SSMOverlayResult("/tmp/ssm_rgba.tif", dt)
    assert result.colorized_path == "/tmp/ssm_rgba.tif"
    assert result.selected_datetime == dt


def test_ssm_colormap_lut_shape():
    lut = SSMService._colormap_lut()
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8
