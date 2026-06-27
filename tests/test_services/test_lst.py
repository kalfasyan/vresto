from datetime import datetime, timezone

import numpy as np

from vresto.services.lst import format_lst_selected_datetime, raw_lst_to_celsius


def test_raw_lst_to_celsius_uses_product_scale():
    raw = np.array([-7000, 0, 100, 2500], dtype=np.float32)
    result = raw_lst_to_celsius(raw)
    assert np.allclose(result, np.array([-70.0, 0.0, 1.0, 25.0], dtype=np.float32))


def test_format_lst_selected_datetime_uses_utc_label():
    selected = datetime(2020, 1, 26, 12, 0, tzinfo=timezone.utc)
    assert format_lst_selected_datetime(selected) == "2020-01-26 13:00 CET"


def test_format_lst_selected_datetime_uses_dst_in_summer():
    selected = datetime(2020, 7, 26, 12, 0, tzinfo=timezone.utc)
    assert format_lst_selected_datetime(selected) == "2020-07-26 14:00 CEST"
