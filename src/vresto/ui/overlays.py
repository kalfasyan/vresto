"""Declarative registry of the map tile overlays and how each one is produced.

Every overlay is described by one :class:`OverlaySpec`: its sidebar metadata, its
legend and a ``fetch`` callable that turns the streamed Sentinel-2 tile (the
*reference raster*) into a colourised raster. ``MapSearchTab`` drives all of them
through one generic load pipeline, so adding an overlay means adding a service
module plus one spec and one fetcher here. No other branching is required.

Fetchers are synchronous and may block on S3/STAC I/O; the UI runs them in a
worker thread. Service modules are imported lazily inside each fetcher because
they pull in heavy geospatial dependencies that the rest of the UI does not need.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

from vresto.ui.widgets.legend import build_continuous_legend_html, build_legend_html


@dataclass(frozen=True)
class OverlayRequest:
    """Inputs handed to an overlay's ``fetch`` / ``precheck`` callables.

    Attributes:
        ref_path: Cached TCI raster of the streamed tile; overlays only use its CRS and extent.
        tile_code: MGRS tile code of the streamed tile (e.g. ``"31UFS"``).
        date: Sensing date of the streamed tile as ``YYYYMMDD``.
        timestamp: Sensing timestamp as ``YYYYMMDDHHMMSS`` (falls back to ``date``).
        setting: Value of the overlay's own selector, if it has one: the year for
            TCD / LC100, the chosen hourly timestamp for LST. Empty otherwise.
        tile_available: ``False`` when the tab has already established that the tile
            lies outside the overlay's coverage.
    """

    ref_path: str
    tile_code: str
    date: str
    timestamp: str = ""
    setting: str = ""
    tile_available: bool = True


@dataclass(frozen=True)
class FetchedOverlay:
    """A colourised overlay raster ready to be served as a map layer.

    Attributes:
        colorized_path: Path of the RGBA raster to serve.
        legend_suffix: Text appended to the legend title in parentheses (a year or the
            snapped product date). Empty when the legend title needs none.
        detail: Human-readable detail about what was selected (e.g. the LST scene
            time). Surfaced in the activity log and status line when present.
    """

    colorized_path: str
    legend_suffix: str = ""
    detail: str = ""


@dataclass(frozen=True)
class OverlaySpec:
    """Declarative specification for one tile overlay.

    Attributes:
        name: Machine key used for state, layer names, and switches.
        title: Human-readable title shown in the expansion header.
        description: Short helper text shown inside the expansion.
        icon: Material icon name for the expansion header.
        opacity: Default opacity (0.2-1.0).
        info: Attribution / source tooltip text.
        label: Short name used in activity-log and notification messages.
        emoji: Emoji prefixed to the "loading" message.
        layer_prefix: Short prefix for the map tile-layer name (must be unique).
        fetch: Builds the colourised raster for a tile; returns ``None`` on failure.
        category: Sidebar group heading (used to render grouped section labels).
        legend_type: ``"discrete"`` (colour swatches) or ``"continuous"`` (gradient ramp).
        legend_title: Legend title, when it differs from ``title``.
        legend_color: CSS colour of the legend title.
        legend_classes: ``"module:ATTRIBUTE"`` path of the class-legend table (discrete only).
        legend_stops: CSS colours of the gradient, low to high (continuous only).
        vmin: Min value for continuous legend (e.g. 0.0).
        vmax: Max value for continuous legend (e.g. 50.0).
        units: Physical unit label shown in the continuous legend (e.g. "degC").
        coverage_note: Human-readable note shown as a badge when the overlay has
            spatial/temporal coverage limits (e.g. "Europe only").
        precheck: Optional gate run before fetching. Returns a user-facing reason
            when the overlay cannot be shown for the tile, ``None`` otherwise.
    """

    name: str
    title: str
    description: str
    icon: str
    opacity: float
    info: str
    label: str
    emoji: str
    layer_prefix: str
    fetch: Callable[[OverlayRequest], Optional[FetchedOverlay]]
    category: str = "Other"
    legend_type: str = "discrete"
    legend_title: str = ""
    legend_color: str = "#333"
    legend_classes: str = ""
    legend_stops: tuple[str, ...] = ()
    vmin: float = 0.0
    vmax: float = 1.0
    units: str = ""
    coverage_note: str = ""
    precheck: Optional[Callable[[OverlayRequest], Optional[str]]] = None


# ── Fetchers ────────────────────────────────────────────────────────────────


def _fetched(path: Optional[str], legend_suffix: str = "") -> Optional[FetchedOverlay]:
    """Wrap a service's colourised path, mapping an empty result to ``None``."""
    return FetchedOverlay(path, legend_suffix=legend_suffix) if path else None


def _fetch_worldcover(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.worldcover import worldcover_service

    return _fetched(worldcover_service.get_colorized_worldcover_path(req.ref_path, 20, "2021"))


def _fetch_lcm(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.lcm import lcm_service

    return _fetched(lcm_service.get_colorized_lcm_path(req.ref_path, 20, "2020"))


def _fetch_tcd(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.tcd import tcd_service

    return _fetched(tcd_service.get_colorized_tcd_path(req.ref_path, 20, req.setting), legend_suffix=req.setting)


def _fetch_dem(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.dem import dem_service

    # 60 m keeps the read on a COG overview (~2-3 s); a terrain backdrop does
    # not need finer than the DEM's ~30 m native sampling.
    return _fetched(dem_service.get_colorized_dem_path(req.ref_path, 60))


def _fetch_lc100(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.lc100 import lc100_service

    return _fetched(lc100_service.get_colorized_lc100_path(req.ref_path, 20, req.setting), legend_suffix=req.setting)


def _fetch_ndvi(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.ndvi import ndvi_lts_period_from_date, ndvi_service

    path = ndvi_service.get_colorized_ndvi_path(req.ref_path, 1000, req.date)
    month, day = ndvi_lts_period_from_date(req.date or "20200101")
    return _fetched(path, legend_suffix=f"{month}-{day}")


def _fetch_lst(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.lst import format_lst_selected_datetime, lst_service

    result = lst_service.get_colorized_lst_result(req.ref_path, 3000, req.setting)
    if not result:
        return None
    selected = format_lst_selected_datetime(result.selected_datetime)
    return FetchedOverlay(result.colorized_path, legend_suffix=selected, detail=selected)


def _fetch_fapar(req: OverlayRequest) -> Optional[FetchedOverlay]:
    from vresto.services.fapar import fapar_service

    result = fapar_service.get_colorized_fapar_result(req.ref_path, 300, req.date)
    return FetchedOverlay(result.colorized_path, legend_suffix=req.date) if result else None


def _stac_fetcher(module: str, service: str, method: str, resolution_m: int) -> Callable[[OverlayRequest], Optional[FetchedOverlay]]:
    """Build a fetcher for the temporal STAC overlays (DMP, SSM, SWI, BA, WB).

    They all resolve their COG through CDSE STAC discovery, snap to the streamed
    date, and return an object exposing ``colorized_path`` and ``selected_datetime``.
    The service is resolved at call time so tests can patch it.
    """

    def fetch(req: OverlayRequest) -> Optional[FetchedOverlay]:
        svc = getattr(importlib.import_module(module), service)
        result = getattr(svc, method)(req.ref_path, resolution_m, req.date)
        if not result:
            return None
        return FetchedOverlay(result.colorized_path, legend_suffix=result.selected_datetime.strftime("%Y-%m-%d"))

    return fetch


# ── Prechecks ───────────────────────────────────────────────────────────────


def _precheck_tcd(req: OverlayRequest) -> Optional[str]:
    if not req.tile_available:
        return "Tree Cover Density is unavailable for this tile (outside pantropical coverage)"
    return None


def _precheck_ssm(req: OverlayRequest) -> Optional[str]:
    """Soil Moisture covers Europe only, so check the tile before making a STAC call."""
    try:
        import rasterio
        from rasterio.warp import transform_bounds

        from vresto.services.ssm import ssm_has_coverage

        with rasterio.open(req.ref_path) as ref:
            left, bottom, right, top = transform_bounds(ref.crs, "EPSG:4326", *ref.bounds)
        if not ssm_has_coverage(left, bottom, right, top):
            return "Soil Moisture (SSM) has European coverage only. This tile is outside the product extent."
    except Exception as exc:
        logger.warning(f"Could not check SSM coverage for {req.tile_code}: {exc}")
    return None


WB_START_DATE = "20201001"


def _precheck_wb(req: OverlayRequest) -> Optional[str]:
    """Water Bodies starts in October 2020; gate early to avoid a silent STAC 404."""
    if req.date and req.date < WB_START_DATE:
        return f"Water Bodies data is only available from October 2020. Streamed date is {req.date[:4]}-{req.date[4:6]}-{req.date[6:8]}."
    return None


# ── Registry ────────────────────────────────────────────────────────────────

# Specs are grouped by ``category`` and rendered in this order.
OVERLAY_REGISTRY: tuple[OverlaySpec, ...] = (
    # ── Land Cover ─────────────────────────────────────────────────────────
    OverlaySpec(
        name="worldcover",
        title="WorldCover 2021",
        description="ESA global land cover classes for quick context.",
        icon="public",
        opacity=0.7,
        info="Source: ESA WorldCover. Creator: European Space Agency (ESA). Website: https://esa-worldcover.org",
        label="WorldCover",
        emoji="🌍",
        layer_prefix="wc",
        fetch=_fetch_worldcover,
        category="Land Cover",
        legend_classes="vresto.services.worldcover:WORLDCOVER_CLASS_LEGENDS",
        legend_color="#1a73e8",
    ),
    OverlaySpec(
        name="lcm",
        title="LCM 2020",
        description="Copernicus Dynamic Land Cover Map for the selected tile.",
        icon="map",
        opacity=0.7,
        info="Source: Copernicus Dynamic Land Cover Map. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu",
        label="LCM",
        emoji="🗺️",
        layer_prefix="lcm",
        fetch=_fetch_lcm,
        category="Land Cover",
        legend_classes="vresto.services.lcm:LCM_CLASS_LEGENDS",
        legend_color="#e8710a",
    ),
    OverlaySpec(
        name="lc100",
        title="Global LC 100m",
        description="Copernicus yearly global land cover classification.",
        icon="layers",
        opacity=0.7,
        info="Source: Copernicus Global Land Cover 100 m. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/lc",
        label="Global LC 100m",
        emoji="🌐",
        layer_prefix="lc100",
        fetch=_fetch_lc100,
        category="Land Cover",
        legend_classes="vresto.services.lc100:LC100_CLASS_LEGENDS",
        legend_color="#00695c",
    ),
    OverlaySpec(
        name="tcd",
        title="Tree Cover Density",
        description="Pantropical yearly tree cover density (disabled outside tropical coverage).",
        icon="park",
        opacity=0.7,
        info="Source: Tree Cover Density 10 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu",
        label="Tree Cover Density",
        emoji="🌳",
        layer_prefix="tcd",
        fetch=_fetch_tcd,
        category="Land Cover",
        legend_classes="vresto.services.tcd:TCD_CLASS_LEGENDS",
        legend_color="#2e7d32",
        coverage_note="Pantropical only",
        precheck=_precheck_tcd,
    ),
    # ── Terrain ─────────────────────────────────────────────────────────────
    OverlaySpec(
        name="dem",
        title="DEM terrain",
        description="Relative terrain shading for the selected tile.",
        icon="terrain",
        opacity=0.75,
        info="Source: Copernicus DEM GLO-30. Creator: Copernicus Programme. Website: https://dataspace.copernicus.eu",
        label="DEM",
        emoji="⛰️",
        layer_prefix="dem",
        fetch=_fetch_dem,
        category="Terrain",
        legend_type="continuous",
        legend_stops=("#1a3a1a", "#4a7c3f", "#8fbc5f", "#d4c27a", "#c8a06e", "#f0f0f0"),
        legend_color="#8d6e63",
        vmin=0.0,
        vmax=100.0,
        units="relative",
    ),
    # ── Vegetation & Productivity ────────────────────────────────────────────
    OverlaySpec(
        name="ndvi",
        title="NDVI climatology",
        description="Long-term NDVI mean for the streamed tile date dekad.",
        icon="eco",
        opacity=0.75,
        info="Source: Copernicus Global Land Service NDVI Long-Term Statistics. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/ndvi",
        label="NDVI climatology",
        emoji="🌿",
        layer_prefix="ndvi",
        fetch=_fetch_ndvi,
        category="Vegetation & Productivity",
        legend_type="continuous",
        legend_stops=("#d73027", "#fee08b", "#1a9850"),
        legend_color="#558b2f",
        vmin=0.0,
        vmax=0.9,
        units="NDVI",
    ),
    OverlaySpec(
        name="fapar",
        title="FAPAR",
        description="Nearest 10-daily FAPAR snapped to the streamed acquisition date.",
        icon="grass",
        opacity=0.75,
        info=("Source: Copernicus Global Land Service Fraction of Absorbed Photosynthetically Active Radiation (FAPAR) 300 m. Creator: Copernicus Global Land Service. Website: https://land.copernicus.eu/global/products/fapar"),
        label="FAPAR",
        emoji="🌿",
        layer_prefix="fapar",
        fetch=_fetch_fapar,
        category="Vegetation & Productivity",
        legend_type="continuous",
        legend_stops=("#ffffcc", "#78c679", "#005a32"),
        legend_color="#2e7d32",
        vmin=0.0,
        vmax=1.0,
        units="FAPAR",
    ),
    OverlaySpec(
        name="dmp",
        title="Dry Matter Prod.",
        description="Nearest 10-daily dry matter productivity snapped to the streamed date.",
        icon="spa",
        opacity=0.75,
        info="Source: Copernicus Global Land Service Dry Matter Productivity 300 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/dmp",
        label="Dry Matter Productivity",
        emoji="🌱",
        layer_prefix="dmp",
        fetch=_stac_fetcher("vresto.services.dmp", "dmp_service", "get_colorized_dmp_result", 300),
        category="Vegetation & Productivity",
        legend_type="continuous",
        legend_title="Dry Matter Productivity",
        legend_stops=("#ffffe5", "#addd8e", "#006837"),
        legend_color="#006837",
        vmin=0.0,
        vmax=150.0,
        units="kg/ha/day",
    ),
    # ── Thermal ──────────────────────────────────────────────────────────────
    OverlaySpec(
        name="lst",
        title="LST hourly",
        description="Nearest hourly land surface temperature snapped to the streamed acquisition time.",
        icon="device_thermostat",
        opacity=0.75,
        info=(
            "Source: Copernicus Global Land Service Land Surface Temperature. "
            "Creator: Copernicus Global Land Service. Website: "
            "https://land.copernicus.eu/en/products/temperature-and-reflectance/"
            "land-surface-temperature. Times are shown in Europe/Brussels local "
            "time (CET/CEST)."
        ),
        label="Hourly LST",
        emoji="🌡️",
        layer_prefix="lst",
        fetch=_fetch_lst,
        category="Thermal",
        legend_type="continuous",
        legend_stops=("#313695", "#abd9e9", "#ffffbf", "#fdae61", "#d73027"),
        legend_color="#d84315",
        vmin=-20.0,
        vmax=50.0,
        units="°C",
    ),
    # ── Water & Soil ─────────────────────────────────────────────────────────
    OverlaySpec(
        name="ssm",
        title="Soil Moisture",
        description="Nearest daily surface soil moisture.",
        icon="water_drop",
        opacity=0.75,
        info="Source: Copernicus Land Monitoring Service Surface Soil Moisture 1 km (Europe). Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/en/products/soil-moisture",
        label="Soil Moisture",
        emoji="💧",
        layer_prefix="ssm",
        fetch=_stac_fetcher("vresto.services.ssm", "ssm_service", "get_colorized_ssm_result", 1000),
        category="Water & Soil",
        legend_type="continuous",
        legend_stops=("#ffffd9", "#7fcdbb", "#081d58"),
        legend_color="#0c2c84",
        vmin=0.0,
        vmax=100.0,
        units="% sat.",
        coverage_note="Europe only",
        precheck=_precheck_ssm,
    ),
    OverlaySpec(
        name="swi",
        title="Soil Water Index",
        description="Nearest daily soil water index (root-zone proxy, T=10).",
        icon="opacity",
        opacity=0.75,
        info="Source: Copernicus Global Land Service Soil Water Index 12.5 km. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/swi",
        label="Soil Water Index",
        emoji="💧",
        layer_prefix="swi",
        fetch=_stac_fetcher("vresto.services.swi", "swi_service", "get_colorized_swi_result", 12500),
        category="Water & Soil",
        legend_type="continuous",
        legend_stops=("#ffffd9", "#7fcdbb", "#225ea8"),
        legend_color="#225ea8",
        vmin=0.0,
        vmax=100.0,
        units="% sat.",
    ),
    OverlaySpec(
        name="wb",
        title="Water Bodies",
        description="Nearest monthly surface water-body extent snapped to the streamed date.",
        icon="water",
        opacity=0.7,
        info="Source: Copernicus Global Land Service Water Bodies 100 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/wb",
        label="Water Bodies",
        emoji="🌊",
        layer_prefix="wb",
        fetch=_stac_fetcher("vresto.services.wb", "wb_service", "get_colorized_wb_result", 100),
        category="Water & Soil",
        legend_classes="vresto.services.wb:WB_CLASS_LEGENDS",
        legend_color="#1f78b4",
        coverage_note="Data from Oct 2020",
        precheck=_precheck_wb,
    ),
    # ── Hazards ───────────────────────────────────────────────────────────────
    OverlaySpec(
        name="ba",
        title="Burned Area",
        description="Nearest monthly burned area (day-of-burn) snapped to the streamed date.",
        icon="local_fire_department",
        opacity=0.8,
        info="Source: Copernicus Global Land Service Burned Area 300 m. Creator: Copernicus Land Monitoring Service (CLMS). Website: https://land.copernicus.eu/global/products/ba",
        label="Burned Area",
        emoji="🔥",
        layer_prefix="ba",
        fetch=_stac_fetcher("vresto.services.ba", "ba_service", "get_colorized_ba_result", 300),
        category="Hazards",
        legend_type="continuous",
        legend_stops=("#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"),
        legend_color="#bd0026",
        vmin=1.0,
        vmax=366.0,
        units="day of year",
    ),
)

OVERLAY_NAMES: tuple[str, ...] = tuple(spec.name for spec in OVERLAY_REGISTRY)


# ── Legends ─────────────────────────────────────────────────────────────────


def build_overlay_legend_html(spec: OverlaySpec, suffix: str = "") -> str:
    """Render the floating legend card for an overlay.

    Args:
        spec: The overlay to describe.
        suffix: Optional text shown in parentheses after the title, typically the
            year or product date reported by the overlay's fetcher.

    Returns:
        Styled HTML for the legend card.
    """
    title = spec.legend_title or spec.title
    if suffix:
        title = f"{title} ({suffix})"

    if spec.legend_type == "continuous":
        return build_continuous_legend_html(
            title,
            vmin=spec.vmin,
            vmax=spec.vmax,
            units=spec.units,
            stops=list(spec.legend_stops),
            title_color=spec.legend_color,
        )

    module_name, _, attribute = spec.legend_classes.partition(":")
    class_legends = getattr(importlib.import_module(module_name), attribute)
    return build_legend_html(title, class_legends, spec.legend_color)
