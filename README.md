<div align="center">
  <img src="docs/assets/vresto_logo.jpg" alt="vresto logo" width="320" />
  
  # vresto
  
  **A beautiful, professional Python toolkit for searching and accessing Copernicus Sentinel satellite data**
  
  [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
  [![Gitleaks](https://img.shields.io/badge/secret%20scanning-gitleaks-blue)](https://github.com/gitleaks/gitleaks)
</div>

---

## Features

- 🗺️ **Interactive Map Interface** - Visually search and filter satellite products
- 🔍 **Smart Search** - Filter by location, date range, cloud cover, and product type
- 📦 **Product Management** - Download quicklooks and metadata from S3
- 🐍 **Professional API** - Clean Python API for programmatic access
- 🔐 **Secure** - Handle S3 credentials safely with static key support
- ⚡ **Efficient** - Batch operations and smart caching

## Quick Start

**Note:** You need Copernicus credentials to use vresto. Get free access at https://dataspace.copernicus.eu/

Clone and install:
```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync
```

Configure your credentials (see [Setup Guide](docs/SETUP.md) for details):
```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

**Web interface:**
```bash
uv run python src/vresto/ui/map_interface.py
```
Opens at http://localhost:8080

**API usage:**
```python
from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig
from vresto.products import ProductsManager

config = CopernicusConfig()
catalog = CatalogSearch(config=config)
bbox = BoundingBox(west=4.65, south=50.85, east=4.75, north=50.90)

products = catalog.search_products(
    bbox=bbox,
    start_date="2024-01-01",
    max_cloud_cover=20,
)

manager = ProductsManager(config=config)
for product in products[:5]:
    quicklook = manager.get_quicklook(product)
    if quicklook:
        quicklook.save_to_file(f"{product.name}.jpg")
```

For detailed setup and usage, see the documentation below.

## Documentation

- **[Setup Guide](docs/SETUP.md)** ⭐ **Start here** - Installation, credentials setup, and configuration
- [API Guide](docs/PROGRAMMATIC_API.md) - Programmatic usage examples and reference
- [AWS CLI Guide](docs/AWS_CLI_GUIDE.md) - Direct S3 access with AWS CLI
- [Contributing](CONTRIBUTING.md) - Development setup

## Requirements

- Python 3.9+
- `uv` package manager (optional but recommended)

## License

See [LICENSE.txt](LICENSE.txt)
