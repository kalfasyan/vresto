from datetime import datetime, timezone

from vresto.services.wb import (
    WB_CLASS_LEGENDS,
    WB_CLASSES,
    WB_RAW_NODATA,
    WBOverlayResult,
    WBService,
)


def test_wb_classes_match_legend():
    for class_id, r, g, b, _label in WB_CLASS_LEGENDS:
        assert WB_CLASSES[class_id] == (r, g, b)


def test_wb_colors_only_water_and_sea():
    # Only Sea (0) and Water (70) are coloured; No-data/No-water stay transparent.
    assert set(WB_CLASSES) == {0, 70}
    assert WB_RAW_NODATA not in WB_CLASSES
    assert 255 not in WB_CLASSES


def test_wb_legend_is_well_formed():
    assert WB_CLASS_LEGENDS
    for _id, r, g, b, label in WB_CLASS_LEGENDS:
        assert all(0 <= channel <= 255 for channel in (r, g, b))
        assert isinstance(label, str) and label


def test_wb_service_aligned_key_uses_reference_and_date_and_resolution(tmp_path):
    service = WBService(cache_root=tmp_path)
    key = service._aligned_key("/path/to/ref.tif", 100, "20230601")
    assert key.startswith("wb_20230601_100m_")


def test_wb_overlay_result_dataclass():
    dt = datetime(2023, 6, 1, tzinfo=timezone.utc)
    result = WBOverlayResult("/tmp/wb_rgba.tif", dt)
    assert result.colorized_path == "/tmp/wb_rgba.tif"
    assert result.selected_datetime == dt
