# Installation & Setup

Getting your Copernicus credentials and setting up vresto.

## Prerequisites

- Python 3.11+
- `uv` package manager ([install from GitHub](https://github.com/astral-sh/uv))

## Step 1: Get Your Credentials

Searching the Copernicus catalogues works without an account, and so does tile streaming: without S3 keys, tiles stream from AWS Open Data. You need Copernicus credentials to download products, and S3 keys to stream tiles from CDSE.

### Copernicus Username & Password

1. Visit [Copernicus Dataspace](https://dataspace.copernicus.eu/)
2. Create an account or sign in
3. Save your email and password

### S3 Access Keys (Recommended)

For better performance and higher quotas, generate static S3 credentials. With them, tiles stream from CDSE, the authoritative source. Without them, tiles stream from AWS Open Data (see [Choosing the tile streaming source](#choosing-the-tile-streaming-source)).

1. Log in to [Copernicus Dataspace](https://dataspace.copernicus.eu/)
2. Navigate to your account settings
3. Follow the official guide: [S3 Registration](https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration)
4. Create your S3 credentials (access key + secret key)

⚠️ **Important:** If you skip this step, vresto will auto-generate temporary S3 credentials with **strict usage limits**. These temporary credentials have limited quotas and will be exhausted quickly if you perform large downloads. To avoid hitting quota restrictions, it's highly recommended to request your own permanent S3 credentials from Copernicus Dataspace (see link above).

## Step 2: Configure Environment

Choose one method:

### Option A: Web Interface (Easiest for S3 Credentials)

Once you've started the web interface:

1. Set your Copernicus credentials in environment variables or .env file (`COPERNICUS_USERNAME` and `COPERNICUS_PASSWORD` are needed for downloads; search works without them)
2. Launch the app with `make docker-up`, `make app`, or `python src/vresto/ui/app.py`
3. Open `http://localhost:8610` in your browser
4. Click the **menu** button (≡) in the top-left corner to open the settings drawer
5. Scroll to the **S3 Credentials** section
6. Enter your S3 Access Key ID and S3 Secret Key
7. Click **Save Credentials**

The credentials are saved to your `.env` file and used immediately. This is convenient if you want to add S3 credentials after initial setup without editing files manually.

### Option B: Environment Variables (Recommended)

```bash
export COPERNICUS_USERNAME="your_email@example.com"
export COPERNICUS_PASSWORD="your_password"
export COPERNICUS_S3_ACCESS_KEY="your_access_key"      # Optional
export COPERNICUS_S3_SECRET_KEY="your_secret_key"      # Optional
```

To make permanent, add to `~/.zshrc`:

```bash
echo 'export COPERNICUS_USERNAME="your_email@example.com"' >> ~/.zshrc
source ~/.zshrc
```

### Option C: .env File

Create `.env` in your project root:

```bash
COPERNICUS_USERNAME=your_email@example.com
COPERNICUS_PASSWORD=your_password
COPERNICUS_S3_ACCESS_KEY=your_access_key
COPERNICUS_S3_SECRET_KEY=your_secret_key
```

vresto automatically loads this file on startup.

### Choosing the Tile Streaming Source

Clicking an MGRS tile streams its true-colour image from one of two sources:

| Source | Used when | Needs an account |
|---|---|---|
| **CDSE** (Copernicus Data Space) | Static S3 keys are set | Yes, S3 keys |
| **AWS Open Data** (Element84 Earth Search) | No static S3 keys are set | No |

Set `VRESTO_STREAM_SOURCE` to change the choice:

- `auto` (default): CDSE when S3 keys are set, AWS Open Data otherwise. This is checked on every click, so keys added in the web interface take effect without a restart.
- `cdse`: always CDSE. Tiles need S3 keys.
- `public`: always AWS Open Data, even when S3 keys are set.

```bash
export VRESTO_STREAM_SOURCE=public   # or cdse, or auto (default)
```

AWS Open Data imagery can lag CDSE by hours to about a day, and its coverage can differ slightly. CDSE remains the authoritative source. Each tile streamed from AWS Open Data is credited in the activity log.

## Step 3: Install vresto

### From PyPI

```bash
pip install vresto
# or with uv
uv pip install vresto
```

### From Source

```bash
git clone https://github.com/kalfasyan/vresto.git
cd vresto
uv sync --extra dev
```

## Troubleshooting

### "Credentials not configured" Error

Make sure your credentials are set in environment variables or `.env` file:

```bash
env | grep COPERNICUS
```

You should see `COPERNICUS_USERNAME` and `COPERNICUS_PASSWORD`.

### "Max number of credentials reached" Error

This means temporary S3 credentials are exhausted. Solution: Add static S3 keys to your environment (see Step 2).

### Tiles come from AWS Open Data even though S3 keys are set

`VRESTO_STREAM_SOURCE` is probably set to `public`. Unset it, or set it to `auto` or `cdse`. Check the keys with `env | grep COPERNICUS_S3`.

### API Connection Issues

- Verify your credentials are correct on [Copernicus Dataspace](https://dataspace.copernicus.eu/)
- Check your internet connection
- Try accessing the S3 endpoint directly: `https://eodata.dataspace.copernicus.eu`

## Next Steps

- Check out the [Quick Start](quickstart.md) guide
- Read the [Web Interface Guide](../user-guide/web-interface.md)
- Or dive into the [API Reference](../user-guide/api.md)
- Visit the main [GitHub repository](https://github.com/kalfasyan/vresto)
