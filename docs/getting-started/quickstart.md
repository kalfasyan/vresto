# Quick Start

Get up and running with vresto in 5 minutes.

## Docker (Fastest Onboarding)

```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
cp .env.example .env
# Optional: edit .env with your credentials (needed only for downloads)
make docker-up
```

`.env` is gitignored—keep your secrets there and never commit it.

Open `http://localhost:8610`

If you need a clean rebuild:

```bash
make docker-rebuild
```

Equivalent Docker Compose command:

```bash
docker compose up -d
```

## Web Interface (Easiest)

### 1. Install and Configure

```bash
# Clone or install
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync

# Optional: set your credentials (needed only to download products)
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

Searching and tile streaming work without an account.

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

1. **Select a date range** - Default is the last 30 days
2. **Choose product level** - L1C (raw), L2A (processed), or both
3. **Set cloud cover filter** - Maximum cloud coverage %
4. **Draw on the map** - Click to mark your area of interest
5. **Click Search** - Retrieve matching Sentinel-2 products
6. **View results**:
   - Click "Quicklook" to see a preview image
   - Click "Metadata" for detailed product information

### 4. Stream a Tile

1. **Zoom in** - From zoom 5 the MGRS grid appears, each tile coloured by how many products per month cover it in your dates (zoomed out, whole grid zones are coloured)
2. **Hover a tile** - See its product count and best cloud cover
3. **Click it** - Pick a scene and stream its true-colour image onto the map. Without S3 keys it comes from AWS Open Data
4. **Add an overlay** - Switch on land cover, NDVI, temperature and more, then use *Inspect values* to read them

See the [Web Interface Guide](../user-guide/web-interface.md#map-search) for details.

---

## Programmatic API

Use vresto in your Python scripts.

### Basic Example

```python
from vresto.api import BoundingBox, CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Credentials are read from env vars or .env; searching works without them
config = CopernicusConfig()
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

### Search by Product Name

```python
from vresto.api import CatalogSearch, CopernicusConfig

config = CopernicusConfig()
catalog = CatalogSearch(config=config)

# Find products whose name matches a pattern (e.g. one MGRS tile)
products = catalog.search_products_by_name(
    "T31UFS",
    match_type="contains",  # or startswith, endswith, eq
    max_results=10,
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
