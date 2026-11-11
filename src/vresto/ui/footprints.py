"""Turn catalogue footprints into GeoJSON the map can draw, and order search results."""

from __future__ import annotations

from typing import Any, Iterable, Optional

# How the results list can be ordered: key -> label shown in the sort menu.
RESULT_SORTS = {
    "newest": "Newest first",
    "oldest": "Oldest first",
    "cloud": "Least cloudy first",
}


def _depth(value: Any) -> int:
    """Nesting depth of a coordinate array: 2 for a ring, 3 for a polygon, 4 for a multipolygon."""
    depth = 0
    while isinstance(value, (list, tuple)) and value:
        value = value[0]
        depth += 1
    return depth


def footprint_geometry(footprint: Any) -> Optional[dict]:
    """Return a GeoJSON geometry for a product footprint, or None if it has none.

    STAC gives a full geometry dict; the OData catalogue gives only the coordinates of a Polygon
    or MultiPolygon, so the type is read from how deep they are nested.
    """
    if isinstance(footprint, dict):
        return footprint if footprint.get("type") and footprint.get("coordinates") else None
    depth = _depth(footprint)
    if depth == 3:
        return {"type": "Polygon", "coordinates": footprint}
    if depth == 4:
        return {"type": "MultiPolygon", "coordinates": footprint}
    return None


def footprints_feature_collection(products: Iterable[Any]) -> dict:
    """GeoJSON of the footprints of ``products``, each feature carrying its 1-based list ``index`` and a label."""
    features = []
    for index, product in enumerate(products, 1):
        geometry = footprint_geometry(getattr(product, "footprint", None))
        if geometry is None:
            continue
        label = f"{index}. {str(product.sensing_date)[:10]}"
        if getattr(product, "cloud_cover", None) is not None:
            label += f" · ☁️ {product.cloud_cover:.0f}%"
        features.append({"type": "Feature", "geometry": geometry, "properties": {"index": index, "label": label}})
    return {"type": "FeatureCollection", "features": features}


def sort_products(products: list, order: str) -> list:
    """Return ``products`` ordered by ``order`` (a :data:`RESULT_SORTS` key). Unknown cloud cover sorts last."""
    if order == "oldest":
        return sorted(products, key=lambda p: str(p.sensing_date))
    if order == "cloud":
        return sorted(products, key=lambda p: (p.cloud_cover is None, p.cloud_cover or 0.0, str(p.sensing_date)))
    return sorted(products, key=lambda p: str(p.sensing_date), reverse=True)
