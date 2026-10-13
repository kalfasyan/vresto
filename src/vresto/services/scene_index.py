"""Exact per-tile scene counts from the s2-stac-geoparquet item index, read with HTTP range requests.

The s2-stac-geoparquet mirror of Earth Search publishes every Sentinel-2 L2A Collection 1 scene
as GeoParquet on Source Cooperative: one ``items.parquet`` per year, sorted by MGRS tile and
time in row groups of about 6,000 scenes, plus ``live-MM.parquet`` files that hold the scenes
fetched since the last monthly fold. Both are public, with no API in front of them.

Counting the scenes of the tiles on the map over exact dates then needs only a small part of
those files. The footer of each file lists, per row group, the byte range of every column and
the lowest and highest tile it holds. A footer is read once and cached. The row groups that can
hold a visible tile are chosen from it, and only their tile, time and cloud-cover columns are
downloaded, as a few merged range requests run in parallel. A year's file is gigabytes; a map
view usually reads a few megabytes of it.
"""

from __future__ import annotations

import bisect
import io
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from loguru import logger

INDEX_URL = "https://data.source.coop/tge-labs/s2-stac-geoparquet/sentinel-2-c1-l2a/year={year}/{name}"
# Columns read for counting: the MGRS tile (without the "T" prefix), sensing time and cloud cover.
COLUMNS = ("_tile", "datetime", "eo:cloud_cover")
REQUEST_TIMEOUT_S = 30.0
# Bytes read from the end of a file to find its footer; a larger footer is completed on demand.
FOOTER_PROBE_BYTES = 1 << 20
# Column-chunk ranges closer than this are fetched as one request.
MERGE_GAP_BYTES = 256 << 10
FETCH_WORKERS = 8
# The index refreshes daily, so a footer is reused for this long.
FOOTER_TTL_S = 6 * 3600.0
FOOTER_CACHE_ENTRIES = 32
# A date range longer than this is not counted exactly; the caller falls back to monthly counts.
MAX_RANGE_DAYS = 400
# Decoded row groups (tile, time and cloud-cover columns only) kept for panning back and forth.
ROW_GROUP_TTL_S = 3600.0
ROW_GROUP_CACHE_ENTRIES = 400
# Files of one count are read concurrently.
FILE_WORKERS = 4

_session_lock = threading.Lock()
_session: Any = None


def _http() -> Any:
    """A shared HTTP session, so range requests reuse open connections instead of new TLS handshakes."""
    global _session
    with _session_lock:
        if _session is None:
            import requests
            from requests.adapters import HTTPAdapter

            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=FETCH_WORKERS * FILE_WORKERS)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            _session = session
        return _session


class _Missing(Exception):
    """The file does not exist (HTTP 404), which is normal for a month without a live tail."""


def _http_range(url: str, start: int, end: Optional[int] = None) -> tuple[bytes, int]:
    """Fetch bytes ``start..end`` (inclusive) of ``url``; a negative ``start`` reads the last bytes.

    Returns the bytes and the total size of the file. Raises ``_Missing`` on 404, and refuses a
    server that ignores the range, so a multi-gigabyte file is never downloaded whole.
    """
    spec = f"bytes={start}" if start < 0 else f"bytes={start}-{'' if end is None else end}"
    with _http().get(url, headers={"Range": spec}, timeout=REQUEST_TIMEOUT_S, stream=True) as response:
        if response.status_code == 404:
            raise _Missing(url)
        if response.status_code != 206:
            response.raise_for_status()
            raise IOError(f"{url} answered {response.status_code} to a range request")
        total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        return response.content, total


class _SparseRemoteFile(io.RawIOBase):
    """A read-only file over HTTP that keeps only the byte ranges it has fetched.

    pyarrow reads it like a local file. Ranges fetched up front with :meth:`prefetch` are served
    from memory; any other read is fetched on demand.
    """

    def __init__(self, url: str, size: int, blocks: Optional[dict[int, bytes]] = None):
        super().__init__()
        self.url = url
        self.size = size
        self._blocks: dict[int, bytes] = dict(blocks or {})
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buffer) -> int:
        n = min(len(buffer), self.size - self._pos)
        if n <= 0:
            return 0
        data = self._read_at(self._pos, n)
        buffer[:n] = data
        self._pos += n
        return n

    def _read_at(self, start: int, n: int) -> bytes:
        for offset, block in self._blocks.items():
            if offset <= start and start + n <= offset + len(block):
                return block[start - offset : start - offset + n]
        data, _ = _http_range(self.url, start, start + n - 1)
        self._blocks[start] = data
        return data

    def prefetch(self, ranges: Sequence[tuple[int, int]]) -> None:
        """Fetch ``(start, end)`` ranges (end exclusive) in parallel, merging close ones."""
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start - merged[-1][1] <= MERGE_GAP_BYTES:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        if not merged:
            return
        with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(merged))) as pool:
            fetched = pool.map(lambda r: (r[0], _http_range(self.url, r[0], r[1] - 1)[0]), merged)
            for start, data in fetched:
                self._blocks[start] = data


class _Cache:
    """Thread-safe LRU cache whose entries expire; ``get`` tells a cached None from a miss."""

    def __init__(self, ttl_s: float, max_entries: int) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._entries: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> tuple[bool, Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or time.monotonic() - entry[0] > self._ttl_s:
                return False, None
            self._entries.move_to_end(key)
            return True, entry[1]

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# Footer metadata per file URL (None for a file that does not exist).
_footers = _Cache(FOOTER_TTL_S, FOOTER_CACHE_ENTRIES)
# (url, row group) -> rows of (tile, sensing time, cloud cover).
_row_groups = _Cache(ROW_GROUP_TTL_S, ROW_GROUP_CACHE_ENTRIES)


def _open(url: str) -> Optional[tuple[_SparseRemoteFile, Any]]:
    """Return the file and its parsed footer, or None when the file does not exist."""
    import pyarrow.parquet as pq

    known, cached = _footers.get(url)
    if known:
        if cached is None:
            return None
        size, metadata = cached
        return _SparseRemoteFile(url, size), metadata
    try:
        tail, size = _http_range(url, -FOOTER_PROBE_BYTES)
    except _Missing:
        _footers.put(url, None)
        return None
    remote = _SparseRemoteFile(url, size, {max(0, size - len(tail)): tail})
    metadata = pq.read_metadata(remote)
    _footers.put(url, (size, metadata))
    return remote, metadata


def _row_groups_for(metadata, tiles: list[str]) -> list[int]:
    """Row groups whose ``_tile`` range can hold one of the sorted ``tiles`` (all of them without stats)."""
    names = [metadata.row_group(0).column(j).path_in_schema for j in range(metadata.num_columns)] if metadata.num_row_groups else []
    if "_tile" not in names:
        return list(range(metadata.num_row_groups))
    tile_column = names.index("_tile")
    selected = []
    for i in range(metadata.num_row_groups):
        stats = metadata.row_group(i).column(tile_column).statistics
        if stats is None or not stats.has_min_max:
            selected.append(i)
            continue
        low, high = str(stats.min), str(stats.max)
        k = bisect.bisect_left(tiles, low)
        if k < len(tiles) and tiles[k] <= high:
            selected.append(i)
    return selected


def _chunk_ranges(metadata, row_groups: list[int]) -> list[tuple[int, int]]:
    """Byte ranges of the counted columns in the chosen row groups."""
    ranges = []
    for i in row_groups:
        row_group = metadata.row_group(i)
        for j in range(row_group.num_columns):
            chunk = row_group.column(j)
            if chunk.path_in_schema not in COLUMNS:
                continue
            start = chunk.data_page_offset
            if chunk.has_dictionary_page and chunk.dictionary_page_offset:
                start = min(start, chunk.dictionary_page_offset)
            ranges.append((start, start + chunk.total_compressed_size))
    return ranges


def _count_file(url: str, tiles: list[str], start: datetime, end: datetime) -> tuple[dict[str, int], dict[str, float]]:
    """Count one file's scenes for ``tiles`` between ``start`` and ``end`` (exclusive).

    A file that does not exist counts nothing.
    """
    import pyarrow.parquet as pq

    t0 = time.perf_counter()
    counts: dict[str, int] = {}
    min_cloud: dict[str, float] = {}
    opened = _open(url)
    if opened is None:
        return counts, min_cloud
    remote, metadata = opened
    selected = _row_groups_for(metadata, tiles)

    rows_by_group: dict[int, list] = {}
    missing = []
    for i in selected:
        known, rows = _row_groups.get((url, i))
        if known:
            rows_by_group[i] = rows
        else:
            missing.append(i)
    fetched_bytes = 0
    if missing:
        ranges = _chunk_ranges(metadata, missing)
        fetched_bytes = sum(end_ - start_ for start_, end_ in ranges)
        remote.prefetch(ranges)
        parquet = pq.ParquetFile(remote, metadata=metadata)
        for i in missing:
            table = parquet.read_row_group(i, columns=list(COLUMNS))
            rows = list(zip(*(table.column(name).to_pylist() for name in COLUMNS)))
            _row_groups.put((url, i), rows)
            rows_by_group[i] = rows

    wanted = set(tiles)
    for rows in rows_by_group.values():
        for tile, when, cloud in rows:
            if tile not in wanted or when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if not start <= when < end:
                continue
            counts[tile] = counts.get(tile, 0) + 1
            if cloud is not None and cloud < min_cloud.get(tile, float("inf")):
                min_cloud[tile] = float(cloud)
    logger.debug(f"[perf] scene index {url.rsplit('/', 2)[-2]}/{url.rsplit('/', 1)[-1]}: {len(selected)}/{metadata.num_row_groups} row groups, {len(missing)} downloaded ({fetched_bytes / 1e6:.1f} MB) in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return counts, min_cloud


def _files_for(first: date, last: date) -> list[str]:
    """The year files and monthly live tails that can hold scenes from ``first`` to ``last``."""
    urls = [INDEX_URL.format(year=year, name="items.parquet") for year in range(first.year, last.year + 1)]
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        urls.append(INDEX_URL.format(year=year, name=f"live-{month:02d}.parquet"))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return urls


def count_scenes(tile_codes: Sequence[str], start_date: str, end_date: str) -> Optional[tuple[dict[str, int], dict[str, float]]]:
    """Count the scenes of each tile from ``start_date`` to ``end_date`` (whole days, UTC).

    Returns ``(counts, min_cloud)`` keyed by tile code such as ``"31UFS"``, or None when the dates
    are unusable or span more than ``MAX_RANGE_DAYS``. Network failures raise, so the caller can
    fall back to another source.
    """
    try:
        first, last = date.fromisoformat(start_date[:10]), date.fromisoformat(end_date[:10])
    except ValueError:
        return None
    if last < first or (last - first).days > MAX_RANGE_DAYS:
        return None
    tiles = sorted({code.removeprefix("T") for code in tile_codes if code})
    if not tiles:
        return {}, {}
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
    end = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)

    t0 = time.perf_counter()
    counts: dict[str, int] = {}
    min_cloud: dict[str, float] = {}
    urls = _files_for(first, last)
    with ThreadPoolExecutor(max_workers=min(FILE_WORKERS, len(urls))) as pool:
        for file_counts, file_cloud in pool.map(lambda url: _count_file(url, tiles, start, end), urls):
            for tile, count in file_counts.items():
                counts[tile] = counts.get(tile, 0) + count
            for tile, cloud in file_cloud.items():
                if cloud < min_cloud.get(tile, float("inf")):
                    min_cloud[tile] = cloud
    logger.debug(f"[perf] scene index: {sum(counts.values())} scenes over {len(counts)} tiles in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return counts, min_cloud


def prewarm() -> None:
    """Read the footers of the current year and the latest live tails, so the first map view is fast.

    Safe to run in a background thread at startup; failures are only logged.
    """
    today = datetime.now(timezone.utc).date()
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=FILE_WORKERS) as pool:
            list(pool.map(_open, _files_for(first, today)))
        logger.info(f"[perf] scene index prewarm: footers read in {(time.perf_counter() - t0) * 1000:.0f} ms")
    except Exception as exc:
        logger.debug(f"scene index prewarm skipped ({exc})")
