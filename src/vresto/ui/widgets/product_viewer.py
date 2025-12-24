"""Product viewer widget for displaying quicklook images and metadata.

Provides ProductViewerWidget which encapsulates dialogs for viewing
product quicklooks and metadata information.
"""

from loguru import logger
from nicegui import ui

from vresto.products import ProductsManager


class ProductViewerWidget:
    """Encapsulates product quicklook and metadata viewing in dialogs.

    This widget handles showing product information (quicklook images and metadata)
    in dialog windows. It's used by search tabs to display product details.

    Usage:
        viewer = ProductViewerWidget()
        await viewer.show_quicklook(product, messages_column)
        await viewer.show_metadata(product, messages_column)
    """

    def __init__(self):
        """Initialize the product viewer widget."""
        self.manager = ProductsManager()

    async def show_quicklook(self, product, messages_column):
        """Show quicklook image for a product in a dialog.

        Args:
            product: ProductInfo object with product details
            messages_column: NiceGUI column element for logging messages
        """

        def add_message(text: str):
            """Add a message to the activity log."""
            with messages_column:
                ui.label(text).classes("text-sm text-gray-700 break-words")

        try:
            ui.notify("📥 Downloading quicklook...", position="top", type="info")
            add_message(f"📥 Downloading quicklook for {getattr(product, 'display_name', product.name)}")

            # Initialize products manager and download quicklook
            quicklook = self.manager.get_quicklook(product)

            if quicklook:
                # Show quicklook in a dialog
                with ui.dialog() as dialog:
                    with ui.card():
                        ui.label(f"Quicklook: {getattr(product, 'display_name', product.name)}").classes("text-lg font-semibold mb-3")
                        ui.label(f"Sensing Date: {product.sensing_date}").classes("text-sm text-gray-600 mb-3")

                        # Display image
                        base64_image = quicklook.get_base64()
                        ui.image(source=f"data:image/jpeg;base64,{base64_image}").classes("w-full rounded-lg")

                        with ui.row().classes("w-full gap-2 mt-4"):
                            ui.button("Close", on_click=dialog.close).classes("flex-1")

                dialog.open()
                ui.notify("✅ Quicklook loaded", position="top", type="positive")
                add_message(f"✅ Quicklook loaded for {getattr(product, 'display_name', product.name)}")
            else:
                ui.notify("❌ Could not load quicklook", position="top", type="negative")
                add_message(f"❌ Quicklook not available for {getattr(product, 'display_name', product.name)}")

        except Exception as e:
            logger.error(f"Error loading quicklook: {e}")
            ui.notify(f"❌ Error: {str(e)}", position="top", type="negative")
            add_message(f"❌ Quicklook error: {str(e)}")

    async def show_metadata(self, product, messages_column):
        """Show metadata for a product in a dialog.

        Args:
            product: ProductInfo object with product details
            messages_column: NiceGUI column element for logging messages
        """

        def add_message(text: str):
            """Add a message to the activity log."""
            with messages_column:
                ui.label(text).classes("text-sm text-gray-700 break-words")

        try:
            ui.notify("📥 Downloading metadata...", position="top", type="info")
            add_message(f"📥 Downloading metadata for {getattr(product, 'display_name', product.name)}")

            # Initialize products manager and download metadata
            metadata = self.manager.get_metadata(product)

            if metadata:
                # Show metadata in a dialog with scrollable XML
                with ui.dialog() as dialog:
                    with ui.card():
                        ui.label(f"Metadata: {getattr(product, 'display_name', product.name)}").classes("text-lg font-semibold mb-3")
                        ui.label("File: MTD_MSIL2A.xml").classes("text-sm text-gray-600 mb-3")

                        # Display metadata in a scrollable area
                        with ui.scroll_area().classes("w-full h-96"):
                            ui.code(metadata.metadata_xml, language="xml").classes("w-full text-xs")

                        with ui.row().classes("w-full gap-2 mt-4"):
                            ui.button("Close", on_click=dialog.close).classes("flex-1")

                dialog.open()
                ui.notify("✅ Metadata loaded", position="top", type="positive")
                add_message(f"✅ Metadata loaded for {getattr(product, 'display_name', product.name)}")
            else:
                ui.notify("❌ Could not load metadata", position="top", type="negative")
                add_message(f"❌ Metadata not available for {getattr(product, 'display_name', product.name)}")

        except Exception as e:
            logger.error(f"Error loading metadata: {e}")
            ui.notify(f"❌ Error: {str(e)}", position="top", type="negative")
            add_message(f"❌ Metadata error: {str(e)}")
