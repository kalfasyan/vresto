"""Copernicus Data Space API for product search and access."""

from .auth import AuthenticationError, CopernicusAuth, get_shared_auth
from .catalog import BoundingBox, CatalogSearch, ProductInfo
from .config import CopernicusConfig

__all__ = [
    "CopernicusAuth",
    "CopernicusConfig",
    "CatalogSearch",
    "BoundingBox",
    "ProductInfo",
    "AuthenticationError",
    "get_shared_auth",
]
