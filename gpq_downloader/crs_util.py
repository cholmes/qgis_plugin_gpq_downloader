from osgeo import ogr, osr
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

def get_source_crs_from_metadata(url):
    """
    Retrieves the Coordinate Reference System (CRS) from the GeoParquet metadata
    """
    try:
        # Open the data source (local path or remote URL)
        data_source = ogr.Open(url) 
        
        if data_source is not None: 
            layer = data_source.GetLayer()
            spatial_ref = layer.GetSpatialRef()  
            if spatial_ref:
                return QgsCoordinateReferenceSystem(spatial_ref.ExportToWkt())
            
        # if the CRS field is missing, it defaults to EPSG:4326
        return QgsCoordinateReferenceSystem("EPSG:4326")
        
    except Exception as e:
        # Fallback to EPSG:4326 if metadata cannot be read
        print(f"Error reading metadata from {url}: {e}")
        return QgsCoordinateReferenceSystem("EPSG:4326")

def extent_to_source_crs(qgis_extent, target_crs):
    """
    Transforms the QGIS viewport extent (BBOX) to match the source data's CRS.
    """
    source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    # 1. If the source data is already in 4326, no transformation is needed
    if target_crs == source_crs:
        return qgis_extent
    # 2. Set up the coordinate transformation
    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    try:
        # Transform the bounding box coordinates to the source file CRS
        transformed_extent = transform.transformBoundingBox(qgis_extent)
        return transformed_extent
    except Exception as e:
        print(f"BBOX transformation failed: {e}")
        return qgis_extent