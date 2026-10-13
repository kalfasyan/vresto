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

Tile streaming does not need them. Without S3 keys, tiles stream from AWS Open Data (see **MGRS grid** under [Map Search](#map-search)).

Learn more in the [Setup Guide](../getting-started/setup.md).

## General Layout

**Header** — App title at the top, visible across all tabs

**Tabs** — Horizontal tab bar for different screens:
- 🗺️ **Map Search** — Search by drawing on a map
- 🛰️ **Hi-Res Tiler** — Interactive full-resolution visualization
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

**MGRS grid** (left sidebar, *Tile Streaming*)
- Zoomed out (below zoom 5), the grid shows the ~1,200 MGRS grid zones (6° by 8°, such as *31U*) so you can see the whole world at once. Each zone is coloured by the average products per tile per month of its tiles; click a zone to zoom in to its 100 km tiles
- Tiles appear from zoom 5. Each one is shaded by how many Sentinel-2 L2A products per month cover it in the selected dates: deeper blue means more products, and orange dashed tiles have none. Blue and orange stay distinct with red-green colour blindness. The legend under the switch shows the bins, and *Colour tiles by product count* turns the shading off
- Hover a tile to see its product count and best cloud cover, then click it to stream
- Counts come from the monthly MGRS tile statistics of [s2-stac-geoparquet](https://github.com/taylor-geospatial/s2-stac-geoparquet), a daily-refreshed mirror of the Earth Search index on Source Cooperative: one small public file per month, so every view colours almost at once and no account is needed. These statistics cover whole calendar months, so a 13 Sep – 13 Oct range counts all of September and October; the caption under the legend names the months
- If the statistics cannot be reached, counts fall back to one anonymous search of the CDSE catalogue over the view. There, a view with more than 2,000 products shows counts as ≥ N, and if CDSE rate-limits the searches the legend says so and the colours return after about a minute
- **Where tiles come from** (see [Choosing the tile streaming source](../getting-started/setup.md#choosing-the-tile-streaming-source)):
    - With CDSE S3 keys (☰ → Settings, or `COPERNICUS_S3_ACCESS_KEY` and `COPERNICUS_S3_SECRET_KEY`), tiles stream from CDSE, the authoritative source
    - Without them, tiles stream from AWS Open Data (Element84 Earth Search), anonymously. The sidebar says so, the chooser shows each scene's thumbnail, and each streamed tile is credited *Imagery via AWS Open Data (Element84 Earth Search)* in the activity log
    - AWS Open Data can lag CDSE by hours to about a day, and its coverage can differ slightly. The product counts on the grid come from a mirror of the same Earth Search index
    - `VRESTO_STREAM_SOURCE` overrides the choice: `auto` (default), `cdse` or `public`
    - Overlays that read CDSE S3 need S3 keys: land cover (LCM, Global LC 100m, Tree Cover Density), NDVI, FAPAR, Dry Matter Productivity, hourly LST, Soil Moisture, Soil Water Index, Water Bodies and Burned Area. WorldCover and the Copernicus DEM load without keys

**Reading overlay values** (left sidebar, *Overlays*)
- Click an MGRS grid tile to stream it, then switch on an overlay such as land cover, NDVI or land surface temperature
- Turn on **Inspect values** and click the tile to read the value under the cursor in a popup, with its position and the source resolution
- While it is on, clicking the map reads values instead of selecting tiles; turn it off to select tiles again
- The popup says *Outside the streamed tile* or *No data at this point* when there is nothing to read
- Coarse products (for example the 12.5 km Soil Water Index) are resampled onto the tile grid, so neighbouring pixels share one value
- The switch is disabled until an overlay has loaded, and turns off when you change the overlay, its year or time, or the tile

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

**Band-Resolution Matrix** (left side, below product)
- A granular grid showing all available band/resolution combinations on S3
- Checkboxes to select specific (band, resolution) pairs
- **Select All** / **Deselect All** — Quick bulk actions
- **Select all 10m / 20m / 60m** — Efficiency buttons to select all bands of a specific resolution
- **Destination Folder** — Where to save downloads
- **Download** button (automatically de-duplicates files if overlapping resolutions are selected)

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
- List of discovered products (stripped of `.SAFE` for readability)
- Scrollable list with **Inspect** action
- Selection populates preview area

**Preview & Bands** (right side)
- **Band Availability Grid**: A clear matrix showing exactly which resolutions (10m, 20m, 60m) are locally available for each band
- Single-band selector
- RGB composite builder
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

### High-Resolution Visualization (Hi-Res Tiler)
For full-resolution inspection, use the **Hi-Res Tiler** tab:
1. Select a product from your local downloads.
2. Select a preferred resolution (10m, 20m, 60m).
3. Toggle individual bands to instantly see them rendered as a map layer.
4. Use the map to zoom in and inspect fine details at the native resolution of the satellite instrument.

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
