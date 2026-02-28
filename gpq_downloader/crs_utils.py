from osgeo import ogr
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

def get_source_crs_from_metadata(url):
    """
    Retrieves the CRS from GeoParquet metadata.
    """
    try:
        # Https URLs need to be prefixed for GDAL to read them
        # local files can be read directly
        # s3 and other cloud storage URLs may need additional handling, don't have enough test data yet
        if url.startswith('http'):
            connection_url = f"/vsicurl/{url}"
        else:
            connection_url = url

        data_source = ogr.Open(connection_url) 
        if data_source:
            layer = data_source.GetLayer()
            spatial_ref = layer.GetSpatialRef()  
            if spatial_ref:
                return QgsCoordinateReferenceSystem(spatial_ref.ExportToWkt())
            
        # Default to 4326 if missing per GeoParquet spec
        return QgsCoordinateReferenceSystem("EPSG:4326")
    except Exception as e:
        print(f"Metadata detection failed: {e}")
        return QgsCoordinateReferenceSystem("EPSG:4326")

def extent_to_source_crs(qgis_extent, target_crs):
    """
    Transforms viewport extent to match the source file's CRS.
    """
    canvas_crs = QgsProject.instance().crs()
    if canvas_crs.authid() == target_crs.authid():
        return qgis_extent
    transform = QgsCoordinateTransform(canvas_crs, target_crs, QgsProject.instance())
    
    try:
        transformed_extent = transform.transformBoundingBox(qgis_extent)
        return transformed_extent
    except Exception as e:
        print(f"BBOX transform failed: {e}")
        return qgis_extent