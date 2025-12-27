"""Catalog search module for Copernicus Data Space Ecosystem."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

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

    def to_list(self) -> list[float]:
        """Convert to list [west, south, east, north]."""
        return [float(self.west), float(self.south), float(self.east), float(self.north)]


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
    assets: Optional[dict] = None  # Store STAC assets if available

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


class BaseCatalogSearch(ABC):
    """Abstract base class for catalog search providers."""

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
        """Execute HTTP request with exponential backoff retry logic."""
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
                raise

        if last_exception:
            raise last_exception
        return None

    @abstractmethod
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
        """Search for products in the catalog."""
        pass

    @abstractmethod
    def get_product_by_name(self, product_name: str) -> Optional[ProductInfo]:
        """Get product details by exact name."""
        pass

    @abstractmethod
    def search_products_by_name(
        self,
        name_pattern: str,
        match_type: str = "contains",
        max_results: int = 100,
    ) -> list[ProductInfo]:
        """Search for products by name pattern."""
        pass


class ODataCatalogSearch(BaseCatalogSearch):
    """OData-based implementation of catalog search."""

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
        if end_date is None:
            end_date = start_date

        filters = []
        filters.append(f"Collection/Name eq '{collection}'")
        filters.append(f"ContentDate/Start ge {start_date}T00:00:00.000Z")
        filters.append(f"ContentDate/Start le {end_date}T23:59:59.999Z")

        wkt_polygon = bbox.to_wkt()
        filters.append(f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt_polygon}')")

        if max_cloud_cover is not None:
            filters.append(f"Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le {max_cloud_cover})")

        if product_level is not None:
            if collection == "SENTINEL-2" and product_level in ("L1C", "L2A"):
                msil = f"MSI{product_level}"
                filters.append(f"contains(Name, '{msil}')")
            elif collection == "LANDSAT-8" and product_level in ("L0", "L1GT", "L1GS", "L1TP", "L2SP"):
                filters.append(f"contains(Name, '{product_level}')")

        filter_string = " and ".join(filters)
        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {"$filter": filter_string, "$top": max_results, "$orderby": "ContentDate/Start desc", "$expand": "Attributes"}

        logger.info(f"OData search filter: {filter_string}")

        try:
            headers = self.auth.get_headers()

            def make_request():
                return requests.get(url, params=params, headers=headers, timeout=60)

            response = self._retry_request(make_request)
            if response.status_code == 200:
                return self._parse_products(response.json())
            return []
        except requests.RequestException as e:
            logger.error(f"OData search failed: {e}")
            return []

    def _parse_products(self, response_data: dict) -> list[ProductInfo]:
        products = []
        for item in response_data.get("value", []):
            cloud_cover = None
            attributes = item.get("Attributes", [])
            for attr in attributes:
                if attr.get("Name") == "cloudCover":
                    cloud_cover = attr.get("Value")
                    break

            sensing_date = item.get("ContentDate", {}).get("Start", "")
            if sensing_date:
                try:
                    dt = datetime.fromisoformat(sensing_date.replace("Z", "+00:00"))
                    sensing_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

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
        url = f"{self.config.ODATA_BASE_URL}/Products"
        names_to_try = [product_name]
        if not product_name.endswith(".SAFE"):
            names_to_try.append(f"{product_name}.SAFE")
        elif product_name.endswith(".SAFE"):
            names_to_try.append(product_name[:-5])

        try:
            headers = self.auth.get_headers()
            for name_variant in names_to_try:
                params = {"$filter": f"Name eq '{name_variant}'", "$expand": "Attributes"}
                response = requests.get(url, params=params, headers=headers, timeout=30)
                if response.status_code == 200:
                    products = self._parse_products(response.json())
                    if products:
                        return products[0]
            return None
        except requests.RequestException as e:
            logger.error(f"OData get_product_by_name failed: {e}")
            return None

    def search_products_by_name(
        self,
        name_pattern: str,
        match_type: str = "contains",
        max_results: int = 100,
    ) -> list[ProductInfo]:
        valid_types = ["contains", "startswith", "endswith", "eq"]
        if match_type not in valid_types:
            raise ValueError(f"match_type must be one of {valid_types}, got '{match_type}'")

        if match_type == "contains":
            filter_string = f"contains(Name, '{name_pattern}')"
        elif match_type == "startswith":
            filter_string = f"startswith(Name, '{name_pattern}')"
        elif match_type == "endswith":
            filter_string = f"endswith(Name, '{name_pattern}')"
        else:
            filter_string = f"Name eq '{name_pattern}'"

        url = f"{self.config.ODATA_BASE_URL}/Products"
        params = {"$filter": filter_string, "$top": max_results, "$orderby": "ContentDate/Start desc", "$expand": "Attributes"}

        try:
            headers = self.auth.get_headers()

            def make_request():
                return requests.get(url, params=params, headers=headers, timeout=60)

            response = self._retry_request(make_request)
            if response.status_code == 200:
                return self._parse_products(response.json())
            return []
        except requests.RequestException as e:
            logger.error(f"OData search_products_by_name failed: {e}")
            return []


class STACCatalogSearch(BaseCatalogSearch):
    """STAC-based implementation of catalog search using pystac-client."""

    def __init__(self, auth: Optional[CopernicusAuth] = None, config: Optional[CopernicusConfig] = None, max_retries: int = 5):
        super().__init__(auth, config, max_retries)
        from pystac_client import Client

        self.client = Client.open(self.config.STAC_BASE_URL)
        # Internal OData searcher for operations where STAC is too slow (e.g., name search)
        self._odata_searcher = ODataCatalogSearch(auth, config, max_retries)

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
        from .stac_mappings import get_stac_collection_id

        stac_collection = get_stac_collection_id(collection, product_level)
        if not stac_collection:
            logger.warning(f"No STAC collection mapping for {collection} {product_level}")
            return []

        # STAC API expects ISO8601 interval. If only YYYY-MM-DD is provided,
        # expand it to cover the full day(s).
        if len(start_date) == 10:
            start_date_iso = f"{start_date}T00:00:00Z"
        else:
            start_date_iso = start_date

        if end_date:
            if len(end_date) == 10:
                end_date_iso = f"{end_date}T23:59:59Z"
            else:
                end_date_iso = end_date
        else:
            # If no end_date, cover the full start_date day if it was YYYY-MM-DD
            if len(start_date) == 10:
                end_date_iso = f"{start_date}T23:59:59Z"
            else:
                end_date_iso = start_date_iso

        datetime_range = f"{start_date_iso}/{end_date_iso}"

        # CQL2 filter for cloud cover if applicable
        filter_dict = None
        if max_cloud_cover is not None:
            filter_dict = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, max_cloud_cover]}

        try:
            search_params = {
                "collections": [stac_collection],
                "bbox": bbox.to_list(),
                "datetime": datetime_range,
                "max_items": max_results,
            }
            if filter_dict:
                search_params["filter"] = filter_dict
                search_params["filter_lang"] = "cql2-json"

            search = self.client.search(**search_params)

            products = []
            for item in search.items():
                products.append(self._parse_stac_item(item))
            return products
        except Exception as e:
            logger.error(f"STAC search failed: {e}")
            return []

    def _parse_stac_item(self, item) -> ProductInfo:
        props = item.properties
        # Convert datetime to sensing_date format
        sensing_date = props.get("datetime", "")
        if sensing_date:
            try:
                from dateutil import parser

                dt = parser.isoparse(sensing_date)
                sensing_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        # CDSE STAC items usually have an S3 path in assets or properties
        # Try multiple locations as it varies by collection
        s3_path = props.get("s3:path") or props.get("s3_path")

        if not s3_path and "product" in item.assets:
            s3_path = item.assets["product"].href
        if not s3_path and "Product" in item.assets:
            s3_path = item.assets["Product"].href

        # Normalize s3_path: Ensure it doesn't contain 's3://https://' due to incorrect concatenation
        if s3_path and s3_path.startswith("https://zipper.dataspace.copernicus.eu"):
            # This is an HTTPS URL from the OData-based STAC implementation
            # We keep it as-is and will handle it in ProductsManager
            pass
        elif s3_path and s3_path.startswith("s3://https://"):
            s3_path = s3_path[5:]  # Strip redundant s3:// prefix

        if not s3_path and "safe_manifest" in item.assets:
            # Derive product path from safe_manifest path if needed
            manifest_href = item.assets["safe_manifest"].href
            if manifest_href.endswith("/manifest.safe"):
                s3_path = manifest_href[:-13]

        size_mb = 0
        if "product" in item.assets:
            size_mb = item.assets["product"].extra_fields.get("file:size", 0) / (1024 * 1024)

        return ProductInfo(
            id=item.id,
            name=props.get("title", item.id),
            collection=item.collection_id,
            sensing_date=sensing_date,
            size_mb=size_mb,
            s3_path=s3_path,
            cloud_cover=props.get("eo:cloud_cover"),
            footprint=item.geometry,
            assets={k: v.to_dict() for k, v in item.assets.items()},
        )

    def get_product_by_name(self, product_name: str) -> Optional[ProductInfo]:
        # In STAC, items can often be fetched directly by ID if it matches product name
        try:
            # We don't necessarily know the collection, so we might need to search or try specific ones
            # For simplicity, we search with a filter on item ID
            search = self.client.search(ids=[product_name])
            items = list(search.items())
            if items:
                return self._parse_stac_item(items[0])

            # Try .SAFE variant
            if not product_name.endswith(".SAFE"):
                search = self.client.search(ids=[f"{product_name}.SAFE"])
                items = list(search.items())
                if items:
                    return self._parse_stac_item(items[0])

            return None
        except Exception as e:
            logger.error(f"STAC get_product_by_name failed: {e}")
            return None

    def search_products_by_name(
        self,
        name_pattern: str,
        match_type: str = "contains",
        max_results: int = 100,
        force_stac: bool = False,
    ) -> list[ProductInfo]:
        """Search for products by name. Falls back to OData for performance.

        STAC global search across all collections is extremely slow on CDSE.
        OData provides much faster name-based filtering and is used as an
        internal optimization for this specific operation.

        Args:
            name_pattern: Name pattern to search for
            match_type: 'contains', 'startswith', 'endswith', or 'eq'
            max_results: Maximum number of results
            force_stac: If True, force search via STAC instead of falling back to OData.
                        Note: This is significantly slower on CDSE.
        """
        if not force_stac:
            logger.info(f"Using OData backend for name search performance: {name_pattern} ({match_type})")
            return self._odata_searcher.search_products_by_name(name_pattern, match_type, max_results)

        logger.warning(f"Forcing STAC for name search: {name_pattern} ({match_type}) - this will be slow!")

        # CQL2 filter for name matching
        if match_type == "eq":
            filter_dict = {"op": "=", "args": [{"property": "title"}, name_pattern]}
        elif match_type == "contains":
            filter_dict = {"op": "like", "args": [{"property": "title"}, f"%{name_pattern}%"]}
        elif match_type == "startswith":
            filter_dict = {"op": "like", "args": [{"property": "title"}, f"{name_pattern}%"]}
        elif match_type == "endswith":
            filter_dict = {"op": "like", "args": [{"property": "title"}, f"%{name_pattern}"]}
        else:
            raise ValueError(f"Unsupported match_type for STAC: {match_type}")

        try:
            # Global search (no collection or bbox) is slow
            search = self.client.search(
                filter=filter_dict,
                filter_lang="cql2-json",
                max_items=max_results,
            )

            products = []
            for item in search.items():
                products.append(self._parse_stac_item(item))
            return products
        except Exception as e:
            logger.error(f"STAC name search failed: {e}")
            return []


def CatalogSearch(auth: Optional[CopernicusAuth] = None, config: Optional[CopernicusConfig] = None, max_retries: int = 5) -> Union[ODataCatalogSearch, STACCatalogSearch]:
    """Factory function to create the configured catalog search provider."""
    cfg = config or CopernicusConfig()
    if cfg.search_provider == "stac":
        logger.info("Using STAC catalog search")
        return STACCatalogSearch(auth, cfg, max_retries)
    else:
        logger.info("Using OData catalog search")
        return ODataCatalogSearch(auth, cfg, max_retries)
