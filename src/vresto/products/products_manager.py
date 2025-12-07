"""Product management module for handling Copernicus product data."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from loguru import logger

from vresto.api.auth import CopernicusAuth
from vresto.api.catalog import ProductInfo
from vresto.api.config import CopernicusConfig


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

    def __init__(self, config: Optional[CopernicusConfig] = None, auth: Optional[CopernicusAuth] = None):
        """Initialize products manager.

        Args:
            config: CopernicusConfig instance. If not provided, will create one.
            auth: CopernicusAuth instance. If not provided, will create one.
        """
        self.config = config or CopernicusConfig()
        self.auth = auth or CopernicusAuth(self.config)

        # Initialize S3 client with Copernicus credentials
        access_key, secret_key = self._get_s3_credentials()
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.config.s3_endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="default",
        )

        logger.info("ProductsManager initialized")

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

        Args:
            s3_path: S3 path (e.g., "s3://eodata/Sentinel-2/..." or "s3:///eodata/...")

        Returns:
            Tuple of (bucket, key)
        """
        # Remove s3:// or s3:/// prefix
        if s3_path.startswith("s3://"):
            s3_path = s3_path[5:]  # Remove s3://
        elif s3_path.startswith("s3:///"):
            s3_path = s3_path[6:]  # Remove s3:///

        # Remove leading slashes
        s3_path = s3_path.lstrip("/")

        # Split on first slash to separate bucket from key
        parts = s3_path.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], ""

    def get_quicklook(self, product: ProductInfo) -> Optional[ProductQuicklook]:
        """Download quicklook image for a product.

        For Sentinel-2 products, the quicklook is typically named:
        S2A/S2B_MSIL2A_<timestamp>_N<level>_R<relative_orbit>_T<tile>_<product_timestamp>-ql.jpg
        or
        S2A/S2B_MSIL1C_<timestamp>_N<level>_R<relative_orbit>_T<tile>_<product_timestamp>-ql.jpg

        Args:
            product: ProductInfo object

        Returns:
            ProductQuicklook if successful, None otherwise
        """
        if not product.s3_path:
            logger.warning(f"Product {product.name} has no S3 path")
            return None

        try:
            # Extract bucket and key from s3_path
            bucket, base_key = self._extract_s3_path_components(product.s3_path)

            # Construct full key for quicklook
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

                    # Download from S3
                    response = self.s3_client.get_object(Bucket=bucket, Key=quicklook_key)
                    image_data = response["Body"].read()

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

        Automatically detects the appropriate metadata file based on product type:
        - L2A products: MTD_MSIL2A.xml
        - L1C products: MTD_MSIL1C.xml
        - Generic: MTD_SAFL1C.xml (may also work)

        Args:
            product: ProductInfo object
            metadata_filename: Specific metadata filename to download. If None, will try auto-detection.

        Returns:
            ProductMetadata if successful, None otherwise
        """
        if not product.s3_path:
            logger.warning(f"Product {product.name} has no S3 path")
            return None

        try:
            # Extract bucket and key from s3_path
            bucket, base_key = self._extract_s3_path_components(product.s3_path)

            # Construct full key for metadata
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

                    # Download from S3
                    response = self.s3_client.get_object(Bucket=bucket, Key=metadata_key)
                    metadata_xml = response["Body"].read().decode("utf-8")

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
