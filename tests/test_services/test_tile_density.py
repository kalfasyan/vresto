"""Tests for the per-tile product counts that colour the MGRS grid.

The catalogue is never contacted: a fake client stands in for pystac-client.
"""

from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from vresto.api.config import CopernicusConfig
from vresto.services import tile_density
from vresto.services.tile_density import DEFAULT_MAX_ITEMS, PAGE_SIZE, STAC_COLLECTION, TileDensity, _TTLCache, count_products_per_tile

BBOX = (4.30, 50.20, 4.40, 50.30)
START, END = "2024-01-01", "2024-01-31"


def _s2_id(tile, day="20240105"):
    return f"S2B_MSIL2A_{day}T105339_N0510_R051_T{tile}_{day}T115012"


def _item(item_id, *, grid_code=None, cloud=None):
    props = {"datetime": "2024-01-05T10:53:39Z"}
    if grid_code is not None:
        props["grid:code"] = grid_code
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    return {"type": "Feature", "id": item_id, "properties": props}


class _FakeSearch:
    """Mimics pystac-client's ItemSearch: the request happens when the items are read."""

    def __init__(self, outcome):
        self._outcome = outcome

    def items_as_dicts(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return iter(self._outcome)


class _FakeClient:
    """Serves one outcome (a list of items, or an exception) per search and records each request."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.searches = []

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return _FakeSearch(self._outcomes.pop(0))


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    tile_density._cache.clear()
    tile_density._month_cache.clear()
    monkeypatch.setattr(tile_density, "_cooldown_until", 0.0)
    monkeypatch.setattr(tile_density, "_stats_down_until", 0.0)
    yield
    tile_density._cache.clear()
    tile_density._month_cache.clear()


@pytest.fixture
def stac(monkeypatch):
    """Return a helper that routes searches to a fake client answering with the given outcomes."""

    def install(*outcomes):
        client = _FakeClient(outcomes)
        monkeypatch.setattr(tile_density, "_get_client", lambda: client)
        return client

    return install


@pytest.fixture
def warnings_seen():
    """Collect the WARNING messages logged during a test."""
    messages = []
    handler_id = logger.add(lambda message: messages.append(message.record["message"]), level="WARNING")
    yield messages
    logger.remove(handler_id)


class TestCounting:
    def test_counts_products_and_lowest_cloud_cover_per_tile(self, stac):
        stac([
            _item(_s2_id("31UFS"), grid_code="MGRS-31UFS", cloud=10.0),
            _item(_s2_id("31UFS", "20240108"), grid_code="MGRS-31UFS", cloud=3.2),
            _item(_s2_id("31UFS", "20240111"), grid_code="MGRS-31UFS", cloud=50.0),
            _item(_s2_id("31TFJ"), grid_code="MGRS-31TFJ"),  # no cloud cover reported
        ])

        density = count_products_per_tile(BBOX, START, END)

        assert (density.counts, density.min_cloud, density.truncated) == ({"31UFS": 3, "31TFJ": 1}, {"31UFS": 3.2}, False)
        assert density.months == pytest.approx(31 / 30.44)  # January, for products per month

    def test_tile_code_comes_from_grid_code_before_the_product_id(self, stac):
        stac([_item(_s2_id("31TFJ"), grid_code="MGRS-31UFS", cloud=5.0)])

        assert count_products_per_tile(BBOX, START, END).counts == {"31UFS": 1}

    def test_tile_code_falls_back_to_the_product_id(self, stac):
        stac([_item(_s2_id("31UFS"), cloud=7.0), _item("product-without-a-tile", cloud=1.0)])

        density = count_products_per_tile(BBOX, START, END)

        assert density.counts == {"31UFS": 1}  # the item with no tile code is skipped
        assert density.min_cloud == {"31UFS": 7.0}

    def test_items_repeated_across_pages_are_counted_once(self, stac):
        item = _item(_s2_id("31UFS"), grid_code="MGRS-31UFS", cloud=4.0)
        stac([item, dict(item)])

        assert count_products_per_tile(BBOX, START, END).counts == {"31UFS": 1}

    @pytest.mark.parametrize("cloud", ["n/a", True, None], ids=["text", "bool", "missing"])
    def test_cloud_cover_that_is_not_a_number_is_ignored(self, stac, cloud):
        item = {"id": _s2_id("31UFS"), "properties": {"grid:code": "MGRS-31UFS", "eo:cloud_cover": cloud}}
        stac([item])

        density = count_products_per_tile(BBOX, START, END)

        assert density.counts == {"31UFS": 1}
        assert density.min_cloud == {}


class TestTileCode:
    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            ({"id": "x", "properties": {"grid:code": "MGRS-31UFS"}}, "31UFS"),
            ({"id": _s2_id("31TFJ"), "properties": {"grid:code": "MGRS-"}}, "31TFJ"),
            ({"id": _s2_id("31TFJ")}, "31TFJ"),
            ({"id": "S2A_no_tile_here"}, None),
        ],
        ids=["grid-code", "empty-grid-code", "product-id", "no-tile"],
    )
    def test_tile_code(self, item, expected):
        assert tile_density._tile_code(item) == expected


class TestTruncation:
    def test_flagged_when_the_search_reaches_max_items(self, stac):
        stac([_item(_s2_id("31UFS"), grid_code="MGRS-31UFS"), _item(_s2_id("31UFS", "20240108"), grid_code="MGRS-31UFS")])

        assert count_products_per_tile(BBOX, START, END, max_items=2).truncated is True

    def test_not_flagged_when_the_search_finishes_early(self, stac):
        stac([_item(_s2_id("31UFS"), grid_code="MGRS-31UFS")])

        assert count_products_per_tile(BBOX, START, END, max_items=5).truncated is False


class TestRequest:
    def test_one_anonymous_search_over_whole_days(self, stac):
        client = stac([])

        count_products_per_tile(BBOX, START, END)

        assert len(client.searches) == 1
        request = client.searches[0]
        assert request["collections"] == [STAC_COLLECTION] == ["sentinel-2-l2a"]
        assert request["bbox"] == [4.25, 50.0, 4.5, 50.5]  # widened to the 0.25 degree grid
        assert request["datetime"] == "2024-01-01T00:00:00Z/2024-01-31T23:59:59Z"
        assert request["max_items"] == DEFAULT_MAX_ITEMS
        assert request["limit"] == PAGE_SIZE
        assert request["fields"]["include"] == ["id", "properties.grid:code", "properties.eo:cloud_cover", "properties.datetime"]
        assert "filter" not in request

    def test_full_timestamps_are_passed_through(self, stac):
        client = stac([])

        count_products_per_tile(BBOX, "2024-01-01T06:00:00Z", "2024-01-31T18:00:00Z")

        assert client.searches[0]["datetime"] == "2024-01-01T06:00:00Z/2024-01-31T18:00:00Z"

    def test_cloud_limit_is_sent_as_a_cql2_filter(self, stac):
        client = stac([])

        count_products_per_tile(BBOX, START, END, max_cloud=20)

        request = client.searches[0]
        assert request["filter"] == {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 20]}
        assert request["filter_lang"] == "cql2-json"

    def test_fields_request_is_retried_once_without_fields(self, stac):
        client = stac(RuntimeError("400: fields not supported"), [_item(_s2_id("31UFS"), grid_code="MGRS-31UFS")])

        density = count_products_per_tile(BBOX, START, END)

        assert density.counts == {"31UFS": 1}
        assert len(client.searches) == 2
        assert "fields" in client.searches[0]
        assert "fields" not in client.searches[1]

    @pytest.mark.parametrize(
        "bbox",
        [(4.4, 50.2, 4.3, 50.3), (4.3, 50.3, 4.4, 50.2), (float("nan"), 50.2, 4.4, 50.3), (4.3, 50.2, 4.4)],
        ids=["inverted-lon", "inverted-lat", "not-finite", "too-short"],
    )
    def test_unusable_bbox_returns_none_without_searching(self, stac, bbox):
        client = stac()

        assert count_products_per_tile(bbox, START, END) is None
        assert client.searches == []

    def test_bbox_is_clamped_to_world_coordinates(self, stac):
        client = stac([])

        count_products_per_tile((-190.0, -95.0, 190.0, 95.0), START, END)

        assert client.searches[0]["bbox"] == [-180.0, -90.0, 180.0, 90.0]


class TestCache:
    def test_repeated_request_is_served_from_the_cache(self, stac):
        client = stac([_item(_s2_id("31UFS"), grid_code="MGRS-31UFS")])

        first = count_products_per_tile(BBOX, START, END)
        second = count_products_per_tile(BBOX, START, END)

        assert second == first
        assert len(client.searches) == 1

    def test_nearby_views_share_one_search(self, stac):
        client = stac([])

        count_products_per_tile((4.301, 50.201, 4.399, 50.299), START, END)
        count_products_per_tile((4.305, 50.205, 4.395, 50.295), START, END)

        assert len(client.searches) == 1

    def test_other_dates_or_cloud_limit_need_their_own_search(self, stac):
        client = stac([], [], [])

        count_products_per_tile(BBOX, START, END)
        count_products_per_tile(BBOX, "2024-02-01", "2024-02-29")
        count_products_per_tile(BBOX, START, END, max_cloud=30)

        assert len(client.searches) == 3

    def test_failures_are_not_cached(self, stac):
        client = stac(ConnectionError("down"), ConnectionError("down"), [])

        assert count_products_per_tile(BBOX, START, END) is None
        assert count_products_per_tile(BBOX, START, END) is not None
        assert len(client.searches) == 3

    def test_entries_expire_and_the_least_recently_used_is_evicted(self):
        now = [0.0]
        cache = _TTLCache(ttl_s=10.0, max_entries=2, clock=lambda: now[0])
        density = TileDensity(counts={}, min_cloud={}, truncated=False)
        cache.put("a", density)
        cache.put("b", density)
        assert cache.get("a") is density  # "a" is now the most recently used
        cache.put("c", density)  # evicts "b"

        assert cache.get("b") is None
        assert cache.get("a") is density
        assert cache.get("c") is density
        now[0] = 10.5
        assert cache.get("a") is None  # expired


class TestFailures:
    def test_search_failure_returns_none_and_logs_a_warning(self, stac, warnings_seen):
        stac(ConnectionError("cdse unreachable"), ConnectionError("cdse unreachable"))

        assert count_products_per_tile(BBOX, START, END) is None
        assert any("Could not count products per MGRS tile" in message for message in warnings_seen)

    def test_rate_limit_is_not_retried_without_fields(self, stac):
        client = stac(RuntimeError('{"error":"WAF","message":"Rate limit exceeded","status":429}'))

        assert count_products_per_tile(BBOX, START, END) is None
        assert len(client.searches) == 1

    def test_server_error_is_not_retried_without_fields(self, stac):
        client = stac(RuntimeError("503 Service Unavailable"))

        assert count_products_per_tile(BBOX, START, END) is None
        assert len(client.searches) == 1

    def test_rate_limit_pauses_searches_for_the_cooldown(self, stac, monkeypatch, warnings_seen):
        now = [100.0]
        monkeypatch.setattr(tile_density.time, "monotonic", lambda: now[0])
        client = stac(RuntimeError("APIError: Rate limit exceeded"), [])

        assert count_products_per_tile(BBOX, START, END) is None
        assert tile_density.rate_limited()
        assert any("rate-limiting" in message for message in warnings_seen)

        assert count_products_per_tile(BBOX, START, END) is None  # paused: no request sent
        assert len(client.searches) == 1

        now[0] += tile_density.RATE_LIMIT_COOLDOWN_S + 1
        assert not tile_density.rate_limited()
        assert count_products_per_tile(BBOX, START, END) is not None
        assert len(client.searches) == 2

    def test_catalogue_that_cannot_be_opened_returns_none(self, monkeypatch, warnings_seen):
        def unreachable():
            raise OSError("no route to host")

        monkeypatch.setattr(tile_density, "_get_client", unreachable)

        assert count_products_per_tile(BBOX, START, END) is None
        assert warnings_seen


class TestClient:
    def test_catalogue_is_opened_once_and_reused(self, monkeypatch):
        monkeypatch.setattr(tile_density, "_client", None)
        client = MagicMock(name="pystac client")

        with patch("pystac_client.Client.open", return_value=client) as open_mock:
            assert tile_density._get_client() is client
            assert tile_density._get_client() is client

        open_mock.assert_called_once()
        assert open_mock.call_args.args == (CopernicusConfig.STAC_BASE_URL,)
        stac_io = open_mock.call_args.kwargs["stac_io"]
        assert stac_io.timeout == tile_density.REQUEST_TIMEOUT_S

    def test_throttled_responses_are_retried_with_backoff(self, monkeypatch):
        monkeypatch.setattr(tile_density, "_client", None)

        with patch("pystac_client.Client.open", return_value=MagicMock()) as open_mock:
            tile_density._get_client()

        retry = open_mock.call_args.kwargs["stac_io"].session.get_adapter("https://").max_retries
        assert 429 in retry.status_forcelist
        assert retry.respect_retry_after_header
        assert retry.allowed_methods is None  # POST searches are retried too
        assert retry.total == tile_density.RETRY_TOTAL


def _month_parquet(rows):
    """Encode (tile, scene_count, min_cloud_cover) rows the way the monthly statistics store them."""
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({
        "mgrs_tile": pa.array([r[0] for r in rows], pa.string()),
        "scene_count": pa.array([r[1] for r in rows], pa.uint16()),
        "min_cloud_cover": pa.array([r[2] for r in rows], pa.uint8()),
        "median_cloud_cover": pa.array([r[2] for r in rows], pa.uint8()),
    })
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


@pytest.fixture
def months(monkeypatch):
    """Serve monthly statistics files from a dict of month -> rows (None for a 404) and record the URLs read."""

    def install(by_month, error=None):
        requested = []

        def fake_get(url, timeout):
            requested.append(url)
            if error is not None:
                raise error
            month = url.rsplit("/", 1)[-1].removesuffix(".parquet")
            rows = by_month.get(month)
            response = MagicMock(status_code=404 if rows is None else 200)
            response.content = b"" if rows is None else _month_parquet(rows)
            return response

        monkeypatch.setattr("requests.get", fake_get)
        return requested

    return install


class TestMonthlyStatistics:
    def test_counts_sum_over_the_months_and_keep_the_lowest_cloud(self, months, stac):
        client = stac()
        requested = months({
            "2026-09": [("31UFS", 7, 12), ("31UFT", 3, 40), ("10SEG", 9, 1)],
            "2026-10": [("31UFS", 2, 5)],
        })

        density = count_products_per_tile(BBOX, "2026-09-13", "2026-10-13", tile_codes=["31UFS", "31UFT", "31UGS"])

        assert density.counts == {"31UFS": 9, "31UFT": 3}  # only the tiles on the map; 31UGS has none
        assert density.min_cloud == {"31UFS": 5.0, "31UFT": 40.0}
        assert density.truncated is False
        assert density.source == tile_density.STATS_SOURCE
        assert density.period == "Sep-Oct 2026"
        assert density.months == 2.0
        assert [url.rsplit("/", 1)[-1] for url in requested] == ["2026-09.parquet", "2026-10.parquet"]
        assert client.searches == []  # the CDSE catalogue is not searched

    def test_months_are_downloaded_once_and_reused(self, months, stac):
        stac()
        requested = months({"2026-09": [("31UFS", 7, 12)]})

        count_products_per_tile(BBOX, "2026-09-01", "2026-09-30", tile_codes=["31UFS"])
        count_products_per_tile((10.0, 45.0, 11.0, 46.0), "2026-09-05", "2026-09-20", tile_codes=["32TNR"])

        assert len(requested) == 1

    def test_a_month_without_a_file_counts_as_empty(self, months, stac):
        stac()
        months({})  # every month 404s

        density = count_products_per_tile(BBOX, "2030-01-01", "2030-01-31", tile_codes=["31UFS"])

        assert density.counts == {}
        assert density.source == tile_density.STATS_SOURCE

    def test_unreachable_statistics_fall_back_to_the_catalogue(self, months, stac, warnings_seen):
        stac([_item(_s2_id("31UFS"), grid_code="MGRS-31UFS")])
        requested = months({}, error=ConnectionError("source.coop unreachable"))

        density = count_products_per_tile(BBOX, START, END, tile_codes=["31UFS"])

        assert density.source == tile_density.STAC_SOURCE
        assert density.counts == {"31UFS": 1}
        assert any("Monthly tile statistics unavailable" in message for message in warnings_seen)

        stac([])
        count_products_per_tile(BBOX, "2024-02-01", "2024-02-29", tile_codes=["31UFS"])
        assert len(requested) == 1  # not retried straight away

    def test_a_cloud_limit_uses_the_catalogue(self, months, stac):
        client = stac([])
        requested = months({"2024-01": [("31UFS", 7, 12)]})

        count_products_per_tile(BBOX, START, END, max_cloud=20, tile_codes=["31UFS"])

        assert requested == []
        assert len(client.searches) == 1

    def test_without_tile_codes_the_catalogue_is_used(self, months, stac):
        client = stac([])
        requested = months({"2024-01": [("31UFS", 7, 12)]})

        count_products_per_tile(BBOX, START, END)

        assert requested == []
        assert len(client.searches) == 1

    @pytest.mark.parametrize(
        ("start", "end", "label"),
        [("2026-09-01", "2026-09-30", "Sep 2026"), ("2025-12-20", "2026-01-10", "Dec 2025-Jan 2026")],
    )
    def test_period_label(self, start, end, label):
        assert tile_density._period_label(tile_density._months_between(start, end)) == label

    @pytest.mark.parametrize(("start", "end"), [("2026-10-01", "2026-09-01"), ("bad", "2026-09-01"), ("2020-01-01", "2026-01-01")])
    def test_unusable_or_too_long_ranges_have_no_months(self, start, end):
        assert tile_density._months_between(start, end) is None
