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

    # API endpoints
    AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    ODATA_BASE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
    S3_ENDPOINT_URL = "https://eodata.dataspace.copernicus.eu"
    S3_KEYS_MANAGER_URL = "https://s3-keys-manager.cloudferro.com/api/user/credentials"

    # Authentication
    CLIENT_ID = "cdse-public"

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        """Initialize configuration.

        Args:
            username: Copernicus username. If not provided, will try to get from env var COPERNICUS_USERNAME
            password: Copernicus password. If not provided, will try to get from env var COPERNICUS_PASSWORD
        """
        self.username = username or os.getenv("COPERNICUS_USERNAME")
        self.password = password or os.getenv("COPERNICUS_PASSWORD")

    def validate(self) -> bool:
        """Check if credentials are configured."""
        return bool(self.username and self.password)

    def get_credentials(self) -> tuple[str, str]:
        """Get credentials or raise error if not configured.

        Returns:
            Tuple of (username, password)

        Raises:
            ValueError: If credentials are not configured
        """
        if not self.validate():
            raise ValueError(
                "Copernicus credentials not configured. "
                "Set COPERNICUS_USERNAME and COPERNICUS_PASSWORD environment variables "
                "or provide them when initializing CopernicusConfig."
            )
        return self.username, self.password
