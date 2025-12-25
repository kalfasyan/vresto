# Programmatic API Reference

Use vresto's Python API to automate satellite data searches and downloads in your applications.

## Installation

```bash
pip install vresto
# or with uv
uv pip install vresto
```

## Configuration

Set credentials via environment variables:

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
export COPERNICUS_S3_ACCESS_KEY="your_s3_access_key"      # Optional for downloads
export COPERNICUS_S3_SECRET_KEY="your_s3_secret_key"      # Optional for downloads
```

Or create `.env` file in your project:

```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
COPERNICUS_S3_ACCESS_KEY=your_s3_access_key
COPERNICUS_S3_SECRET_KEY=your_s3_secret_key
```

### ⚠️ Important: S3 Credentials

**Without static S3 credentials, vresto will auto-generate temporary credentials with strict usage limits.** These temporary credentials have limited quotas and will be exhausted quickly with large downloads.

To avoid hitting quota restrictions, it's highly recommended to request your own permanent S3 credentials from [Copernicus Dataspace](https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration) and add them to your environment.

## Core Classes

### CatalogSearch

Search for Sentinel-2 products in a bounding box.

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig

config = CopernicusConfig()
catalog = CatalogSearch(config=config)

bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    end_date="2024-01-31",
    max_cloud_cover=20,
)

for product in products:
    print(f"{product.name} - Cloud: {product.cloud_cover}%")
```

**Search Parameters:**
- `bbox`: BoundingBox (geographic area)
- `start_date`: Start date (YYYY-MM-DD)
- `end_date`: End date (YYYY-MM-DD, optional)
- `max_cloud_cover`: Max cloud coverage (0-100%, optional)

### ProductsManager

Single manager for all product downloads: quicklooks, metadata, and spectral bands.

```python
from vresto.products import ProductsManager

manager = ProductsManager(config=config)

# Download preview image
quicklook = manager.get_quicklook(product)
if quicklook:
    quicklook.save_to_file("preview.jpg")

# Download metadata XML file
metadata = manager.get_metadata(product)
if metadata:
    metadata.save_to_file("metadata.xml")

# Download spectral bands (B02, B03, B04, etc.)
files = manager.download_product_bands(
    product, 
    bands=['B02', 'B03', 'B04'],  # Red, Green, Blue
    resolution=10,                # meters
    dest_dir='./data'
)
```

**Methods:**
- `get_quicklook(product)` → JPEG preview
- `get_metadata(product)` → XML metadata file
- `download_product_bands(product, bands, resolution, dest_dir)` → GeoTIFF band files
- `batch_get_quicklooks(products)` → Multiple previews
- `batch_get_metadata(products)` → Multiple metadata files

## Complete Examples

### Example 1: Search and Download Quicklooks

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Setup
config = CopernicusConfig()
catalog = CatalogSearch(config=config)
manager = ProductsManager(config=config)

# Search
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    end_date="2024-01-07",
    max_cloud_cover=20,
)

print(f"Found {len(products)} products")

# Download quicklooks for first 5
for product in products[:5]:
    quicklook = manager.get_quicklook(product)
    if quicklook:
        quicklook.save_to_file(f"quicklooks/{product.name}.jpg")
        print(f"✅ {product.name}")
```

### Example 2: Download Spectral Bands

Download individual spectral bands (B02=Blue, B03=Green, B04=Red) as GeoTIFF files.

The `download_product_bands()` method accepts three input formats:

```python
# Option 1: From catalog search results (ProductInfo object)
# Contains metadata: name, s3_path, cloud_cover, sensing_date, etc.
product = products[0]  # ProductInfo object from search
files = manager.download_product_bands(
    product,
    bands=['B02', 'B03', 'B04'],  # Blue, Green, Red (RGB)
    resolution=10,                # 10-meter resolution
    dest_dir='./data'
)

# Option 2: Using short product name string (most user-friendly)
# Just the product name, no need for catalog search
files = manager.download_product_bands(
    "S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207",
    bands=['B02', 'B03', 'B04'],
    resolution=10,
    dest_dir='./data'
)

# Option 3: Using full S3 path string
files = manager.download_product_bands(
    "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/",
    bands=['B02', 'B03', 'B04'],
    resolution=10,
    dest_dir='./data'
)

for filepath in files:
    print(f"Downloaded: {filepath}")
```

**Available Bands** (Sentinel-2):
- 10m: B02 (Blue), B03 (Green), B04 (Red), B08 (NIR)
- 20m: B05, B06, B07, B11 (SWIR), B12 (SWIR), B8A (Red Edge), SCL (Scene Classification)
- 60m: B01 (Aerosol), B09 (Water Vapor), B10 (Cirrus)

**Path Construction** (auto-constructs from short name):
```
Input:  S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207
→       s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/
```

**Download Options:**
- `resolution`: 10, 20, 60 (meters) or 'native'
- `resample`: Auto-resample to target resolution (requires rasterio)
- `overwrite`: Overwrite existing files
- `preserve_s3_structure`: Mirror S3 structure locally (default: True)
  - With `True`: `./data/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_.../IMG_DATA/T59UNV_20201212T235129_B02_10m.jp2`
  - With `False`: `./data/T59UNV_20201212T235129_B02_10m.jp2`

## Next Steps

- [Quick Start](../getting-started/quickstart.md) - Get started quickly
- [Web Interface Guide](web-interface.md) - Visual search interface
- [AWS CLI Guide](../advanced/aws-cli.md) - Direct S3 access
