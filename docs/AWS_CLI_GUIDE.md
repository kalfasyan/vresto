# AWS CLI Quick Reference

Use AWS CLI to directly browse and download Copernicus Sentinel-2 data via S3.

## Setup

### 1. Install AWS CLI
```bash
brew install awscli
aws --version
```

### 2. Configure credentials
```bash
aws configure --profile copernicus
# Enter your S3 credentials (from SETUP.md)
# Region: default | Output: json
```

### 3. Set endpoint
```bash
export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"
```

Add to `~/.zshrc` to make permanent.

## Common Commands

### List buckets
```bash
aws s3 ls --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

### Browse products by date
```bash
# Browse by year/month/day
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/ \
  --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

### Download a product
```bash
PRODUCT="S2A_MSIL2A_20241120T103321_*.SAFE"
aws s3 cp s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/${PRODUCT}/ \
  ./downloads/${PRODUCT}/ --recursive \
  --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

### Download specific files only (faster)
```bash
# Quicklook
aws s3 cp s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT.SAFE/preview.jpg \
  ./preview.jpg --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT

# Specific band (e.g., Band 4 - Red)
aws s3 cp \
  "s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT.SAFE/GRANULE/*/IMG_DATA/R10m/*_B04_10m.jp2" \
  ./band4.jp2 --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

### Check file sizes
```bash
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/PRODUCT.SAFE/ \
  --recursive --human-readable --summarize \
  --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

## Workflow: Use vresto, Download with CLI

1. **Search in vresto** - Get product name from UI:
   ```bash
   uv run python src/vresto/ui/map_interface.py
   ```

2. **Download with AWS CLI** - Use product name and date path found in vresto

## Helpful Alias

Add to `~/.zshrc`:
```bash
alias s3cop='aws s3 --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT'
```

Then use:
```bash
s3cop ls s3://eodata/Sentinel-2/
s3cop cp s3://eodata/path/ ./local/ --recursive
```

## Data Structure

```
s3://eodata/Sentinel-2/MSI/L2A/YYYY/MM/DD/PRODUCT.SAFE/
├── GRANULE/L2A_*/IMG_DATA/
│   ├── R10m/  (10m bands: B02, B03, B04, B08)
│   ├── R20m/  (20m bands)
│   └── R60m/  (60m bands)
├── preview.jpg
└── MTD_MSIL2A.xml
```

## Tips

- **Vresto first** - Use the app to find products faster than browsing S3 by date
- **Check size** - Products are 500MB-1GB; list first with `--summarize`
- **Download selectively** - Download only needed bands, not the whole product
- **Use aliases** - Reduces typing for frequently-used commands

## Other Sentinel Collections

- `s3://eodata/Sentinel-1/` - SAR data
- `s3://eodata/Sentinel-3/` - Ocean/land monitoring  
- `s3://eodata/Sentinel-5P/` - Atmospheric monitoring

## Troubleshooting

**"Unable to locate credentials"** - Run `aws configure --profile copernicus`

**"Could not connect to endpoint"** - Check endpoint: `echo $COPERNICUS_ENDPOINT`

**"Access Denied"** - Verify credentials in `~/.aws/credentials` under `[copernicus]`
