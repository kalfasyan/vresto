"""Authentication module for Copernicus Data Space Ecosystem."""

import json
from typing import Optional

import requests
from loguru import logger

from .config import CopernicusConfig


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class CopernicusAuth:
    """Handle authentication with Copernicus Data Space Ecosystem."""

    def __init__(self, config: Optional[CopernicusConfig] = None):
        """Initialize authentication handler.

        Args:
            config: CopernicusConfig instance. If not provided, will create one from env vars.
        """
        self.config = config or CopernicusConfig()
        self._access_token: Optional[str] = None
        self._s3_credentials: Optional[dict] = None

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Get access token for API calls.

        Args:
            force_refresh: Force getting a new token even if one is cached

        Returns:
            Access token string

        Raises:
            AuthenticationError: If authentication fails
        """
        if self._access_token and not force_refresh:
            return self._access_token

        username, password = self.config.get_credentials()

        auth_data = {
            "client_id": self.config.CLIENT_ID,
            "grant_type": "password",
            "username": username,
            "password": password,
        }

        try:
            response = requests.post(self.config.AUTH_URL, data=auth_data, verify=True, allow_redirects=False, timeout=30)

            if response.status_code == 200:
                self._access_token = json.loads(response.text)["access_token"]
                logger.info("Successfully obtained access token")
                return self._access_token
            else:
                raise AuthenticationError(f"Failed to retrieve access token. Status code: {response.status_code}, Response: {response.text}")
        except requests.RequestException as e:
            raise AuthenticationError(f"Request failed: {e}")

    def get_headers(self) -> dict[str, str]:
        """Get authentication headers for API requests.

        Returns:
            Dictionary with Authorization and Accept headers
        """
        token = self.get_access_token()
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get_s3_credentials(self, force_refresh: bool = False) -> dict:
        """Get temporary S3 credentials for accessing data.

        Args:
            force_refresh: Force getting new credentials even if cached

        Returns:
            Dictionary with 'access_id' and 'secret' keys

        Raises:
            AuthenticationError: If credential creation fails
        """
        if self._s3_credentials and not force_refresh:
            return self._s3_credentials

        headers = self.get_headers()

        try:
            response = requests.post(self.config.S3_KEYS_MANAGER_URL, headers=headers, timeout=30)

            if response.status_code == 200:
                self._s3_credentials = response.json()
                logger.info(f"Successfully created temporary S3 credentials. Access ID: {self._s3_credentials['access_id']}")
                return self._s3_credentials
            else:
                raise AuthenticationError(f"Failed to create temporary S3 credentials. Status code: {response.status_code}, Response: {response.text}")
        except requests.RequestException as e:
            raise AuthenticationError(f"Request failed: {e}")

    def delete_s3_credentials(self, access_id: Optional[str] = None) -> bool:
        """Delete temporary S3 credentials.

        Args:
            access_id: Access ID to delete. If not provided, uses cached credentials.

        Returns:
            True if deletion was successful
        """
        if not access_id and self._s3_credentials:
            access_id = self._s3_credentials.get("access_id")

        if not access_id:
            logger.warning("No S3 credentials to delete")
            return False

        headers = self.get_headers()
        delete_url = f"{self.config.S3_KEYS_MANAGER_URL}/access_id/{access_id}"

        try:
            response = requests.delete(delete_url, headers=headers, timeout=30)

            if response.status_code == 204:
                logger.info(f"Successfully deleted S3 credentials: {access_id}")
                if self._s3_credentials and self._s3_credentials.get("access_id") == access_id:
                    self._s3_credentials = None
                return True
            else:
                logger.warning(f"Failed to delete S3 credentials. Status code: {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"Request to delete S3 credentials failed: {e}")
            return False
