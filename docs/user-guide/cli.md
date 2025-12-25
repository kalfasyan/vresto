# Command-Line Interface (CLI)

vresto CLI allows you to search and download Copernicus Sentinel satellite data from the terminal with ease.

## Quick Setup

### 1. Get Credentials

Get free access at [https://dataspace.copernicus.eu/](https://dataspace.copernicus.eu/)

### 2. Set Environment Variables

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

Or save to a `.env` file in the current directory:
```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
```

### 3. Check Installation

```bash
vresto-cli --help
```

### 4. Validate Credentials (Optional)

```bash
vresto-cli validate-credentials
```

Expected output:
```
✅ Credentials are valid
```

## Usage Examples

### Search for Products

```bash
# Search for Sentinel-2 Level 2A products
vresto-cli search-name "S2A_MSIL2A"

# See S3 paths for AWS CLI (verbose mode)
vresto-cli search-name "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" -v

# Search with results limit
vresto-cli search-name "S2A" --max-results 5
```

### Download Quicklook (Preview Image)

```bash
vresto-cli download-quicklook "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018"

# Save to specific directory
vresto-cli download-quicklook "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" --output ./quicklooks
```

### Download Metadata

```bash
vresto-cli download-metadata "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018"
```

### Download Spectral Bands

```bash
# Download RGB bands
vresto-cli download-bands "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" "B04,B03,B02"

# Download at 10m resolution
vresto-cli download-bands "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" "B04,B03,B02" --resolution 10
```

## All Commands

| Command | Purpose |
|---------|---------|
| `search-name PATTERN` | Find products by name |
| `download-quicklook PRODUCT_NAME` | Download preview image |
| `download-metadata PRODUCT_NAME` | Download XML metadata |
| `download-bands PRODUCT BANDS` | Download spectral bands |
| `validate-credentials` | Check if credentials work |

## Options

### search-name
- `--match-type`: `contains` (default), `startswith`, `endswith`, `eq`
- `--max-results`: Number of results (default: 10)
- `-v, --verbose`: Show S3 paths for AWS CLI access

### download-quicklook, download-metadata
- `-o, --output`: Output directory

### download-bands
- `--resolution`: 10, 20, 60, or 'native' (default)
- `--resample`: Resample to target resolution
- `-o, --output`: Output directory (default: ./data)

## Tips

- Use `-v` flag with `search-name` to see full S3 paths for AWS CLI
- Products are sorted by date (newest first)
- Use `vresto-cli COMMAND --help` for detailed help
