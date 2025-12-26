"""Configuration for product level support across different collections.

This module defines which processing levels are available for each satellite collection.
"""

from typing import Dict, List

# Mapping of collection names to their supported product levels
# Note: Sentinel-3 product types (OLCI, SLSTR, SY) are attributes in the API, not collection names
COLLECTION_PRODUCT_LEVELS: Dict[str, List[str]] = {
    "SENTINEL-2": ["L1C", "L2A"],  # Raw and atmospherically corrected data
    "SENTINEL-3": ["L0", "L1", "L2"],  # Raw, basic, and higher-level processing (includes OLCI, SLSTR, SY)
    "LANDSAT-8": ["L0", "L1GT", "L1GS", "L1TP", "L2SP"],  # Various processing levels
}

# Mapping of UI-friendly names to actual product level codes
UI_LEVEL_MAPPING = {
    "SENTINEL-2": {
        "L1C": "Raw data",
        "L2A": "Atmospherically corrected",
    },
    "SENTINEL-3": {
        "L0": "Raw data",
        "L1": "Basic processing",
        "L2": "Higher-level processing",
    },
    "LANDSAT-8": {
        "L0": "Raw data",
        "L1GT": "Ground-truth corrected",
        "L1GS": "Ground-truth shifted",
        "L1TP": "Terrain corrected",
        "L2SP": "Surface reflectance",
    },
}

# Collections that are fully supported with metadata/bands download
FULLY_SUPPORTED_COLLECTIONS = ["SENTINEL-2"]

# Collections with limited/beta support
BETA_SUPPORT_COLLECTIONS = ["SENTINEL-3", "LANDSAT-8"]

# Collections that may not be available through Copernicus Data Space API yet
# Note: Even though these are listed, they may not return results depending on API availability
POTENTIALLY_UNAVAILABLE_COLLECTIONS = ["SENTINEL-3", "LANDSAT-8"]


def get_supported_levels(collection: str) -> List[str]:
    """Get the list of supported product levels for a collection.

    Args:
        collection: Collection name (e.g., 'SENTINEL-2')

    Returns:
        List of supported product levels
    """
    return COLLECTION_PRODUCT_LEVELS.get(collection, [])


def is_level_supported(collection: str, level: str) -> bool:
    """Check if a product level is supported for a given collection.

    Args:
        collection: Collection name
        level: Product level (e.g., 'L1C', 'L2A')

    Returns:
        True if the level is supported, False otherwise
    """
    supported = get_supported_levels(collection)
    return level in supported


def get_unsupported_levels(collection: str, selected_levels: List[str]) -> List[str]:
    """Get a list of unsupported levels from a list of selected levels.

    Args:
        collection: Collection name
        selected_levels: List of selected product levels

    Returns:
        List of unsupported levels
    """
    supported = get_supported_levels(collection)
    return [level for level in selected_levels if level not in supported]


def is_collection_fully_supported(collection: str) -> bool:
    """Check if a collection has full support (metadata/bands download).

    Args:
        collection: Collection name

    Returns:
        True if collection is fully supported
    """
    return collection in FULLY_SUPPORTED_COLLECTIONS


def get_level_description(collection: str, level: str) -> str:
    """Get a human-readable description of a product level.

    Args:
        collection: Collection name
        level: Product level

    Returns:
        Description string
    """
    descriptions = UI_LEVEL_MAPPING.get(collection, {})
    return descriptions.get(level, level)
