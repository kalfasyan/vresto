# Programmatic API Guide

Vresto provides a clean, professional Python API for searching and accessing Copernicus Sentinel satellite data. Use it in your own scripts and applications without the web interface.

## Installation

```bash
pip install vresto
# or with uv
uv pip install vresto
```

## Quick Start

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Setup credentials
config = CopernicusConfig()  # Reads from env vars or .env file

# Search for products
catalog = CatalogSearch(config=config)
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    end_date="2024-01-07",
    max_cloud_cover=20,
)

# Download and view product data
products_manager = ProductsManager(config=config)
for product in products[:3]:
    quicklook = products_manager.get_quicklook(product)
    metadata = products_manager.get_metadata(product)
    
    if quicklook:
        quicklook.save_to_file(f"{product.name}_quicklook.jpg")
```

## Configuration

### Environment Variables

Set your credentials before using the API:

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
export COPERNICUS_S3_ACCESS_KEY="your_s3_access_key"      # Optional but recommended
export COPERNICUS_S3_SECRET_KEY="your_s3_secret_key"      # Optional but recommended
```

Or create a `.env` file:

```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
COPERNICUS_S3_ACCESS_KEY=your_s3_access_key
COPERNICUS_S3_SECRET_KEY=your_s3_secret_key
```

### Programmatic Configuration

Pass credentials directly when initializing:

```python
from vresto.api import CopernicusConfig, CatalogSearch

config = CopernicusConfig(
    username="your_email@example.com",
    password="your_password",
    s3_access_key="your_s3_access_key",
    s3_secret_key="your_s3_secret_key",
)

catalog = CatalogSearch(config=config)
```

## Core Classes

### CopernicusConfig

Manages all configuration and credentials.

```python
from vresto.api import CopernicusConfig

config = CopernicusConfig()

# Validate credentials
if config.validate():
    print("✅ Credentials configured")

# Check S3 credentials
if config.has_static_s3_credentials():
    print("✅ S3 credentials available")

# Get credentials
username, password = config.get_credentials()
```

### BoundingBox

Define geographic search areas using WGS84 coordinates.

```python
from vresto.api import BoundingBox

# Create a bounding box (west, south, east, north)
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

# Convert to different formats
wkt = bbox.to_wkt()  # "POLYGON((4.65 50.85, 4.75 50.85, 4.75 50.9, 4.65 50.9, 4.65 50.85))"
bbox_str = bbox.to_bbox_string()  # "4.65,50.85,4.75,50.9"
```

### CatalogSearch

Search the Copernicus catalog for products.

```python
from vresto.api import CatalogSearch, BoundingBox

catalog = CatalogSearch()
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

# Search for products
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",      # Required: YYYY-MM-DD format
    end_date="2024-01-31",         # Optional: defaults to start_date
    collection="SENTINEL-2",       # Optional: default is SENTINEL-2
    max_cloud_cover=20,            # Optional: 0-100, for optical sensors
    max_results=100,               # Optional: limit results
)

# Results are ProductInfo objects
for product in products:
    print(f"Name: {product.name}")
    print(f"Date: {product.sensing_date}")
    print(f"Size: {product.size_mb:.2f} MB")
    print(f"Cloud Cover: {product.cloud_cover}%")
    print(f"S3 Path: {product.s3_path}")
    print()

# Find a product by name
product = catalog.get_product_by_name("S2A_MSIL2A_20240101T...")
```

### ProductsManager

Download and manage product data (quicklooks, metadata, bands).

```python
from vresto.products import ProductsManager

products_manager = ProductsManager()

# Get quicklook for a single product
quicklook = products_manager.get_quicklook(product)
if quicklook:
    quicklook.save_to_file("quicklook.jpg")
    print(f"Quicklook size: {len(quicklook.data)} bytes")

# Get metadata (XML file)
metadata = products_manager.get_metadata(product)
if metadata:
    metadata.save_to_file("metadata.xml")
    print(metadata.content[:500])  # Print first 500 chars

# Batch download quicklooks
quicklooks = products_manager.batch_get_quicklooks(products)
for product_name, quicklook in quicklooks.items():
    if quicklook:
        quicklook.save_to_file(f"{product_name}_quicklook.jpg")

# Batch download metadata
metadata_dict = products_manager.batch_get_metadata(
    products,
    metadata_filename="MTD_MSIL2A.xml",
    skip_errors=True  # Continue if some products fail
)
```

### ProductInfo

Represents a single product with metadata.

```python
# ProductInfo attributes
product.name              # e.g., "S2A_MSIL2A_20240101T..."
product.collection        # e.g., "SENTINEL-2"
product.sensing_date      # datetime object
product.size_mb           # File size in megabytes
product.cloud_cover       # Cloud coverage percentage (0-100) or None
product.s3_path           # Path in S3: s3://eodata/Sentinel-2/...
product.processing_level  # e.g., "L2A" or "L1C"
product.footprint         # WKT polygon of coverage area
product.metadata          # Dict of additional attributes
```

### ProductQuicklook and ProductMetadata

Data containers for product files.

```python
# ProductQuicklook
quicklook.data          # Raw image bytes
quicklook.filename      # Original filename
quicklook.to_base64()   # Encode as base64
quicklook.save_to_file(filepath)  # Save to disk

# ProductMetadata
metadata.content        # Raw XML content as string
metadata.filename       # Original filename
metadata.save_to_file(filepath)  # Save to disk
```

## Common Workflows

### Search and Download Quicklooks

```python
from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig
from vresto.products import ProductsManager

# Initialize
config = CopernicusConfig()
catalog = CatalogSearch(config=config)
products_manager = ProductsManager(config=config)

# Search
bbox = BoundingBox(west=2.2, south=48.8, east=2.5, north=49.0)  # Paris area
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-11-01",
    end_date="2024-11-30",
    max_cloud_cover=15,
    max_results=10,
)

# Download and save
for product in products:
    print(f"Downloading quicklook for {product.name}...")
    quicklook = products_manager.get_quicklook(product)
    if quicklook:
        filepath = f"quicklooks/{product.name}_preview.jpg"
        quicklook.save_to_file(filepath)
        print(f"✓ Saved to {filepath}")
```

### Filter Products by Type (L1C vs L2A)

```python
# L1C = raw, uncorrected data
# L2A = atmospherically corrected data

l2a_products = [p for p in products if "L2A" in p.name]
l1c_products = [p for p in products if "L1C" in p.name]

print(f"Found {len(l2a_products)} L2A products")
print(f"Found {len(l1c_products)} L1C products")
```

### Access Product Metadata

```python
# Get detailed metadata
metadata = products_manager.get_metadata(product)

if metadata:
    # Save for manual inspection
    metadata.save_to_file(f"{product.name}_metadata.xml")
    
    # Or parse it in your application
    import xml.etree.ElementTree as ET
    root = ET.fromstring(metadata.content)
    # ... parse and extract what you need
```

### Batch Operations with Error Handling

```python
# Get data for multiple products, skipping failures
quicklooks = products_manager.batch_get_quicklooks(
    products[:10],
    skip_errors=True
)

successful = sum(1 for q in quicklooks.values() if q is not None)
print(f"Successfully retrieved {successful}/{len(products[:10])} quicklooks")

# Manual error handling
for product in products:
    try:
        quicklook = products_manager.get_quicklook(product)
        if quicklook:
            quicklook.save_to_file(f"{product.name}.jpg")
    except Exception as e:
        print(f"Failed to download {product.name}: {e}")
```

## Error Handling

```python
from vresto.api import AuthenticationError, CopernicusConfig

try:
    config = CopernicusConfig()
    username, password = config.get_credentials()
except ValueError as e:
    print(f"Configuration error: {e}")
    print("Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD")

try:
    catalog = CatalogSearch()
    products = catalog.search_products(...)
except AuthenticationError as e:
    print(f"Authentication failed: {e}")
    print("Check your credentials")
```

## Complete Example: Download Latest Products

```python
#!/usr/bin/env python3
"""Download the latest Sentinel-2 products for a location."""

from datetime import datetime, timedelta
from pathlib import Path

from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig
from vresto.products import ProductsManager


def download_latest_products():
    """Search and download the latest cloud-free products."""
    # Setup
    config = CopernicusConfig()
    if not config.validate():
        raise ValueError("Credentials not configured")

    catalog = CatalogSearch(config=config)
    products_manager = ProductsManager(config=config)

    # Define area of interest (San Francisco Bay Area)
    bbox = BoundingBox(
        west=-123.5,
        south=37.0,
        east=-122.0,
        north=38.5
    )

    # Search for recent products
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30)

    products = catalog.search_products(
        bbox=bbox,
        start_date=str(start_date),
        end_date=str(end_date),
        max_cloud_cover=10,
        max_results=20,
    )

    if not products:
        print("No products found")
        return

    # Create output directory
    output_dir = Path("downloads")
    output_dir.mkdir(exist_ok=True)

    # Download
    print(f"Found {len(products)} products. Downloading...")
    for i, product in enumerate(products[:5], 1):
        print(f"\n[{i}/5] {product.name}")
        print(f"      Date: {product.sensing_date.date()}")
        print(f"      Cloud: {product.cloud_cover}%")

        quicklook = products_manager.get_quicklook(product)
        if quicklook:
            filepath = output_dir / f"{product.name}_quicklook.jpg"
            quicklook.save_to_file(filepath)
            print(f"      ✓ Quicklook saved")

        metadata = products_manager.get_metadata(product)
        if metadata:
            filepath = output_dir / f"{product.name}_metadata.xml"
            metadata.save_to_file(filepath)
            print(f"      ✓ Metadata saved")

    print(f"\n✅ All downloads complete. Files in {output_dir}/")


if __name__ == "__main__":
    download_latest_products()
```

## API Reference

### CopernicusConfig

- `validate() -> bool` - Check if basic credentials are configured
- `has_static_s3_credentials() -> bool` - Check if S3 credentials are available
- `get_credentials() -> tuple[str, str]` - Get (username, password), raises ValueError if not set
- `get_s3_credentials() -> tuple[str, str]` - Get (access_key, secret_key), raises ValueError if not set

### BoundingBox

- `__init__(west: float, south: float, east: float, north: float)`
- `to_wkt() -> str` - Convert to WKT polygon format
- `to_bbox_string() -> str` - Convert to "west,south,east,north" string

### CatalogSearch

- `__init__(auth: Optional[CopernicusAuth] = None, config: Optional[CopernicusConfig] = None)`
- `search_products(...) -> list[ProductInfo]` - Search catalog
- `get_product_by_name(product_name: str) -> Optional[ProductInfo]` - Find by name

### ProductsManager

- `__init__(config: Optional[CopernicusConfig] = None, auth: Optional[CopernicusAuth] = None)`
- `get_quicklook(product: ProductInfo) -> Optional[ProductQuicklook]` - Download quicklook
- `get_metadata(product: ProductInfo, metadata_filename: Optional[str] = None) -> Optional[ProductMetadata]` - Download metadata
- `batch_get_quicklooks(products: list[ProductInfo], skip_errors: bool = True) -> dict` - Batch download quicklooks
- `batch_get_metadata(products: list[ProductInfo], metadata_filename: str = "MTD_MSIL2A.xml", skip_errors: bool = True) -> dict` - Batch download metadata

## Getting Help

- GitHub Issues: https://github.com/kalfasyan/vresto/issues
- Copernicus API Docs: https://documentation.dataspace.copernicus.eu/
- S3 API Reference: https://documentation.dataspace.copernicus.eu/APIs/S3.html
