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
  [![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/kalfasyan)
</div>

---

## Demo

[![vresto Demo](https://img.youtube.com/vi/B4gt4EUrPOU/maxresdefault.jpg)](https://youtu.be/B4gt4EUrPOU)

## Features

- 🗺️ **Interactive Map Interface** — visually search and filter satellite products
- 🔓 **No Account Needed to Explore** — catalogue search, tile streaming and the coloured grid all work anonymously; credentials are only needed to download
- 🎯 **Click-to-Stream** — click any MGRS grid tile to stream its latest True Color Image (TCI), picking the scene from a chooser with thumbnails
- 🟦 **Coverage at a Glance** — MGRS tiles are coloured by how many Sentinel-2 L2A products per month cover them in your exact date range (colour-blind-safe palette, tiles with no products dashed in orange). Zoomed out, the ~1,200 MGRS grid zones are coloured instead, so you can scan the whole world at once. Counts come from the public [s2-stac-geoparquet](https://github.com/taylor-geospatial/s2-stac-geoparquet) index on Source Cooperative
- 🔀 **Two Streaming Sources** — with CDSE S3 keys, tiles stream from CDSE; without them, from AWS Open Data (Element84 Earth Search). Override with `VRESTO_STREAM_SOURCE`
- 🛰️ **High-Resolution Tile Server** — visualize full-resolution product bands on the map (via `localtileserver`)
- 🌍 **13 Contextual Overlays** — land cover (WorldCover, LCM, LC100, TCD), terrain (Copernicus DEM), vegetation & productivity (NDVI climatology, FAPAR, Dry Matter Productivity), thermal (hourly LST), water & soil (Soil Moisture, Soil Water Index, Water Bodies), and hazard layers (Burned Area)
- 🔎 **Point Inspector** — turn on *Inspect values* and click a streamed tile to read the exact value under the cursor (land-cover class, NDVI, temperature, soil moisture, elevation, …)
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

> **Credentials:** Searching the Copernicus catalogues works without an account, and so does tile streaming: without S3 keys, tiles come from AWS Open Data (Element84 Earth Search). Downloading products needs free Copernicus credentials, and streaming from CDSE needs S3 keys — get them at <https://dataspace.copernicus.eu/>. See the [Setup Guide](https://kalfasyan.github.io/vresto/getting-started/setup/) for configuration details.

## Installation

**From PyPI:**

```bash
pip install vresto
```

**For development:**

```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync --extra dev
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

Search and download from Python (searching needs no credentials; downloading does):

```python
from vresto.api import CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

config = CopernicusConfig()
catalog = CatalogSearch(config=config)
manager = ProductsManager(config=config)

# Search for a product by name (works anonymously)
products = catalog.search_products_by_name("S2A_MSIL2A", max_results=5)

# Download specific bands (Red, Green, Blue) — needs Copernicus credentials
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
- [Web Interface Guide](https://kalfasyan.github.io/vresto/user-guide/web-interface/) — map search, tile streaming, grid colours, overlays
- [API Guide](https://kalfasyan.github.io/vresto/user-guide/api/) — programmatic usage
- [CLI Guide](https://kalfasyan.github.io/vresto/user-guide/cli/) — command-line reference
- [Catalog Providers](https://kalfasyan.github.io/vresto/user-guide/catalogs/) — OData vs STAC
- [AWS CLI Guide](https://kalfasyan.github.io/vresto/advanced/aws-cli/) — direct S3 access
- [Contributing](CONTRIBUTING.md) — development setup

## Support

If vresto is useful to you, consider [buying me a coffee](https://buymeacoffee.com/kalfasyan) ☕

## License

This project is licensed under the terms in [LICENSE.txt](LICENSE.txt).
