# About vresto

## What is vresto?

**vresto** (short for "viewing resto" - as in viewing satellite data at rest) is a professional Python toolkit for searching and accessing Copernicus Sentinel satellite data. It combines a modern web interface with a powerful API to make satellite data accessible to researchers, developers, and geospatial enthusiasts.

## Why vresto?

### The Problem

Accessing Copernicus Sentinel satellite data traditionally requires:
- Complex authentication flows
- Understanding AWS S3 bucket structures
- Managing credentials securely
- Writing boilerplate code for every project

### Our Solution

vresto provides:

- **Intuitive Interface** - Visually search satellite products without technical knowledge
- **Clean API** - Professional Python interface for automation
- **Secure** - Handles credentials safely with built-in best practices
- **Zero Friction** - Get started with 5 minutes of setup

## Key Features

✨ **Interactive Map Interface** - Click on the map to search
🎯 **Click-to-Stream** - Stream any MGRS tile's true-colour image, with or without an account
🟦 **Coverage at a Glance** - Grid tiles coloured by product count in your dates
📊 **Product Filtering** - Date range, cloud cover, product level
🚀 **Fast Searching** - Optimized queries to the Copernicus API
📥 **Easy Downloads** - Download quicklooks and metadata with one line
🔐 **Secure Credentials** - S3 key management built-in
⚡ **Programmatic** - Automate your workflow with Python

## Technology Stack

- **Python 3.11+** - Modern Python with async support
- **NiceGUI** - Fast, responsive web interface
- **Boto3** - AWS S3 client for data access
- **Requests** - HTTP client for API communication
- **Loguru** - Advanced logging

## Data Sources

vresto searches and downloads data from the [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/) (CDSE):

- **Sentinel-2** Multispectral Imaging Satellites
- **L1C** (Raw) and **L2A** (Processed) product levels
- **10m resolution** RGB preview data
- **Global coverage** since 2015

For the map, it also uses public sources that need no account:

- **AWS Open Data** ([Element84 Earth Search](https://element84.com/earth-search/)) - Sentinel-2 L2A cloud-optimised GeoTIFFs, streamed when no CDSE S3 keys are set
- **[s2-stac-geoparquet](https://github.com/taylor-geospatial/s2-stac-geoparquet)** on Source Cooperative - a GeoParquet mirror of the Earth Search index, used to colour MGRS tiles by product count
- **Copernicus Land Monitoring Service, ESA WorldCover and the Copernicus DEM** for the map overlays (land cover, DEM, NDVI, LST, soil moisture, ...)

## Open Source

vresto is open source and available on [GitHub](https://github.com/kalfasyan/vresto).

- **License**: Creative Commons Attribution 4.0 International (see LICENSE.txt)
- **Contributing**: See CONTRIBUTING.md for guidelines
- **Issues**: Report bugs on [GitHub Issues](https://github.com/kalfasyan/vresto/issues)

## Getting Started

- [Installation & Setup](getting-started/setup.md) - 5 minute setup
- [Quick Start](getting-started/quickstart.md) - See it in action
- [API Reference](user-guide/api.md) - Full API documentation
- [GitHub Repository](https://github.com/kalfasyan/vresto) - Source code

## Acknowledgments

- [Copernicus Dataspace Ecosystem](https://dataspace.copernicus.eu/) - For hosting Sentinel data
- [NiceGUI](https://nicegui.io/) - Web framework
- [Boto3](https://boto3.amazonaws.com/) - AWS SDK
- [Element84 Earth Search](https://element84.com/earth-search/) and [AWS Open Data](https://registry.opendata.aws/sentinel-2-l2a-cogs/) - Public Sentinel-2 imagery
- [s2-stac-geoparquet](https://github.com/taylor-geospatial/s2-stac-geoparquet) on [Source Cooperative](https://source.coop/) - Per-tile scene index
- [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) - Documentation theme
