<div align="center">
  <img src="docs/assets/vresto_logo.jpg" alt="vresto logo" width="320" />
  
  # vresto
  
  **A beautiful, professional Python toolkit for searching and accessing Copernicus Sentinel satellite data**
  
  [![PyPI version](https://badge.fury.io/py/vresto.svg)](https://badge.fury.io/py/vresto)
  [![Tests](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml/badge.svg)](https://github.com/kalfasyan/vresto/actions/workflows/tests.yml)
  [![Docs](https://github.com/kalfasyan/vresto/actions/workflows/build-docs.yml/badge.svg)](https://github.com/kalfasyan/vresto/actions/workflows/build-docs.yml)
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
    - Just run `docker compose up -d` without credentials
    - The app will start at http://localhost:8610
    - Click the **☰ menu button** in the top-left corner to open the Settings drawer
    - Add your Copernicus credentials through the Settings menu anytime
    - S3 credentials are optional—without them you'll get temporary credentials with usage limits (see [Setup Guide](docs/getting-started/setup.md) for details)

✅ **Done!** 🎉

Your vresto dashboard is now running at:  
🌐 [http://localhost:8610](http://localhost:8610)

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
uv pip install -e .
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
make app
```

Or directly with Python:
```bash
python src/vresto/ui/app.py
```

Opens at http://localhost:8610

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

- **[Setup Guide](docs/getting-started/setup.md)** ⭐ **Start here** - Installation, credentials setup, and configuration
- [API Guide](docs/user-guide/api.md) - Programmatic usage examples and reference
- [AWS CLI Guide](docs/advanced/aws-cli.md) - Direct S3 access with AWS CLI
- [Contributing](CONTRIBUTING.md) - Development setup

## Requirements

- Python 3.9+
- `uv` package manager (optional but recommended)

## License

See [LICENSE.txt](LICENSE.txt)
