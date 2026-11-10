"""Credentials management side menu widget.

Allows users to:
- View existing Copernicus API and S3 credentials from .env file
- Input/update Copernicus API credentials (required for downloads; search works without them) via UI form
- Input/update S3 credentials (optional for downloads) via UI form
- Save all credentials to .env file

In public mode (a shared website) the menu is given a ``VisitorCredentials`` and keeps what
the visitor types in memory for their browser tab instead, without touching ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from nicegui import ui

from vresto.api.auth import reset_shared_auth
from vresto.api.config import CopernicusConfig
from vresto.api.env_loader import parse_env_file, write_env_file

if TYPE_CHECKING:
    from vresto.ui.public_mode import VisitorCredentials

# Shown wherever a download is blocked because neither an account nor S3 keys are set.
DOWNLOAD_CREDENTIALS_HINT = "Searching works without an account; add Copernicus credentials in ☰ → Settings to download."

# Shown when tile streaming is attempted without the static S3 keys it reads from.
STREAM_CREDENTIALS_HINT = "Tile streaming needs S3 credentials; add them in ☰ → Settings. Searching works without an account."


def download_credentials_configured(visitor: Optional[VisitorCredentials] = None) -> bool:
    """Check whether downloads can be authorised.

    Downloads work with a Copernicus account (username and password), or with
    static S3 keys on their own.

    Args:
        visitor: In public mode, the visitor's own credentials; the server's are not used.

    Returns:
        True if either the account or the static S3 keys are configured
    """
    if visitor is not None:
        return visitor.has_download_access()
    config = CopernicusConfig()
    return config.has_credentials() or config.has_static_s3_credentials()


class CredentialsMenu:
    """Side menu for managing Copernicus API and S3 credentials.

    Provides UI for:
    - Reading Copernicus API credentials (username, password) from .env file
    - Reading S3 credentials from .env file
    - Inputting/updating all credentials via UI form
    - Saving credentials back to .env file
    """

    def __init__(self, env_path: Optional[Path] = None, on_credentials_updated: Optional[Callable] = None, visitor: Optional[VisitorCredentials] = None):
        """Initialize credentials menu.

        Args:
            env_path: Path to .env file. Defaults to project root/.env
            on_credentials_updated: Optional callback when credentials are updated
            visitor: In public mode, where this visitor's credentials are kept instead of .env
        """
        self.env_path = env_path or (Path.cwd() / ".env")
        self.on_credentials_updated = on_credentials_updated
        self.visitor = visitor

        # UI elements
        self.username_input = None
        self.password_input = None
        self.access_key_input = None
        self.secret_key_input = None
        self.status_label = None
        self.save_button = None

        # Load current credentials
        self._load_credentials()

    def _load_credentials(self):
        """Load current credentials from .env file and environment (or the visitor's, in public mode)."""
        # In public mode the server's own credentials must never be shown to a visitor.
        self.config = self.visitor.config() if self.visitor is not None else CopernicusConfig()
        self.current_username = self.config.username
        self.current_password = self.config.password
        self.current_access_key = self.config.s3_access_key
        self.current_secret_key = self.config.s3_secret_key
        self.current_search_provider = self.config.search_provider

    def _get_env_data(self) -> dict:
        """Get all data from .env file."""
        if self.env_path.exists():
            return parse_env_file(self.env_path)
        return {}

    def _save_credentials_to_env(
        self,
        username: str = "",
        password: str = "",
        access_key: str = "",
        secret_key: str = "",
        search_provider: str = "",
        s3_endpoint: str = "",
    ) -> bool:
        """Save credentials and settings to .env file.

        Args:
            username: Copernicus API username
            password: Copernicus API password
            access_key: S3 access key ID
            secret_key: S3 secret key
            search_provider: Search backend provider ('odata' or 'stac')
            s3_endpoint: Custom S3 endpoint URL

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get existing env data
            env_data = self._get_env_data()

            # Update with new settings
            if username:
                env_data["COPERNICUS_USERNAME"] = username
            if password:
                env_data["COPERNICUS_PASSWORD"] = password
            if access_key:
                env_data["COPERNICUS_S3_ACCESS_KEY"] = access_key
            if secret_key:
                env_data["COPERNICUS_S3_SECRET_KEY"] = secret_key
            if s3_endpoint:
                env_data["COPERNICUS_S3_ENDPOINT"] = s3_endpoint
            if search_provider:
                env_data["VRESTO_SEARCH_PROVIDER"] = search_provider

            # Write back to file
            write_env_file(self.env_path, env_data)

            # Also update environment variables
            if username:
                os.environ["COPERNICUS_USERNAME"] = username
            if password:
                os.environ["COPERNICUS_PASSWORD"] = password
            if access_key:
                os.environ["COPERNICUS_S3_ACCESS_KEY"] = access_key
            if secret_key:
                os.environ["COPERNICUS_S3_SECRET_KEY"] = secret_key
            if s3_endpoint:
                os.environ["COPERNICUS_S3_ENDPOINT"] = s3_endpoint
            if search_provider:
                os.environ["VRESTO_SEARCH_PROVIDER"] = search_provider

            # Reload config
            self._load_credentials()
            # The process-wide CopernicusAuth keeps the config it was built with;
            # drop it so the new credentials are used from the next request on.
            reset_shared_auth()
            return True
        except Exception as e:
            print(f"Error saving credentials: {e}")
            return False

    def create(self) -> ui.element:
        """Create and return the credentials menu UI element.

        Returns:
            The root UI element of the credentials menu
        """
        self.backend_select = None
        with ui.card().classes("p-4 w-full") as menu_card:
            if self.visitor is not None:
                # The search backend is a server-wide setting, so visitors of a shared site can't change it.
                with ui.row().classes("w-full items-start gap-1 mb-3 p-2 rounded bg-blue-50 border border-blue-200"):
                    ui.icon("lock", size="xs").classes("text-blue-500 mt-0.5 flex-shrink-0")
                    ui.label(
                        "This is a shared public site. Searching and streaming need no account. Credentials you add here are kept only for this browser tab, used only for your own downloads and previews, and forgotten when you close it."
                    ).classes("text-xs text-blue-700")
            else:
                # Search Settings Section
                ui.label("Search Settings").classes("text-lg font-bold mb-2")

                ui.label("Search Backend Provider").classes("text-sm font-semibold mb-1")
                self.backend_select = ui.select(
                    options={"odata": "OData (Mature)", "stac": "STAC (Modern)"},
                    value=self.current_search_provider or "odata",
                ).classes("w-full mb-4")

                ui.separator().classes("my-3")

            # API Credentials Section
            ui.label("Copernicus API Credentials").classes("text-lg font-bold mb-2")

            # Status for API credentials
            if self.current_username and self.current_password:
                ui.label("✅ API credentials found").classes("text-sm text-green-600 mb-2")
                ui.label(f"Username: {self.current_username}").classes("text-xs text-gray-600 break-words mb-2")
            else:
                ui.label("⚠️ No API credentials configured").classes("text-sm text-orange-600 mb-2")

            ui.separator().classes("my-2")

            # API Credentials form
            ui.label("Update API Credentials").classes("text-sm font-semibold mb-2")

            self.username_input = ui.input(
                label="Copernicus Username",
                value=self.current_username or "",
                placeholder="Your email or username",
            ).classes("w-full mb-2")
            self.username_input.props("clearable")

            self.password_input = ui.input(
                label="Copernicus Password",
                value=self.current_password or "",
                placeholder="Your password",
                password=True,
            ).classes("w-full mb-3")
            self.password_input.props("clearable")

            ui.separator().classes("my-3")

            # S3 Credentials Section
            ui.label("S3 Credentials (Optional)").classes("text-lg font-bold mb-2")

            # Status info for S3
            if self.current_access_key and self.current_secret_key:
                ui.label("✅ S3 credentials found").classes("text-sm text-green-600 mb-2")
                ui.label(f"Access ID: {self.current_access_key[:10]}...").classes("text-xs text-gray-600 break-words mb-2")
            else:
                ui.label("⚠️ No S3 credentials configured").classes("text-sm text-orange-600 mb-2")

            ui.separator().classes("my-2")

            # Form section for S3
            ui.label("Update S3 Credentials").classes("text-sm font-semibold mb-2")

            self.access_key_input = ui.input(
                label="S3 Access Key ID",
                value=self.current_access_key or "",
                placeholder="Your S3 access key ID",
            ).classes("w-full mb-2")
            self.access_key_input.props("clearable")

            self.secret_key_input = ui.input(
                label="S3 Secret Key",
                value=self.current_secret_key or "",
                placeholder="Your S3 secret key",
                password=True,
            ).classes("w-full mb-3")
            self.secret_key_input.props("clearable")

            # Buttons row
            with ui.row().classes("w-full gap-2"):

                async def _on_save_click():
                    await self._handle_save()

                save_label = "🔐 Use for this tab" if self.visitor is not None else "💾 Save All"
                self.save_button = ui.button(save_label, on_click=_on_save_click).classes("flex-1")
                self.save_button.props("unelevated color=deep-orange")

                def _on_clear_click():
                    self.username_input.set_value("")
                    self.password_input.set_value("")
                    self.access_key_input.set_value("")
                    self.secret_key_input.set_value("")
                    if self.visitor is not None:
                        self.visitor.clear()
                        self._load_credentials()
                        self.status_label.set_text("Credentials forgotten for this tab.")

                clear_button = ui.button("Clear All", on_click=_on_clear_click).classes("flex-1")
                clear_button.props("outline color=warning")

            # Status message
            ui.separator().classes("my-3")
            self.status_label = ui.label("").classes("text-sm text-gray-600 break-words min-h-5")

            # Info section
            ui.separator().classes("my-3")
            ui.label("About Credentials:").classes("text-xs font-semibold text-gray-600 mb-1")
            ui.label("🔐 API Credentials: Required for downloads; searching works without them. Get free Copernicus account at https://dataspace.copernicus.eu/").classes("text-xs text-blue-600 break-words font-semibold mb-2")
            ui.label("📌 S3 Credentials (Optional): To avoid quota restrictions on downloads, request permanent S3 credentials from https://documentation.dataspace.copernicus.eu/APIs/S3.html#registration").classes(
                "text-xs text-orange-600 break-words font-semibold mb-2"
            )
            ui.label("Leave fields empty to keep current values.").classes("text-xs text-gray-500 break-words")

        return menu_card

    async def _handle_save(self):
        """Handle save button click."""
        username = self.username_input.value.strip() if self.username_input else ""
        password = self.password_input.value.strip() if self.password_input else ""
        access_key = self.access_key_input.value.strip() if self.access_key_input else ""
        secret_key = self.secret_key_input.value.strip() if self.secret_key_input else ""
        search_provider = self.backend_select.value if self.backend_select else ""
        # Endpoint is currently not in the UI, but we'll preserve it if it exists in env
        # Or we could add an input for it. For now, let's keep it as is or get from current config
        s3_endpoint = self.config.s3_endpoint or "https://eodata.dataspace.copernicus.eu/"

        # API credentials are required
        if not username or not password:
            self.status_label.set_text("⚠️ API credentials (username & password) are required")
            ui.notify("Please enter API credentials", type="warning")
            return

        # If S3 credentials are partially filled, require both
        if (access_key and not secret_key) or (not access_key and secret_key):
            self.status_label.set_text("⚠️ S3 credentials must be both filled or both empty")
            ui.notify("S3 credentials must be complete", type="warning")
            return

        if self.visitor is not None:
            self.visitor.set(username=username, password=password, s3_access_key=access_key, s3_secret_key=secret_key)
            self._load_credentials()
            self.status_label.set_text("✅ Credentials kept for this browser tab only.")
            ui.notify("Credentials set for this tab", type="positive")
            if self.on_credentials_updated:
                self.on_credentials_updated()
            return

        # Save credentials
        if self._save_credentials_to_env(
            username=username,
            password=password,
            access_key=access_key,
            secret_key=secret_key,
            search_provider=search_provider,
            s3_endpoint=s3_endpoint,
        ):
            self.status_label.set_text("✅ All credentials saved successfully!")
            ui.notify("Credentials saved", type="positive")

            # Call callback if provided
            if self.on_credentials_updated:
                self.on_credentials_updated()
        else:
            self.status_label.set_text("❌ Failed to save credentials")
            ui.notify("Failed to save credentials", type="negative")
