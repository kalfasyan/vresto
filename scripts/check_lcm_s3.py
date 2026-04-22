"""Diagnostic: list actual S3 objects under the CLMS LCM prefix.

Run with:
    python scripts/check_lcm_s3.py

Uses COPERNICUS_S3_ACCESS_KEY / COPERNICUS_S3_SECRET_KEY from .env (or env).
"""

import os
import sys
from pathlib import Path

# Load .env from repo root
_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from vresto.api.env_loader import load_env  # noqa: E402

load_env()

import boto3  # noqa: E402

BUCKET = "eodata"
BASE_PREFIX = "CLMS/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/"
ENDPOINT = os.getenv("COPERNICUS_S3_ENDPOINT", "https://eodata.dataspace.copernicus.eu")
ACCESS_KEY = os.getenv("COPERNICUS_S3_ACCESS_KEY")
SECRET_KEY = os.getenv("COPERNICUS_S3_SECRET_KEY")

if not ACCESS_KEY or not SECRET_KEY:
    print("ERROR: COPERNICUS_S3_ACCESS_KEY or COPERNICUS_S3_SECRET_KEY not set")
    sys.exit(1)

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
)

print(f"\n=== Top-level prefixes under {BASE_PREFIX} ===")
resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=BASE_PREFIX, Delimiter="/", MaxKeys=50)
for cp in resp.get("CommonPrefixes", []):
    print("  DIR:", cp["Prefix"])
for obj in resp.get("Contents", []):
    print("  OBJ:", obj["Key"], f"({obj['Size']:,} bytes)")

print(f"\n=== First 30 objects recursively under {BASE_PREFIX} ===")
paginator = s3.get_paginator("list_objects_v2")
count = 0
for page in paginator.paginate(Bucket=BUCKET, Prefix=BASE_PREFIX, PaginationConfig={"MaxItems": 30}):
    for obj in page.get("Contents", []):
        print(f"  {obj['Key']}  ({obj['Size']:,} bytes)")
        count += 1
        if count >= 30:
            break
    if count >= 30:
        break

if count == 0:
    print("  (no objects found)")
