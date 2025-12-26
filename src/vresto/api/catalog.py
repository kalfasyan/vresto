"""Catalog search module for Copernicus Data Space Ecosystem."""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from loguru import logger

from .auth import CopernicusAuth
from .config import CopernicusConfig


@dataclass
class BoundingBox:
    """Represents a geographic bounding box."""

    west: float  # Min longitude
    south: float  # Min latitude
    east: float  # Max longitude
    north: float  # Max latitude

    def to_wkt(self) -> str:
        """Convert to WKT (Well-Known Text) POLYGON format for OData queries."""
        # Ensure polygon has non-zero area; if bbox has zero width or height, expand slightly
        try:
            west = float(self.west)
            south = float(self.south)
            east = float(self.east)
            north = float(self.north)
        except Exception:
            # Fallback to original formatting if casting fails
            return f"POLYGON(({self.west} {self.south},{self.east} {self.south},{self.east} {self.north},{self.west} {self.north},{self.west} {self.south}))"

        # tiny epsilon in degrees (~0.11 meter at equator per 1e-6 deg)
        EPS = 1e-6
        if abs(east - west) < EPS:
            east = west + EPS
            west = west - EPS
        if abs(north - south) < EPS:
            north = south + EPS
            south = south - EPS

        return f"POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))"

    def to_bbox_string(self) -> str:
        """Convert to comma-separated bbox string."""
        return f"{self.west},{self.south},{self.east},{self.north}"


@dataclass
class ProductInfo:
    """Information about a Copernicus data product."""

    id: str
    name: str
    collection: str
    sensing_date: str
    size_mb: float
    s3_path: Optional[str] = None
    cloud_cover: Optional[float] = None
    footprint: Optional[str] = None

    def __str__(self) -> str:
        """String representation of product."""
        size_str = f"{self.size_mb:.2f} MB"
        cloud_str = f", Cloud: {self.cloud_cover}%" if self.cloud_cover is not None else ""
        return f"{self.name} ({self.collection}, {self.sensing_date}, {size_str}{cloud_str})"

    @property
    def display_name(self) -> str:
        """Return a user-friendly product name with any trailing '.SAFE' removed.

        This is intended for presentation only; internal logic should keep using
        the original `name` where the suffix may be significant.
        """
        try:
            if isinstance(self.name, str) and self.name.upper().endswith(".SAFE"):
                return self.name[:-5]
        except Exception:
            pass
        return self.name


class CatalogSearch:
    """Search Copernicus catalog for products."""

    def __init__(self, auth: Optional[CopernicusAuth] = None, config: Optional[CopernicusConfig] = None, max_retries: int = 5):
        """Initialize catalog search.

        Args:
            auth: CopernicusAuth instance. If not provided, will create one.
            config: CopernicusConfig instance. If not provided, will create one from env vars.
            max_retries: Maximum number of retries for API requests (default: 5)
        """
        self.config = config or CopernicusConfig()
        self.auth = auth or CopernicusAuth(self.config)
        self.max_retries = max_retries

    def _retry_request(self, func, max_attempts: Optional[int] = None, initial_delay: float = 1.0):
        """Execute HTTP request with exponential backoff retry logic.

        Args:
            func: Callable that makes the HTTP request and returns response
            max_attempts: Maximum number of attempts (uses self.max_retries if None)
            initial_delay: Initial delay in seconds before retry (default: 1.0)

        Returns:
            Response object from the successful request

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
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ConnectionError) as e:
                last_exception = e
                if attempt < max_attempts:
                    logger.warning(f"Attempt {attempt}/{max_attempts} failed: {type(e).__name__}. Retrying in {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, 60)  # Exponential backoff, cap at 60s
                else:
                    logger.error(f"All {max_attempts} attempts failed: {type(e).__name__}")
            except requests.RequestException:
                # For other HTTP errors, don't retry
                raise

        if last_exception:
            raise last_exception
        return None

    def search_products(
        self,
        bbox: BoundingBox,
        start_date: str,
        end_date: Optional[str] = None,
        collection: str = "SENTINEL-2",
        max_cloud_cover: Optional[float] = None,
        max_results: int = 100,
        product_level: Optional[str] = None,
    ) -> list[ProductInfo]:
        """Search for products in the catalog.

        Args:
            bbox: Bounding box for spatial search
            start_date: Start date in format 'YYYY-MM-DD'
            end_date: End date in format 'YYYY-MM-DD'. If not provided, uses start_date.
            collection: Product collection (e.g., 'SENTINEL-2', 'SENTINEL-1')
            max_cloud_cover: Maximum cloud cover percentage (0-100). Only for optical products.
            max_results: Maximum number of results to return
            product_level: Optional product processing level filter (e.g., 'L1C', 'L2A').
                When provided, only products matching this processing level will be returned.

        Returns:
            List of ProductInfo objects
        """
        if end_date is None:
            end_date = start_date

        # Build OData filter query
        filters = []

        # Collection filter
        filters.append(f"Collection/Name eq '{collection}'")

        # Date range filter
        filters.append(f"ContentDate/Start ge {start_date}T00:00:00.000Z")
        filters.append(f"ContentDate/Start le {end_date}T23:59:59.999Z")

        # Spatial filter using OGC intersects
        wkt_polygon = bbox.to_wkt()
        filters.append(f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt_polygon}')")

        # Cloud cover filter (only for optical sensors)
        if max_cloud_cover is not None:
            filters.append(f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le {max_cloud_cover})")

        # Product processing level filter (e.g., 'L1C' or 'L2A')
        if product_level is not None:
            # Map user-friendly level to the short code found in product names (e.g., 'L2A' -> 'MSIL2A')
            if collection == "SENTINEL-2" and product_level in ("L1C", "L2A"):
                msil = f"MSI{product_level}"
                # Use OData v4 `contains(Name, 'pattern')` which is supported by the service
                filters.append(f"contains(Name, '{msil}')")
            elif collection == "SENTINEL-3" and product_level in ("L0", "L1", "L2"):
                # Sentinel-3 uses different naming conventions, filtering may be unreliable
                # Product names like: S3A_OL_2_EFR, S3B_SY_2_SYN, etc. may not always contain _L0_, _L1_, _L2_
                # Skip server-side filtering for Sentinel-3; client-side filtering will be used instead
                logger.debug(f"Product level filtering for Sentinel-3 '{product_level}' is handled client-side due to naming convention variations")
            elif collection == "LANDSAT-8" and product_level in ("L0", "L1GT", "L1GS", "L1TP", "L2SP"):
                # LANDSAT-8 uses level codes like LC08_L1GT, LC08_L2SP
                filters.append(f"contains(Name, '{product_level}')")
            else:
                # If an unknown level is provided, log and ignore it
                logger.debug(f"Unknown product_level '{product_level}' provided for {collection}; ignoring level filter")

        # Combine filters
        filter_string = " and ".join(filters)

        # Build full query URL
        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {"$filter": filter_string, "$top": max_results, "$orderby": "ContentDate/Start desc", "$expand": "Attributes"}

        logger.info(f"Searching catalog with filter: {filter_string}")

        try:
            headers = self.auth.get_headers()

            def make_request():
                return requests.get(url, params=params, headers=headers, timeout=60)

            response = self._retry_request(make_request)

            if response.status_code == 200:
                data = response.json()
                products = self._parse_products(data)
                logger.info(f"Found {len(products)} products")
                return products
            else:
                logger.error(f"Catalog search failed. Status: {response.status_code}, Response: {response.text}")
                return []

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return []

    def _parse_products(self, response_data: dict) -> list[ProductInfo]:
        """Parse OData response into ProductInfo objects.

        Args:
            response_data: JSON response from OData API

        Returns:
            List of ProductInfo objects
        """
        products = []

        for item in response_data.get("value", []):
            # Extract cloud cover from attributes if available
            cloud_cover = None
            attributes = item.get("Attributes", [])
            for attr in attributes:
                if attr.get("Name") == "cloudCover":
                    cloud_cover = attr.get("Value")
                    break

            # Parse sensing date
            sensing_date = item.get("ContentDate", {}).get("Start", "")
            if sensing_date:
                # Convert ISO format to readable date
                try:
                    dt = datetime.fromisoformat(sensing_date.replace("Z", "+00:00"))
                    sensing_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

            # Get size in MB
            size_bytes = item.get("ContentLength", 0)
            size_mb = size_bytes / (1024 * 1024)

            product = ProductInfo(
                id=item.get("Id", ""),
                name=item.get("Name", ""),
                collection=item.get("Collection", {}).get("Name", ""),
                sensing_date=sensing_date,
                size_mb=size_mb,
                s3_path=item.get("S3Path", ""),
                cloud_cover=cloud_cover,
                footprint=item.get("GeoFootprint", {}).get("coordinates") if item.get("GeoFootprint") else None,
            )

            products.append(product)

        return products

    def get_product_by_name(self, product_name: str) -> Optional[ProductInfo]:
        """Get product details by exact name.

        Args:
            product_name: Exact product name (with or without .SAFE suffix)

        Returns:
            ProductInfo object or None if not found
        """
        # Try with the product name as-is first
        url = f"{self.config.ODATA_BASE_URL}/Products"

        # List of names to try (with and without .SAFE suffix)
        names_to_try = [product_name]
        if not product_name.endswith(".SAFE"):
            names_to_try.append(f"{product_name}.SAFE")
        elif product_name.endswith(".SAFE"):
            names_to_try.append(product_name[:-5])  # Remove .SAFE

        try:
            headers = self.auth.get_headers()

            for name_variant in names_to_try:
                params = {"$filter": f"Name eq '{name_variant}'", "$expand": "Attributes"}
                response = requests.get(url, params=params, headers=headers, timeout=30)

                if response.status_code == 200:
                    data = response.json()
                    products = self._parse_products(data)
                    if products:
                        return products[0]

            # If no product found with any variant
            logger.debug(f"Product '{product_name}' not found in catalog")
            return None
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

    def search_products_by_name(
        self,
        name_pattern: str,
        match_type: str = "contains",
        max_results: int = 100,
    ) -> list[ProductInfo]:
        """Search for products by name pattern.

        Search for products by name using flexible pattern matching. Useful for
        discovering products when you don't know the exact name.

        Args:
            name_pattern: The product name pattern to search for (e.g., 'S2A_MSIL2A', '*_20240101*')
            match_type: Type of pattern matching:
                - 'contains': Product name contains the pattern (default)
                - 'startswith': Product name starts with the pattern
                - 'endswith': Product name ends with the pattern
                - 'eq': Exact match (same as get_product_by_name)
            max_results: Maximum number of results to return (default: 100)

        Returns:
            List of ProductInfo objects matching the pattern

        Raises:
            ValueError: If match_type is not one of the valid options

        Examples:
            >>> catalog = CatalogSearch()
            >>> # Find all S2 products from a specific date
            >>> products = catalog.search_products_by_name('*_20240101*', match_type='contains')
            >>> # Find products starting with a specific mission identifier
            >>> products = catalog.search_products_by_name('S1A_', match_type='startswith')
            >>> # Find level-2 processing products
            >>> products = catalog.search_products_by_name('L2A', match_type='contains', max_results=50)
        """
        # Validate match_type
        valid_types = ["contains", "startswith", "endswith", "eq"]
        if match_type not in valid_types:
            raise ValueError(f"match_type must be one of {valid_types}, got '{match_type}'")

        # Build OData filter based on operator type
        if match_type == "contains":
            # Use OData v4 `contains(Name, 'pattern')` for substring matches
            filter_string = f"contains(Name, '{name_pattern}')"
        elif match_type == "startswith":
            filter_string = f"startswith(Name, '{name_pattern}')"
        elif match_type == "endswith":
            filter_string = f"endswith(Name, '{name_pattern}')"
        else:  # eq
            filter_string = f"Name eq '{name_pattern}'"

        # Build full query URL
        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {
            "$filter": filter_string,
            "$top": max_results,
            "$orderby": "ContentDate/Start desc",
            "$expand": "Attributes",
        }

        logger.info(f"Searching for products by name with filter: {filter_string}")

        try:
            headers = self.auth.get_headers()

            def make_request():
                return requests.get(url, params=params, headers=headers, timeout=60)

            response = self._retry_request(make_request)

            if response.status_code == 200:
                data = response.json()
                products = self._parse_products(data)
                logger.info(f"Found {len(products)} products matching pattern '{name_pattern}'")
                return products
            else:
                logger.error(f"Name search failed. Status: {response.status_code}, Response: {response.text}")
                return []

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return []
