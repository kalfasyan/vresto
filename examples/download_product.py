"""Example: download specific bands from a Sentinel-2 product on Copernicus S3.

Usage:
  # Using short product name
  python examples/download_product.py --product S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207 --bands B02 B03 B04 --resolution 10 --output ./data

  # Using full S3 path
  python examples/download_product.py --product s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/ --bands B02 B03 B04 --resolution 10 --output ./data

Environment:
  COPERNICUS_S3_ACCESS_KEY, COPERNICUS_S3_SECRET_KEY, COPERNICUS_ENDPOINT (optional)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from vresto.api.config import CopernicusConfig
from vresto.products import ProductsManager


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--product",
        required=True,
        help="Product identifier: short name (S2A_MSIL2A_...), S3 path (s3://...), or .SAFE directory",
    )
    p.add_argument("--bands", nargs="+", required=True, help="Bands to download (e.g. B02 B03 B04)")
    p.add_argument("--resolution", type=int, default="native", help="Target resolution in meters (10/20/60) or 'native'")
    p.add_argument("--output", default="./data", help="Local destination directory")
    p.add_argument("--resample", action="store_true", help="Resample bands to target resolution")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    p.add_argument(
        "--preserve-structure",
        action="store_true",
        default=True,
        help="Mirror S3 structure locally (default: True)",
    )
    args = p.parse_args()

    # Read endpoint and credentials from environment or CopernicusConfig
    config = CopernicusConfig()
    pm = ProductsManager(config=config)

    Path(args.output).mkdir(parents=True, exist_ok=True)
    print(f"Downloading {args.bands} from {args.product}")
    files = pm.download_product_bands(
        args.product,
        args.bands,
        args.resolution,
        args.output,
        resample=args.resample,
        overwrite=args.overwrite,
        preserve_s3_structure=args.preserve_structure,
    )
    for f in files:
        print(f"✅ {f}")


if __name__ == "__main__":
    main()
