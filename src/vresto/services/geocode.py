"""Find a place on the map by name, or by typing its coordinates.

Names are looked up with OpenStreetMap's Nominatim, which is free and needs no key.
Its usage policy asks for an identifying User-Agent and at most one request per second,
which a person typing into a search box stays well under. Answers are cached for the session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import requests
from loguru import logger

from vresto._version import __version__

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"vresto/{__version__} (+https://github.com/kalfasyan/vresto)"
TIMEOUT_S = 10

# Half-size, in degrees, of the box shown around a typed coordinate (about 11 km).
_POINT_HALF_SPAN = 0.1

_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Place:
    """A place to show on the map."""

    name: str
    lat: float
    lon: float
    # (min_lat, min_lon, max_lat, max_lon), ready for ``MapWidget.fit_bounds``.
    bounds: tuple[float, float, float, float]


def parse_latlon(text: str) -> Optional[Place]:
    """Return a Place for text such as ``"50.85, 4.35"``, or None when it is not a coordinate pair."""
    match = _LATLON_RE.match(text or "")
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    d = _POINT_HALF_SPAN
    bounds = (max(lat - d, -90.0), max(lon - d, -180.0), min(lat + d, 90.0), min(lon + d, 180.0))
    return Place(name=f"{lat:.5f}, {lon:.5f}", lat=lat, lon=lon, bounds=bounds)


def find_place(query: str) -> Optional[Place]:
    """Return the best match for ``query`` (a place name or ``"lat, lon"``), or None when nothing matches.

    Raises ``requests.RequestException`` when Nominatim cannot be reached.
    """
    query = (query or "").strip()
    if not query:
        return None
    point = parse_latlon(query)
    if point is not None:
        return point
    return _lookup(query.lower())


@lru_cache(maxsize=256)
def _lookup(query: str) -> Optional[Place]:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    hit = results[0]
    try:
        south, north, west, east = (float(v) for v in hit["boundingbox"])
        place = Place(
            name=hit.get("display_name") or query,
            lat=float(hit["lat"]),
            lon=float(hit["lon"]),
            bounds=(south, west, north, east),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning(f"Unexpected Nominatim answer for {query!r}: {hit!r}")
        return None
    return place
