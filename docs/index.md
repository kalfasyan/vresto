# Welcome to vresto

**vresto** is a professional Python toolkit for searching and accessing Copernicus Sentinel satellite data.

## Key Features

- 🗺️ **Interactive Map Interface** - Visually search and filter satellite products
- 🔓 **No Account Needed to Explore** - Catalogue search, tile streaming and the coloured grid work anonymously; credentials are only needed to download
- 🎯 **Click-to-Stream** - Click an MGRS grid tile to stream its true-colour image, from CDSE with S3 keys or from AWS Open Data without them
- 🟦 **Coverage at a Glance** - Grid tiles are coloured by how many Sentinel-2 products per month cover them in your dates; zoomed out, whole MGRS grid zones are coloured
- 🌍 **13 Contextual Overlays** - Land cover, terrain, vegetation, temperature, soil moisture, water and burned area
- 🔎 **Point Inspector** - Click a streamed tile to read the exact value of a map overlay (land cover class, NDVI, temperature, ...)
- 🔍 **Smart Search** - Filter by location, date range, cloud cover, and product type, via OData or STAC
- 📦 **Granular Downloads** - Pick exact band/resolution pairs, plus quicklooks and metadata
- 🐍 **Professional API & CLI** - Clean Python API and `vresto-cli` for automation
- 🔐 **Secure** - Handle S3 credentials safely with static key support

## Quick Links

- **[Installation & Setup](getting-started/setup.md)** - Get started in minutes
- **[Quick Start](getting-started/quickstart.md)** - See it in action
- **[API Reference](user-guide/api.md)** - Use vresto in your code
- **[Web Interface Guide](user-guide/web-interface.md)** - Browser-based search

## Requirements

- Python 3.11+
- Optional: free [Copernicus Dataspace](https://dataspace.copernicus.eu/) account, needed to download products and to stream tiles from CDSE. Searching and streaming from AWS Open Data work without one
- Optional: AWS CLI for direct S3 access

## Getting Help

- 📖 Check the [documentation](getting-started/setup.md)
- 🐛 Report issues on [GitHub](https://github.com/kalfasyan/vresto/issues)
- 💬 See [CONTRIBUTING.md](https://github.com/kalfasyan/vresto/blob/main/CONTRIBUTING.md)
