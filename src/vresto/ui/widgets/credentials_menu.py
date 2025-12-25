"""Credentials management side menu widget.

Allows users to:
- View existing S3 credentials from .env file
- Input/update S3 credentials via UI form
- Save credentials to .env file
"""

import os
from pathlib import Path
from typing import Callable, Optional

from nicegui import ui

from vresto.api.config import CopernicusConfig
from vresto.api.env_loader import parse_env_file, write_env_file


class CredentialsMenu:
    """Side menu for managing S3 credentials.

    Provides UI for:
    - Reading S3 credentials from .env file
    - Inputting S3 access ID and secret key
    - Saving credentials back to .env file
    """

    def __init__(self, env_path: Optional[Path] = None, on_credentials_updated: Optional[Callable] = None):
        """Initialize credentials menu.

        Args:
            env_path: Path to .env file. Defaults to project root/.env
            on_credentials_updated: Optional callback when credentials are updated
        """
        self.env_path = env_path or (Path.cwd() / ".env")
        self.on_credentials_updated = on_credentials_updated

        # UI elements
        self.access_key_input = None
        self.secret_key_input = None
        self.status_label = None
        self.save_button = None

        # Load current credentials
        self._load_credentials()

    def _load_credentials(self):
        """Load current S3 credentials from .env file and environment."""
        self.config = CopernicusConfig()
        self.current_access_key = self.config.s3_access_key
        self.current_secret_key = self.config.s3_secret_key

    def _get_env_data(self) -> dict:
        """Get all data from .env file."""
        if self.env_path.exists():
            return parse_env_file(self.env_path)
        return {}

    def _save_credentials_to_env(self, access_key: str, secret_key: str) -> bool:
        """Save S3 credentials to .env file.

        Args:
            access_key: S3 access key ID
            secret_key: S3 secret key

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get existing env data
            env_data = self._get_env_data()

            # Update with new credentials
            if access_key:
                env_data["COPERNICUS_S3_ACCESS_KEY"] = access_key
            if secret_key:
                env_data["COPERNICUS_S3_SECRET_KEY"] = secret_key

            # Write back to file
            write_env_file(self.env_path, env_data)

            # Also update environment variables
            if access_key:
                os.environ["COPERNICUS_S3_ACCESS_KEY"] = access_key
            if secret_key:
                os.environ["COPERNICUS_S3_SECRET_KEY"] = secret_key

            # Reload config
            self._load_credentials()
            return True
        except Exception as e:
            print(f"Error saving credentials: {e}")
            return False

    def create(self) -> ui.element:
        """Create and return the credentials menu UI element.

        Returns:
            The root UI element of the credentials menu
        """
        with ui.card().classes("p-4 max-w-sm") as menu_card:
            ui.label("S3 Credentials").classes("text-lg font-bold mb-3")

            # Status info
            if self.current_access_key and self.current_secret_key:
                ui.label("✅ Credentials found").classes("text-sm text-green-600 mb-2")
                ui.label(f"Access ID: {self.current_access_key[:10]}...").classes("text-xs text-gray-600 break-words mb-2")
            else:
                ui.label("⚠️ No credentials configured").classes("text-sm text-orange-600 mb-2")

            ui.separator().classes("my-3")

            # Form section
            ui.label("Update S3 Credentials").classes("text-sm font-semibold mb-2")

            self.access_key_input = ui.input(
                label="S3 Access Key ID",
                value=self.current_access_key or "",
                placeholder="Your access key ID",
            ).classes("w-full mb-2")
            self.access_key_input.props("clearable")

            self.secret_key_input = ui.input(
                label="S3 Secret Key",
                value=self.current_secret_key or "",
                placeholder="Your secret key",
                password=True,
            ).classes("w-full mb-3")
            self.secret_key_input.props("clearable")

            # Buttons row
            with ui.row().classes("w-full gap-2"):

                async def _on_save_click():
                    await self._handle_save()

                self.save_button = ui.button("💾 Save Credentials", on_click=_on_save_click).classes("flex-1")
                self.save_button.props("color=primary")

                def _on_clear_click():
                    self.access_key_input.set_value("")
                    self.secret_key_input.set_value("")

                clear_button = ui.button("Clear", on_click=_on_clear_click).classes("flex-1")
                clear_button.props("color=warning")

            # Status message
            ui.separator().classes("my-3")
            self.status_label = ui.label("").classes("text-sm text-gray-600 break-words min-h-5")

            # Info section
            ui.separator().classes("my-3")
            ui.label("About S3 Credentials:").classes("text-xs font-semibold text-gray-600 mb-1")
            ui.label("These are temporary credentials for accessing Copernicus data via S3. Leave credentials in the form empty to use values from .env file.").classes("text-xs text-gray-500 break-words")

        return menu_card

    async def _handle_save(self):
        """Handle save button click."""
        access_key = self.access_key_input.value.strip() if self.access_key_input else ""
        secret_key = self.secret_key_input.value.strip() if self.secret_key_input else ""

        # Validate that at least one field is filled if we're trying to save
        if not access_key and not secret_key:
            self.status_label.set_text("⚠️ Please enter at least one credential")
            ui.notify("Please enter credentials", type="warning")
            return

        # Both should be filled for valid S3 credentials
        if access_key and not secret_key:
            self.status_label.set_text("⚠️ Please enter both access key and secret key")
            ui.notify("Please enter both credentials", type="warning")
            return

        if not access_key and secret_key:
            self.status_label.set_text("⚠️ Please enter both access key and secret key")
            ui.notify("Please enter both credentials", type="warning")
            return

        # Save credentials
        if self._save_credentials_to_env(access_key, secret_key):
            self.status_label.set_text("✅ Credentials saved successfully!")
            ui.notify("Credentials saved", type="positive")

            # Call callback if provided
            if self.on_credentials_updated:
                self.on_credentials_updated()
        else:
            self.status_label.set_text("❌ Failed to save credentials")
            ui.notify("Failed to save credentials", type="negative")
