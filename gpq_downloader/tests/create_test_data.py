#!/usr/bin/env python3
"""
Create test data files for the GPQ Downloader plugin tests.

This module can create:
1. Non-GeoParquet compliant parquet files (compatible parquet as per spec)
2. Standard GeoParquet files (with proper metadata)
3. Other test data as needed
"""

import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import LineString
from shapely import wkb
import pandas as pd


def create_non_geoparquet_file(output_path="non_geoparquet_with_geometry.parquet"):
    """Create a parquet file with WKB geometry but no GeoParquet metadata."""
    
    # Create sample LineString geometries representing street segments in San Francisco
    # Using approximate coordinates for real SF streets
    geometries = [
        # Market Street segment
        LineString([(-122.4194, 37.7749), (-122.4184, 37.7759), (-122.4174, 37.7769)]),
        
        # Mission Street segment
        LineString([(-122.4180, 37.7600), (-122.4170, 37.7610), (-122.4160, 37.7620)]),
        
        # Geary Boulevard segment
        LineString([(-122.4650, 37.7810), (-122.4640, 37.7810), (-122.4630, 37.7810)]),
        
        # Van Ness Avenue segment
        LineString([(-122.4220, 37.7750), (-122.4220, 37.7760), (-122.4220, 37.7770)]),
        
        # Embarcadero segment
        LineString([(-122.3950, 37.7950), (-122.3940, 37.7940), (-122.3930, 37.7930)]),
        
        # Lombard Street segment (the famous crooked part)
        LineString([(-122.4186, 37.8021), (-122.4176, 37.8020), (-122.4166, 37.8019)]),
        
        # Golden Gate Park path
        LineString([(-122.4820, 37.7700), (-122.4810, 37.7700), (-122.4800, 37.7700)])
    ]
    
    # Convert to WKB
    wkb_geometries = [wkb.dumps(geom) for geom in geometries]
    
    # Create other sample data
    data = {
        'id': [1, 2, 3, 4, 5, 6, 7],
        'name': [
            'Market St',
            'Mission St', 
            'Geary Blvd',
            'Van Ness Ave',
            'Embarcadero',
            'Lombard St',
            'GG Park Trail'
        ],
        'type': [
            'major_street',
            'major_street',
            'boulevard',
            'avenue',
            'waterfront',
            'tourist_street',
            'park_path'
        ],
        'length_m': [250.5, 220.3, 180.7, 200.9, 190.1, 150.2, 210.4],
        'geometry': wkb_geometries  # WKB-encoded geometry column
    }
    
    # Create DataFrame
    df = pd.DataFrame(data)
    
    # Convert to PyArrow Table
    # Note: We're explicitly NOT adding any geo metadata
    table = pa.Table.from_pandas(df)
    
    # Write to Parquet file WITHOUT any geo metadata
    pq.write_table(table, output_path)
    
    print(f"Created non-GeoParquet file: {output_path}")
    print(f"Columns: {list(data.keys())}")
    print(f"Rows: {len(df)}")
    
    # Verify it has no geo metadata
    parquet_file = pq.ParquetFile(output_path)
    metadata = parquet_file.metadata
    
    # Check that there's no "geo" key in the metadata
    if metadata.metadata:
        metadata_dict = {k.decode(): v.decode() for k, v in metadata.metadata.items()}
        has_geo = 'geo' in metadata_dict
        print(f"Has 'geo' metadata: {has_geo}")
    else:
        print("No metadata present")
    
    return output_path


def create_geoparquet_file(output_path="geoparquet_with_metadata.parquet"):
    """Create a proper GeoParquet file with geo metadata."""
    
    # Same geometries as non-geoparquet version
    geometries = [
        LineString([(-122.4194, 37.7749), (-122.4184, 37.7759), (-122.4174, 37.7769)]),
        LineString([(-122.4180, 37.7600), (-122.4170, 37.7610), (-122.4160, 37.7620)]),
        LineString([(-122.4650, 37.7810), (-122.4640, 37.7810), (-122.4630, 37.7810)]),
        LineString([(-122.4220, 37.7750), (-122.4220, 37.7760), (-122.4220, 37.7770)]),
        LineString([(-122.3950, 37.7950), (-122.3940, 37.7940), (-122.3930, 37.7930)]),
        LineString([(-122.4186, 37.8021), (-122.4176, 37.8020), (-122.4166, 37.8019)]),
        LineString([(-122.4820, 37.7700), (-122.4810, 37.7700), (-122.4800, 37.7700)])
    ]
    
    # Convert to WKB
    wkb_geometries = [wkb.dumps(geom) for geom in geometries]
    
    # Create data
    data = {
        'id': [1, 2, 3, 4, 5, 6, 7],
        'name': [
            'Market St',
            'Mission St', 
            'Geary Blvd',
            'Van Ness Ave',
            'Embarcadero',
            'Lombard St',
            'GG Park Trail'
        ],
        'type': [
            'major_street',
            'major_street',
            'boulevard',
            'avenue',
            'waterfront',
            'tourist_street',
            'park_path'
        ],
        'length_m': [250.5, 220.3, 180.7, 200.9, 190.1, 150.2, 210.4],
        'geometry': wkb_geometries
    }
    
    df = pd.DataFrame(data)
    table = pa.Table.from_pandas(df)
    
    # Create GeoParquet metadata
    geo_metadata = {
        "version": "1.0.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["LineString"],
                "crs": {
                    "$schema": "https://proj.org/schemas/v0.6/projjson.schema.json",
                    "type": "GeographicCRS",
                    "name": "WGS 84",
                    "datum": {
                        "type": "GeodeticReferenceFrame",
                        "name": "World Geodetic System 1984",
                        "ellipsoid": {
                            "name": "WGS 84",
                            "semi_major_axis": 6378137,
                            "inverse_flattening": 298.257223563
                        }
                    },
                    "coordinate_system": {
                        "subtype": "ellipsoidal",
                        "axis": [
                            {
                                "name": "Geodetic longitude",
                                "abbreviation": "Lon",
                                "direction": "east",
                                "unit": "degree"
                            },
                            {
                                "name": "Geodetic latitude",
                                "abbreviation": "Lat",
                                "direction": "north",
                                "unit": "degree"
                            }
                        ]
                    },
                    "id": {
                        "authority": "EPSG",
                        "code": 4326
                    }
                }
            }
        }
    }
    
    # Convert metadata to JSON string
    import json
    geo_metadata_str = json.dumps(geo_metadata)
    
    # Create new metadata with geo key
    metadata = table.schema.metadata or {}
    metadata[b'geo'] = geo_metadata_str.encode('utf-8')
    
    # Create new table with metadata
    table = table.replace_schema_metadata(metadata)
    
    # Write GeoParquet file
    pq.write_table(table, output_path)
    
    print(f"Created GeoParquet file: {output_path}")
    print(f"Columns: {list(data.keys())}")
    print(f"Rows: {len(df)}")
    
    # Verify it has geo metadata
    parquet_file = pq.ParquetFile(output_path)
    metadata = parquet_file.metadata
    
    if metadata.metadata and b'geo' in metadata.metadata:
        print("Has 'geo' metadata: True")
    else:
        print("Has 'geo' metadata: False")
    
    return output_path


if __name__ == "__main__":
    import os
    
    # Create data directory if it doesn't exist
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    # Create both types of files
    non_geo_path = os.path.join(data_dir, 'non_geoparquet_with_geometry.parquet')
    geo_path = os.path.join(data_dir, 'geoparquet_with_metadata.parquet')
    
    print("Creating test data files...")
    print("-" * 50)
    create_non_geoparquet_file(non_geo_path)
    print("-" * 50)
    create_geoparquet_file(geo_path)
    print("-" * 50)
    print("Test data creation complete!")