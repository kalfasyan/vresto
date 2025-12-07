# Getting Started with Vresto

A simple guide to set up and run the Copernicus satellite product browser.

## Prerequisites

- Python 3.9+
- `uv` package manager (install from https://github.com/astral-sh/uv)

## 1. Get Your Credentials

You need credentials to access Copernicus satellite data:

### Copernicus Username & Password
1. Go to https://dataspace.copernicus.eu/
2. Create an account or sign in
3. Save your email and password

### S3 Access Keys (Recommended)
Follow the official guide: https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration

See the "Registration" and "Generate secrets" sections to create your S3 credentials.

**Note:** If you don't provide S3 keys, the app will auto-generate temporary ones (which have usage limits).

## 2. Configure Environment

Create a `.env` file in the project root (or use environment variables):

```bash
# Required: Your Copernicus login
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password

# Optional but recommended: S3 access keys
COPERNICUS_S3_ACCESS_KEY=your_access_key
COPERNICUS_S3_SECRET_KEY=your_secret_key
```

### Alternative: Environment Variables

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
export COPERNICUS_S3_ACCESS_KEY="your_access_key"
export COPERNICUS_S3_SECRET_KEY="your_secret_key"
```

Add to `~/.zshrc` to make permanent.

## 3. Run the Application

```bash
# Install dependencies
uv sync

# Start the app
uv run python src/vresto/ui/map_interface.py
```

The application will open at `http://localhost:8080`

## 4. Using the App

1. **Select a date range** - Default is July 2020 (whole month)
2. **Choose product level** - L1C (raw), L2A (processed), or both
3. **Set cloud cover filter** - Max cloud coverage %
4. **Draw a location** - Click on the map to mark an area (default: Stockholm)
5. **Search** - Click "Search Products"
6. **View results** - Click "Quicklook" or "Metadata" on any product

## Troubleshooting

### "Credentials not configured" Error
Make sure your `.env` file exists and has `COPERNICUS_USERNAME` and `COPERNICUS_PASSWORD`

### "Max number of credentials reached" Error
This means temporary S3 credentials are maxed out. Solution: Add static S3 keys to `.env` (see section 2)

### "Quicklook not found" Error
Not all products have quicklooks. Try searching for different date ranges or locations.

## Next Steps

- Check the [README.md](../README.md) for more info
- See [CONTRIBUTING.md](../CONTRIBUTING.md) to contribute
- For detailed S3 API information: https://documentation.dataspace.copernicus.eu/APIs/S3.html
