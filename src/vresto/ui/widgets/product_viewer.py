"""Product viewer widget for displaying quicklook images and metadata.

Provides ProductViewerWidget which encapsulates dialogs for viewing
product quicklooks and metadata information.
"""

import xml.etree.ElementTree as ET

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

    def _parse_sentinel2_metadata(self, xml_content: str) -> dict:
        """Parse Sentinel-2 metadata XML following the exact XML structure.

        Args:
            xml_content: Raw XML string

        Returns:
            Dictionary with metadata organized exactly as in the XML sections
        """
        try:
            root = ET.fromstring(xml_content)

            def clean_tag(tag):
                return tag.split("}")[-1] if "}" in tag else tag

            def parse_element(element):
                """Recursively parse XML elements into a dictionary/list structure."""
                # If element has children, parse them
                children = list(element)
                if children:
                    # Check if children have same tag (e.g., quality_check)
                    # or if they are just unique fields
                    res = {}
                    for child in children:
                        tag = clean_tag(child.tag)
                        child_res = parse_element(child)

                        # Handle attributes for quality_check specifically or others
                        if tag == "quality_check":
                            check_type = child.get("checkType")
                            if check_type:
                                tag = f"quality_check ({check_type})"

                        if tag in res:
                            # If it's already there, make it a list or append to it
                            if not isinstance(res[tag], list):
                                res[tag] = [res[tag]]
                            res[tag].append(child_res)
                        else:
                            res[tag] = child_res
                    return res
                else:
                    # Leaf node
                    return element.text or ""

            # We want to extract main sections directly under the root (usually n1:Level-...)
            metadata_sections = {}
            for section in root:
                section_name = clean_tag(section.tag)
                metadata_sections[section_name] = parse_element(section)

            return metadata_sections
        except Exception as e:
            logger.error(f"Error parsing metadata XML: {e}")
            return {}

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
                parsed_metadata = self._parse_sentinel2_metadata(metadata.metadata_xml)

                # Show metadata in a dialog
                with ui.dialog() as dialog:
                    with ui.card().classes("w-[600px] max-w-none"):
                        with ui.row().classes("w-full items-center justify-between mb-2"):
                            ui.label(f"Metadata: {getattr(product, 'display_name', product.name)}").classes("text-lg font-semibold")
                            ui.button(icon="close", on_click=dialog.close).props("flat round")

                        with ui.tabs().classes("w-full") as tabs:
                            info_tab = ui.tab("Information")
                            xml_tab = ui.tab("Raw XML")

                        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
                            with ui.tab_panel(info_tab):

                                def render_metadata_item(key, value, level=0):
                                    """Recursively render metadata items."""
                                    indent = level * 4
                                    if isinstance(value, dict):
                                        with ui.expansion(key, icon="folder" if level == 0 else "chevron_right").classes(f"w-full mb-1 ml-{indent}").props("dense" if level > 0 else ""):
                                            for k, v in value.items():
                                                render_metadata_item(k, v, level + 1)
                                    elif isinstance(value, list):
                                        with ui.expansion(f"{key} ({len(value)} items)", icon="list").classes(f"w-full mb-1 ml-{indent}").props("dense"):
                                            for idx, item in enumerate(value):
                                                render_metadata_item(f"Item {idx + 1}", item, level + 1)
                                    else:
                                        # Key-value pair - use a two-column grid for better alignment
                                        with ui.grid(columns="1fr 1fr").classes(f"w-full gap-x-2 items-center ml-{indent} py-0.5"):
                                            ui.label(f"{key}:").classes("font-bold text-gray-700 text-[11px] text-right break-words")
                                            ui.label(str(value)).classes("text-gray-600 text-[11px] break-all")

                                if parsed_metadata:
                                    with ui.scroll_area().classes("w-full h-[500px]"):
                                        for section_name, content in parsed_metadata.items():
                                            render_metadata_item(section_name, content)
                                else:
                                    ui.label("Could not parse metadata fields. Please check the Raw XML tab.").classes("text-italic text-gray-500")

                            with ui.tab_panel(xml_tab):
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
