"""Copernicus Data Space API for product search and access."""

from .auth import AuthenticationError, CopernicusAuth
from .catalog import BoundingBox, CatalogSearch, ProductInfo
from .config import CopernicusConfig

__all__ = [
    "CopernicusAuth",
    "CopernicusConfig",
    "CatalogSearch",
    "BoundingBox",
    "ProductInfo",
    "AuthenticationError",
]
