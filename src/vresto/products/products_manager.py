"""Product management module for handling Copernicus product data."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import boto3
import botocore.exceptions
import requests
from botocore.config import Config
from loguru import logger

from vresto.api.auth import CopernicusAuth
from vresto.api.catalog import ProductInfo
from vresto.api.config import CopernicusConfig
from vresto.products.downloader import ProductDownloader
from vresto.products.product_name import ProductName


@dataclass
class ProductQuicklook:
    """Container for product quicklook data."""

    product_name: str
    image_data: bytes
    image_format: str = "jpeg"  # "jpeg" or "png"

    def save_to_file(self, filepath: Path) -> None:
        """Save quicklook image to a file.

        Args:
            filepath: Path where to save the image
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(self.image_data)
        logger.info(f"Quicklook saved to {filepath}")

    def get_base64(self) -> str:
        """Get base64 encoded image data for embedding in HTML.

        Returns:
            Base64 encoded image string (without data:image/jpeg;base64, prefix)
        """
        import base64

        return base64.b64encode(self.image_data).decode("utf-8")


@dataclass
class ProductMetadata:
    """Container for product metadata."""

    product_name: str
    metadata_xml: str  # MTD_MSIL2A.xml or equivalent

    def save_to_file(self, filepath: Path) -> None:
        """Save metadata to a file.

        Args:
            filepath: Path where to save the metadata
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(self.metadata_xml)
        logger.info(f"Metadata saved to {filepath}")


class ProductsManager:
    """Manage Copernicus product data including quicklooks and metadata."""

    def __init__(self, config: Optional[CopernicusConfig] = None, auth: Optional[CopernicusAuth] = None, max_retries: int = 5):
        """Initialize products manager.

        Args:
            config: CopernicusConfig instance. If not provided, will create one.
            auth: CopernicusAuth instance. If not provided, will create one.
            max_retries: Maximum number of retries for S3 operations (default: 5)
        """
        self.config = config or CopernicusConfig()
        self.auth = auth or CopernicusAuth(self.config)
        self.max_retries = max_retries

        # Initialize S3 client with Copernicus credentials
        access_key, secret_key = self._get_s3_credentials()

        # Configure S3 client with retry policy and timeouts
        s3_config = Config(
            retries={"max_attempts": max_retries, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=60,
            max_pool_connections=10,
        )

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.config.s3_endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="default",
            config=s3_config,
        )

        logger.info("ProductsManager initialized with max_retries=%d", max_retries)

    def _retry_with_backoff(self, func, max_attempts: Optional[int] = None, initial_delay: float = 1.0):
        """Execute function with exponential backoff retry logic.

        Args:
            func: Callable to execute
            max_attempts: Maximum number of attempts (uses self.max_retries if None)
            initial_delay: Initial delay in seconds before retry (default: 1.0)

        Returns:
            Result of func if successful

        Raises:
            The last exception if all attempts fail
        """
        if max_attempts is None:
            max_attempts = self.max_retries

        last_exception = None
        delay = initial_delay

        for attempt in range(1, max_attempts + 1):
            try:
                return func()
            except (botocore.exceptions.ConnectionError, botocore.exceptions.ConnectTimeoutError, botocore.exceptions.ReadTimeoutError) as e:
                last_exception = e
                if attempt < max_attempts:
                    logger.warning(f"Attempt {attempt}/{max_attempts} failed: {type(e).__name__}. Retrying in {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)  # Exponential backoff, cap at 60s
                else:
                    logger.error(f"All {max_attempts} attempts failed: {type(e).__name__}")
            except self.s3_client.exceptions.NoSuchKey:
                # Don't retry on NoSuchKey - file doesn't exist
                raise
            except Exception:
                # For other exceptions, don't retry
                raise

        if last_exception:
            raise last_exception
        return None

    def _get_s3_credentials(self) -> tuple[str, str]:
        """Get S3 credentials from Copernicus config.

        Prefers static credentials if configured, otherwise requests temporary credentials
        from the S3 Keys Manager API.

        Returns:
            Tuple of (access_key, secret_key)

        Raises:
            ValueError: If credentials cannot be obtained
        """
        # Try static credentials first
        if self.config.has_static_s3_credentials():
            access_key, secret_key = self.config.get_s3_credentials()
            logger.info(f"Using static S3 credentials: {access_key}")
            return access_key, secret_key

        # Fall back to temporary credentials via API
        logger.info("No static S3 credentials found, requesting temporary credentials...")
        creds = self.auth.get_s3_credentials()
        access_key = creds.get("access_id")
        secret_key = creds.get("secret")

        if not access_key or not secret_key:
            raise ValueError(f"Invalid S3 credentials format: {creds}")

        logger.info(f"Obtained temporary S3 credentials: {access_key}")
        return access_key, secret_key

    def _extract_s3_path_components(self, s3_path: str) -> tuple[str, str]:
        """Extract bucket and key from S3 path.

        Handles both standard (s3://bucket/key) and variant (s3:///bucket/key) formats.
        Also handles paths without s3:// prefix.

        Args:
            s3_path: S3 URI (e.g., "s3://eodata/Sentinel-2/..." or "eodata/Sentinel-2/...")

        Returns:
            Tuple of (bucket, key)
        """
        # Remove s3:// prefix if present (handle both s3:// and s3:/// variants)
        if s3_path.startswith("s3://"):
            rest = s3_path[5:].lstrip("/")
        else:
            # Normalize paths that may start with a leading slash (e.g. `/eodata/...`) so
            # splitting yields a valid bucket name instead of an empty string.
            rest = s3_path.lstrip("/")

        # Split on first slash to separate bucket from key
        parts = rest.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        return bucket, key

    def _construct_s3_path_from_name(self, product_name: str) -> str:
        """Construct S3 path from product identifier.

        Supports multiple input formats:
        1. Short Sentinel-2 product name:
           Input:  "S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"
           Output: "s3://eodata/Sentinel-2/MSI/L2A_N0500/2020/12/12/S2B_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE/"

        2. Existing S3 URI:
           Input:  "s3://eodata/Sentinel-2/..."
           Output: (returned as-is)

        3. SAFE directory name:
           Input:  "S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207.SAFE"
           Output: (returned as-is)

        For case 1, uses Copernicus EODATA standard layout (Sentinel-2 only).
        For unsupported product types (S1, S5P), returns input with .SAFE suffix.

        Args:
            product_name: Product identifier (short name, S3 URI, or .SAFE directory name)

        Returns:
            S3 path string

        Raises:
            ValueError: If input format is invalid
        """
        # Fast path: already an S3 URI
        if product_name.startswith("s3://"):
            return product_name

        # Fast path: already a .SAFE directory name
        if product_name.endswith(".SAFE"):
            return product_name

        # Try to parse as short product name and construct full S3 path
        pn = ProductName(product_name)
        try:
            s3_prefix = pn.s3_prefix()
            if s3_prefix:
                return s3_prefix
        except NotImplementedError:
            logger.debug(f"S3 path construction not supported for {pn.product_type} products; falling back to .SAFE suffix only")

        # Fallback: If parsing failed (product_type is None), log a warning but still try to construct a path
        if pn.product_type is None:
            logger.warning(f"Could not parse product name '{product_name}'. Please provide either: (1) a valid Sentinel-2 short name, (2) an S3 path (s3://bucket/...), or (3) a .SAFE directory name.")
            return f"{product_name}.SAFE"

        logger.info(f"Could not construct full S3 path from '{product_name}'. Returning with .SAFE suffix only.")
        return f"{product_name}.SAFE"

    def get_quicklook(self, product: ProductInfo) -> Optional[ProductQuicklook]:
        """Download quicklook image for a product.

        For Sentinel-2 products, the quicklook is typically named:
        `<product_name>-ql.jpg` (e.g., `S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207-ql.jpg`)

        Args:
            product: ProductInfo with valid s3_path

        Returns:
            ProductQuicklook if successful, None otherwise
        """
        # If STAC backend provided a thumbnail asset, use it directly
        if product.assets and "thumbnail" in product.assets:
            thumbnail_url = product.assets["thumbnail"]["href"]
            if thumbnail_url.startswith("s3://"):
                bucket, key = self._extract_s3_path_components(thumbnail_url)
                try:
                    logger.info(f"Downloading STAC thumbnail from S3: {thumbnail_url}")
                    response = self.s3_client.get_object(Bucket=bucket, Key=key)
                    image_data = response["Body"].read()
                    return ProductQuicklook(product_name=product.name, image_data=image_data, image_format="jpeg")
                except Exception as e:
                    logger.warning(f"Failed to download STAC thumbnail from S3: {e}")
            elif thumbnail_url.startswith("https://"):
                try:
                    logger.info(f"Downloading STAC thumbnail from HTTPS: {thumbnail_url}")
                    headers = self.auth.get_headers()
                    response = requests.get(thumbnail_url, headers=headers, timeout=30)
                    response.raise_for_status()
                    return ProductQuicklook(product_name=product.name, image_data=response.content, image_format="jpeg")
                except Exception as e:
                    logger.warning(f"Failed to download STAC thumbnail from HTTPS: {e}")

        if not product.s3_path:
            logger.warning(f"Product {product.name} has no S3 path")
            return None

        try:
            # Extract bucket and key from s3_path
            bucket, base_key = self._extract_s3_path_components(product.s3_path)

            # Ensure base_key ends with / so we can append filenames
            if base_key and not base_key.endswith("/"):
                base_key += "/"

            # Remove .SAFE suffix from product name if present
            product_name_clean = product.name.replace(".SAFE", "")

            # Try multiple quicklook filename patterns
            # Pattern 1: <product_name>-ql.jpg (most common)
            quicklook_filenames = [
                f"{product_name_clean}-ql.jpg",
                # Could add more patterns here if needed
            ]

            for quicklook_filename in quicklook_filenames:
                quicklook_key = base_key + quicklook_filename

                try:
                    logger.info(f"Downloading quicklook: s3://{bucket}/{quicklook_key}")

                    def download_quicklook_func():
                        response = self.s3_client.get_object(Bucket=bucket, Key=quicklook_key)
                        return response["Body"].read()

                    # Download from S3 with retry logic
                    image_data = self._retry_with_backoff(download_quicklook_func)

                    logger.info(f"Successfully downloaded quicklook for {product.name} ({len(image_data)} bytes)")

                    return ProductQuicklook(product_name=product.name, image_data=image_data, image_format="jpeg")

                except self.s3_client.exceptions.NoSuchKey:
                    logger.debug(f"Quicklook not found at {quicklook_key}, trying next pattern...")
                    continue

            # If we get here, no quicklook was found with any pattern
            logger.warning(f"Quicklook not found for {product.name} with any known pattern")
            return None

        except Exception as e:
            logger.error(f"Error downloading quicklook for {product.name}: {e}")
            return None

    def get_metadata(self, product: ProductInfo, metadata_filename: Optional[str] = None) -> Optional[ProductMetadata]:
        """Download metadata XML file for a product.

        Auto-detects metadata filename based on product type:
        - L2A: `MTD_MSIL2A.xml`
        - L1C: `MTD_MSIL1C.xml`, `MTD_SAFL1C.xml`
        - Other: tries all known patterns

        Args:
            product: ProductInfo with valid s3_path
            metadata_filename: Specific metadata filename (e.g., "MTD_MSIL2A.xml").
                If None, auto-detects based on product name.

        Returns:
            ProductMetadata if successful, None otherwise
        """
        # If STAC backend provided a metadata asset, use it directly
        stac_metadata_keys = ["product_metadata", "granule_metadata", "safe_manifest"]
        if product.assets:
            for key in stac_metadata_keys:
                if key in product.assets:
                    metadata_url = product.assets[key]["href"]
                    if metadata_url.startswith("s3://"):
                        bucket, key = self._extract_s3_path_components(metadata_url)
                        try:
                            logger.info(f"Downloading STAC metadata from S3: {metadata_url}")
                            response = self.s3_client.get_object(Bucket=bucket, Key=key)
                            metadata_xml = response["Body"].read().decode("utf-8")
                            return ProductMetadata(product_name=product.name, metadata_xml=metadata_xml)
                        except Exception as e:
                            logger.warning(f"Failed to download STAC metadata from S3: {e}")
                    elif metadata_url.startswith("https://"):
                        try:
                            logger.info(f"Downloading STAC metadata from HTTPS: {metadata_url}")
                            headers = self.auth.get_headers()
                            response = requests.get(metadata_url, headers=headers, timeout=30)
                            response.raise_for_status()
                            return ProductMetadata(product_name=product.name, metadata_xml=response.text)
                        except Exception as e:
                            logger.warning(f"Failed to download STAC metadata from HTTPS: {e}")

        if not product.s3_path:
            logger.warning(f"Product {product.name} has no S3 path")
            return None

        try:
            # Extract bucket and key from s3_path
            bucket, base_key = self._extract_s3_path_components(product.s3_path)

            # Ensure base_key ends with / so we can append filenames
            if base_key and not base_key.endswith("/"):
                base_key += "/"

            # If metadata filename not specified, try common ones
            if metadata_filename:
                metadata_filenames = [metadata_filename]
            else:
                # Try to auto-detect based on product name
                if "L2A" in product.name:
                    metadata_filenames = ["MTD_MSIL2A.xml"]
                elif "L1C" in product.name:
                    metadata_filenames = ["MTD_MSIL1C.xml", "MTD_SAFL1C.xml"]
                else:
                    # Fallback: try both in order
                    metadata_filenames = ["MTD_MSIL2A.xml", "MTD_MSIL1C.xml", "MTD_SAFL1C.xml"]

            for mtd_filename in metadata_filenames:
                metadata_key = base_key + mtd_filename

                try:
                    logger.info(f"Downloading metadata: s3://{bucket}/{metadata_key}")

                    def download_metadata_func():
                        response = self.s3_client.get_object(Bucket=bucket, Key=metadata_key)
                        return response["Body"].read().decode("utf-8")

                    # Download from S3 with retry logic
                    metadata_xml = self._retry_with_backoff(download_metadata_func)

                    logger.info(f"Successfully downloaded metadata for {product.name} ({len(metadata_xml)} bytes)")

                    return ProductMetadata(product_name=product.name, metadata_xml=metadata_xml)

                except self.s3_client.exceptions.NoSuchKey:
                    logger.debug(f"Metadata file {mtd_filename} not found, trying next pattern...")
                    continue

            # If we get here, no metadata was found with any pattern
            logger.warning(f"Metadata file not found for {product.name} with any known filename")
            return None

        except Exception as e:
            logger.error(f"Error downloading metadata for {product.name}: {e}")
            return None

    def batch_get_quicklooks(self, products: list[ProductInfo], skip_errors: bool = True) -> dict[str, Optional[ProductQuicklook]]:
        """Download quicklooks for multiple products.

        Args:
            products: List of ProductInfo objects
            skip_errors: If True, continue on errors; if False, raise on first error

        Returns:
            Dictionary mapping product name to ProductQuicklook (or None if failed)
        """
        results = {}
        for product in products:
            try:
                results[product.name] = self.get_quicklook(product)
            except Exception as e:
                logger.error(f"Error getting quicklook for {product.name}: {e}")
                if not skip_errors:
                    raise
                results[product.name] = None

        return results

    def batch_get_metadata(self, products: list[ProductInfo], metadata_filename: Optional[str] = None, skip_errors: bool = True) -> dict[str, Optional[ProductMetadata]]:
        """Download metadata for multiple products.

        Args:
            products: List of ProductInfo objects
            metadata_filename: Name of metadata file (if None, auto-detect based on product type)
            skip_errors: If True, continue on errors; if False, raise on first error

        Returns:
            Dictionary mapping product name to ProductMetadata (or None if failed)
        """
        results = {}
        for product in products:
            try:
                results[product.name] = self.get_metadata(product, metadata_filename)
            except Exception as e:
                logger.error(f"Error getting metadata for {product.name}: {e}")
                if not skip_errors:
                    raise
                results[product.name] = None

        return results

    def download_product_bands(self, product: Union[ProductInfo, str], bands: list[str], resolution: Union[int, str], dest_dir: Union[str, Path], resample: bool = False, overwrite: bool = False, preserve_s3_structure: bool = True) -> list[Path]:
        """Download selected bands for a product into `dest_dir`.

        Accepts product as either a ProductInfo object (from catalog search) or a string identifier.

        Args:
            product: Product identifier in one of three formats:
                1. ProductInfo object: From catalog.search_products() with s3_path and metadata
                2. Short product name string: "S2A_MSIL2A_20201212T235129_N0500_R073_T59UNV_20230226T030207"
                   (auto-converts to Copernicus EODATA S3 path)
                3. Full S3 path string: "s3://eodata/Sentinel-2/.../PRODUCT.SAFE/" or .SAFE directory name
            bands: list of band names (e.g., ['B02', 'B03', 'B04'])
            resolution: 10, 20, 60, or 'native' (in meters)
            dest_dir: local destination directory
            resample: if True, resample bands to target resolution (requires rasterio)
            overwrite: if True, overwrite existing downloaded files
            preserve_s3_structure: if True, mirror S3 key structure locally starting from collection name
                (e.g., dest_dir/Sentinel-2/MSI/L2A_N0500/2020/12/12/...). If False, download to dest_dir directly.

        Returns:
            List of Path objects to successfully downloaded files

        Raises:
            FileNotFoundError: If product not found on S3
        """
        # Allow `product` to be either a ProductInfo or a short product name string
        s3_path = None
        product_name = None
        if isinstance(product, str):
            product_name = product
            s3_path = self._construct_s3_path_from_name(product)
        else:
            # assume ProductInfo
            product_name = getattr(product, "name", str(product))
            s3_path = getattr(product, "s3_path", None)

        if not s3_path:
            logger.error(f"Product {product_name} has no s3_path")
            return []

        downloader = ProductDownloader(s3_client=self.s3_client)
        return downloader.download_product(s3_path, bands, resolution, dest_dir, resample=resample, overwrite=overwrite, preserve_s3_structure=preserve_s3_structure)
