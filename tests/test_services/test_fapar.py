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
    assert result.aligned_path == ""  # optional, so callers that only need the colorized raster keep working


def test_dns_are_coloured_by_their_fapar_value():
    from vresto.services.fapar import FAPAR_RGB_BY_DN, fapar_legend_bin

    # DN 125 is FAPAR 0.50: the "0.50" bin, not legend row 125 (which does not exist).
    assert fapar_legend_bin(125) == 51
    assert FAPAR_LEGEND[50][4] == "0.50"
    assert tuple(FAPAR_RGB_BY_DN[125]) == FAPAR_COLOR_BY_VALUE[51]
    # DN 0 is FAPAR 0.00, which used to be drawn black; values above 0.94 fall in the last bin.
    assert tuple(FAPAR_RGB_BY_DN[0]) == FAPAR_COLOR_BY_VALUE[1]
    assert fapar_legend_bin(250) == len(FAPAR_LEGEND)


def test_colorize_hides_flags_and_nodata(tmp_path):
    import numpy as np

    from vresto.services.fapar import FAPARService

    dn = np.array([[0, 125, 250, 251, 254, 255]], dtype=np.uint8)
    r, g, b, a = FAPARService(cache_root=tmp_path)._colorize(dn)
    assert a.tolist() == [[255, 255, 255, 0, 0, 0]]
    assert (r[0, 1], g[0, 1], b[0, 1]) == FAPAR_COLOR_BY_VALUE[51]


def test_ui_legend_shows_the_palette_the_map_uses():
    from vresto.ui.overlays import FAPAR_LEGEND_STOPS, OVERLAY_REGISTRY

    spec = next(s for s in OVERLAY_REGISTRY if s.name == "fapar")
    sampled = tuple(f"#{r:02x}{g:02x}{b:02x}" for _, r, g, b, _ in (FAPAR_LEGEND[i] for i in (0, 19, 38, 57, 76, 94)))
    assert spec.legend_stops == FAPAR_LEGEND_STOPS == sampled
    assert spec.vmax == float(FAPAR_LEGEND[-1][4])
