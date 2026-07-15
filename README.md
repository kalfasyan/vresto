<div align="center">
  <img src="docs/assets/vresto_logo.jpg" alt="vresto logo" width="320" />
  
  # vresto
  
  **An elegant Python interface for discovering and retrieving Copernicus Sentinel data.**
  
  [![PyPI version](https://badge.fury.io/py/vresto.svg)](https://badge.fury.io/py/vresto)
  [![PyPI Downloads](https://static.pepy.tech/personalized-badge/vresto?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/vresto)
  [![Tests](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml/badge.svg)](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml)
  [![Docs - MkDocs](https://img.shields.io/badge/docs-mkdocs-blue)](https://kalfasyan.github.io/vresto/)
  [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
  [![Gitleaks](https://img.shields.io/badge/secret%20scanning-gitleaks-blue)](https://github.com/gitleaks/gitleaks)
</div>

---

## Demo

[![vresto Demo](https://img.youtube.com/vi/B4gt4EUrPOU/maxresdefault.jpg)](https://youtu.be/B4gt4EUrPOU)

## Features

- 🗺️ **Interactive Map Interface** — visually search and filter satellite products
- 🛰️ **High-Resolution Tile Server** — visualize full-resolution product bands on the map (via `localtileserver`)
- 🎯 **Click-to-Stream** — click any MGRS grid tile to stream its latest True Color Image (TCI)
- 🌍 **14 Contextual Overlays** — land cover (WorldCover, LCM, LC100, TCD), terrain (Copernicus DEM), vegetation & productivity (NDVI climatology, FAPAR, Dry Matter Productivity), thermal (hourly LST), water & soil (Soil Moisture, Soil Water Index, Water Bodies), and hazard layers (Burned Area)
- 🔍 **Smart Search** — filter by location, date range, cloud cover, and product type
- 📦 **Granular Downloads** — Band-Resolution matrix for precise data selection and de-duplicated downloads
- 🔌 **Dual Backend Support** — discovery via **OData** or **STAC** APIs
- 🐍 **Professional API** — clean Python API for programmatic access
- 🔐 **Secure** — handle S3 credentials safely with static key support
- ⚡ **Efficient** — batch operations and smart caching

## Requirements

- Python 3.11+
- Docker and Docker Compose *(optional, for the Docker setup)*
- `uv` package manager *(optional, recommended for development)*

> **Credentials:** You need free Copernicus credentials to use vresto — get them at <https://dataspace.copernicus.eu/>. See the [Setup Guide](https://kalfasyan.github.io/vresto/getting-started/setup/) for configuration details.

## Installation

**From PyPI:**

```bash
pip install vresto
```

**For development:**

```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync
```

## Usage

### Docker

The fastest way to run vresto:

```bash
git clone https://github.com/kalfasyan/vresto.git && cd vresto
make docker-up
```

The dashboard opens at <http://localhost:8610>. Add your Copernicus credentials anytime via the **☰ menu → Settings**, or provide them up front in a `.env` file (see `.env.example`). Use `make docker-rebuild` after pulling changes.

### Python

Launch the app:

```bash
vresto  # or: uv run vresto / pixi run vresto / make app
```

Search and download from Python:

```python
from vresto.api import CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

config = CopernicusConfig()
catalog = CatalogSearch(config=config)
manager = ProductsManager(config=config)

# Search for a product by name
products = catalog.search_products_by_name("S2A_MSIL2A", max_results=5)

# Download specific bands (Red, Green, Blue)
manager.download_product_bands(
    product=products[0].name,
    bands=["B04", "B03", "B02"],
    resolution=10,
    dest_dir="./data",
)
```

### CLI

```bash
vresto-cli search-name "S2A_MSIL2A" --max-results 5
vresto-cli download-bands "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" "B04,B03,B02" --resolution 10 --output ./data
```

## Documentation

📖 **[Full Documentation](https://kalfasyan.github.io/vresto/)**

- **[Setup Guide](https://kalfasyan.github.io/vresto/getting-started/setup/)** ⭐ start here — installation, credentials, configuration
- [API Guide](https://kalfasyan.github.io/vresto/user-guide/api/) — programmatic usage
- [CLI Guide](https://kalfasyan.github.io/vresto/user-guide/cli/) — command-line reference
- [AWS CLI Guide](https://kalfasyan.github.io/vresto/advanced/aws-cli/) — direct S3 access
- [Contributing](CONTRIBUTING.md) — development setup

## License

This project is licensed under the terms in [LICENSE.txt](LICENSE.txt).
