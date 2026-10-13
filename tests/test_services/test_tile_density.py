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
def _clean_cache():
    tile_density._cache.clear()
    yield
    tile_density._cache.clear()


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

        assert density == TileDensity(counts={"31UFS": 3, "31TFJ": 1}, min_cloud={"31UFS": 3.2}, truncated=False)

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
        assert request["bbox"] == [4.30, 50.20, 4.40, 50.30]
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

        open_mock.assert_called_once_with(CopernicusConfig.STAC_BASE_URL, timeout=tile_density.REQUEST_TIMEOUT_S)
