# Copernicus Data Space API

This module provides a Python interface to search and access satellite products from the Copernicus Data Space Ecosystem.

## Features

- **Authentication**: Get access tokens and temporary S3 credentials
- **Catalog Search**: Search for products by location, date, and other criteria
- **Multiple Collections**: Support for Sentinel-1, Sentinel-2, Sentinel-3, and Sentinel-5P
- **Flexible Queries**: Filter by cloud cover, date range, bounding box, etc.

## Setup

### 1. Get Copernicus Credentials

Register at [Copernicus Data Space](https://dataspace.copernicus.eu/) to get your username and password.

### 2. Set Environment Variables

```bash
export COPERNICUS_USERNAME="your_username"
export COPERNICUS_PASSWORD="your_password"
```

Or create a `.env` file in your project root.

## Usage

### Basic Search Example

```python
from vresto.api import BoundingBox, CatalogSearch

# Initialize catalog search (uses env vars for credentials)
catalog = CatalogSearch()

# Define search area (bounding box)
bbox = BoundingBox(
    west=4.65,   # Min longitude
    south=50.85, # Min latitude
    east=4.75,   # Max longitude
    north=50.90  # Max latitude
)

# Search for products by location and date
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    end_date="2024-01-07",
    collection="SENTINEL-2",
    max_cloud_cover=20,
    max_results=10
)

# Process results
for product in products:
    print(f"{product.name}")
    print(f"  Date: {product.sensing_date}")
    print(f"  Size: {product.size_mb:.2f} MB")
    print(f"  Cloud: {product.cloud_cover}%")

# Search for products by name pattern
products = catalog.search_products_by_name(
    "20240101",
    match_type="contains"  # Also supports: startswith, endswith, eq
)
```
```

### Available Collections

- `SENTINEL-1`: SAR (Synthetic Aperture Radar) imagery
- `SENTINEL-2`: Multispectral optical imagery
- `SENTINEL-3`: Ocean and land monitoring
- `SENTINEL-5P`: Atmospheric monitoring

### Filtering Options

- **Date Range**: `start_date` and `end_date` (format: `YYYY-MM-DD`)
- **Cloud Cover**: `max_cloud_cover` (0-100, only for optical sensors)
- **Max Results**: `max_results` (default: 100)
- **Bounding Box**: Geographic area of interest

## API Modules

### `config.py`
Configuration and credential management.

### `auth.py`
Authentication with Copernicus API:
- Get access tokens
- Manage temporary S3 credentials

### `catalog.py`
Product search functionality:
- Search by location and date
- Filter by collection and cloud cover
- Parse and structure results

## UI Integration

The API is integrated with the map interface. To use:

1. Select a date or date range using the date picker
2. Draw a marker on the map to select a location
3. Choose satellite collection and filters
4. Click "Search Products" to query the catalog
5. View results in the right panel

## Example Script

Run the example search:

```bash
uv run python examples/search_example.py
```

## Documentation

For more information:
- [Copernicus Data Space Documentation](https://documentation.dataspace.copernicus.eu/)
- [OData API Reference](https://documentation.dataspace.copernicus.eu/APIs/OData.html)
- [S3 Access Guide](https://documentation.dataspace.copernicus.eu/APIs/S3.html)
