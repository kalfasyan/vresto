# Command-Line Interface (CLI)

Search and download Copernicus Sentinel satellite data from the terminal with ease.

## Setup

Get free Copernicus credentials at [https://dataspace.copernicus.eu/](https://dataspace.copernicus.eu/), then configure:

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
```

Or create a `.env` file:
```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
```

### ⚠️ Important: S3 Credentials

**Without static S3 credentials, vresto will auto-generate temporary credentials with strict usage limits.** These temporary credentials have limited quotas and will be exhausted quickly with large downloads.

To avoid hitting quota restrictions, it's highly recommended to request your own permanent S3 credentials from [Copernicus Dataspace](https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration) and add them to your environment:

```bash
export COPERNICUS_S3_ACCESS_KEY="your_access_key"
export COPERNICUS_S3_SECRET_KEY="your_secret_key"
```

Then validate your setup:
```bash
vresto-cli validate-credentials
```

## Quick Examples

```bash
# 🔍 Search for products
vresto-cli search-name "S2A_MSIL2A_20200612" --max-results 5

# 📸 Download quicklook (preview image)
vresto-cli download-quicklook "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" --output ./quicklooks

# 📋 Download metadata
vresto-cli download-metadata "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" --output ./metadata

# 🎨 Download specific bands at 10m resolution
vresto-cli download-bands "S2A_MSIL2A_20200612T023601_N0500_R089_T50NKJ_20230327T190018" "B04,B03,B02" --resolution 10 --output ./data
```

## All Commands

| Command | Description |
|---------|-------------|
| `search-name PATTERN` | Find products by name pattern |
| `download-quicklook PRODUCT` | Download preview image |
| `download-metadata PRODUCT` | Download XML metadata |
| `download-bands PRODUCT BANDS` | Download spectral bands (e.g., "B04,B03,B02") |
| `validate-credentials` | Test your credentials |

## Command Options

### search-name
- `--max-results N` — Limit number of results (default: 10)
- `--match-type` — Search mode: `contains`, `startswith`, `endswith`, `eq` (default: contains)
- `-v, --verbose` — Show S3 paths for direct AWS CLI access

### download-quicklook, download-metadata
- `-o, --output DIR` — Save to directory (default: current directory)

### download-bands
- `--resolution RES` — Output resolution: 10, 20, 60, or 'native' (default)
- `--resample` — Resample to target resolution
- `-o, --output DIR` — Save to directory (default: ./data)

## Tips

- Use `-v` flag with `search-name` to get S3 paths for AWS CLI access
- For more details, run: `vresto-cli COMMAND --help`
- See the [API Guide](../user-guide/api.md) for programmatic usage
