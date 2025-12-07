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


_BAND_RE = re.compile(r"_(?P<band>B\d{2}|B8A|TCI|SCL|AOT|WVP)_(?P<res>\d+)m\.jp2$", re.IGNORECASE)


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError("s3 uri must start with s3://")
    rest = uri[5:]
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

        If not found, returns None.
        """
        bucket, prefix = _parse_s3_uri(img_uri)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        target_suffix = f"_{band}_{resolution}m.jp2"
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(target_suffix):
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

    def list_available_bands(self, product_uri: str) -> Dict[str, Set[int]]:
        """Discover available bands and their native resolutions under product.

        Returns a mapping `band -> {resolutions_in_meters}`.
        """
        img_uri = self.mapper.resolve_img_prefix(product_uri)
        bucket, prefix = _parse_s3_uri(img_uri)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        bands: Dict[str, Set[int]] = {}
        paginator = self.mapper.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                m = _BAND_RE.search(key)
                if not m:
                    continue
                band = m.group("band").upper()
                res = int(m.group("res"))
                bands.setdefault(band, set()).add(res)
        return bands

    def build_keys_for_bands(self, product_uri: str, bands: Iterable[str], resolution: Union[int, str]) -> List[str]:
        """Return full S3 URIs for the requested bands at given resolution.

        If `resolution` is 'native', the best (smallest number) native resolution
        available for each band is chosen.
        """
        img_uri = self.mapper.resolve_img_prefix(product_uri)
        bucket, _ = _parse_s3_uri(img_uri)
        available = self.list_available_bands(product_uri)
        keys: List[str] = []
        for band in bands:
            band_u = band.upper()
            if band_u not in available:
                raise KeyError(f"Band {band} not found in product")
            if resolution == "native":
                res_to_use = min(available[band_u])
            else:
                res_to_use = int(resolution)
                if res_to_use not in available[band_u]:
                    raise KeyError(f"Resolution {res_to_use}m not available for band {band_u}")
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
                m = _BAND_RE.search(p.name)
                if not m:
                    resampled.append(p)
                    continue
                native = int(m.group("res"))
                if target_res is None or target_res == native:
                    resampled.append(p)
                    continue
                out_path = p.with_name(p.stem + f"_{target_res}m" + p.suffix)
                self._resample_raster(p, out_path, target_res, resample_method)
                resampled.append(out_path)
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
