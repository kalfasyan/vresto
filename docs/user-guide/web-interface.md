# Web Interface Guide

A visual guide to using vresto's interactive map interface.

## Starting the Interface

```bash
make app
```

Or directly:

```bash
python src/vresto/ui/app.py
```

Opens at `http://localhost:8610` in your browser.

## Interface Layout

### Settings & Credentials (Top-Left Menu)

Click the **menu** button (≡) in the top-left corner to open the settings drawer.

**S3 Credentials Section**
- View your current S3 credentials status
- Enter new S3 Access Key ID and Secret Key
- **Save Credentials** button to persist to `.env` file
- **Clear** button to remove entered values
- Helpful information about temporary vs. static credentials

This is useful for:
- Adding S3 credentials after initial setup
- Updating credentials without editing files
- Checking if credentials are properly configured

### Search Panel (Left Side)

**Date Range**
- Set start and end dates for your search
- Default: July 2020 (entire month)
- Supported: Any date range with Sentinel-2 data

**Product Level**
- **L1C** - Raw, unprocessed data
- **L2A** - Atmospherically corrected data (recommended)
- **Both** - Include all levels

**Cloud Cover Filter**
- Slider: 0-100%
- Default: 20%
- Lower values = clearer images

### Map Panel (Center)

**Drawing Tools**
- Click on the map to select your area of interest
- Default location: Stockholm, Sweden
- Polygon selection available

**Result Display**
- Products appear as clickable items
- Shows product name and acquisition date
- Quick access buttons for quicklook and metadata

### Results Panel

For each product:

**Quicklook**
- Click "Quicklook" to view a preview image
- JPEG preview of the satellite data
- Shows clouds, terrain, and water bodies

**Metadata**
- Click "Metadata" for detailed information
- Includes:
  - Product ID and timestamp
  - Cloud coverage percentage
  - Processing level
  - Acquisition mode
  - Orbit details

## Typical Workflow

1. **Define your area** - Click on the map to select a region (or use default)
2. **Set date range** - Adjust start and end dates
3. **Configure filters**:
   - Choose product level (usually L2A)
   - Set maximum cloud cover (e.g., 20%)
4. **Search** - Click "Search Products"
5. **Review results** - Browse the list with dates and cloud cover
6. **Preview data**:
   - Click "Quicklook" for a visual preview
   - Click "Metadata" for technical details
7. **Export/Download** - Use the API for batch downloads (see [API Reference](api.md))

## Tips & Tricks

- **Large area searches** - Use broader date ranges if results are sparse
- **Cloud cover** - Lower percentages = better quality, but fewer options
- **Time of day** - Polar regions (north) have limited lighting in winter
- **Wet season** - Tropical regions have more clouds in rainy seasons
- **Archive data** - Sentinel-2 has data going back to 2015

## Troubleshooting

### No results found

- Try expanding the date range
- Increase cloud cover tolerance
- Select a larger area on the map
- Verify your location has satellite coverage

### Quicklook not available

- Not all products have quicklooks
- Try a different date range
- Some products may be processing

### Slow performance

- Reduce the search date range
- Select a smaller area on the map
- Clear browser cache and refresh

## Next Steps

- [Programmatic API Guide](api.md) - Automate searches and downloads
- [AWS CLI Guide](../advanced/aws-cli.md) - Direct S3 access
