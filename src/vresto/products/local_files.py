"""Locate band files inside locally downloaded Sentinel-2 products."""

import os
from typing import Optional, Tuple

from vresto.products.downloader import _BAND_RE


def find_img_data(path: str) -> Optional[str]:
    """Find IMG_DATA directory in product."""
    if path.endswith(".SAFE"):
        granule = os.path.join(path, "GRANULE")
        if os.path.isdir(granule):
            for g in os.scandir(granule):
                img = os.path.join(g.path, "IMG_DATA")
                if os.path.isdir(img):
                    return img
    else:
        granule = os.path.join(path, "GRANULE")
        if os.path.isdir(granule):
            for g in os.scandir(granule):
                img = os.path.join(g.path, "IMG_DATA")
                if os.path.isdir(img):
                    return img

    # Fallback: search recursively for jp2 files
    for root, dirs, files in os.walk(path):
        for f in files:
            if f.lower().endswith(".jp2"):
                return os.path.dirname(os.path.join(root, f))

    return None


def list_available_bands(img_root: str) -> dict:
    """List available bands in IMG_DATA directory."""
    bands_map = {}
    # Support recursive search in case of nested resolution folders or different structures
    for root, dirs, files in os.walk(img_root):
        for f in files:
            m = _BAND_RE.search(f)
            if not m:
                # Special case for TCI in some versions where regex might not perfectly match
                if "TCI" in f.upper() and f.lower().endswith((".jp2", ".tif")):
                    res = None
                    if "10m" in root:
                        res = 10
                    elif "20m" in root:
                        res = 20
                    elif "60m" in root:
                        res = 60
                    # Try to find a default resolution if none found in path
                    if res is None:
                        res = 10
                    bands_map.setdefault("TCI", set()).add(res)
                continue
            band = m.group("band").upper()
            # Handle both L2A (with resolution) and L1C (without resolution) formats
            res_str = m.group("res")
            if res_str:
                # L2A format: resolution is in the filename
                res = int(res_str)
            else:
                # L1C format: use native resolution lookup
                # Import the L1C band resolutions mapping
                from vresto.products.downloader import _L1C_BAND_RESOLUTIONS

                res = _L1C_BAND_RESOLUTIONS.get(band, 10)  # default to 10m if unknown
            bands_map.setdefault(band, set()).add(res)
    return bands_map


def default_rgb(bands_map: dict) -> Tuple[str, str, str]:
    """Choose default RGB bands."""
    for combo in [("B04", "B03", "B02")]:
        if all(b in bands_map for b in combo):
            return combo
    band_names = sorted(bands_map.keys())
    return tuple(band_names[:3]) if len(band_names) >= 3 else tuple(band_names)


def find_band_file(band_name: str, img_root: str, preferred_resolution: str = "native") -> Optional[str]:
    """Find band file with preferred resolution."""
    matches = []
    # Support recursive search in case of nested resolution folders or different structures
    # Use a case-insensitive match for the band name in the filename
    for rootp, dirs, files in os.walk(img_root):
        for f in files:
            m = _BAND_RE.search(f)
            if not m:
                # Special case for TCI in some versions where regex might not perfectly match
                # but file contains TCI
                if band_name.upper() == "TCI" and "TCI" in f.upper() and f.lower().endswith((".jp2", ".tif")):
                    # try to extract resolution from folder name if not in filename
                    res = None
                    if "10m" in rootp:
                        res = 10
                    elif "20m" in rootp:
                        res = 20
                    elif "60m" in rootp:
                        res = 60
                    matches.append((res, os.path.join(rootp, f)))
                continue

            b = m.group("band").upper()
            if b != band_name.upper():
                continue
            try:
                r = int(m.group("res"))
            except Exception:
                # Try to extract from folder name
                if "10m" in rootp:
                    r = 10
                elif "20m" in rootp:
                    r = 20
                elif "60m" in rootp:
                    r = 60
                else:
                    r = None
            matches.append((r, os.path.join(rootp, f)))

    if not matches:
        return None

    if preferred_resolution != "native":
        try:
            pref = int(preferred_resolution)
            # First try exact match
            for r, p in matches:
                if r == pref:
                    return p
            # Then try any match if exact preferred not found
        except Exception:
            pass

    # Return best available (lowest resolution/native)
    valid = [m for m in matches if m[0] is not None]
    if valid:
        return min(valid, key=lambda x: x[0])[1]
    return matches[0][1] if matches else None
