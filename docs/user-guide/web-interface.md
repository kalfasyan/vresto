# Web Interface Guide

A professional, interactive map interface for searching Copernicus Sentinel satellite data with visual filters and instant results.

## Launch the Interface

```bash
make app
```

Or directly:

```bash
python src/vresto/ui/app.py
```

Opens at `http://localhost:8610` in your browser.

## ⚠️ Important: S3 Credentials Setup

**Without static S3 credentials, vresto will auto-generate temporary credentials with strict usage limits.** These temporary credentials have limited quotas and will be exhausted quickly with large downloads.

To avoid hitting quota restrictions, it's highly recommended to:

1. Request your own permanent S3 credentials from [Copernicus Dataspace](https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration)
2. Configure them in your environment or `.env` file:
   ```bash
   export COPERNICUS_S3_ACCESS_KEY="your_access_key"
   export COPERNICUS_S3_SECRET_KEY="your_secret_key"
   ```
3. Or add them via the web interface (see **Settings → S3 Credentials** below)

Learn more in the [Setup Guide](../getting-started/setup.md).

## General Layout

**Header** — App title at the top, visible across all tabs

**Tabs** — Horizontal tab bar for different screens:
- 🗺️ **Map Search** — Search by drawing on a map
- 🔍 **Search by Name** — Find products by name pattern
- 📥 **Download Product** — Get bands and metadata
- 📊 **Product Analysis** — Inspect local downloads
- **Settings** — Credentials and configuration

**Three-Column Layout** — Most tabs use:
- **Left sidebar** — Controls and activity log
- **Center** — Main interactive area (map, list, or preview)
- **Right sidebar** — Results, details, or preview panel

## Map Search

Visually search for products by drawing on an interactive map.

**Date Selector** (top-left)
- Single date or date range
- Default: recent dates

**Activity Log** (left sidebar)
- Real-time search status
- Download progress
- Errors and confirmations

**Interactive Map** (center)
- Click or draw to define search area
- Supports point and polygon selection
- Pan and zoom

**Search Controls** (top-right)
- **Collection**: Sentinel-2 (or other available)
- **Product Level**: L1C (raw) or L2A (corrected, recommended)
- **Cloud Cover**: Slider 0-100%, default 20%
- **Max Results**: Limit returned products
- **Search** button

**Results** (right panel, below controls)
- Product name and sensing date
- Size and cloud cover percentage
- 📸 **Quicklook** — Preview image
- 📋 **Metadata** — Detailed product info

**Notifications** — Brief alerts near top for status updates

## Search by Name

Text-based search for products by name or pattern.

**Search Input** (left side)
- Enter product name or partial name
- Supports wildcards and patterns

**Activity Log** (left, below input)
- Search history and results count
- Filtering status

**Results Panel** (center/right)
- Summary: Total results and filtered count
- Scrollable product list
- Same product cards as Map Search:
  - Name, sensing date, size, cloud cover
  - 📸 Quicklook button
  - 📋 Metadata button

**Quick Actions**
- Quicklook opens in modal dialog
- Metadata displays in scrollable window

## Download Product

Fetch specific spectral bands from products for analysis.

**Product Input** (left side)
- Enter product name or S3 path
- **Fetch Bands** button discovers available bands

**Band Selection** (left side, below product)
- Checkboxes for individual bands
- **Resolution** dropdown: Native, 60m, 20m, 10m
- **Select All** — Mark all bands
- **Select by Resolution** — Quick filters (10m, 20m, 60m)
- **Destination Folder** — Where to save downloads
- **Download** button

**Activity Log & Progress** (right side)
- Real-time download status for each file
- Progress bar and completion counter
- Error messages and retry hints

## Product Analysis (Local)

Inspect and visualize products you've already downloaded.

**Folder Scanner** (left side)
- Enter path to local download folder
- **Scan** button to discover products
- Text filter to narrow results

**Product List** (center)
- Dropdown of discovered products
- Scrollable list with **Inspect** action
- Selection populates preview area

**Preview & Bands** (right side)
- Available bands for selected product
- Single-band selector
- RGB composite builder
- Resolution hints
- **Preview** button to generate visualization
- In-browser preview area (heatmap, RGB, or band thumbnails)

## Tips & Workflows

### Find products by location
1. Open "Map Search"
2. Draw or click on the map
3. Set date range and filters
4. Press "Search Products"
5. Browse results and preview

### Find products by name
1. Open "Search by Name"
2. Type product name or pattern
3. Press search
4. View results and open quicklooks or metadata

### Download and analyze
1. Open "Download Product"
2. Enter product name or S3 path
3. Select bands and resolution
4. Set destination and press Download
5. Monitor progress on the right

### Inspect local files
1. Open "Product Analysis"
2. Point to a download folder and scan
3. Select product from list
4. Use preview controls to visualize bands

### Best Practices

- **Location searches** — Draw or mark a location before searching
- **Date ranges** — Use shorter ranges for faster results
- **Cloud cover** — Lower values = clearer images but fewer options
- **Preview resolution** — In-browser previews are optimized for lower resolution; use external tools for full-resolution analysis
- **Seasonal patterns** — Tropical regions have more clouds in rainy season; polar regions have limited winter daylight

## Keyboard & Controls

- **Map interactions** — Scroll to zoom, click-drag to pan
- **Notifications** — Brief confirmations appear at top
- **Modals** — Quicklooks and metadata open in dialog windows
- **Activity logs** — Scroll to review history in each tab

## Next Steps

- [CLI Guide](cli.md) — Command-line search and download
- [API Reference](api.md) — Programmatic access and automation
- [AWS CLI Guide](../advanced/aws-cli.md) — Direct S3 access for developers
