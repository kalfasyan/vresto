# Programmatic API Reference

Use vresto's Python API to automate satellite data searches and downloads in your applications.

## Installation

```bash
pip install vresto
# or with uv
uv pip install vresto
```

## Configuration

### Using Environment Variables

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
export COPERNICUS_S3_ACCESS_KEY="your_s3_access_key"      # Optional
export COPERNICUS_S3_SECRET_KEY="your_s3_secret_key"      # Optional
```

### Using .env File

Create `.env` in your project:

```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
COPERNICUS_S3_ACCESS_KEY=your_s3_access_key
COPERNICUS_S3_SECRET_KEY=your_s3_secret_key
```

### Programmatic Configuration

```python
from vresto.api import CopernicusConfig

config = CopernicusConfig(
    username="your_email@example.com",
    password="your_password",
    s3_access_key="your_s3_access_key",
    s3_secret_key="your_s3_secret_key",
)
```

## Core Classes

### CopernicusConfig

Manages credentials and configuration.

```python
from vresto.api import CopernicusConfig

config = CopernicusConfig()

# Validate credentials
if config.validate():
    print("✅ Credentials configured")

# Check S3 credentials
if config.has_static_s3_credentials():
    print("✅ S3 credentials available")
```

### BoundingBox

Define a geographic search area using coordinates.

```python
from vresto.api import BoundingBox

# Amsterdam area
bbox = BoundingBox(
    west=4.65,
    south=50.85,
    east=4.75,
    north=50.90
)

print(f"Search area: {bbox.west}°W, {bbox.south}°S to {bbox.east}°E, {bbox.north}°N")
```

**Coordinate format**: WGS84 (latitude/longitude)
- Negative values for West and South
- Positive values for East and North

### CatalogSearch

Search for Sentinel-2 products.

```python
from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig

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

#### Search Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `bbox` | `BoundingBox` | Geographic search area |
| `start_date` | `str` | Start date (YYYY-MM-DD format) |
| `end_date` | `str` | End date (YYYY-MM-DD format) |
| `max_cloud_cover` | `int` | Maximum cloud coverage (0-100%) |
| `product_type` | `str` | "L1C", "L2A", or "Both" (default: "Both") |

#### Search by Location Name

```python
products = catalog.search_by_name(
    location_name="Amsterdam",
    start_date="2024-01-01",
    max_cloud_cover=15,
)
```

### ProductsManager

Download and manage product data.

```python
from vresto.products import ProductsManager

products_manager = ProductsManager(config=config)

for product in products:
    # Get quicklook
    quicklook = products_manager.get_quicklook(product)
    if quicklook:
        quicklook.save_to_file(f"quicklooks/{product.name}.jpg")
    
    # Get metadata
    metadata = products_manager.get_metadata(product)
    print(metadata)
```

#### Available Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_quicklook(product)` | `Image` | Preview image (JPEG) |
| `get_metadata(product)` | `dict` | Product metadata (JSON) |
| `batch_download(products, output_dir)` | `list` | Download multiple products |

## Complete Examples

### Example 1: Search and Preview

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Setup
config = CopernicusConfig()
catalog = CatalogSearch(config=config)
products_manager = ProductsManager(config=config)

# Search
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    max_cloud_cover=20,
)

print(f"Found {len(products)} products")

# Download first 5 quicklooks
for product in products[:5]:
    quicklook = products_manager.get_quicklook(product)
    if quicklook:
        quicklook.save_to_file(f"preview_{product.name}.jpg")
        print(f"✅ Downloaded: {product.name}")
```

### Example 2: Batch Processing

```python
import json
from datetime import datetime, timedelta

# Search multiple regions
regions = {
    "Amsterdam": BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90),
    "Rotterdam": BoundingBox(west=4.4, south=51.8, east=4.55, north=51.95),
}

results = {}

for region_name, bbox in regions.items():
    products = catalog.search_products(
        bbox=bbox,
        start_date="2024-01-01",
        max_cloud_cover=15,
    )
    results[region_name] = {
        "count": len(products),
        "products": [p.name for p in products],
    }

# Save results
with open("search_results.json", "w") as f:
    json.dump(results, f, indent=2)
```

### Example 3: Time Series Analysis

```python
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

# Search entire month
start = datetime(2024, 1, 1)
end = datetime(2024, 1, 31)

products = catalog.search_products(
    bbox=bbox,
    start_date=start.strftime("%Y-%m-%d"),
    end_date=end.strftime("%Y-%m-%d"),
    max_cloud_cover=50,
)

# Analyze cloud cover over time
dates = [p.acquisition_date for p in products]
cloud_cover = [p.cloud_cover for p in products]

plt.figure(figsize=(12, 6))
plt.scatter(dates, cloud_cover, alpha=0.6)
plt.xlabel("Date")
plt.ylabel("Cloud Cover (%)")
plt.title("Cloud Cover Over Time")
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig("cloud_cover_analysis.png")
```

## Error Handling

```python
from vresto.api import CopernicusConfig, CatalogSearch

try:
    config = CopernicusConfig()
    
    if not config.validate():
        print("❌ Invalid credentials")
        exit(1)
    
    catalog = CatalogSearch(config=config)
    products = catalog.search_products(bbox=bbox, start_date="2024-01-01")
    
except Exception as e:
    print(f"❌ Error: {e}")
```

## Next Steps

- [Web Interface Guide](web-interface.md) - Visual search interface
- [AWS CLI Guide](../advanced/aws-cli.md) - Direct S3 access
- [Setup Guide](../getting-started/setup.md) - Configuration details
