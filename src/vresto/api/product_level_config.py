"""Configuration for product level support across different collections.

This module defines which processing levels are available for each satellite collection,
and which UI features (quicklook, metadata, visualization) are available per collection.
"""

from dataclasses import dataclass
from typing import Dict, List

# Mapping of collection names to their supported product levels
# Note: Sentinel-3 product types (OLCI, SLSTR, SY) are attributes in the API, not collection names
COLLECTION_PRODUCT_LEVELS: Dict[str, List[str]] = {
    "SENTINEL-1": ["GRD", "SLC", "RAW", "OCN"],  # SAR products at various processing stages
    "SENTINEL-2": ["L1C", "L2A"],  # Raw and atmospherically corrected data
    "SENTINEL-3": ["L0", "L1", "L2"],  # Raw, basic, and higher-level processing (includes OLCI, SLSTR, SY)
    "SENTINEL-5P": ["L1B", "L2"],  # Radiance and geophysical data
    "LANDSAT-8": ["L0", "L1GT", "L1GS", "L1TP", "L2SP"],  # Various processing levels
}

# Mapping of UI-friendly names to actual product level codes
UI_LEVEL_MAPPING = {
    "SENTINEL-1": {
        "GRD": "Ground Range Detected",
        "SLC": "Single Look Complex",
        "RAW": "Raw data",
        "OCN": "Ocean products",
    },
    "SENTINEL-2": {
        "L1C": "Raw data",
        "L2A": "Atmospherically corrected",
    },
    "SENTINEL-3": {
        "L0": "Raw data",
        "L1": "Basic processing",
        "L2": "Higher-level processing",
    },
    "SENTINEL-5P": {
        "L1B": "Radiance data",
        "L2": "Geophysical data",
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
BETA_SUPPORT_COLLECTIONS = ["SENTINEL-1", "SENTINEL-3", "SENTINEL-5P", "LANDSAT-8"]

# Collections that may not be available through Copernicus Data Space API yet
# Note: Even though these are listed, they may not return results depending on API availability
POTENTIALLY_UNAVAILABLE_COLLECTIONS = ["SENTINEL-3", "LANDSAT-8"]


@dataclass
class ProductCapabilities:
    """Describes which UI features are available for a given product collection.

    Attributes:
        quicklook_available: Whether quicklook images can be downloaded.
            ``True`` means fully supported (S3 path known).
            ``None`` means *maybe* – STAC may return a thumbnail but it is not guaranteed.
            ``False`` means not supported.
        metadata_available: Whether structured metadata XML can be downloaded.
        visualization_available: Whether Hi-Res Tiler / band analysis works.
        quicklook_note: Optional human-readable explanation shown as a tooltip.
        metadata_note: Optional human-readable explanation shown as a tooltip.
    """

    quicklook_available: bool | None  # True=yes, None=maybe/STAC-only, False=no
    metadata_available: bool
    visualization_available: bool
    quicklook_note: str = ""
    metadata_note: str = ""


# Per-collection capability definitions
COLLECTION_CAPABILITIES: Dict[str, ProductCapabilities] = {
    "SENTINEL-2": ProductCapabilities(
        quicklook_available=True,
        metadata_available=True,
        visualization_available=True,
    ),
    "SENTINEL-1": ProductCapabilities(
        quicklook_available=None,
        metadata_available=False,
        visualization_available=False,
        quicklook_note="Quicklook may be available via STAC thumbnail (not guaranteed for all products)",
        metadata_note="Metadata download is not supported for Sentinel-1 products",
    ),
    "SENTINEL-3": ProductCapabilities(
        quicklook_available=None,
        metadata_available=False,
        visualization_available=False,
        quicklook_note="Quicklook may be available via STAC thumbnail (not guaranteed for all products)",
        metadata_note="Metadata download is not supported for Sentinel-3 products",
    ),
    "SENTINEL-5P": ProductCapabilities(
        quicklook_available=None,
        metadata_available=False,
        visualization_available=False,
        quicklook_note="Quicklook may be available via STAC thumbnail (not guaranteed for all products)",
        metadata_note="Metadata download is not supported for Sentinel-5P products",
    ),
    "LANDSAT-8": ProductCapabilities(
        quicklook_available=None,
        metadata_available=False,
        visualization_available=False,
        quicklook_note="Quicklook may be available via STAC thumbnail (not guaranteed for all products)",
        metadata_note="Metadata download is not supported for Landsat-8 products",
    ),
}


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


def get_product_capabilities(collection: str) -> ProductCapabilities:
    """Return the capability flags for the given collection.

    Args:
        collection: Collection name (e.g. 'SENTINEL-2', 'SENTINEL-1')

    Returns:
        ProductCapabilities for the collection, or a safe default with all
        features disabled when the collection is unknown.
    """
    return COLLECTION_CAPABILITIES.get(
        collection,
        ProductCapabilities(
            quicklook_available=None,
            metadata_available=False,
            visualization_available=False,
            quicklook_note="Quicklook support is unknown for this collection",
            metadata_note="Metadata download is not supported for this collection",
        ),
    )
