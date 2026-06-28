from datetime import datetime, timezone

import numpy as np

from vresto.services.fapar import (
    FAPAR_COLOR_BY_VALUE,
    FAPAR_LEGEND,
    FAPAR_OFFSET,
    FAPAR_SCALE,
    FAPARService,
    raw_fapar_to_physical,
)


def test_raw_fapar_to_physical_uses_product_scale_and_offset():
    raw = np.array([0, 50, 125, 250], dtype=np.float32)
    result = raw_fapar_to_physical(raw)
    expected = np.array([0.0, 50.0, 125.0, 250.0], dtype=np.float32) * FAPAR_SCALE + FAPAR_OFFSET
    assert np.allclose(result, expected)


def test_fapar_color_by_value_matches_legend():
    for class_id, r, g, b, _label in FAPAR_LEGEND:
        assert FAPAR_COLOR_BY_VALUE[class_id] == (r, g, b)


def test_fapar_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = FAPARService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 300, "20200615")
    assert key.startswith("fapar_20200615_300m_")


def test_fapar_overlay_result_dataclass():
    from vresto.services.fapar import FAPAROverlayResult

    dt = datetime(2020, 6, 15, 12, 0, tzinfo=timezone.utc)
    result = FAPAROverlayResult("/tmp/fapar_rgba.tif", dt)
    assert result.colorized_path == "/tmp/fapar_rgba.tif"
    assert result.selected_datetime == dt
