# Quick Start

Get up and running with vresto in 5 minutes.

## Web Interface (Easiest)

### 1. Install and Configure

```bash
# Clone or install
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync

# Set your credentials
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

See [Installation & Setup](setup.md) for more details.

**Tip:** You can also configure S3 credentials directly in the web interface after launching - see [Installation & Setup](setup.md#option-a-web-interface-easiest-for-s3-credentials) for details.

### 2. Launch the App

```bash
make app
```

Or directly:

```bash
python src/vresto/ui/app.py
```

The interface opens at `http://localhost:8610`

### 3. Search for Products

1. **Select a date range** - Default is July 2020
2. **Choose product level** - L1C (raw), L2A (processed), or both
3. **Set cloud cover filter** - Maximum cloud coverage %
4. **Draw on the map** - Click to mark your area of interest
5. **Click Search** - Retrieve matching Sentinel-2 products
6. **View results**:
   - Click "Quicklook" to see a preview image
   - Click "Metadata" for detailed product information

---

## Programmatic API

Use vresto in your Python scripts.

### Basic Example

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Initialize with your credentials
config = CopernicusConfig()  # Reads from env vars or .env file
catalog = CatalogSearch(config=config)

# Define your search area
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

# Search for products
products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    end_date="2024-01-07",
    max_cloud_cover=20,
)

print(f"Found {len(products)} products")

# Download quicklooks and metadata
products_manager = ProductsManager(config=config)
for product in products[:3]:
    quicklook = products_manager.get_quicklook(product)
    metadata = products_manager.get_metadata(product)
    
    if quicklook:
        quicklook.save_to_file(f"{product.name}_quicklook.jpg")
    
    print(metadata)
```

### Advanced Configuration

Pass credentials directly:

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

---

## Common Tasks

### Search by Region Name

```python
from vresto.api import CatalogSearch, CopernicusConfig

config = CopernicusConfig()
catalog = CatalogSearch(config=config)

# Search by name (e.g., "Amsterdam")
products = catalog.search_by_name(
    location_name="Amsterdam",
    start_date="2024-01-01",
    max_cloud_cover=15,
)
```

### Download Multiple Products

```python
from vresto.products import ProductsManager

products_manager = ProductsManager(config=config)

for product in products:
    # Download quicklook
    quicklook = products_manager.get_quicklook(product)
    quicklook.save_to_file(f"downloads/{product.name}.jpg")
```

---

## Next Steps

- [Full API Reference](../user-guide/api.md)
- [Web Interface Guide](../user-guide/web-interface.md)
- [AWS CLI Guide](../advanced/aws-cli.md) for direct S3 access
