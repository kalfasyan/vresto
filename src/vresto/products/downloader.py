"""Product downloader utilities for Sentinel-2-style products on S3.

This module provides a flexible `S3Mapper` to discover product IMG_DATA layout
and a `ProductDownloader` class to list available bands, build S3 keys and
download selected bands with optional resampling using rasterio.

The implementation aims to be conservative and configurable: it can auto-
discover files under a product prefix or accept explicit S3 URIs/templates.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # pragma: no cover - boto3 is expected in runtime
    boto3 = None

try:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.warp import reproject

    has_rasterio = True
except Exception:  # rasterio is optional
    rasterio = None
    Resampling = None
    Affine = None
    reproject = None
    has_rasterio = False

try:
    from tqdm import tqdm

    has_tqdm = True
except Exception:
    tqdm = None
    has_tqdm = False

LOG = logging.getLogger(__name__)


# Regex for L2A format with resolution: _B02_10m.jp2
_BAND_RE_L2A = re.compile(r"_(?P<band>B\d{2}|B8A|TCI|SCL|AOT|WVP)_(?P<res>\d+)m\.jp2$", re.IGNORECASE)

# Regex for L1C format without resolution: _B01.jp2
_BAND_RE_L1C = re.compile(r"_(?P<band>B\d{2}|B8A|TCI|SCL|AOT|WVP)\.jp2$", re.IGNORECASE)

# Mapping of L1C band names to their native resolution in meters
# Based on Sentinel-2 MSI instrument specifications
_L1C_BAND_RESOLUTIONS = {
    "B01": 60,
    "B02": 10,
    "B03": 10,
    "B04": 10,
    "B05": 20,
    "B06": 20,
    "B07": 20,
    "B08": 10,
    "B8A": 20,
    "B09": 60,
    "B10": 60,
    "B11": 20,
    "B12": 20,
    "TCI": 10,  # True Color Image at 10m
    "SCL": 20,  # Scene Classification Layer (L2A only, but including for completeness)
    "AOT": 10,  # Aerosol Optical Thickness (typically 10m)
    "WVP": 10,  # Water Vapour (typically 10m)
}


def _parse_band_from_filename(filename: str) -> Optional[Tuple[str, int]]:
    """Parse band name and resolution from a filename.

    Handles both L2A format (with resolution) and L1C format (without resolution).
    Returns (band_name, resolution) or None if not a band file.

    For L1C files, the resolution is looked up from the native resolution mapping.
    """
    # Try L2A format first (with resolution suffix)
    m = _BAND_RE_L2A.search(filename)
    if m:
        band = m.group("band").upper()
        res = int(m.group("res"))
        return (band, res)

    # Try L1C format (no resolution in filename)
    m = _BAND_RE_L1C.search(filename)
    if m:
        band = m.group("band").upper()
        res = _L1C_BAND_RESOLUTIONS.get(band, 10)  # default to 10m if unknown
        return (band, res)

    return None


# For backward compatibility with code that imports _BAND_RE
# This regex will match either L2A or L1C format
_BAND_RE = re.compile(r"_(?P<band>B\d{2}|B8A|TCI|SCL|AOT|WVP)(?:_(?P<res>\d+)m)?\.jp2$", re.IGNORECASE)


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError("s3 uri must start with s3://")
    # Normalize variants like s3:///bucket/key by stripping leading slashes
    rest = uri[5:].lstrip("/")
    parts = rest.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    return bucket, key


class S3Mapper:
    """Discover product files on S3 and build keys.

    Usage patterns supported:
    - Provide a product-level prefix that contains `GRANULE/<granule>/IMG_DATA/`.
    - Provide the IMG_DATA prefix directly (e.g. `s3://bucket/.../IMG_DATA/`).

    The mapper will attempt to locate R10m/R20m/R60m subfolders automatically.
    """

    def __init__(self, s3_client=None):
        if s3_client is None:
            if boto3 is None:
                raise RuntimeError("boto3 is required for S3 access")
            s3_client = boto3.client("s3")
        self.s3 = s3_client

    def _list_common_prefixes(self, bucket: str, prefix: str) -> List[str]:
        paginator = self.s3.get_paginator("list_objects_v2")
        prefixes: List[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                prefixes.append(cp.get("Prefix"))
        return prefixes

    def resolve_img_prefix(self, product_uri: str) -> str:
        """Return an IMG_DATA/ prefix for the given product URI.

        Examples of accepted inputs:
        - `s3://bucket/.../GRANULE/L2A_T59UNV.../IMG_DATA/`
        - `s3://bucket/.../GRANULE/L2A_T59UNV.../`
        - `s3://bucket/.../S2B_MSIL2A_...SAFE/`

        For L1C products, tries both with and without processing baseline in the path.
        """
        bucket, prefix = _parse_s3_uri(product_uri)

        # If IMG_DATA already present
        if "IMG_DATA/" in prefix:
            idx = prefix.find("IMG_DATA/")
            return f"s3://{bucket}/{prefix[: idx + len('IMG_DATA/')]}"

        # Try to find granule subfolders containing IMG_DATA
        search_prefixes = []
        if prefix.endswith("GRANULE") or prefix.endswith("GRANULE/"):
            search_prefixes.append(prefix if prefix.endswith("/") else prefix + "/")
        else:
            search_prefixes.append(prefix + "GRANULE/" if prefix and not prefix.endswith("/") else prefix + "GRANULE/")
            search_prefixes.append(prefix)

        for sp in search_prefixes:
            try:
                cps = self._list_common_prefixes(bucket, sp)
            except (BotoCoreError, ClientError):
                cps = []
            for cp in cps:
                if cp.endswith("IMG_DATA/"):
                    return f"s3://{bucket}/{cp}"
                # look inside this prefix for IMG_DATA/
                try:
                    inner = self._list_common_prefixes(bucket, cp)
                except (BotoCoreError, ClientError):
                    inner = []
                for ip in inner:
                    if ip.endswith("IMG_DATA/"):
                        return f"s3://{bucket}/{ip}"

        # For L1C products, try alternative path without processing baseline if original failed
        # (some buckets store L1C products at L1C/ instead of L1C_N0500/)
        if "L1C" in prefix and "_N" in prefix:
            # Try removing the processing baseline (e.g., L1C_N0500 -> L1C)
            alt_prefix = prefix.replace("L1C_N", "L1C/").rsplit("/", 1)[0] + "/" + prefix.rsplit("/", 1)[-1]
            # Simpler: replace "L1C_NXXXX/" with "L1C/"
            import re

            alt_prefix = re.sub(r"L1C_N\d{4}/", "L1C/", prefix)

            if alt_prefix != prefix:
                LOG.debug(f"Original path failed, trying alternative L1C path: {alt_prefix}")
                try:
                    result = self.resolve_img_prefix(f"s3://{bucket}/{alt_prefix}")
                    LOG.debug("Found IMG_DATA using alternative L1C path")
                    return result
                except FileNotFoundError:
                    pass

        raise FileNotFoundError(f"IMG_DATA prefix not found under {product_uri}")

    def list_img_objects(self, img_uri: str) -> Iterable[str]:
        """Yield object keys under an IMG_DATA/ prefix."""
        bucket, prefix = _parse_s3_uri(img_uri)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def find_band_key(self, img_uri: str, band: str, resolution: int) -> Optional[str]:
        """Return the S3 key (not URI) for a band at requested resolution if present.

        Handles both L2A format (with resolution suffix) and L1C format (no resolution suffix).
        If not found, returns None.
        """
        bucket, prefix = _parse_s3_uri(img_uri)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"

        # Try L2A format first (with resolution suffix)
        target_suffix_l2a = f"_{band}_{resolution}m.jp2"

        # L1C format (no resolution suffix)
        target_suffix_l1c = f"_{band}.jp2"

        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Try L2A format first
                if key.endswith(target_suffix_l2a):
                    return key
                # Try L1C format
                if key.endswith(target_suffix_l1c):
                    return key
        return None


class ProductDownloader:
    """High-level downloader for products on S3.

    Key methods:
    - `list_available_bands(product_uri)` -> Dict[band, Set[resolutions]]
    - `build_keys_for_bands(product_uri, bands, resolution)` -> List[s3://.../key]
    - `download_product(product_uri, bands, resolution, dest_dir, ...)`
    """

    def __init__(self, s3_client=None, concurrency: int = 4, retries: int = 3):
        self.mapper = S3Mapper(s3_client=s3_client)
        self.concurrency = max(1, int(concurrency))
        self.retries = max(0, int(retries))

    def _detect_product_type_and_list_bands(self, bucket: str, prefix: str) -> Tuple[str, Dict[str, Set[int]]]:
        """Detect if product is L1C or L2A and return bands with their resolutions.

        Returns: (product_type, bands_dict) where product_type is 'L1C' or 'L2A'
        """
        bands: Dict[str, Set[int]] = {}
        product_type = None
        paginator = self.mapper.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                # Try L2A format first (has resolution in filename)
                m = _BAND_RE_L2A.search(key)
                if m:
                    product_type = "L2A"
                    band = m.group("band").upper()
                    res = int(m.group("res"))
                    bands.setdefault(band, set()).add(res)
                    continue

                # Try L1C format (no resolution in filename)
                m = _BAND_RE_L1C.search(key)
                if m:
                    product_type = "L1C"
                    band = m.group("band").upper()
                    # For L1C, get native resolution from lookup table
                    res = _L1C_BAND_RESOLUTIONS.get(band, 10)  # default to 10m if unknown
                    bands.setdefault(band, set()).add(res)
                    continue

        return product_type or "L2A", bands

    def list_available_bands(self, product_uri: str) -> Dict[str, Set[int]]:
        """Discover available bands and their native resolutions under product.

        Returns a mapping `band -> {resolutions_in_meters}`.
        """
        img_uri = self.mapper.resolve_img_prefix(product_uri)
        bucket, prefix = _parse_s3_uri(img_uri)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        product_type, bands = self._detect_product_type_and_list_bands(bucket, prefix)
        LOG.debug("Discovered product type: %s, bands for %s: %s", product_type, product_uri, {k: sorted(v) for k, v in bands.items()})
        return bands

    def build_keys_for_bands(self, product_uri: str, bands: Iterable[str], resolution: Union[int, str]) -> List[str]:
        """Return full S3 URIs for the requested bands at given resolution.

        If `resolution` is 'native', the best (smallest number) native resolution
        available for each band is chosen.

        For L1C products where bands have fixed native resolutions, if the exact
        requested resolution is not available, the native resolution is used
        (the caller can enable resampling if a specific output resolution is needed).
        """
        img_uri = self.mapper.resolve_img_prefix(product_uri)
        bucket, _ = _parse_s3_uri(img_uri)
        available = self.list_available_bands(product_uri)
        keys: List[str] = []
        for band in bands:
            band_u = band.upper()
            if band_u not in available:
                raise KeyError(f"Band {band} not found in product")
            # Determine which resolution to use. If 'native' choose the best (smallest)
            # native resolution.
            if resolution == "native":
                res_to_use = min(available[band_u])
            else:
                req_res = int(resolution)
                if req_res in available[band_u]:
                    res_to_use = req_res
                else:
                    # For L1C products, bands have fixed native resolutions.
                    # If exact resolution not available and there's only one available,
                    # use the native resolution. The caller can enable resampling
                    # to get the desired output resolution.
                    available_res = sorted(available[band_u])
                    if len(available_res) == 1:
                        res_to_use = available_res[0]
                        LOG.debug(f"Band {band_u} not available at {req_res}m, using native resolution {res_to_use}m. Enable resampling for {req_res}m output.")
                    else:
                        # Multiple resolutions available but exact match not found
                        raise KeyError(f"Resolution {req_res}m not available for band {band_u}. Available: {available_res}")
            found_key = self.mapper.find_band_key(img_uri, band_u, res_to_use)
            if not found_key:
                raise FileNotFoundError(f"Key for band {band_u} at {res_to_use}m not found")
            keys.append(f"s3://{bucket}/{found_key}")
        return keys

    def _download_one(self, s3_uri: str, dest: Path, overwrite: bool = False) -> Path:
        bucket, key = _parse_s3_uri(s3_uri)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not overwrite:
            LOG.debug("Destination exists and overwrite=False: %s", dest)
            return dest
        tmpfd, tmppath = tempfile.mkstemp(prefix=dest.name, dir=str(dest.parent))
        os.close(tmpfd)
        attempts = 0
        backoff = 1.0
        # Get expected size if available
        expected_size = None
        expected_etag = None
        try:
            head = self.mapper.s3.head_object(Bucket=bucket, Key=key)
            expected_size = int(head.get("ContentLength"))
            expected_etag = head.get("ETag")
        except Exception:
            # head may fail for public endpoints or permissions; continue without verification
            expected_size = None
            expected_etag = None

        while attempts <= self.retries:
            attempts += 1
            try:
                LOG.debug("Downloading s3://%s/%s -> %s (attempt %d)", bucket, key, tmppath, attempts)
                # If tqdm available and file large, stream with progress
                if has_tqdm:
                    # use get_object Body streaming
                    obj = self.mapper.s3.get_object(Bucket=bucket, Key=key)
                    body = obj["Body"]
                    total = expected_size or obj.get("ContentLength") or None
                    with open(tmppath, "wb") as f:
                        if total and has_tqdm:
                            for chunk in tqdm(body.iter_chunks(chunk_size=32 * 1024), total=(total // (32 * 1024)) + 1, desc=dest.name, unit="chunk"):
                                f.write(chunk)
                        else:
                            for chunk in body.iter_chunks(chunk_size=32 * 1024):
                                f.write(chunk)
                else:
                    # fallback to download_file
                    self.mapper.s3.download_file(bucket, key, tmppath)

                # verify size if possible
                if expected_size is not None:
                    actual = os.path.getsize(tmppath)
                    if actual != expected_size:
                        raise IOError(f"size mismatch for {key}: expected {expected_size}, got {actual}")

                # move into place atomically
                shutil.move(tmppath, str(dest))
                # optionally verify etag format (simple)
                if expected_etag and expected_etag.startswith('"'):
                    # strip quotes
                    expected_etag_value = expected_etag.strip('"')
                    # for multipart uploads ETag may contain '-' and not be useful
                    if "-" not in expected_etag_value:
                        # compute local md5
                        import hashlib

                        h = hashlib.md5()
                        with open(dest, "rb") as f:
                            for chunk in iter(lambda: f.read(8192), b""):
                                h.update(chunk)
                        if h.hexdigest() != expected_etag_value:
                            raise IOError("etag mismatch after download")

                return dest
            except Exception as exc:
                LOG.warning("Attempt %d failed for %s: %s", attempts, key, exc)
                try:
                    os.remove(tmppath)
                except Exception:
                    pass
                if attempts > self.retries:
                    LOG.error("Exceeded retries for %s", key)
                    raise
                # exponential backoff with jitter
                sleep = backoff * (1 + 0.1 * (2 * (0.5 - os.urandom(1)[0] / 255)))
                time.sleep(sleep)
                backoff *= 2

    def download_product(
        self,
        product_uri: str,
        bands: Iterable[str],
        resolution: Union[int, str],
        dest_dir: Union[str, Path],
        resample: bool = False,
        overwrite: bool = False,
        allow_missing: bool = False,
        resample_method: str = "bilinear",
        preserve_s3_structure: bool = True,
    ) -> List[Path]:
        """Download requested bands for product to `dest_dir`.

        - `product_uri`: S3 product prefix (see S3Mapper.resolve_img_prefix)
        - `bands`: iterable of band names like ['B02','B03']
        - `resolution`: 10|20|60 or 'native'
        - `resample`: if True, resample to `resolution` when native differs
        - `preserve_s3_structure`: if True, mirror S3 structure locally (e.g., Sentinel-2/MSI/L2A_N0500/...)
          If False, download directly to dest_dir

        Returns list of local `Path` where files were written.
        """
        dest_dir = Path(dest_dir)
        keys = []
        try:
            keys = self.build_keys_for_bands(product_uri, bands, resolution)
        except KeyError as e:
            if allow_missing:
                LOG.warning("Missing band/resolution: %s", e)
            else:
                raise

        results: List[Path] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futures = {}
            for s3uri in keys:
                filename = Path(s3uri).name
                if preserve_s3_structure:
                    # Extract key from S3 URI and use it to preserve structure
                    # s3://bucket/path/to/file -> path/to/file
                    bucket, key = _parse_s3_uri(s3uri)
                    dest = dest_dir / key
                else:
                    dest = dest_dir / filename
                fut = ex.submit(self._download_one, s3uri, dest, overwrite)
                futures[fut] = (s3uri, dest)

            for fut in as_completed(futures):
                s3uri, dest = futures[fut]
                try:
                    path = fut.result()
                    results.append(path)
                except Exception as exc:
                    LOG.error("Failed to download %s: %s", s3uri, exc)
                    if not allow_missing:
                        raise

        if resample:
            if not has_rasterio:
                raise RuntimeError("rasterio is required for resampling; install rasterio")
            resampled: List[Path] = []
            target_res = None if resolution == "native" else int(resolution)
            for p in results:
                # Try L2A format first (has resolution in filename)
                m = _BAND_RE_L2A.search(p.name)
                if m:
                    native = int(m.group("res"))
                    if target_res is None or target_res == native:
                        resampled.append(p)
                        continue
                    out_path = p.with_name(p.stem + f"_{target_res}m" + p.suffix)
                    self._resample_raster(p, out_path, target_res, resample_method)
                    resampled.append(out_path)
                    continue

                # Try L1C format (no resolution in filename)
                m = _BAND_RE_L1C.search(p.name)
                if m:
                    band = m.group("band").upper()
                    native = _L1C_BAND_RESOLUTIONS.get(band, 10)
                    if target_res is None or target_res == native:
                        resampled.append(p)
                        continue
                    out_path = p.with_name(p.stem + f"_{target_res}m" + p.suffix)
                    self._resample_raster(p, out_path, target_res, resample_method)
                    resampled.append(out_path)
                    continue

                # File doesn't match either pattern, keep as-is
                resampled.append(p)
            results = resampled

        return results

    def _resample_raster(self, src_path: Path, dst_path: Path, target_res_m: int, method: str = "bilinear") -> None:
        if not has_rasterio:
            raise RuntimeError("rasterio is required for resampling")
        method_map = {
            "nearest": Resampling.nearest,
            "bilinear": Resampling.bilinear,
            "cubic": Resampling.cubic,
        }
        resampling = method_map.get(method, Resampling.bilinear)
        with rasterio.open(str(src_path)) as src:
            left, bottom, right, top = src.bounds
            new_width = int((right - left) / float(target_res_m))
            new_height = int((top - bottom) / float(target_res_m))
            dst_transform = Affine(target_res_m, 0, left, 0, -target_res_m, top)
            profile = src.profile.copy()
            profile.update({
                "transform": dst_transform,
                "width": new_width,
                "height": new_height,
            })
            with rasterio.open(str(dst_path), "w", **profile) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=src.crs,
                        resampling=resampling,
                    )
