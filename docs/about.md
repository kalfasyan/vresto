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

## Data Source

vresto accesses data from [Copernicus Dataspace Ecosystem](https://dataspace.copernicus.eu/):

- **Sentinel-2** Multispectral Imaging Satellites
- **L1C** (Raw) and **L2A** (Processed) product levels
- **10m resolution** RGB preview data
- **Global coverage** since 2015

## Open Source

vresto is open source and available on [GitHub](https://github.com/kalfasyan/vresto).

- **License**: MIT (see LICENSE.txt)
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
- [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) - Documentation theme
