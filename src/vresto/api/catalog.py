"""Catalog search module for Copernicus Data Space Ecosystem."""

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
        return f"POLYGON(({self.west} {self.south},{self.east} {self.south},{self.east} {self.north},{self.west} {self.north},{self.west} {self.south}))"

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


class CatalogSearch:
    """Search Copernicus catalog for products."""

    def __init__(self, auth: Optional[CopernicusAuth] = None, config: Optional[CopernicusConfig] = None):
        """Initialize catalog search.

        Args:
            auth: CopernicusAuth instance. If not provided, will create one.
            config: CopernicusConfig instance. If not provided, will create one from env vars.
        """
        self.config = config or CopernicusConfig()
        self.auth = auth or CopernicusAuth(self.config)

    def search_products(
        self,
        bbox: BoundingBox,
        start_date: str,
        end_date: Optional[str] = None,
        collection: str = "SENTINEL-2",
        max_cloud_cover: Optional[float] = None,
        max_results: int = 100,
    ) -> list[ProductInfo]:
        """Search for products in the catalog.

        Args:
            bbox: Bounding box for spatial search
            start_date: Start date in format 'YYYY-MM-DD'
            end_date: End date in format 'YYYY-MM-DD'. If not provided, uses start_date.
            collection: Product collection (e.g., 'SENTINEL-2', 'SENTINEL-1')
            max_cloud_cover: Maximum cloud cover percentage (0-100). Only for optical products.
            max_results: Maximum number of results to return

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

        # Combine filters
        filter_string = " and ".join(filters)

        # Build full query URL
        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {"$filter": filter_string, "$top": max_results, "$orderby": "ContentDate/Start desc", "$expand": "Attributes"}

        logger.info(f"Searching catalog with filter: {filter_string}")

        try:
            headers = self.auth.get_headers()
            response = requests.get(url, params=params, headers=headers, timeout=60)

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
            product_name: Exact product name

        Returns:
            ProductInfo object or None if not found
        """
        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {"$filter": f"Name eq '{product_name}'", "$expand": "Attributes"}

        try:
            headers = self.auth.get_headers()
            response = requests.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()
                products = self._parse_products(data)
                return products[0] if products else None
            else:
                logger.error(f"Product lookup failed. Status: {response.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
