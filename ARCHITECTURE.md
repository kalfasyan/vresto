# Vresto Architecture

This document provides an overview of the vresto project's architecture, component interactions, and data flow.

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Copernicus Data Space Ecosystem                           │
│  (Sentinel-1, Sentinel-2, Sentinel-3, Sentinel-5P Satellite Data)          │
└───────────────────┬────────────────────────────────────────────┬────────────┘
                    │                                             │
        ┌───────────▼────────────┐                  ┌────────────▼──────────┐
        │   OData API Endpoint   │                  │   STAC API Endpoint   │
        │ (JSON-based discovery) │                  │  (Catalog standard)   │
        └────────┬────────────────┘                  └───────────┬──────────┘
                 │                                              │
        ┌────────▼──────────────────────────────────────────────▼─────────┐
        │                   API Module (api/)                             │
        │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │
        │  │  config.py   │  │  auth.py     │  │  catalog.py        │   │
        │  │ (Credentials,│  │ (Tokens,     │  │ (Search, Filtering)│   │
        │  │  .env setup) │  │  S3 creds)   │  │ (ProductInfo DTO)  │   │
        │  └──────────────┘  └──────────────┘  └────────────────────┘   │
        └────────┬───────────────────────────────────────────────────────┘
                 │
    ┌────────────┴────────────────────┬─────────────────────┬──────────────┐
    │                                  │                     │              │
    │                                  │                     │              │
┌───▼──────────────┐  ┌──────────────▼─────┐  ┌────────────▼──┐  ┌───────▼──┐
│  CLI Module      │  │   UI Module        │  │ Services      │  │ Products │
│  (cli/)          │  │   (ui/)            │  │ Module        │  │ & Bands  │
│                  │  │                    │  │ (services/)   │  │ (products/│
│ ┌──────────────┐ │  │ ┌────────────────┐ │  │               │  │ bands/)  │
│ │  main.py     │ │  │ │  app.py        │ │  │ • Manages     │  │          │
│ │  (typer CLI) │ │  │ │ (NiceGUI app)  │ │  │   product     │  │ • Band   │
│ └──────────────┘ │  │ │                │ │  │   operations  │  │   data   │
│                  │  │ └────────────────┘ │  │ • Caching     │  │   access │
│ Commands:        │  │                    │  │ • Download    │  │ • Handle │
│ • search-name    │  │ ┌────────────────┐ │  │   mgmt        │  │   band   │
│ • download-*     │  │ │ Tabs (widgets/)│ │  │               │  │   metadata
│ • etc.           │  │ │                │ │  └───────────────┘  └──────────┘
└─────────┬────────┘  │ ├──────────────┤ │
          │           │ │MapSearchTab  │ │
          │           │ ├──────────────┤ │
          │           │ │NameSearchTab │ │
          │           │ ├──────────────┤ │
          │           │ │DownloadTab   │ │
          │           │ ├──────────────┤ │
          │           │ │ AnalysisTab  │ │
          │           │ └────────────────┘ │
          │           │                    │
          │           │ ┌────────────────┐ │
          │           │ │map_interface   │ │
          │           │ │(Orchestration) │ │
          │           │ └────────────────┘ │
          │           └────────────────────┘
          │
          ▼
    ┌──────────────────────────────────────┐
    │   User Interaction Points            │
    │                                      │
    │  • Command Line Interface (CLI)      │
    │  • Web Browser (http://localhost:   │
    │    8610)                            │
    │  • Python API (Direct import)       │
    └──────────────────────────────────────┘
```

---

## Core Modules

### 1. **API Module** (`src/vresto/api/`)
**Purpose**: Manages authentication and product discovery from Copernicus Data Space

**Components**:
- **`config.py`**: Configuration and credential management
  - Reads from environment variables or `.env` files
  - Provides masked password properties for safe logging
  - Manages S3 endpoint configuration

- **`auth.py`**: Authentication and authorization
  - Obtains access tokens for API requests
  - Retrieves temporary S3 credentials
  - Validates credentials automatically

- **`catalog.py`**: Product search and discovery
  - Supports both OData and STAC search backends
  - Filters by location (bounding box), date, cloud cover, collection
  - Returns structured `ProductInfo` objects
  - Provides search by name, location, and custom queries

**Key Classes**:
- `CopernicusConfig` - Credential configuration
- `CatalogSearch` - Main search interface
- `ProductInfo` - DTO for search results

---

### 2. **UI Module** (`src/vresto/ui/`)
**Purpose**: Provides interactive web interface using NiceGUI framework

**Structure**:
```
ui/
├── app.py                    # Main NiceGUI application
├── map_interface.py          # Tab orchestration layer
├── widgets/                  # Modular UI components
│   ├── activity_log.py       # Activity/status logging widget
│   ├── date_picker.py        # Date range selection
│   ├── map_widget.py         # Interactive map with drawing
│   ├── search_results_panel.py # Search filters & controls
│   ├── product_viewer.py     # Quicklook/metadata display
│   ├── map_search_tab.py     # Map-based search interface
│   ├── name_search_tab.py    # Name-based search interface
│   ├── download_tab.py       # Product download interface
│   └── product_analysis_tab.py # Local product analysis
├── visualization/            # Visualization utilities
│   └── ...
└── static/                   # Static assets (CSS, JS)
```

**Key Tabs**:

1. **Map Search Tab** (`widgets/map_search_tab.py`)
   - Interactive map for geographic selection
   - Date range filtering
   - Collection/level/cloud cover filters
   - Real-time product display on map

2. **Name Search Tab** (`widgets/name_search_tab.py`)
   - Product name pattern matching
   - Support for "contains", "startswith", "endswith", "exact" matching
   - Client-side filtering by date and level

3. **Download Tab** (`widgets/download_tab.py`)
   - Product selection and band selection
   - Resolution picker (60m, 20m, 10m, native)
   - Progress tracking
   - Batch download capability

4. **Product Analysis Tab** (`widgets/product_analysis_tab.py`)
   - Local product inspection
   - Band visualization (heatmaps)
   - RGB composite generation
   - Thumbnail grid view
   - JP2 to PNG conversion

---

### 3. **CLI Module** (`src/vresto/cli/`)
**Purpose**: Command-line interface for programmatic access

**Main Commands** (`main.py`):
- `search-name` - Search products by name pattern
- `download-quicklook` - Download preview images
- `download-metadata` - Download product metadata
- `download-bands` - Download specific bands with resolution control

**Implementation**:
- Built with `typer` (modern CLI framework)
- Outputs formatted results via `rich` library
- Supports batch operations

---

### 4. **Services Module** (`src/vresto/services/`)
**Purpose**: Core business logic and data operations

**Typical Responsibilities** (based on structure):
- Product lifecycle management
- Download orchestration
- Caching strategies
- S3 integration
- Local file management

---

### 5. **Products Module** (`src/vresto/products/`)
**Purpose**: Product representation and operations

**Typical Responsibilities**:
- Product metadata parsing
- Band availability checking
- Resolution management
- Download coordination with services

---

### 6. **Bands Module** (`src/vresto/bands/`)
**Purpose**: Band-level operations for satellite imagery

**Typical Responsibilities**:
- Band metadata (name, wavelength, resolution)
- Band filtering by type/resolution
- Band data access and caching

---

## Data Flow Diagram

### Search Flow (API → UI/CLI)
```
User Query (Map/Name/Filter)
    │
    ▼
┌─────────────────────────┐
│ CatalogSearch.search_*()│  Query Construction
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ OData/STAC API Request          │  Remote API Call
│ (auth via CopernicusConfig)     │
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Parse Results            │  Response Parsing
│ → ProductInfo objects    │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ UI/CLI Display           │  User Presentation
│ • Map overlay            │
│ • Results list           │
│ • Metadata preview       │
└──────────────────────────┘
```

### Download Flow (UI/CLI → Local Storage)
```
User Selection (Product + Bands)
    │
    ▼
┌──────────────────────────┐
│ ProductsManager          │  Download Manager
│ .download_bands()        │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Get S3 Credentials       │  Auth Module
│ (temporary or static)    │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Boto3 S3 Download        │  AWS SDK
│ Band data from bucket    │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Local File Storage       │  Filesystem
│ (GeoTIFF, JP2, etc.)     │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Analysis/Visualization   │  Local Processing
│ (matplotlib, rasterio)   │
└──────────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **API Integration** | `requests`, `pystac-client` | HTTP requests, STAC catalog parsing |
| **S3 Access** | `boto3` | AWS S3 data download |
| **Web UI** | `nicegui` | Modern web framework |
| **Maps** | `Leaflet` (JS), `localtileserver` | Interactive maps, tile serving |
| **Visualization** | `matplotlib`, `plotly`, `rasterio`, `Pillow` | Image rendering, geospatial processing |
| **Data Processing** | `numpy`, `rasterio` | Raster/band data manipulation |
| **CLI** | `typer`, `rich` | Command-line interface |
| **Configuration** | `python-dotenv` | Environment variable management |
| **Utilities** | `loguru`, `psutil` | Logging, system monitoring |
| **Build/Deploy** | `Docker`, `Docker Compose` | Containerization |

---

## Entry Points

### 1. **Web UI** (User-Friendly)
```bash
vresto
# or
python src/vresto/ui/app.py
# Runs NiceGUI server on http://localhost:8610
```

### 2. **CLI** (Programmatic)
```bash
vresto-cli search-name "S2A_MSIL2A"
vresto-cli download-bands <product_name> B04,B03,B02 --resolution 10
# etc.
```

### 3. **Python API** (Development)
```python
from vresto.api import CatalogSearch, CopernicusConfig
from vresto.products import ProductsManager

config = CopernicusConfig()
catalog = CatalogSearch(config=config)
manager = ProductsManager(config=config)

products = catalog.search_products_by_name("S2A_MSIL2A", max_results=5)
manager.download_product_bands(products[0].name, ["B04", "B03", "B02"])
```

### 4. **Docker** (Deployment)
```bash
docker compose up -d
# Runs at http://localhost:8610
```

---

## Key Design Patterns

### 1. **Separation of Concerns**
- **API Layer**: Handles authentication and discovery
- **Service Layer**: Business logic and data operations
- **UI Layer**: User-facing interfaces (web, CLI)
- **Product Layer**: Domain models and operations

### 2. **Modular Widgets** (UI)
- Each UI tab is a self-contained widget class
- Widgets communicate via callbacks (dependency injection)
- No global state (except configuration)
- Reusable across different interfaces

### 3. **DTO Pattern**
- `ProductInfo` objects encapsulate search results
- Clean API boundaries between layers
- Type-safe data transfer

### 4. **Dual Backend Support**
- Abstract search interface supporting both OData and STAC
- Configurable at runtime
- Enables flexibility and future extensibility

### 5. **Configuration Management**
- Environment-based configuration
- `.env` file support for development
- Static and temporary S3 credentials
- Masked logging for security

---

## Extension Points

### Adding a New Search Backend
1. Implement search in `api/catalog.py` alongside OData/STAC
2. Configure via `CopernicusConfig.search_provider`
3. Update search method dispatching logic

### Adding a New UI Tab
1. Create widget class in `ui/widgets/`
2. Implement `create()` method returning NiceGUI elements
3. Use callback injection for external interactions
4. Register in `ui/map_interface.py`

### Adding CLI Commands
1. Create function in `cli/main.py`
2. Decorate with `@app.command()` from typer
3. Use `ProductsManager` or `CatalogSearch` as needed

### Custom Visualization
1. Add visualization logic to `ui/visualization/`
2. Integrate into `ProductAnalysisTab` or new tab
3. Use matplotlib, plotly, or rasterio as appropriate

---

## Configuration & Deployment

### Environment Variables
```bash
COPERNICUS_USERNAME          # Copernicus Data Space username
COPERNICUS_PASSWORD          # Copernicus Data Space password
COPERNICUS_SEARCH_PROVIDER   # "odata" or "stac" (default: "odata")
COPERNICUS_S3_ACCESS_KEY     # Optional: static S3 access key
COPERNICUS_S3_SECRET_KEY     # Optional: static S3 secret key
COPERNICUS_S3_ENDPOINT       # Optional: custom S3 endpoint
VRESTO_BASE_TILE_PORT        # Tile server port (default: 8611)
```

### Docker Deployment
- `Dockerfile` defines container image
- `docker-compose.yml` orchestrates services
- `.env` file for credential injection
- Makefile provides convenient commands

---

## Summary

**Vresto** is a well-architected satellite data discovery and analysis tool with:
- **Clean separation** between API, service, and UI layers
- **Multiple access methods** (Web, CLI, Python API)
- **Modular UI design** with reusable widget components
- **Flexible authentication** and S3 credential management
- **Extensible backend** supporting multiple discovery protocols
- **Production-ready** deployment via Docker

The architecture supports both interactive exploration (web UI) and programmatic access (API/CLI), making it suitable for both end-users and developers.
