"""Product counts per MGRS tile, used to colour the map grid.

The counts come from the monthly MGRS tile statistics published by the
s2-stac-geoparquet mirror of Earth Search (Sentinel-2 L2A Collection 1) on
Source Cooperative: one small public Parquet file per month holds, for every
tile in the world, its scene count and lowest cloud cover. Colouring a view is
then one download per month in the date range, cached, and a lookup.

Those statistics cover whole calendar months. When they cannot be read, or when
a cloud-cover limit is asked for, the counts fall back to one anonymous search
of the CDSE STAC catalogue over the map view, grouped by tile.
"""

from __future__ import annotations

import io
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Optional, Sequence, Tuple

from loguru import logger

from vresto.api.config import CopernicusConfig

# Monthly per-tile statistics over Earth Search's Sentinel-2 L2A Collection 1 (s2-stac-geoparquet).
STATS_MONTH_URL = "https://data.source.coop/tge-labs/s2-stac-geoparquet/stats-c1/months/{month}.parquet"
STATS_SOURCE = "monthly-stats"
INDEX_SOURCE = "scene-index"
STAC_SOURCE = "cdse-stac"
# The statistics refresh daily, so a downloaded month is reused for this long.
STATS_TTL_S = 6 * 3600.0
STATS_MAX_MONTHS = 36
STATS_REQUEST_TIMEOUT_S = 20.0
# After the statistics cannot be read, the STAC search is used for this long before trying them again.
STATS_RETRY_AFTER_S = 300.0
_STATS_COLUMNS = ["mgrs_tile", "scene_count", "min_cloud_cover"]

# Sentinel-2 L2A collection on the Copernicus Data Space STAC API.
STAC_COLLECTION = "sentinel-2-l2a"
# Search cap used by the map. Reaching it marks the result as truncated.
DEFAULT_MAX_ITEMS = 2000
# Items requested per page. pystac-client follows the paging links itself.
PAGE_SIZE = 500
# Seconds before a silent server is given up on, so a stalled search cannot hold a worker thread forever.
REQUEST_TIMEOUT_S = 30.0
# Retries for throttled or briefly unavailable responses. CDSE answers bursts with 429 and a Retry-After header.
RETRY_TOTAL = 2
RETRY_BACKOFF_S = 2.0
RETRY_STATUSES = (429, 502, 503, 504)
# After the catalogue still refuses with 429, no search is sent for this many seconds.
RATE_LIMIT_COOLDOWN_S = 60.0
# Search areas are widened to this grid (degrees), so small pans reuse one cached search.
BBOX_SNAP_DEG = 0.25
# How long a result is reused, and how many distinct searches are kept.
CACHE_TTL_S = 600.0
CACHE_MAX_ENTRIES = 64
# The only item fields the search asks for: enough to count tiles and rank them by cloud cover.
_FIELDS = {
    "include": ["id", "properties.grid:code", "properties.eo:cloud_cover", "properties.datetime"],
    "exclude": [],
}
_GRID_CODE_PREFIX = "MGRS-"
_TILE_ID_PATTERN = re.compile(r"_T(\d{2}[A-Z]{3})_")


@dataclass
class TileDensity:
    """Products per MGRS tile found by one catalogue search.

    Attributes:
        counts: Product count per tile code, such as ``"31UFS"``. Tiles without products are absent.
        min_cloud: Lowest cloud cover (percent) among each tile's products. A tile is absent when
            none of its products report a cloud cover.
        truncated: True when the search stopped at its cap, so every count is a lower bound.
        source: ``"scene-index"`` (exact dates), ``"monthly-stats"`` (whole months) or ``"cdse-stac"``,
            where the counts came from.
        period: For monthly statistics, the months counted, such as ``"Sep-Oct 2026"``.
        months: Length of the counted period in months, to turn counts into products per month.
        tiles: For grid-zone counts, how many tiles with products each zone holds, to average per tile.
    """

    counts: dict[str, int]
    min_cloud: dict[str, float]
    truncated: bool
    source: str = STAC_SOURCE
    period: Optional[str] = None
    months: float = 1.0
    tiles: dict[str, int] = field(default_factory=dict)


class _TTLCache:
    """Thread-safe least-recently-used cache whose entries expire after ``ttl_s`` seconds."""

    def __init__(self, ttl_s: float, max_entries: int, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, Tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Any:
        """Return the live entry for ``key``, or ``None`` if it is missing or expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if self._clock() - stored_at > self._ttl_s:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: Hashable, value: Any) -> None:
        """Store ``value`` under ``key``, evicting the least recently used entries beyond the cap."""
        with self._lock:
            self._entries[key] = (self._clock(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget every entry."""
        with self._lock:
            self._entries.clear()


_cache = _TTLCache(CACHE_TTL_S, CACHE_MAX_ENTRIES)
# Month ("2026-09") -> {tile: (scene_count, min_cloud_cover or None)}.
_month_cache = _TTLCache(STATS_TTL_S, STATS_MAX_MONTHS)
_stats_down_until = 0.0
_index_down_until = 0.0
_client: Any = None
_client_lock = threading.Lock()
_cooldown_until = 0.0


def _get_client() -> Any:
    """Return the shared anonymous STAC client, opening it on first use.

    pystac-client reads the catalogue root when it is opened, so one client is kept for the process.
    """
    global _client
    with _client_lock:
        if _client is None:
            from pystac_client import Client
            from pystac_client.stac_api_io import StacApiIO
            from urllib3.util.retry import Retry

            retry = Retry(
                total=RETRY_TOTAL,
                status_forcelist=RETRY_STATUSES,
                backoff_factor=RETRY_BACKOFF_S,
                allowed_methods=None,  # searches are POSTs, which urllib3 does not retry by default
                respect_retry_after_header=True,
                raise_on_status=False,  # hand the last response to pystac-client, which raises APIError
            )
            stac_io = StacApiIO(timeout=REQUEST_TIMEOUT_S, max_retries=retry)
            _client = Client.open(CopernicusConfig.STAC_BASE_URL, stac_io=stac_io)
        return _client


def _status_code(exc: Exception) -> Optional[int]:
    """Return the HTTP status behind a failed search, if it can be told."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    match = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _is_rate_limited(exc: Exception) -> bool:
    """True when the catalogue refused the search for sending too many requests."""
    return _status_code(exc) == 429 or "rate limit" in str(exc).lower()


def rate_limited() -> bool:
    """True while searches are paused because the catalogue answered with 429."""
    return time.monotonic() < _cooldown_until


def _search_bbox(bbox: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    """Clamp a WGS84 bbox to valid coordinates and round it outwards to ``BBOX_SNAP_DEG``.

    Rounding outwards keeps every tile that touches the view inside the search, and nearby
    views share one cache entry. Returns ``None`` for a bbox that is not finite or is empty.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (min_lon, min_lat, max_lon, max_lat)):
        return None
    min_lon, max_lon = max(-180.0, min_lon), min(180.0, max_lon)
    min_lat, max_lat = max(-90.0, min_lat), min(90.0, max_lat)
    if min_lon >= max_lon or min_lat >= max_lat:
        return None
    steps = 1 / BBOX_SNAP_DEG
    # Round away float noise before floor/ceil: 4.4 * 100 is 440.00000000000006, which ceil would push to 441.
    min_lon, min_lat, max_lon, max_lat = (round(value * steps, 6) for value in (min_lon, min_lat, max_lon, max_lat))
    return (
        max(-180.0, math.floor(min_lon) / steps),
        max(-90.0, math.floor(min_lat) / steps),
        min(180.0, math.ceil(max_lon) / steps),
        min(90.0, math.ceil(max_lat) / steps),
    )


def _datetime_range(start_date: str, end_date: str) -> str:
    """Build the STAC ``datetime`` interval, widening plain ``YYYY-MM-DD`` dates to whole UTC days."""
    start = f"{start_date}T00:00:00Z" if len(start_date) == 10 else start_date
    end = f"{end_date}T23:59:59Z" if len(end_date) == 10 else end_date
    return f"{start}/{end}"


def _fetch_items(params: dict[str, Any]) -> list[dict]:
    """Run the search and return the matching items as plain dicts.

    The first attempt asks the server for the slim ``fields`` payload. If the server rejects that
    request (for instance because it does not support the Fields extension), it is retried once
    without ``fields``. Throttling and server errors are not retried here: repeating the search
    straight away would only add load.
    """
    client = _get_client()
    try:
        return list(client.search(fields=_FIELDS, **params).items_as_dicts())
    except Exception as exc:
        status = _status_code(exc)
        if _is_rate_limited(exc) or (status is not None and status >= 500):
            raise
        logger.debug(f"STAC search with reduced fields failed ({exc}); retrying without fields")
    return list(client.search(**params).items_as_dicts())


def _tile_code(item: dict) -> Optional[str]:
    """Return an item's MGRS tile: its ``grid:code`` (``MGRS-31UFS``) if present, else the code in its product id."""
    grid_code = (item.get("properties") or {}).get("grid:code")
    if isinstance(grid_code, str):
        code = grid_code.removeprefix(_GRID_CODE_PREFIX)
        if code:
            return code
    match = _TILE_ID_PATTERN.search(str(item.get("id") or ""))
    return match.group(1) if match else None


def _cloud_cover(item: dict) -> Optional[float]:
    """Return an item's cloud cover in percent, or ``None`` when the catalogue does not report it."""
    value = (item.get("properties") or {}).get("eo:cloud_cover")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _summarise(items: Sequence[dict], truncated: bool) -> TileDensity:
    """Group STAC item dicts by tile and record each tile's lowest cloud cover."""
    counts: dict[str, int] = {}
    min_cloud: dict[str, float] = {}
    seen_ids: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if item_id:
            if item_id in seen_ids:
                continue  # a page boundary can repeat an item
            seen_ids.add(item_id)
        code = _tile_code(item)
        if code is None:
            continue
        counts[code] = counts.get(code, 0) + 1
        cloud = _cloud_cover(item)
        if cloud is not None and cloud < min_cloud.get(code, math.inf):
            min_cloud[code] = cloud
    return TileDensity(counts=counts, min_cloud=min_cloud, truncated=truncated)


def _months_between(start_date: str, end_date: str) -> Optional[list[Tuple[int, int]]]:
    """Return the (year, month) pairs from ``start_date`` to ``end_date``, or None for unusable dates."""
    try:
        start_year, start_month = int(start_date[:4]), int(start_date[5:7])
        end_year, end_month = int(end_date[:4]), int(end_date[5:7])
    except (TypeError, ValueError):
        return None
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        return None
    months = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append((year, month))
        if len(months) > STATS_MAX_MONTHS:
            return None
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months or None


def _span_months(start_date: str, end_date: str) -> float:
    """Length of a date range in months (30.44 days each), at least one day."""
    from datetime import date

    try:
        days = (date.fromisoformat(end_date[:10]) - date.fromisoformat(start_date[:10])).days + 1
    except ValueError:
        return 1.0
    return max(days, 1) / 30.44


def _period_label(months: Sequence[Tuple[int, int]]) -> str:
    """Name a run of months briefly: ``"Sep 2026"``, ``"Sep-Oct 2026"`` or ``"Dec 2025-Jan 2026"``."""
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    (first_year, first_month), (last_year, last_month) = months[0], months[-1]
    if (first_year, first_month) == (last_year, last_month):
        return f"{names[first_month - 1]} {first_year}"
    if first_year == last_year:
        return f"{names[first_month - 1]}-{names[last_month - 1]} {first_year}"
    return f"{names[first_month - 1]} {first_year}-{names[last_month - 1]} {last_year}"


def _read_month(year: int, month: int) -> dict[str, Tuple[int, Optional[float]]]:
    """Download one month of tile statistics, keyed by tile. A month the mirror lacks reads as empty."""
    import pyarrow.parquet as pq
    import requests

    key = f"{year:04d}-{month:02d}"
    cached = _month_cache.get(key)
    if cached is not None:
        return cached
    response = requests.get(STATS_MONTH_URL.format(month=key), timeout=STATS_REQUEST_TIMEOUT_S)
    if response.status_code == 404:
        rows: dict[str, Tuple[int, Optional[float]]] = {}  # no scenes indexed for that month (yet)
    else:
        response.raise_for_status()
        table = pq.read_table(io.BytesIO(response.content), columns=_STATS_COLUMNS).to_pydict()
        rows = {tile: (int(count or 0), None if cloud is None else float(cloud)) for tile, count, cloud in zip(table["mgrs_tile"], table["scene_count"], table["min_cloud_cover"]) if tile}
    _month_cache.put(key, rows)
    return rows


def _count_from_scene_index(start_date: str, end_date: str, tile_codes: Sequence[str]) -> Optional[TileDensity]:
    """Count each tile's scenes over the exact dates from the item index, or None when it cannot be used."""
    global _index_down_until
    if time.monotonic() < _index_down_until:
        return None
    key = ("index", tuple(sorted(set(tile_codes))), start_date, end_date)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    from vresto.services import scene_index

    try:
        result = scene_index.count_scenes(tile_codes, start_date, end_date)
    except Exception as exc:
        _index_down_until = time.monotonic() + STATS_RETRY_AFTER_S
        logger.warning(f"Scene index unavailable ({exc}); counting whole months from the monthly statistics instead")
        return None
    if result is None:
        return None
    counts, min_cloud = result
    density = TileDensity(counts=counts, min_cloud=min_cloud, truncated=False, source=INDEX_SOURCE, months=_span_months(start_date, end_date))
    _cache.put(key, density)
    return density


def _load_months(start_date: str, end_date: str, fallback: str) -> Optional[Tuple[list, list]]:
    """Return the months of a date range and their tile statistics, or None when they cannot be read.

    A read failure pauses the statistics for ``STATS_RETRY_AFTER_S``; ``fallback`` says in the
    warning what happens instead.
    """
    global _stats_down_until
    if time.monotonic() < _stats_down_until:
        return None
    months = _months_between(start_date, end_date)
    if months is None:
        return None
    try:
        return months, [_read_month(year, month) for year, month in months]
    except Exception as exc:
        _stats_down_until = time.monotonic() + STATS_RETRY_AFTER_S
        logger.warning(f"Monthly tile statistics unavailable ({exc}); {fallback}")
        return None


def count_whole_months(start_date: str, end_date: str, tile_codes: Sequence[str]) -> Optional[TileDensity]:
    """Fast per-tile counts over the whole calendar months of the dates, from the monthly statistics.

    One small cached download per month, so it answers almost at once; the map shows these
    while the exact counts of :func:`count_products_per_tile` are on their way. None when the
    statistics cannot be read.
    """
    return _count_from_monthly_stats(start_date, end_date, tile_codes)


def _count_from_monthly_stats(start_date: str, end_date: str, tile_codes: Sequence[str]) -> Optional[TileDensity]:
    """Count products per tile from the monthly statistics, or None when they cannot be used."""
    loaded = _load_months(start_date, end_date, "counting from the CDSE catalogue instead")
    if loaded is None:
        return None
    months, monthly = loaded

    counts: dict[str, int] = {}
    min_cloud: dict[str, float] = {}
    for code in set(tile_codes):
        total = 0
        for rows in monthly:
            count, cloud = rows.get(code, (0, None))
            total += count
            if cloud is not None and cloud < min_cloud.get(code, math.inf):
                min_cloud[code] = cloud
        if total:
            counts[code] = total
    return TileDensity(counts=counts, min_cloud=min_cloud, truncated=False, source=STATS_SOURCE, period=_period_label(months), months=float(len(months)))


def count_products_per_zone(start_date: str, end_date: str, zone_codes: Sequence[str]) -> Optional[TileDensity]:
    """Sum the monthly tile statistics over MGRS grid zones (such as ``"31U"``), for the world view.

    Returns per-zone product counts, the lowest cloud cover in each zone, and in ``tiles`` how many
    of the zone's tiles have products, or None when the statistics cannot be read. There is no
    catalogue fallback: a search over a continent would be far too slow.
    """
    loaded = _load_months(start_date, end_date, "grid zones stay plain")
    if loaded is None:
        return None
    months, monthly = loaded

    wanted = set(zone_codes)
    counts: dict[str, int] = {}
    min_cloud: dict[str, float] = {}
    tiles_with_products: dict[str, set] = {}
    for rows in monthly:
        for tile, (count, cloud) in rows.items():
            zone = tile[:3]
            if zone not in wanted or not count:
                continue
            counts[zone] = counts.get(zone, 0) + count
            tiles_with_products.setdefault(zone, set()).add(tile)
            if cloud is not None and cloud < min_cloud.get(zone, math.inf):
                min_cloud[zone] = cloud
    return TileDensity(
        counts=counts,
        min_cloud=min_cloud,
        truncated=False,
        source=STATS_SOURCE,
        period=_period_label(months),
        months=float(len(months)),
        tiles={zone: len(codes) for zone, codes in tiles_with_products.items()},
    )


def count_products_per_tile(
    bbox: Sequence[float],
    start_date: str,
    end_date: str,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_cloud: Optional[float] = None,
    tile_codes: Optional[Sequence[str]] = None,
) -> Optional[TileDensity]:
    """Count the Sentinel-2 L2A products in each MGRS tile over a map view and date range.

    With ``tile_codes`` and no ``max_cloud``, the counts come from the s2-stac-geoparquet item
    index over the exact dates; if it cannot be read, from the monthly tile statistics, which
    cover whole calendar months. Otherwise, or when neither can be read, it makes one anonymous
    STAC search, or none when the same search is cached.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84.
        start_date: First sensing day, ``YYYY-MM-DD``.
        end_date: Last sensing day, ``YYYY-MM-DD``.
        max_items: Search cap. Reaching it sets ``truncated`` on the result.
        max_cloud: If set, count only products whose cloud cover is at or below this percentage.
        tile_codes: The tiles shown on the map, such as ``"31UFS"``; enables the monthly statistics.

    Returns:
        The per-tile counts, or ``None`` when the bbox is unusable or the catalogue cannot be
        reached. Failures are logged as warnings and not cached, so the next call tries again.
    """
    search_bbox = _search_bbox(bbox)
    if search_bbox is None:
        logger.debug(f"Skipping tile density for unusable bbox {bbox}")
        return None

    if tile_codes and max_cloud is None:
        density = _count_from_scene_index(start_date, end_date, tile_codes)
        if density is None:
            density = _count_from_monthly_stats(start_date, end_date, tile_codes)
        if density is not None:
            return density

    key = (search_bbox, start_date, end_date, max_items, max_cloud)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    if rate_limited():
        logger.debug("Skipping tile density: the catalogue is rate-limiting searches")
        return None

    params: dict[str, Any] = {
        "collections": [STAC_COLLECTION],
        "bbox": list(search_bbox),
        "datetime": _datetime_range(start_date, end_date),
        "max_items": max_items,
        "limit": min(PAGE_SIZE, max_items),
    }
    if max_cloud is not None:
        params["filter"] = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, max_cloud]}
        params["filter_lang"] = "cql2-json"

    global _cooldown_until
    try:
        items = _fetch_items(params)
    except Exception as exc:
        if _is_rate_limited(exc):
            _cooldown_until = time.monotonic() + RATE_LIMIT_COOLDOWN_S
            logger.warning(f"Catalogue is rate-limiting searches; pausing tile counts for {RATE_LIMIT_COOLDOWN_S:.0f} s")
        else:
            logger.warning(f"Could not count products per MGRS tile: {exc}")
        return None

    density = _summarise(items, truncated=len(items) >= max_items)
    density.months = _span_months(start_date, end_date)
    _cache.put(key, density)
    logger.debug(f"Counted {sum(density.counts.values())} products over {len(density.counts)} tiles (truncated={density.truncated})")
    return density
