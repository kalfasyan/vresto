"""What a shared link restores: the map view, dates, streamed tile and overlay, read from the URL.

The browser keeps these in the address bar as the user works (see ``static/url_state.js``), for
example ``/?ll=50.85,4.35&z=9&from=2025-06-01&to=2025-06-30&tile=31UES&date=20250612&overlay=ndvi``.
Each value is checked here, and one that does not parse is dropped rather than failing the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional

_TILE_RE = re.compile(r"^\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}$")
_SCENE_DATE_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class SharedView:
    """The parts of a view a link carries. Each is None when the link leaves it out."""

    center: Optional[tuple[float, float]] = None
    zoom: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    tile: Optional[str] = None
    scene_date: Optional[str] = None
    overlay: Optional[str] = None


def _iso_date(value: Optional[str]) -> Optional[str]:
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def parse_shared_view(params: Mapping[str, str], overlay_names: tuple[str, ...] | list[str] = ()) -> SharedView:
    """Read a :class:`SharedView` from URL query parameters, dropping values that do not parse."""
    center = None
    try:
        lat, lon = (float(v) for v in (params.get("ll") or "").split(","))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            center = (lat, lon)
    except ValueError:
        pass

    zoom = None
    try:
        z = int(params.get("z") or "")
        if 1 <= z <= 19:
            zoom = z
    except ValueError:
        pass

    date_from, date_to = _iso_date(params.get("from")), _iso_date(params.get("to"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    if bool(date_from) != bool(date_to):
        date_from = date_to = date_from or date_to  # one date is a one-day range

    tile = (params.get("tile") or "").upper().lstrip("T")
    tile = tile if _TILE_RE.match(tile) else None
    scene_date = params.get("date") or ""
    scene_date = scene_date if tile and _SCENE_DATE_RE.match(scene_date) else None
    overlay = params.get("overlay") or None
    if overlay not in overlay_names:
        overlay = None

    return SharedView(center=center, zoom=zoom, date_from=date_from, date_to=date_to, tile=tile, scene_date=scene_date, overlay=overlay)
