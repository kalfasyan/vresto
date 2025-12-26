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

### 3. Configure the Custom Endpoint (IMPORTANT)

Copernicus uses a **custom S3-compatible endpoint** that requires explicit configuration.

**Environment Variable (Recommended):**

```bash
export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"
```

Make it permanent by adding to `~/.zshrc` (or `~/.bashrc`):

```bash
echo 'export COPERNICUS_ENDPOINT="https://eodata.dataspace.copernicus.eu"' >> ~/.zshrc
source ~/.zshrc
```

**Or configure in AWS Config file:**

Edit `~/.aws/config` and add the endpoint to your copernicus profile:

```ini
[profile copernicus]
region = default
s3 =
    endpoint_url = https://eodata.dataspace.copernicus.eu
    signature_version = s3v4
```

**Note:** AWS CLI's `aws s3` commands don't automatically use the endpoint from the config file, so you'll need to add `--endpoint-url $COPERNICUS_ENDPOINT` to each command (see examples below).

## Common Commands

### List Buckets

```bash
aws s3 ls --profile copernicus --endpoint-url $COPERNICUS_ENDPOINT
```

Output:
```
2024-01-01 00:00:00 eodata
```

### Browse Available Product Levels

First, check the available processing levels and versions:

```bash
aws s3 ls s3://eodata/Sentinel-2/MSI/ \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

This shows available product levels and versions:
- `L1C/` - Raw L1C data (older processing)
- `L1C_N0500/` - Raw L1C data (latest processing) ⭐
- `L2A/` - Processed L2A data (older processing)
- `L2A_N0400/` - Processed L2A data (intermediate processing)
- `L2A_N0500/` - Processed L2A data (latest processing) ⭐

**Note:** The `_N0500` versions contain the latest reprocessed products and are recommended for most use cases (used by vresto by default).

### Browse by Date

List products from a specific date (using L2A_N0500 as example):

```bash
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A_N0500/2024/11/20/ \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

Navigate the hierarchy:
- `s3://eodata/Sentinel-2/MSI/L1C_N0500/YYYY/MM/DD/` - Raw L1C data (latest)
- `s3://eodata/Sentinel-2/MSI/L2A_N0500/YYYY/MM/DD/` - Processed L2A data (latest)

### Download a Product

List files at a specific image resolution directory (using the latest _N0500 version):

```bash
aws s3 ls s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/01/01/S2B_MSIL2A_20200101T234739_N0500_R130_T57MUM_20230425T005858.SAFE/GRANULE/L2A_T57MUM_A014742_20200101T234736/IMG_DATA/R60m/ \
  --profile copernicus \
  --endpoint-url $COPERNICUS_ENDPOINT
```

Output:
```
2023-10-25 12:42:54      51799 T57MUM_20200101T234739_AOT_60m.jp2
2023-10-25 12:42:54    1316139 T57MUM_20200101T234739_B01_60m.jp2
2023-10-25 12:42:54    1436599 T57MUM_20200101T234739_B02_60m.jp2
2023-10-25 12:42:54    1421840 T57MUM_20200101T234739_B03_60m.jp2
2023-10-25 12:42:54    1409459 T57MUM_20200101T234739_B04_60m.jp2
2023-10-25 12:42:54    1449715 T57MUM_20200101T234739_B05_60m.jp2
2023-10-25 12:42:55    1429981 T57MUM_20200101T234739_B06_60m.jp2
2023-10-25 12:42:55    1421915 T57MUM_20200101T234739_B07_60m.jp2
2023-10-25 12:42:53    1415903 T57MUM_20200101T234739_B09_60m.jp2
2023-10-25 12:42:53    1360282 T57MUM_20200101T234739_B11_60m.jp2
2023-10-25 12:42:53    1381005 T57MUM_20200101T234739_B12_60m.jp2
2023-10-25 12:42:56    1416344 T57MUM_20200101T234739_B8A_60m.jp2
2023-10-25 12:42:54     172926 T57MUM_20200101T234739_SCL_60m.jp2
2023-10-25 12:42:54    1448976 T57MUM_20200101T234739_TCI_60m.jp2
2023-10-25 12:42:54      54170 T57MUM_20200101T234739_WVP_60m.jp2
```

Once you've identified the files you need, use `aws s3 cp` with the `--recursive` flag to download them.

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
