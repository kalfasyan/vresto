"""Map Viewer tab widget for high-resolution product inspection."""

from nicegui import ui

from vresto.ui.widgets.map_widget import MapWidget


class MapViewerTab:
    """Encapsulates the Map Viewer tab for high-resolution visualization."""

    def __init__(self):
        self.map_widget_obj = None
        self.map_container = None

    def create(self):
        """Create and return the Map Viewer tab UI."""
        with ui.column().classes("w-full h-full gap-4") as self.map_container:
            self.map_widget_obj = MapWidget(center=(50.0, 10.0), zoom=5, title="High-Resolution Map View", draw_control=False)
            self.map_widget_obj.create()

            # Setup JavaScript event listener to receive tile URLs from ProductAnalysisTab
            ui.add_body_html("""
                <script>
                window.addEventListener("vresto:show_tiles", (event) => {
                    // This is a bridge between the global custom event and NiceGUI
                    const detail = event.detail;
                    console.log("vresto:show_tiles event received", detail);
                    // We call a NiceGUI function defined in the component
                    if (window.vresto_update_map) {
                        window.vresto_update_map(detail.url, detail.name, detail.bounds, detail.attribution);
                    }
                });
                </script>
            """)

            def update_map(url, name, bounds, attribution):
                if self.map_widget_obj:
                    self.map_widget_obj.clear_tile_layers()
                    self.map_widget_obj.add_tile_layer(url, name=name, attribution=attribution)
                    if bounds:
                        self.map_widget_obj.fit_bounds(bounds)

            # Expose the update function to JavaScript
            def _setup_js():
                ui.run_javascript(f'''
                    window.vresto_update_map = (url, name, bounds, attribution) => {{
                        const container = document.getElementById("{self.map_container.id}");
                        if (container) {{
                            container.dispatchEvent(new CustomEvent("update_map", {{
                                detail: {{url, name, bounds, attribution}}
                            }}));
                        }}
                    }};
                ''')

            ui.context.client.on_connect(_setup_js)

            self.map_container.on("update_map", lambda e: update_map(e.args["detail"]["url"], e.args["detail"]["name"], e.args["detail"]["bounds"], e.args["detail"]["attribution"]))

        return self.map_container
