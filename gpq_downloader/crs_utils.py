from osgeo import ogr
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

def get_source_crs_from_metadata(url):
    """
    Retrieves the CRS from GeoParquet metadata.
    """
    try:
        # FIX: GDAL requires /vsicurl/ prefix for remote HTTP Parquet files
        connection_url = f"/vsicurl/{url}" if url.startswith('http') else url
        
        data_source = ogr.Open(connection_url) 
        if data_source:
            layer = data_source.GetLayer()
            spatial_ref = layer.GetSpatialRef()  
            if spatial_ref:
                # Use WKT for maximum compatibility
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
    # FIX: Get the ACTUAL CRS of your map canvas
    canvas_crs = QgsProject.instance().crs()
    
    # If they already match, do nothing
    if canvas_crs.authid() == target_crs.authid():
        return qgis_extent

    # Set up transformation: [Current Map] -> [Source Parquet File]
    transform = QgsCoordinateTransform(canvas_crs, target_crs, QgsProject.instance())
    
    try:
        # Perform the actual numerical transformation
        transformed_extent = transform.transformBoundingBox(qgis_extent)
        
        # DEBUG: Check if numbers changed from millions (3857) to degrees (4326) or RD New (28992)
        print(f"DEBUG: Transform from {canvas_crs.authid()} to {target_crs.authid()}")
        print(f"DEBUG: Before: {qgis_extent.xMinimum()}, After: {transformed_extent.xMinimum()}")
        
        return transformed_extent
    except Exception as e:
        print(f"BBOX transform failed: {e}")
        return qgis_extent