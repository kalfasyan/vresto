from datetime import datetime, timezone

import numpy as np

from vresto.services.ba import (
    BA_LEGEND,
    BA_MAX_VALUE,
    BA_MIN_VALUE,
    BA_NODATA,
    BAOverlayResult,
    BAService,
)


def test_ba_day_of_burn_range_is_valid_day_of_year():
    assert BA_MIN_VALUE == 1.0
    assert BA_MAX_VALUE == 366.0


def test_ba_nodata_is_negative_sentinel():
    # Unburned (0), flags (<0) and no-data must all fall outside [1, 366].
    assert BA_NODATA < BA_MIN_VALUE


def test_ba_legend_is_well_formed():
    assert BA_LEGEND
    for _id, r, g, b, label in BA_LEGEND:
        assert all(0 <= channel <= 255 for channel in (r, g, b))
        assert isinstance(label, str) and label


def test_ba_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = BAService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 300, "20230815")
    assert key.startswith("ba_20230815_300m_")


def test_ba_overlay_result_dataclass():
    dt = datetime(2023, 8, 1, tzinfo=timezone.utc)
    result = BAOverlayResult("/tmp/ba_rgba.tif", dt)
    assert result.colorized_path == "/tmp/ba_rgba.tif"
    assert result.selected_datetime == dt


def test_ba_colormap_lut_shape():
    lut = BAService._colormap_lut()
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8
