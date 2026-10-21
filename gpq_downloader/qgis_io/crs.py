"""QGIS-side CRS conversion for the Worker: the query window into EPSG:4326,
and the data's own CRS from its GeoParquet metadata.

Moved here from utils.py (#195), which still re-exports these names.
"""

from __future__ import annotations

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

from .. import logger


def crs_from_geo(crs_text: str | None):
    """A QgsCoordinateReferenceSystem for a parse_geo_crs() value, or None when
    the data is already in longitude/latitude. False when the CRS cannot be read."""
    if not crs_text:
        return None
    crs = QgsCoordinateReferenceSystem(crs_text)
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem()
        crs.createFromUserInput(crs_text)
    if not crs.isValid():
        return False
    if crs == QgsCoordinateReferenceSystem("EPSG:4326"):
        return None  # already lon/lat; nothing to transform
    return crs


def transform_rect(rect, source_crs, dest_crs):
    """rect from source_crs to dest_crs, or None if the transform fails."""
    try:
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        return transform.transformBoundingBox(rect)
    except Exception as e:
        logger.log(f"Could not transform the query window: {e}")
        return None


def transform_bbox_to_4326(extent, source_crs):
    """
    Transform a bounding box to EPSG:4326 (WGS84)

    Args:
        extent (QgsRectangle): The input extent to transform
        source_crs (QgsCoordinateReferenceSystem): The source CRS of the extent

    Returns:
        QgsRectangle: The transformed extent in EPSG:4326, or None if inputs are invalid
    """
    if extent is None or source_crs is None:
        return None
        
    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    if source_crs != dest_crs:
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    
    return extent
