# Product Level Support

> ⚠️ **Important Disclaimer**
>
> **Full Support**: Only **SENTINEL-2** is fully supported with all features including metadata extraction and band downloads.
>
> **Under Development**: Support for **SENTINEL-3** and **LANDSAT-8** is currently under active development. These collections can be searched and products can be found, but metadata/quicklook downloads and other advanced features may not work correctly. **Use with caution and expect limitations**.
>
> For production workflows, we strongly recommend using **SENTINEL-2** exclusively until Sentinel-3 and Landsat-8 support is fully implemented.

## Overview

This document describes the product level support across different satellite collections in the Vresto application.

## Supported Product Levels by Collection

### SENTINEL-2 ✅ (Fully Supported)
- **L1C**: Raw radiometrically corrected data
- **L2A**: Atmospherically corrected data (recommended for most use cases)

**Status**: Full support with metadata and bands download capabilities.

### SENTINEL-3 ⚠️ (Under Development)
- **L0**: Raw instrument data
- **L1**: Basic processing (radiometry)
- **L2**: Higher-level processing (geophysical products)

**Status**: Limited/beta support. Product search and filtering are available, but metadata and bands download may have limitations.

**Data Availability Note**: Sentinel-3 has limited global coverage with focus on:
- Coastal zones
- Ocean monitoring
- Not available for all land areas (e.g., inland Britain may have limited/no coverage)
- L2 products are most commonly available
- L0 is rarely available in public archives

### LANDSAT-8 ⚠️ (Under Development)
- **L0**: Raw instrument data
- **L1GT**: Ground Truth Corrected
- **L1GS**: Ground Truth Shifted
- **L1TP**: Terrain Corrected (Level-1 Precision and Terrain Corrected)
- **L2SP**: Surface Reflectance (Level-2 Science Product)

**Status**: Limited/beta support. Product search and filtering are available, but metadata and bands download may have limitations.

## Implementation Details

### Configuration Module: `product_level_config.py`

The new `vresto.api.product_level_config` module provides:

- **`COLLECTION_PRODUCT_LEVELS`**: Dictionary mapping collections to supported levels
- **`UI_LEVEL_MAPPING`**: Human-readable descriptions for each level
- **`FULLY_SUPPORTED_COLLECTIONS`**: List of fully supported collections (currently only SENTINEL-2)
- **`BETA_SUPPORT_COLLECTIONS`**: List of beta/limited support collections
- **Helper functions**:
  - `get_supported_levels(collection)`: Get supported levels for a collection
  - `is_level_supported(collection, level)`: Check if a level is supported
  - `get_unsupported_levels(collection, selected_levels)`: Get list of unsupported levels
  - `is_collection_fully_supported(collection)`: Check if collection has full support

### UI Enhancements: `search_results_panel.py`

The search panel now provides dynamic product level selection:

1. **Dynamic Product Level Options**: When you select a collection, the product level dropdown automatically updates to show only supported levels.

2. **Visual Status Indicators**:
   - ✅ Green indicator for SENTINEL-2: "Full support for L1C & L2A"
   - ⚠️ Orange indicator for SENTINEL-3: "Sentinel-3 (beta) • Includes OLCI, SLSTR, SY products"
   - ⚠️ Orange indicator for LANDSAT-8: "Limited support: L0, L1GT, L1GS, L1TP, L2SP (beta)"

### Search Validation: `map_search_tab.py`

When performing a search:

1. **Product Level Validation**: The search handler validates that selected product levels are supported for the chosen collection.

2. **Warning Messages**: If unsupported levels are selected, users receive:
   - A warning notification at the top of the screen
   - A message in the activity log explaining which levels are unsupported and which are available

Example warning message:
```
⚠️ LANDSAT-8 does not support product level(s): L1C, L2A. 
Supported levels: L0, L1GT, L1GS, L1TP, L2SP
```

### Catalog Filtering: `catalog.py`

The catalog search API now properly handles product level filtering for all collections:

- **SENTINEL-2**: Filters using `MSIL1C` or `MSIL2A` in product names
- **SENTINEL-3**: Returns all products (product type filtering available in future versions)
- **LANDSAT-8**: Filters using level codes like `L1GT`, `L1GS`, `L1TP`, `L2SP` in product names

## Usage

### For Users

1. Open the Sentinel Browser web interface
2. Select a collection (SENTINEL-2 recommended, SENTINEL-3/LANDSAT-8 for testing only)
3. The product level dropdown automatically shows available levels for that collection
4. Check the status indicator below the dropdown to see support level
5. Select your desired product level and proceed with the search
6. For SENTINEL-3/LANDSAT-8: Product search works, but download features may fail

### For Developers

To check product level support in code:

```python
from vresto.api.product_level_config import (
    is_level_supported,
    get_supported_levels,
    is_collection_fully_supported
)

# Check if a level is supported
if is_level_supported("LANDSAT-8", "L1C"):
    print("Supported!")
else:
    print("Not supported")

# Get all supported levels for a collection
levels = get_supported_levels("SENTINEL-3")  # Returns ["L0", "L1", "L2"]

# Check if a collection has full support
if is_collection_fully_supported("SENTINEL-2"):
    print("Fully supported with all features")
```

## Limitations

### Current Limitations
- **Sentinel-3 Product Search**: ✅ Works! Products can be found
- **Sentinel-3 Metadata/Quicklook**: ❌ Not yet supported - requires different file path handling than Sentinel-2
  - Sentinel-3 files don't use `MTD_MSIL2A.xml` naming convention
  - Quicklook file formats differ between instruments
  - S3 path structure is different (e.g., `/Sentinel-3/SYNERGY/SY_2_VG1___/` vs `/Sentinel-2/MSI/L2A/`)
- LANDSAT-8: Limited availability in Copernicus Data Space API
- Some collections may not have all requested product levels available in the catalog

### Verified Working
- ✅ Finding Sentinel-3 products via map search
- ✅ Displaying Sentinel-3 product metadata (name, date, size, cloud cover)
- ✅ Product level filtering and warnings

### Known Issues
- Metadata file detection for Sentinel-3 fails (looks for Sentinel-2 MTD files)
- Quicklook download attempts use wrong file paths for Sentinel-3
- botocore Timeout exception handling needs update

### Future Enhancements
- Full support for SENTINEL-3 with proper metadata extraction
- Instrument-specific quicklook handling (OLCI, SLSTR, SY)
- Support for additional satellite collections
- Custom handling for collection-specific product naming conventions
- LANDSAT-8 metadata extraction and downloads

## Testing

The following test scenarios can be performed:

1. **SENTINEL-2**: Select L1C, L2A, and L1C+L2A - should work without warnings (recommended)
2. **SENTINEL-3**: Select L0, L1, L2 - should show beta warning but allow search (beta testing)
3. **LANDSAT-8**: Select L1GT, L1GS, L1TP, L2SP - should show beta warning but allow search (beta testing)
4. **Unsupported Combinations**: Try selecting L1C/L2A for SENTINEL-3 or LANDSAT-8 - should show clear warning messages

## Support Status Summary

| Collection | Product Search | Metadata Download | Quicklook | Bands Download | Status |
|------------|-----------------|-------------------|-----------|---|---------|
| SENTINEL-2 | ✅ | ✅ | ✅ | ✅ | **Production Ready** |
| SENTINEL-3 | ✅ | ❌ | ❌ | ❌ | **Beta - Use with Caution** |
| LANDSAT-8 | ✅ | ❌ | ❌ | ❌ | **Beta - Use with Caution** |

## References

- [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/)
- [Sentinel-2 Products Specification](https://sentinels.copernicus.eu/documents/247904/685211/Sentinel-2_L2A_SCP_PDGS.pdf)
- [Sentinel-3 Product Types](https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-3-olci)
- [LANDSAT Product Documentation](https://www.usgs.gov/faqs/what-are-band-designations-landsat-satellites)
