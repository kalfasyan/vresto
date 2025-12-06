# Using AWS CLI with Copernicus S3

This guide shows how to browse and download Copernicus satellite data directly using AWS CLI.

## Installation

### macOS
```bash
brew install awscli
```

### Verify installation
```bash
aws --version
```

## Configuration

### 1. Configure AWS CLI with your Copernicus S3 credentials

```bash
aws configure --profile copernicus
```

When prompted, enter your credentials from your `copernicus_s3.env` file:
- **AWS Access Key ID**: `<your access-key>`
- **AWS Secret Access Key**: `<your secret-key>`
- **Default region name**: `default` (or press Enter)
- **Default output format**: `json` (or press Enter)

### 2. Set the Copernicus S3 endpoint

The Copernicus S3 endpoint is different from standard AWS S3:
```bash
export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"
```

Add this to your `~/.zshrc` to make it permanent:
```bash
echo 'export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"' >> ~/.zshrc
source ~/.zshrc
```

## Basic Usage

### List available buckets
```bash
aws s3 ls --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

You should see the main bucket: `eodata`

### Browse Sentinel-2 data structure
```bash
# List collections
aws s3 ls s3://eodata/ --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT

# Browse Sentinel-2 products
aws s3 ls s3://eodata/Sentinel-2/ --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT

# Browse by date (example: 2024 data)
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/ --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT

# Browse specific month and day
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/ --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

### Search for products in a specific location

The S3 structure is organized by date, not location. To find products for a specific area, you'll need to:
1. Use the vresto app to search and find product names
2. Then download those specific products using AWS CLI

### Download a specific product

Once you have a product name from the vresto search (e.g., `S2A_MSIL2A_20241120T103321_N0511_R108_T31UFS_20241120T143314.SAFE`):

```bash
# Download entire product
aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/S2A_MSIL2A_20241120T103321_N0511_R108_T31UFS_20241120T143314.SAFE/ \
  ./downloads/S2A_MSIL2A_20241120T103321_N0511_R108_T31UFS_20241120T143314.SAFE/ \
  --recursive \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

### Download only specific files (faster)

```bash
# Download only the quicklook image (for preview)
aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT_NAME.SAFE/preview.jpg \
  ./preview.jpg \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT

# Download only specific bands (e.g., Band 4 - Red)
aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT_NAME.SAFE/GRANULE/*/IMG_DATA/R10m/*_B04_10m.jp2 \
  ./band4.jp2 \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

### List files in a product without downloading

```bash
aws s3 ls \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT_NAME.SAFE/ \
  --recursive \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

### Check product size before downloading

```bash
aws s3 ls \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT_NAME.SAFE/ \
  --recursive \
  --human-readable \
  --summarize \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

## Sentinel-2 Data Structure

```
s3://eodata/Sentinel-2/MSI/L2A/
├── 2024/
│   ├── 11/
│   │   ├── 20/
│   │   │   ├── S2A_MSIL2A_20241120T103321_*.SAFE/
│   │   │   │   ├── GRANULE/
│   │   │   │   │   └── L2A_*/
│   │   │   │   │       ├── IMG_DATA/
│   │   │   │   │       │   ├── R10m/  (10m resolution bands: B02, B03, B04, B08)
│   │   │   │   │       │   ├── R20m/  (20m resolution bands)
│   │   │   │   │       │   └── R60m/  (60m resolution bands)
│   │   │   │   ├── preview.jpg
│   │   │   │   └── MTD_MSIL2A.xml (metadata)
```

## Workflow: Search in vresto, Download with AWS CLI

### 1. Use vresto to find products
```bash
# Run the app
uv run python src/vresto/ui/map_interface.py
```

- Select location and date in the UI
- Search for products
- Note the product names you want

### 2. Download with AWS CLI
```bash
# Example: Download product found in vresto
PRODUCT="S2A_MSIL2A_20241120T103321_N0511_R108_T31UFS_20241120T143314.SAFE"
DATE_PATH="2024/11/20"

aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/${DATE_PATH}/${PRODUCT}/ \
  ./downloads/${PRODUCT}/ \
  --recursive \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

## Useful Aliases

Add these to your `~/.zshrc` for convenience:

```bash
# Copernicus S3 alias
alias s3cop='aws s3 --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT'

# Now you can use:
s3cop ls s3://eodata/
s3cop cp s3://eodata/path/to/file ./local/path
```

## Other Sentinel Collections

```bash
# Sentinel-1 (SAR data)
s3cop ls s3://eodata/Sentinel-1/

# Sentinel-3 (Ocean and land monitoring)
s3cop ls s3://eodata/Sentinel-3/

# Sentinel-5P (Atmospheric monitoring)
s3cop ls s3://eodata/Sentinel-5P/
```

## Tips

1. **Always check size first** - Sentinel-2 products can be 500MB - 1GB each
2. **Download selectively** - You often don't need all bands
3. **Use the vresto search first** - It's faster than browsing S3 by date
4. **Monitor bandwidth** - Downloads can be large

## Troubleshooting

### "Unable to locate credentials"
Make sure you've configured the profile:
```bash
aws configure --profile copernicus
```

### "Could not connect to the endpoint URL"
Check that the endpoint is set:
```bash
echo $COPERNICUS_ENDPOINT
# Should show: https://eodata.dataspace.copernicus.eu
```

### "Access Denied"
Verify your credentials are correct in:
```bash
cat ~/.aws/credentials
```

Look for the `[copernicus]` section with your access and secret keys.
