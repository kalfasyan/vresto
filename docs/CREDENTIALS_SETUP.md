# Setting Up Copernicus Credentials

There are **three ways** to configure your Copernicus credentials:

## Option 1: Create a `.env` file (RECOMMENDED)

1. Copy the example file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your credentials:
   ```bash
   COPERNICUS_USERNAME=your_actual_username
   COPERNICUS_PASSWORD=your_actual_password
   ```

3. The app will automatically load these credentials ✅

**Note**: The `.env` file is gitignored and won't be committed.

## Option 2: Export environment variables in your terminal

```bash
export COPERNICUS_USERNAME="your_username"
export COPERNICUS_PASSWORD="your_password"
```

**Important**: These only work in the current terminal session. To make them permanent, add them to your `~/.zshrc`:

```bash
# Add to ~/.zshrc
export COPERNICUS_USERNAME="your_username"
export COPERNICUS_PASSWORD="your_password"
```

Then reload your shell:
```bash
source ~/.zshrc
```

## Option 3: Pass credentials directly in code

```python
from vresto.api import CopernicusConfig, CatalogSearch

config = CopernicusConfig(
    username="your_username",
    password="your_password"
)
catalog = CatalogSearch(config=config)
```

## Getting Credentials

If you don't have credentials yet:
1. Go to https://dataspace.copernicus.eu/
2. Click "Register"
3. Create a free account
4. Use those credentials in one of the methods above

## Testing Your Setup

Run this to verify your credentials work:

```bash
uv run python -c "from vresto.api import CopernicusConfig; c = CopernicusConfig(); print('✅ Credentials loaded!' if c.validate() else '❌ Credentials not found')"
```

Or run the example:

```bash
uv run python examples/search_example.py
```
