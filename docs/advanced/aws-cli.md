# AWS CLI Quick Reference

Directly browse and download Copernicus Sentinel-2 data via S3 using AWS CLI.

## Setup

### 1. Install AWS CLI

=== "macOS"
    ```bash
    brew install awscli
    aws --version
    ```

=== "Linux"
    ```bash
    sudo apt-get install awscli
    aws --version
    ```

=== "Windows"
    ```powershell
    choco install awscli
    aws --version
    ```

### 2. Configure Credentials

```bash
aws configure --profile copernicus
```

When prompted, enter:
- **AWS Access Key ID**: Your S3 access key (from [Copernicus Dataspace](https://dataspace.copernicus.eu/))
- **AWS Secret Access Key**: Your S3 secret key
- **Default region**: Leave blank or use `default`
- **Default output format**: `json`

### 3. Set Copernicus Endpoint

```bash
export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"
```

Add to `~/.zshrc` to make permanent:

```bash
echo 'export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"' >> ~/.zshrc
source ~/.zshrc
```

## Common Commands

### List Buckets

```bash
aws s3 ls --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

Output:
```
2024-01-01 00:00:00 eodata
```

### Browse by Date

List products from a specific date:

```bash
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/ \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

Navigate the hierarchy:
- `s3://eodata/Sentinel-2/MSI/L1C/YYYY/MM/DD/` - Raw L1C data
- `s3://eodata/Sentinel-2/MSI/L2A/YYYY/MM/DD/` - Processed L2A data

### Download a Product

Download entire product directory:

```bash
PRODUCT="S2A_MSIL2A_20241120T103321_*.SAFE"
aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/${PRODUCT}/ \
  ./downloads/${PRODUCT}/ \
  --recursive \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

### Download Specific Files Only (Faster)

Only download the 10m resolution RGB bands:

```bash
aws s3 cp \
  s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/S2A_MSIL2A_20241120T103321_N0510_R131_T32UPE_20241120T110130.SAFE/GRANULE/L2A_T32UPE_A042706_20241120T104457_N0510_R131_T32UPE_20241120T110130_MTL.xml \
  ./metadata.xml \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

## Advanced Usage

### Batch Download Script

Create `download_sentinel.sh`:

```bash
#!/bin/bash

ENDPOINT="https://eodata.dataspace.copernicus.eu"
PROFILE="copernicus"
OUTPUT_DIR="./downloads"

# Search for all L2A products from a specific date
YEAR=2024
MONTH=11
DAY=20

aws s3 ls "s3://eodata/Sentinel-2/MSI/L2A/$YEAR/$MONTH/$DAY/" \
  --profile $PROFILE \
  --endpoint-url $ENDPOINT | \
  awk '{print $NF}' | \
  while read PRODUCT; do
    if [ ! -z "$PRODUCT" ]; then
      echo "Downloading: $PRODUCT"
      aws s3 cp \
        "s3://eodata/Sentinel-2/MSI/L2A/$YEAR/$MONTH/$DAY/$PRODUCT" \
        "$OUTPUT_DIR/$PRODUCT" \
        --recursive \
        --profile $PROFILE \
        --endpoint-url $ENDPOINT
    fi
  done
```

Run:
```bash
chmod +x download_sentinel.sh
./download_sentinel.sh
```

### Find Low Cloud Cover Products

```bash
# List all products and check metadata
for PRODUCT in $(aws s3 ls s3://eodata/Sentinel-2/MSI/L2A/2024/11/20/ \
  --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT | \
  awk '{print $NF}' | grep -o "S2.*SAFE"); do
  
  echo "Checking: $PRODUCT"
  # Download metadata to check cloud cover
done
```

## Troubleshooting

### "Unable to locate credentials"

Check your AWS configuration:

```bash
aws configure list --profile copernicus
```

Should show your access key and credentials.

### "Access Denied"

- Verify S3 credentials are correct
- Check your Copernicus Dataspace S3 permissions
- Ensure you have the correct endpoint URL

### "NoSuchKey" Error

The product path doesn't exist. Check:
- Date is correct (YYYY/MM/DD format)
- Product exists in that date range
- Product level (L1C vs L2A) is correct

### Slow Downloads

- Use `--no-progress` flag to reduce overhead
- Download specific files instead of entire products
- Check your internet connection speed

## Product Structure

Each Sentinel-2 product contains:

```
S2A_MSIL2A_20241120T103321_N0510_R131_T32UPE_20241120T110130.SAFE/
├── GRANULE/
│   └── L2A_T32UPE_A042706_20241120T104457_N0510_R131_T32UPE_20241120T110130_MTL.xml
├── INSPIRE.xml
├── MTD_MSIL2A.xml
├── manifest.safe
└── rep_info/

# Key files:
# - TCI_10m.jp2   : RGB preview (10m resolution)
# - B02_10m.jp2   : Blue band (10m)
# - B03_10m.jp2   : Green band (10m)
# - B04_10m.jp2   : Red band (10m)
```

## Next Steps

- [API Reference](../user-guide/api.md) - Programmatic access
- [Web Interface Guide](../user-guide/web-interface.md) - Visual search
- [Setup Guide](../getting-started/setup.md) - Configuration
