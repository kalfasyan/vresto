"""Configuration for Copernicus Data Space API."""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Try to load .env file from project root
_project_root = Path(__file__).parent.parent.parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class CopernicusConfig:
    """Configuration for Copernicus Data Space Ecosystem API."""

    # API endpoints (can be overridden by environment variables)
    AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    ODATA_BASE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
    STAC_BASE_URL = "https://stac.dataspace.copernicus.eu/v1"
    S3_ENDPOINT_URL = os.getenv("COPERNICUS_S3_ENDPOINT", "https://eodata.dataspace.copernicus.eu")
    S3_KEYS_MANAGER_URL = "https://s3-keys-manager.cloudferro.com/api/user/credentials"

    # Authentication
    CLIENT_ID = "cdse-public"

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        s3_access_key: Optional[str] = None,
        s3_secret_key: Optional[str] = None,
        s3_endpoint: Optional[str] = None,
        search_provider: Optional[str] = None,
    ):
        """Initialize configuration.

        Args:
            username: Copernicus username. If not provided, will try to get from env var COPERNICUS_USERNAME
            password: Copernicus password. If not provided, will try to get from env var COPERNICUS_PASSWORD
            s3_access_key: Static S3 access key. If not provided, will try to get from env var COPERNICUS_S3_ACCESS_KEY
            s3_secret_key: Static S3 secret key. If not provided, will try to get from env var COPERNICUS_S3_SECRET_KEY
            s3_endpoint: S3 endpoint URL. If not provided, will try to get from env var COPERNICUS_S3_ENDPOINT
            search_provider: Search provider to use ('odata' or 'stac'). Defaults to 'odata'.
        """
        self.username = username or os.getenv("COPERNICUS_USERNAME")
        self.password = password or os.getenv("COPERNICUS_PASSWORD")
        self.s3_access_key = s3_access_key or os.getenv("COPERNICUS_S3_ACCESS_KEY")
        self.s3_secret_key = s3_secret_key or os.getenv("COPERNICUS_S3_SECRET_KEY")
        self.s3_endpoint = s3_endpoint or os.getenv("COPERNICUS_S3_ENDPOINT", self.S3_ENDPOINT_URL)
        self.search_provider = search_provider or os.getenv("VRESTO_SEARCH_PROVIDER", "odata")

    def validate(self) -> bool:
        """Check if credentials are configured."""
        return bool(self.username and self.password)

    def has_static_s3_credentials(self) -> bool:
        """Check if static S3 credentials are configured.

        Returns:
            True if both S3 access key and secret key are set
        """
        return bool(self.s3_access_key and self.s3_secret_key)

    def get_credentials(self) -> tuple[str, str]:
        """Get credentials or raise error if not configured.

        Returns:
            Tuple of (username, password)

        Raises:
            ValueError: If credentials are not configured
        """
        if not self.validate():
            raise ValueError("Copernicus credentials not configured. Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables or provide them when initializing CopernicusConfig.")
        return self.username, self.password

    def get_s3_credentials(self) -> tuple[str, str]:
        """Get S3 credentials or raise error if not configured.

        Returns:
            Tuple of (access_key, secret_key)

        Raises:
            ValueError: If S3 credentials are not configured
        """
        if not self.has_static_s3_credentials():
            raise ValueError("Static S3 credentials not configured. Set COPERNICUS_S3_ACCESS_KEY and COPERNICUS_S3_SECRET_KEY environment variables or provide them when initializing CopernicusConfig.")
        return self.s3_access_key, self.s3_secret_key
