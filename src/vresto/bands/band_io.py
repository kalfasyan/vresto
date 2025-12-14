"""Band IO helpers: scan IMG_DATA for band files and pick best file for a requested band.

This module encapsulates file-system logic so UI code doesn't walk directories.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Set, Tuple

# Pattern used in the repo to identify band files (from downloader module)
# Example filename parts: B04_10m.jp2 or something matching existing _BAND_RE
_BAND_RE = re.compile(r"(?P<band>B\d{2}|SCL|AOT|TCI|WVP)_(?P<res>\d+)m?", re.IGNORECASE)


def scan_img_data(img_root: str) -> Dict[str, Set[int]]:
    """Scan `img_root` folder recursively and return mapping band -> set(resolutions).

    Args:
        img_root: path to IMG_DATA folder or a product folder that contains images.

    Returns:
        dict mapping uppercase band name to set of integer resolutions (when available).
    """
    bands: Dict[str, Set[int]] = {}
    for root, dirs, files in os.walk(img_root):
        for f in files:
            m = _BAND_RE.search(f)
            if not m:
                continue
            band = m.group("band").upper()
            try:
                res = int(m.group("res"))
            except Exception:
                res = None
            bands.setdefault(band, set())
            if res is not None:
                bands[band].add(res)
    return bands


def find_band_file(img_root: str, band_name: str, preferred_resolution: Optional[int | str] = "native") -> Optional[str]:
    """Find a file path for requested band in `img_root`.

    Args:
        img_root: folder to scan
        band_name: band code (e.g., 'B04' or 'SCL')
        preferred_resolution: 'native' or integer resolution to prefer

    Returns:
        absolute file path string if found, else None.
    """
    matches: List[Tuple[Optional[int], str]] = []
    for root, dirs, files in os.walk(img_root):
        for f in files:
            m = _BAND_RE.search(f)
            if not m:
                continue
            b = m.group("band").upper()
            if b != band_name.upper():
                continue
            try:
                r = int(m.group("res"))
            except Exception:
                r = None
            matches.append((r, os.path.join(root, f)))

    if not matches:
        return None

    # If a numeric preferred_resolution provided, prefer it
    if preferred_resolution != "native":
        try:
            pref = int(preferred_resolution)
            for r, p in matches:
                if r == pref:
                    return p
        except Exception:
            pass

    # Otherwise choose smallest non-None resolution (best/native), else first
    valid = [m for m in matches if m[0] is not None]
    if valid:
        best = min(valid, key=lambda x: x[0])[1]
        return best
    return matches[0][1]


__all__ = ["scan_img_data", "find_band_file"]
