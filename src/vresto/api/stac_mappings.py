"""Mappings between internal vresto collection/level names and CDSE STAC collection IDs."""

from typing import Optional

# Mapping from (vresto_collection, vresto_level) -> STAC collection ID
# If level is None or not found, it can fall back to a default or require level
COLLECTION_MAPPING = {
    ("SENTINEL-1", "GRD"): "sentinel-1-grd",
    ("SENTINEL-1", "SLC"): "sentinel-1-slc",
    ("SENTINEL-2", "L1C"): "sentinel-2-l1c",
    ("SENTINEL-2", "L2A"): "sentinel-2-l2a",
    ("SENTINEL-3", "OLCI"): "sentinel-3-olci",  # This may need more granularity based on STAC docs
    ("SENTINEL-3", "SLSTR"): "sentinel-3-slstr",
    ("SENTINEL-5P", "L2"): "sentinel-5p-l2",
    ("LANDSAT-8", "L1"): "landsat-8-l1",
    ("LANDSAT-8", "L2"): "landsat-8-l2",
}

# Reverse mapping for parsing STAC responses back to vresto types if needed
STAC_ID_TO_VRESTO = {v: k for k, v in COLLECTION_MAPPING.items()}


def get_stac_collection_id(collection: str, level: Optional[str] = None) -> Optional[str]:
    """Get the STAC collection ID for a given vresto collection and level.

    Args:
        collection: Internal vresto collection name (e.g., 'SENTINEL-2')
        level: Internal vresto processing level (e.g., 'L2A')

    Returns:
        The STAC collection ID string, or None if no mapping exists.
    """
    # Try exact match
    stac_id = COLLECTION_MAPPING.get((collection, level))
    if stac_id:
        return stac_id

    # Fallback: if level is not provided or not in mapping, try to find a sensible default
    # This is a bit risky, so we prefer explicit mappings
    if collection == "SENTINEL-2":
        return "sentinel-2-l2a" if level == "L2A" else "sentinel-2-l1c"
    if collection == "SENTINEL-1":
        return "sentinel-1-grd"

    return None
