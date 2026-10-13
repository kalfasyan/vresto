"""Tests for exact per-tile scene counts read from the s2-stac-geoparquet item index.

No network: an in-memory server answers the HTTP range requests and records each one.
"""

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from vresto.services import scene_index, tile_density

ITEMS_2026 = scene_index.INDEX_URL.format(year=2026, name="items.parquet")
LIVE_2026_10 = scene_index.INDEX_URL.format(year=2026, name="live-10.parquet")


def _when(day, hour=10, month=9):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def _parquet(rows, row_group_size=4, sort=True):
    """Write (tile, datetime, cloud) rows as the index stores them, sorted by tile and time."""
    if sort:
        rows = sorted(rows, key=lambda r: (r[0], r[1]))
    table = pa.table({
        "id": [f"S2B_T{tile}_{when:%Y%m%dT%H%M%S}_L2A" for tile, when, _ in rows],
        "_tile": [tile for tile, _, _ in rows],
        "datetime": pa.array([when for _, when, _ in rows], pa.timestamp("us", tz="UTC")),
        "eo:cloud_cover": [cloud for _, _, cloud in rows],
        "assets": ['{"visual": {"href": "https://example.test/' + "x" * 400 + '"}}' for _ in rows],
    })
    buffer = io.BytesIO()
    pq.write_table(table, buffer, row_group_size=row_group_size)
    return buffer.getvalue()


class _RangeServer:
    """Serves byte ranges of in-memory files; unknown URLs answer 404."""

    def __init__(self, files, honour_ranges=True):
        self.files = files
        self.honour_ranges = honour_ranges
        self.requests = []  # (url, range header)

    def get(self, url, headers=None, timeout=None, stream=False):
        spec = (headers or {}).get("Range", "")
        self.requests.append((url, spec))
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        data = self.files.get(url)
        if data is None:
            response.status_code = 404
            return response
        if not self.honour_ranges:
            response.status_code = 200
            response.content = data
            response.raise_for_status = lambda: None
            return response
        first, _, last = spec.removeprefix("bytes=").partition("-")
        if spec.startswith("bytes=-"):
            start, end = max(0, len(data) - int(last)), len(data) - 1
        else:
            start, end = int(first), int(last) if last else len(data) - 1
        response.status_code = 206
        response.content = data[start : end + 1]
        response.headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}"}
        return response

    def bytes_read(self, url):
        total = 0
        for requested, spec in self.requests:
            if requested != url or not spec:
                continue
            first, _, last = spec.removeprefix("bytes=").partition("-")
            total += int(last) if spec.startswith("bytes=-") else int(last) - int(first) + 1
        return total


@pytest.fixture(autouse=True)
def _fresh_caches():
    scene_index._footers.clear()
    yield
    scene_index._footers.clear()


@pytest.fixture
def server(monkeypatch):
    def install(files, **kwargs):
        fake = _RangeServer(files, **kwargs)
        monkeypatch.setattr("requests.get", fake.get)
        return fake

    return install


def _many_tiles():
    """Forty tiles with six scenes each, so one tile is a small part of the file."""
    rows = []
    for n in range(40):
        tile = f"{30 + n // 20:02d}U{'ABCDEFGHJKLMNPQRSTUV'[n % 20]}S"
        for day in (2, 7, 12, 17, 22, 27):
            rows.append((tile, _when(day), float(n % 7 + day)))
    return rows


class TestCountScenes:
    def test_counts_exact_dates_and_lowest_cloud(self, server):
        rows = [
            ("31UFS", _when(12, 23), 30.0),
            ("31UFS", _when(13, 0), 12.0),  # first scene inside the range
            ("31UFS", _when(20), 4.5),
            ("31UFS", _when(30, 23), 50.0),  # last day of the range, late in the day
            ("31UFS", _when(1, 0, month=10), 1.0),  # the day after the range
            ("31UFT", _when(15), None),
            ("32ULB", _when(15), 2.0),  # not on the map
        ]
        server({ITEMS_2026: _parquet(rows)})

        counts, min_cloud = scene_index.count_scenes(["31UFS", "T31UFT"], "2026-09-13", "2026-09-30")

        assert counts == {"31UFS": 3, "31UFT": 1}
        assert min_cloud == {"31UFS": 4.5}

    def test_only_the_row_groups_of_visible_tiles_are_downloaded(self, server, monkeypatch):
        # A probe smaller than the footer also exercises completing the footer on demand.
        monkeypatch.setattr(scene_index, "FOOTER_PROBE_BYTES", 512)
        data = _parquet(_many_tiles(), row_group_size=6)
        fake = server({ITEMS_2026: data})
        body_end = len(data) - pq.read_metadata(io.BytesIO(data)).serialized_size - 8

        counts, _ = scene_index.count_scenes(["30UAS"], "2026-09-01", "2026-09-30")

        assert counts == {"30UAS": 6}
        body_read = 0
        for url, spec in fake.requests:
            first, _, last = spec.removeprefix("bytes=").partition("-")
            # Footer reads run to the end of the file (pyarrow reads the last 64 KiB at once).
            if url == ITEMS_2026 and first and int(first) < body_end and int(last) < len(data) - 1:
                body_read += int(last) - int(first) + 1
        assert 0 < body_read < body_end / 20  # one tile's three columns, not the whole file

    def test_live_tail_scenes_are_added(self, server):
        server({
            ITEMS_2026: _parquet([("31UFS", _when(20), 9.0)]),
            LIVE_2026_10: _parquet([("31UFS", _when(2, month=10), 3.0), ("31UFT", _when(3, month=10), 7.0)], sort=False),
        })

        counts, min_cloud = scene_index.count_scenes(["31UFS", "31UFT"], "2026-09-01", "2026-10-31")

        assert counts == {"31UFS": 2, "31UFT": 1}
        assert min_cloud == {"31UFS": 3.0, "31UFT": 7.0}

    def test_footers_are_read_once_and_reused(self, server):
        fake = server({ITEMS_2026: _parquet(_many_tiles(), row_group_size=6)})

        scene_index.count_scenes(["30UAS"], "2026-09-01", "2026-09-30")
        scene_index.count_scenes(["30UBS"], "2026-09-01", "2026-09-30")

        footer_reads = [spec for url, spec in fake.requests if url == ITEMS_2026 and spec.startswith("bytes=-")]
        assert len(footer_reads) == 1

    def test_missing_files_are_skipped(self, server):
        server({})  # every file 404s

        assert scene_index.count_scenes(["31UFS"], "2026-09-01", "2026-09-30") == ({}, {})

    def test_a_server_that_ignores_ranges_is_refused(self, server):
        server({ITEMS_2026: _parquet([("31UFS", _when(20), 9.0)])}, honour_ranges=False)

        with pytest.raises(IOError):
            scene_index.count_scenes(["31UFS"], "2026-09-01", "2026-09-30")

    @pytest.mark.parametrize(("start", "end"), [("2026-09-30", "2026-09-01"), ("bad", "2026-09-01"), ("2024-01-01", "2026-01-01")])
    def test_unusable_or_too_long_ranges(self, start, end):
        assert scene_index.count_scenes(["31UFS"], start, end) is None

    def test_files_cover_each_year_and_month_of_the_range(self):
        urls = scene_index._files_for(datetime(2025, 12, 20).date(), datetime(2026, 1, 5).date())

        assert [url.split("sentinel-2-c1-l2a/")[1] for url in urls] == [
            "year=2025/items.parquet",
            "year=2026/items.parquet",
            "year=2025/live-12.parquet",
            "year=2026/live-01.parquet",
        ]


class TestTileDensityUsesTheIndex:
    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        tile_density._cache.clear()
        tile_density._month_cache.clear()
        monkeypatch.setattr(tile_density, "_index_down_until", 0.0)
        monkeypatch.setattr(tile_density, "_stats_down_until", 0.0)
        yield
        tile_density._cache.clear()

    def test_tile_counts_follow_the_exact_dates(self, server):
        server({ITEMS_2026: _parquet([("31UFS", _when(5), 9.0), ("31UFS", _when(20), 2.0)])})

        density = tile_density.count_products_per_tile((4.0, 50.0, 5.0, 51.0), "2026-09-13", "2026-09-30", tile_codes=["31UFS"])

        assert density.source == tile_density.INDEX_SOURCE
        assert density.counts == {"31UFS": 1}  # the 5 September scene is before the range
        assert density.months == pytest.approx(18 / 30.44)

    def test_an_unreachable_index_falls_back_to_whole_months(self, server, monkeypatch):
        monkeypatch.setattr(scene_index, "count_scenes", MagicMock(side_effect=IOError("source.coop unreachable")))
        monthly = MagicMock(return_value=tile_density.TileDensity(counts={}, min_cloud={}, truncated=False, source=tile_density.STATS_SOURCE))
        monkeypatch.setattr(tile_density, "_count_from_monthly_stats", monthly)

        density = tile_density.count_products_per_tile((4.0, 50.0, 5.0, 51.0), "2026-09-13", "2026-09-30", tile_codes=["31UFS"])

        assert density.source == tile_density.STATS_SOURCE
        assert tile_density._index_down_until > 0
