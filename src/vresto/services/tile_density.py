"""Product counts per MGRS tile, used to colour the map grid.

One anonymous STAC search over the map view and date range returns the
Sentinel-2 L2A products inside it. Grouping them by MGRS tile gives a product
count for each tile and the lowest cloud cover seen on it. Results are kept in
a small TTL cache, so panning back and forth or toggling the grid does not
query the catalogue again.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Optional, Sequence, Tuple

from loguru import logger

from vresto.api.config import CopernicusConfig

# Sentinel-2 L2A collection on the Copernicus Data Space STAC API.
STAC_COLLECTION = "sentinel-2-l2a"
# Search cap used by the map. Reaching it marks the result as truncated.
DEFAULT_MAX_ITEMS = 2000
# Items requested per page. pystac-client follows the paging links itself.
PAGE_SIZE = 500
# Seconds before a silent server is given up on, so a stalled search cannot hold a worker thread forever.
REQUEST_TIMEOUT_S = 30.0
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
    """

    counts: dict[str, int]
    min_cloud: dict[str, float]
    truncated: bool


class _TTLCache:
    """Thread-safe least-recently-used cache whose entries expire after ``ttl_s`` seconds."""

    def __init__(self, ttl_s: float, max_entries: int, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, Tuple[float, TileDensity]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Optional[TileDensity]:
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

    def put(self, key: Hashable, value: TileDensity) -> None:
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
_client: Any = None
_client_lock = threading.Lock()


def _get_client() -> Any:
    """Return the shared anonymous STAC client, opening it on first use.

    pystac-client reads the catalogue root when it is opened, so one client is kept for the process.
    """
    global _client
    with _client_lock:
        if _client is None:
            from pystac_client import Client

            _client = Client.open(CopernicusConfig.STAC_BASE_URL, timeout=REQUEST_TIMEOUT_S)
        return _client


def _search_bbox(bbox: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    """Clamp a WGS84 bbox to valid coordinates and round it outwards to 0.01 degrees.

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
    # Round away float noise before floor/ceil: 4.4 * 100 is 440.00000000000006, which ceil would push to 441.
    min_lon, min_lat, max_lon, max_lat = (round(value * 100, 6) for value in (min_lon, min_lat, max_lon, max_lat))
    return (
        math.floor(min_lon) / 100,
        math.floor(min_lat) / 100,
        math.ceil(max_lon) / 100,
        math.ceil(max_lat) / 100,
    )


def _datetime_range(start_date: str, end_date: str) -> str:
    """Build the STAC ``datetime`` interval, widening plain ``YYYY-MM-DD`` dates to whole UTC days."""
    start = f"{start_date}T00:00:00Z" if len(start_date) == 10 else start_date
    end = f"{end_date}T23:59:59Z" if len(end_date) == 10 else end_date
    return f"{start}/{end}"


def _fetch_items(params: dict[str, Any]) -> list[dict]:
    """Run the search and return the matching items as plain dicts.

    The first attempt asks the server for the slim ``fields`` payload. If that request fails, it is
    retried once without ``fields``, in case the server does not support the Fields extension.
    """
    client = _get_client()
    try:
        return list(client.search(fields=_FIELDS, **params).items_as_dicts())
    except Exception as exc:
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


def count_products_per_tile(
    bbox: Sequence[float],
    start_date: str,
    end_date: str,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_cloud: Optional[float] = None,
) -> Optional[TileDensity]:
    """Count the Sentinel-2 L2A products in each MGRS tile over a map view and date range.

    Makes one anonymous STAC search, or none when the same search is cached.

    Args:
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84.
        start_date: First sensing day, ``YYYY-MM-DD``.
        end_date: Last sensing day, ``YYYY-MM-DD``.
        max_items: Search cap. Reaching it sets ``truncated`` on the result.
        max_cloud: If set, count only products whose cloud cover is at or below this percentage.

    Returns:
        The per-tile counts, or ``None`` when the bbox is unusable or the catalogue cannot be
        reached. Failures are logged as warnings and not cached, so the next call tries again.
    """
    search_bbox = _search_bbox(bbox)
    if search_bbox is None:
        logger.debug(f"Skipping tile density for unusable bbox {bbox}")
        return None

    key = (search_bbox, start_date, end_date, max_items, max_cloud)
    cached = _cache.get(key)
    if cached is not None:
        return cached

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

    try:
        items = _fetch_items(params)
    except Exception as exc:
        logger.warning(f"Could not count products per MGRS tile: {exc}")
        return None

    density = _summarise(items, truncated=len(items) >= max_items)
    _cache.put(key, density)
    logger.debug(f"Counted {sum(density.counts.values())} products over {len(density.counts)} tiles (truncated={density.truncated})")
    return density
