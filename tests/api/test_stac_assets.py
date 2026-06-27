from datetime import datetime, timezone
from types import SimpleNamespace

from vresto.api.stac_assets import normalize_stac_href_to_vsis3, parse_date_like, select_nearest_stac_item


def test_parse_date_like_accepts_compact_and_dashed_dates():
    assert parse_date_like("20200627") == datetime(2020, 6, 27, tzinfo=timezone.utc)
    assert parse_date_like("2020-06-27") == datetime(2020, 6, 27, tzinfo=timezone.utc)
    assert parse_date_like("2020-06-27 13:45:00") == datetime(2020, 6, 27, 13, 45, 0, tzinfo=timezone.utc)
    assert parse_date_like("202006271345") == datetime(2020, 6, 27, 13, 45, 0, tzinfo=timezone.utc)


def test_normalize_stac_href_to_vsis3_converts_s3_urls():
    assert normalize_stac_href_to_vsis3("s3://eodata/path/file.tif") == "/vsis3/eodata/path/file.tif"
    assert normalize_stac_href_to_vsis3("/vsis3/eodata/path/file.tif") == "/vsis3/eodata/path/file.tif"


def test_select_nearest_stac_item_chooses_closest_datetime():
    target = datetime(2020, 7, 15, tzinfo=timezone.utc)
    items = [
        SimpleNamespace(datetime=datetime(2020, 7, 1, tzinfo=timezone.utc)),
        SimpleNamespace(datetime=datetime(2020, 7, 11, tzinfo=timezone.utc)),
        SimpleNamespace(datetime=datetime(2020, 7, 21, tzinfo=timezone.utc)),
    ]
    selected = select_nearest_stac_item(items, target)
    assert selected.datetime == datetime(2020, 7, 11, tzinfo=timezone.utc)
