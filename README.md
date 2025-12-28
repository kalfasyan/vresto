<div align="center">
  <img src="docs/assets/vresto_logo.jpg" alt="vresto logo" width="320" />
  
  # vresto
  
  **An elegant Python interface for discovering and retrieving Copernicus Sentinel data.**
  
  [![PyPI version](https://badge.fury.io/py/vresto.svg)](https://badge.fury.io/py/vresto)
  [![Tests](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml/badge.svg)](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml)
  [![Docs - MkDocs](https://img.shields.io/badge/docs-mkdocs-blue)](https://kalfasyan.github.io/vresto/)
  [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
  [![Gitleaks](https://img.shields.io/badge/secret%20scanning-gitleaks-blue)](https://github.com/gitleaks/gitleaks)
</div>

---

## Demo

![vresto Demo](docs/assets/vresto_demo.gif)

## Features

- 🗺️ **Interactive Map Interface** - Visually search and filter satellite products
- 🛰️ **High-Resolution Tile Server** - Instantly visualize full-resolution product bands on the map (via `localtileserver`)
- 🔍 **Smart Search** - Filter by location, date range, cloud cover, and product type
- 📦 **Granular Download Management** - Advanced Band-Resolution matrix for precise data selection and de-duplicated downloads
- 🔌 **Dual Backend Support** - Flexible discovery via **OData** or **STAC** APIs
- 🐍 **Professional API** - Clean Python API for programmatic access
- 🔐 **Secure** - Handle S3 credentials safely with static key support
- ⚡ **Efficient** - Batch operations and smart caching

## ⚡ Quick Start with Docker 🐳

The fastest way to run `vresto` is by using Docker Compose 🚢

You only need Docker and Docker Compose installed on your machine. If you don't have them yet, you can find installation instructions on the [Docker website](https://docs.docker.com/get-docker/) and [Docker Compose documentation](https://docs.docker.com/compose/install/).

**Note:** You need Copernicus credentials to use vresto. Get free access at https://dataspace.copernicus.eu/

Start `vresto` in just a few steps:

1. **Clone the repository and go to its main directory**
    ```bash
    git clone https://github.com/kalfasyan/vresto.git && cd vresto
    ```

2. **Start the application with Docker Compose**
    ```bash
    docker compose up -d
    ```
    
    ℹ️ **That's it!** The app will start and you can add credentials later via the UI, or provide them now:
    
    **Option A: Add credentials now** (Recommended if you have them)
    - Uncomment and fill the environment variables in `docker-compose.yml`, or
    - Create a `.env` file:
      ```bash
      cp .env.example .env
      # Edit .env with your credentials
      ```
    
    **Option B: Add credentials later** (via the app Settings menu)
    - Just run `docker compose up -d` without credentials (use `docker compose up -d --build` if you just cloned the repo)
    - The app will start at http://localhost:8610
    - Click the **☰ menu button** in the top-left corner to open the Settings drawer
    - Add your Copernicus credentials through the Settings menu anytime
    - S3 credentials are optional—without them you'll get temporary credentials with usage limits (see [Setup Guide](docs/getting-started/setup.md) for details)

✅ **Done!** 🎉

Your vresto dashboard is now running at:  
🌐 [http://localhost:8610](http://localhost:8610)

**Note:** If you pulled recent changes and a feature isn't available, rebuild the Docker image:
```bash
docker compose up -d --build
```

<details>
<summary><strong>🚀 Essential Docker & Docker Compose Commands</strong></summary>

```bash
# Start the app in background (Docker Compose)
docker compose up -d
```

```bash
# View logs (Docker Compose)
docker compose logs -f
```

```bash
# Stop and remove services (Docker Compose)
docker compose down
```

```bash
# Rebuild and start (Docker Compose)
docker compose up -d --build
```

```bash
# Run the container directly (plain Docker)
docker run -d -p 8610:8610 \
  --name vresto-dashboard \
  vresto:latest
```

```bash
# View logs (plain Docker)
docker logs -f vresto-dashboard
```

```bash
# Stop and remove the container (plain Docker)
docker stop vresto-dashboard && docker rm vresto-dashboard
```
</details>

## Quick Start

**Note:** You need Copernicus credentials to use vresto. Get free access at https://dataspace.copernicus.eu/


### Installation

**From PyPI (recommended for users):**
```bash
pip install vresto
```

**For development:**
```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync
```

### Configuration

Configure your credentials (see [Setup Guide](docs/getting-started/setup.md) for details):
```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

Or run the interactive setup helper which writes a `.env` in the project root:
```bash
python scripts/setup_credentials.py
```

### Launch the App

Simply run:
```bash
vresto
```

Opens at http://localhost:8610

**Alternative methods:**
```bash
# Using make
make app

# Or directly with Python
python src/vresto/ui/app.py
```

**Command-Line Interface (CLI):**

Quick searches and downloads from the terminal:

```bash
# 🔍 Search for products
vresto-cli search-name "S2A_MSIL2A_20200612" --max-results 5

# 📸 Download quicklook (preview image)
vresto-cli download-quicklook "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" --output ./quicklooks

# 📋 Download metadata
vresto-cli download-metadata "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" --output ./metadata

# 🎨 Download specific bands
vresto-cli download-bands "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" "B04,B03,B02" --resolution 10 --output ./data
```

For complete CLI documentation, see the [CLI Guide](docs/user-guide/cli.md).

**API usage:**

Get started with just a few lines of Python:

```python
from vresto.api import CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

# Initialize
config = CopernicusConfig()
catalog = CatalogSearch(config=config)
manager = ProductsManager(config=config)

# 🔍 Search for a product by name
products = catalog.search_products_by_name("S2A_MSIL2A", max_results=5)

# 📸 Download quicklook and metadata
for product in products:
    quicklook = manager.get_quicklook(product)
    metadata = manager.get_metadata(product)
    if quicklook:
        quicklook.save_to_file(f"{product.name}.jpg")

# 🎨 Download specific bands for analysis/visualization
manager.download_product_bands(
    product=products[0].name,
    bands=["B04", "B03", "B02"],  # Red, Green, Blue
    resolution=10,
    dest_dir="./data"
)
```

For more examples, see the [examples/](examples/) directory and [API Guide](docs/user-guide/api.md).

For detailed setup and usage, see the documentation below.

## Documentation

📖 **[Full Documentation](https://kalfasyan.github.io/vresto/)** - Hosted on GitHub Pages

- **[Setup Guide](https://kalfasyan.github.io/vresto/getting-started/setup/)** ⭐ **Start here** - Installation, credentials setup, and configuration
- [API Guide](https://kalfasyan.github.io/vresto/user-guide/api/) - Programmatic usage examples and reference
- [AWS CLI Guide](https://kalfasyan.github.io/vresto/advanced/aws-cli/) - Direct S3 access with AWS CLI
- [Contributing](CONTRIBUTING.md) - Development setup

## Requirements

- Python 3.11+
- `uv` package manager (optional but recommended)

## License

See [LICENSE.txt](LICENSE.txt)
