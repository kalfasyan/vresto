# Catalog Providers: OData & STAC

vresto supports two primary backends for discovering satellite products from the Copernicus Data Space Ecosystem (CDSE): **OData** and **STAC**.

This guide explains the differences between them, how to switch providers, and provides examples for both API and CLI usage.

---

## 🧭 Overview

| Feature | OData (Mature) | STAC (Modern) |
| :--- | :--- | :--- |
| **Protocol** | Open Data Protocol (XML/JSON) | SpatioTemporal Asset Catalog |
| **Search Syntax** | Complex Filter Strings | JSON-based (CQL2) |
| **Granularity** | Product Level | Asset Level (Bands/Files) |
| **Speed** | Moderate | Fast / Optimized |
| **Best For** | General product discovery | Direct band access / Modern workflows |

---

## ⚙️ Configuration

By default, vresto uses **OData**. You can switch to **STAC** using environment variables or programmatic configuration.

### Environment Variable
```bash
export VRESTO_SEARCH_PROVIDER="stac"
```

### Programmatic
```python
from vresto.api import CopernicusConfig, CatalogSearch

config = CopernicusConfig(search_provider="stac")
catalog = CatalogSearch(config=config)
```

---

## 🔍 OData Support

OData is a robust, well-established protocol used by many European Space Agency (ESA) services.

### When to use OData:
- **Comprehensive Metadata**: If you require access to legacy CDSE metadata fields (like specific instrument parameters) not yet mapped to STAC.
- **Global Searches**: OData is highly optimized for text-based discovery across the entire catalog (e.g., searching by specific product name patterns).
- **Service Maturity**: Use OData if you prefer a battle-tested service that has been the standard for Copernicus data for many years.

### Example (API):
```python
from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig

catalog = CatalogSearch(config=CopernicusConfig(search_provider="odata"))
bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

products = catalog.search_products(
    bbox=bbox,
    start_date="2020-01-01",
    end_date="2020-01-31",
    collection="SENTINEL-2",
    product_level="L2A"
)
```

---

## ⚡ STAC Support

STAC is a modern community specification for geospatial metadata. It excels at describing individual files ("Assets") within a product.

### When to use STAC:
- **Cloud-Native Workflows**: Ideal for workflows that need to find and stream specific band URLs (S3) without downloading the entire SAFE product.
- **Spatial Granularity**: STAC is highly optimized for geographic queries and provides a cleaner, more standardized JSON structure.
- **Ecosystem Interoperability**: STAC items can be directly used with modern geospatial tools like `pystac`, `stackstac`, or QGIS.

### Example (API):
```python
from vresto.api import CatalogSearch, BoundingBox, CopernicusConfig

catalog = CatalogSearch(config=CopernicusConfig(search_provider="stac"))
bbox = BoundingBox(west=4.0, south=50.0, east=5.0, north=51.0)

# STAC search returns products with direct asset links
products = catalog.search_products(
    bbox=bbox,
    start_date="2020-01-01",
    end_date="2020-01-31",
    collection="SENTINEL-2",
    product_level="L2A",
    max_cloud_cover=10
)

# Accessing assets directly
for product in products:
    if product.assets:
        # Note: Some collections use resolution-specific names (e.g., 'B02_10m')
        blue_band_asset = product.assets.get('B02_10m') or product.assets.get('B02')
        if blue_band_asset:
            print(f"Blue band URL: {blue_band_asset['href']}")
```

---

## 🛠️ CLI Usage

The vresto CLI automatically respects the `VRESTO_SEARCH_PROVIDER` environment variable.

### Searching with OData (Default)
```bash
vresto-cli search-name "S2A_MSIL2A_2024" --max-results 5
```

### Searching with STAC
```bash
VRESTO_SEARCH_PROVIDER="stac" vresto-cli search-name "S2A_MSIL2A_2024" --max-results 5
```

---

## 🧩 Collection Mapping

Note that STAC uses more granular collection IDs. vresto handles this mapping automatically:

---

## 🚀 Performance Optimizations

vresto includes internal optimizations to ensure the best possible experience across different providers:

- **Hybrid Name Search**: Even when the `STAC` provider is selected, vresto internally uses `OData` for name-based searches (`search_products_by_name`). This is because CDSE STAC is currently much slower for global text-based queries, while OData is highly optimized for them.
- **Selective Collection Search**: When performing name searches via STAC (if OData is unavailable), vresto attempts to infer the correct collection from the product name to reduce search space.

---

## 🧩 Collection Mapping

Note that STAC uses more granular collection IDs. vresto handles this mapping automatically:

| vresto Collection | Level | STAC Collection ID |
| :--- | :--- | :--- |
| `SENTINEL-2` | `L1C` | `sentinel-2-l1c` |
| `SENTINEL-2` | `L2A` | `sentinel-2-l2a` |
| `SENTINEL-1` | `GRD` | `sentinel-1-grd` |
| `LANDSAT-8` | `L2` | `landsat-8-l2` |
