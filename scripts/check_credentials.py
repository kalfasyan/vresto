#!/usr/bin/env python3
"""Quick diagnostic script to check credential configuration."""

import os
from pathlib import Path

print("=" * 60)
print("COPERNICUS CREDENTIALS DIAGNOSTIC")
print("=" * 60)

# Check for .env file
env_file = Path(".env")
print("\n1. Checking for .env file...")
if env_file.exists():
    print(f"   ✅ Found .env file at: {env_file.absolute()}")
    with open(env_file) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        has_username = any(line.startswith("COPERNICUS_USERNAME=") for line in lines)
        has_password = any(line.startswith("COPERNICUS_PASSWORD=") for line in lines)
        print(f"   {'✅' if has_username else '❌'} COPERNICUS_USERNAME defined")
        print(f"   {'✅' if has_password else '❌'} COPERNICUS_PASSWORD defined")
else:
    print("   ❌ No .env file found")
    print("   💡 Create one with: cp .env.example .env")

# Check environment variables
print("\n2. Checking environment variables...")
username = os.getenv("COPERNICUS_USERNAME")
password = os.getenv("COPERNICUS_PASSWORD")
print(f"   {'✅' if username else '❌'} COPERNICUS_USERNAME: {'[SET]' if username else '[NOT SET]'}")
print(f"   {'✅' if password else '❌'} COPERNICUS_PASSWORD: {'[SET]' if password else '[NOT SET]'}")

# Try loading with dotenv
print("\n3. Testing python-dotenv loading...")
try:
    from dotenv import load_dotenv

    load_dotenv()
    username_after = os.getenv("COPERNICUS_USERNAME")
    password_after = os.getenv("COPERNICUS_PASSWORD")
    print("   ✅ python-dotenv imported successfully")
    print(f"   {'✅' if username_after else '❌'} COPERNICUS_USERNAME after load_dotenv: {'[SET]' if username_after else '[NOT SET]'}")
    print(f"   {'✅' if password_after else '❌'} COPERNICUS_PASSWORD after load_dotenv: {'[SET]' if password_after else '[NOT SET]'}")
except ImportError:
    print("   ❌ python-dotenv not installed")
    print("   💡 Run: uv pip install python-dotenv")

# Try loading config
print("\n4. Testing CopernicusConfig...")
try:
    from vresto.api import CopernicusConfig

    config = CopernicusConfig()
    if config.validate():
        print("   ✅ Credentials loaded successfully!")
        print(f"   Username: {config.username}")
        print(f"   Password: {'*' * len(config.password)}")
    else:
        print("   ❌ Credentials not configured")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "=" * 60)
print("NEXT STEPS:")
print("=" * 60)
if not env_file.exists():
    print("1. Create .env file: cp .env.example .env")
    print("2. Edit .env and add your credentials")
elif config.validate():
    print("✅ All good! Your credentials are configured.")
    print("Run the app: uv run python src/vresto/ui/map_interface.py")
else:
    print("1. Check your .env file has the correct format")
    print("2. Make sure there are no quotes around values")
    print("3. Make sure there are no spaces around the = sign")
print("=" * 60)
