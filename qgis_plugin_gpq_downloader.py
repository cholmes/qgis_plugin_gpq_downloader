from qgis.PyQt.QtWidgets import (
    QAction, QFileDialog, QMessageBox, QDialog, 
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QComboBox, QProgressDialog, 
    QRadioButton, QStackedWidget, QWidget, QCheckBox
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import pyqtSignal, QObject, Qt, QThread
from qgis.core import (
    QgsProject, QgsRectangle, QgsVectorLayer, 
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsSettings, QgsSettings
)
import os
import datetime
import duckdb
from pathlib import Path
from .utils import transform_bbox_to_4326
from . import resources_rc

PRESET_DATASETS = {
    "overture": {
        "buildings": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=buildings/type=building/*",
            "info_url": "https://docs.overturemaps.org/reference/buildings",
            "needs_validation": False
        },
        "places": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=places/type=place/*",
            "info_url": "https://docs.overturemaps.org/reference/places",
            "needs_validation": False
        },
        "transportation": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=transportation/type=segment/*",
            "info_url": "https://docs.overturemaps.org/reference/transportation",
            "needs_validation": False
        },
        "addresses": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=addresses/type=*/*",
            "info_url": "https://docs.overturemaps.org/reference/addresses",
            "needs_validation": False
        },
        "base": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=base/type={subtype}/*",
            "info_url": "https://docs.overturemaps.org/reference/base",
            "needs_validation": False,
            "subtypes": ["infrastructure", "land", "land_cover", "land_use", "water", "bathymetry"]
        },
        "divisions": {
            "url_template": "s3://overturemaps-us-west-2/release/2025-01-22.0/theme=divisions/type=division_area/*",
            "info_url": "https://docs.overturemaps.org/reference/administrative",
            "needs_validation": False
        }
    },
    "source_cooperative": {
        "planet_eu_boundaries": {
            "url": "https://data.source.coop/planet/eu-field-boundaries/field_boundaries.parquet",
            "info_url": "https://source.coop/repositories/planet/eu-field-boundaries/description",
            "needs_validation": False,
            "display_name": "Planet EU Field Boundaries (2022)"
        },
        "usda_crop": {
            "url": "https://data.source.coop/fiboa/us-usda-cropland/us_usda_cropland.parquet",
            "info_url": "https://source.coop/fiboa/us-usda-cropland/description",
            "needs_validation": False,
            "display_name": "USDA Crop Sequence Boundaries"
        },
        "ca_crop": {
            "url": "https://data.source.coop/fiboa/us-ca-scm/us_ca_scm.parquet",
            "info_url": "https://source.coop/repositories/fiboa/us-ca-scm/description",
            "needs_validation": False,
            "display_name": "California Crop Mapping"
        },
        "vida_buildings": {
            "url": "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country/*/*.parquet",
            "info_url": "https://source.coop/repositories/vida/google-microsoft-osm-open-buildings/description",
            "needs_validation": False,
            "display_name": "VIDA Google/Microsoft/OSM Buildings"
        },
        "us_structures": {
            "url": "s3://us-west-2.opendata.source.coop/wherobots/usa-structures/geoparquet/*.parquet",
            "info_url": "https://source.coop/wherobots/usa-structures/geoparquet",
            "needs_validation": False,
            "display_name": "US Structures from ORNL by Wherobots"
        },
        "fsq_places_fused": {
            "url": "s3://us-west-2.opendata.source.coop/fused/fsq-os-places/2025-01-10/places/*.parquet",
            "info_url": "https://source.coop/repositories/fused/fsq-os-places/description",
            "needs_validation": False,
            "display_name": "Foursquare Open Source Places - Fused-partitioned"
        },
        "nhd_flowlines": {
            "url": "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
            "info_url": "https://source.coop/repositories/cholmes/nhd/description",
            "needs_validation": True, # could be false if we could specify bbox column name
            "display_name": "NHD Flowlines (experimental)"
        }
    },
    "other": {
        "foursquare_places": {
            "url": "hf://datasets/foursquare/fsq-os-places/release/dt=2025-02-06/places/parquet/*.parquet",
            "info_url": "https://docs.foursquare.com/data-products/docs/places-overview",
            "needs_validation": False,
            "display_name": "Foursquare Places"
        }
    }
}

SUPPORTED_FORMATS = {
    "GeoParquet (*.parquet)": {
        "extension": ".parquet",
        "format_options": "(FORMAT 'parquet', COMPRESSION 'ZSTD')"
    },
    "GeoPackage (*.gpkg)": {
        "extension": ".gpkg",
        "format_options": "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326')"
    },
    "FlatGeobuf (*.fgb)": {
        "extension": ".fgb",
        "format_options": "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326')"
    },
    "GeoJSON (*.geojson)": {
        "extension": ".geojson",
        "format_options": "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326')"
    }
}

class Worker(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(str)
    load_layer = pyqtSignal(str)
    info = pyqtSignal(str)
    progress = pyqtSignal(str)
    percent = pyqtSignal(int)
    file_size_warning = pyqtSignal(float)  # Signal for file size warnings (in MB)

    def __init__(self, dataset_url, extent, output_file, iface, validation_results, layer_name=None):
        super().__init__()
        self.dataset_url = dataset_url
        self.extent = extent
        self.output_file = output_file
        self.iface = iface
        self.validation_results = validation_results
        self.killed = False
        self.layer_name = layer_name
        self.size_warning_accepted = False  # Ensure this is False on initialization
        self.size_warning_accepted = False  # Ensure this is False on initialization

    def get_bbox_info_from_metadata(self, conn):
        """Read GeoParquet metadata to find bbox column info"""
        self.progress.emit("Checking for bbox metadata...")
        metadata_query = f"SELECT key, value FROM parquet_kv_metadata('{self.dataset_url}')"
        metadata_results = conn.execute(metadata_query).fetchall()
        
        for key, value in metadata_results:
            if key == b'geo':
                try:
                    decoded_value = value.decode()
                    print("\nRaw metadata value:")
                    print(decoded_value)
                    
                    # Parse JSON using DuckDB's JSON functions
                    json_query = f"SELECT json_parse('{decoded_value}'::VARCHAR) as json"
                    print("\nExecuting JSON query:")
                    print(json_query)
                    
                    geo_metadata = conn.execute(json_query).fetchone()[0]
                    print("\nParsed metadata:")
                    print(geo_metadata)
                    
                    if geo_metadata and 'covering' in geo_metadata:
                        print("\nFound covering:")
                        print(geo_metadata['covering'])
                        if 'bbox' in geo_metadata['covering']:
                            bbox_info = geo_metadata['covering']['bbox']
                            print("\nExtracted bbox info:")
                            print(bbox_info)
                            return bbox_info
                except Exception as e:
                    print(f"\nError parsing geo metadata: {str(e)}")
                    print(f"Exception type: {type(e)}")
                    import traceback
                    print(traceback.format_exc())
                    continue
        return None

    def run(self):
        try:
            layer_info = f" for {self.layer_name}" if self.layer_name else ""
            self.progress.emit(f"Connecting to database{layer_info}...")
            source_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            bbox = transform_bbox_to_4326(self.extent, source_crs)

            conn = duckdb.connect()
            try:
                # Install and load the spatial extension
                self.progress.emit(f"Loading spatial extension for{layer_info}...")

                if self.output_file.lower().endswith('.duckdb'):
                    conn = duckdb.connect(self.output_file)  # Connect directly to output file
                else:
                    conn = duckdb.connect() 

                conn.execute("INSTALL httpfs;")
                conn.execute("INSTALL spatial;")
                conn.execute("LOAD httpfs;")
                conn.execute("LOAD spatial;")

                # Get schema early as we need it for both column names and bbox check
                schema_query = f"DESCRIBE SELECT * FROM read_parquet('{self.dataset_url}')"
                schema_result = conn.execute(schema_query).fetchall()
                self.validation_results['schema'] = schema_result

                table_name = "download_data"

                self.progress.emit(f"Preparing query for{layer_info}...")
                select_query = "SELECT *"
                if not self.output_file.endswith(".parquet"):
                    # Construct the SELECT clause with array conversion to strings
                    columns = []
                    for row in schema_result:
                        col_name = row[0]
                        col_type = row[1]
                        
                        # Quote the column name to handle special characters
                        quoted_col_name = f'"{col_name}"'
                        
                        if 'STRUCT' in col_type.upper() or 'MAP' in col_type.upper():
                            columns.append(f"TO_JSON({quoted_col_name}) AS {quoted_col_name}")
                        elif '[]' in col_type:  # Check for array types like VARCHAR[]
                            columns.append(f"array_to_string({quoted_col_name}, ', ') AS {quoted_col_name}")
                        elif col_type.upper() == 'UTINYINT':
                            columns.append(f"CAST({quoted_col_name} AS INTEGER) AS {quoted_col_name}")
                        else:
                            columns.append(quoted_col_name)

                    # Check if this is Overture data and has a names column
                    has_names_column = any('names' in row[0] for row in schema_result)
                    if 'overture' in self.dataset_url and has_names_column:
                        select_query = f'SELECT "names"."primary" as name,{", ".join(columns)}'
                    else:
                        select_query = f'SELECT {", ".join(columns)}'

                # Construct WHERE clause based on bbox information
                bbox_column = self.validation_results.get('bbox_column')
                if bbox_column:
                    # Use the validated bbox column (either 'bbox' or from metadata)
                    where_clause = f"""
                    WHERE "{bbox_column}".xmin BETWEEN {bbox.xMinimum()} AND {bbox.xMaximum()}
                    AND "{bbox_column}".ymin BETWEEN {bbox.yMinimum()} AND {bbox.yMaximum()}
                    """
                else:
                    # Fall back to ST_Intersects if no bbox column found
                    where_clause = f"""
                    WHERE ST_Intersects(
                        geometry,
                        ST_GeomFromText('POLYGON(({bbox.xMinimum()} {bbox.yMinimum()},
                                            {bbox.xMaximum()} {bbox.yMinimum()},
                                            {bbox.xMaximum()} {bbox.yMaximum()},
                                            {bbox.xMinimum()} {bbox.yMaximum()},
                                            {bbox.xMinimum()} {bbox.yMinimum()}))')
                    )
                    """

                # Base query
                base_query = f"""
                CREATE TABLE {table_name} AS (
                    {select_query} FROM read_parquet('{self.dataset_url}')
                    {where_clause}
                ) 
                """
                self.progress.emit(f"Downloading{layer_info} data...")
                print("Executing SQL query:")
                print(base_query)
                conn.execute(base_query)
                
                # Add check for empty results
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if row_count == 0:
                    self.info.emit(f"No data found{layer_info} in the requested area. Check that your map extent overlaps with the data and/or expand your map extent. Skipping to next dataset if available.")
                    self.finished.emit()
                    return

                self.progress.emit(f"Processing{layer_info} data to requested format...")

                file_extension = self.output_file.lower().split('.')[-1]

                if file_extension == 'duckdb':
                    # Commit the transaction to ensure the data is saved
                    conn.commit()
                    if not self.killed:
                        self.info.emit(
                            "Data has been successfully saved to DuckDB database.\n\n"
                            "Note: QGIS does not currently support loading DuckDB files directly."
                        )
                else:
                    # Check size if exporting to GeoJSON
                    if self.output_file.lower().endswith('.geojson'):
                        estimated_size = self.estimate_file_size(conn, table_name)
                        if estimated_size > 4096 and not self.size_warning_accepted:  # 20MB warning threshold
                            self.file_size_warning.emit(estimated_size)
                            return

                    copy_query = f"COPY {table_name} TO '{self.output_file}'"

                    if file_extension == "parquet":
                        format_options = "(FORMAT 'parquet', COMPRESSION 'ZSTD');"
                    elif self.output_file.endswith(".gpkg"):
                        format_options = "(FORMAT GDAL, DRIVER 'GPKG');"
                    elif self.output_file.endswith(".fgb"):
                        format_options = "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');"
                    elif self.output_file.endswith(".geojson"):
                        format_options = "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');"
                    else:
                        self.error.emit("Unsupported file format.")
                    
                    print("Executing SQL query:")
                    print(copy_query + format_options)
                    conn.execute(copy_query + format_options)

                
                if self.killed:
                    return

                if not self.killed:
                    if self.output_file.lower().endswith('.duckdb'):
                        self.info.emit(
                            "Data has been successfully saved to DuckDB database.\n\n"
                            "Note: QGIS does not currently support loading DuckDB files directly."
                        )
                    else:
                        self.load_layer.emit(self.output_file)
                    self.finished.emit()

            except Exception as e:
                if not self.killed:
                    # Change error to info if it's a "no data" error
                    error_str = str(e)
                    if "No data found" in error_str:
                        self.info.emit(f"No data found{layer_info} in the requested area for {self.dataset_url}. Skipping to next dataset if available.")
                        self.finished.emit()
                    else:
                        self.error.emit(error_str)
            finally:
                if not self.output_file.lower().endswith('.duckdb'): # Clean up temporary table
                    try:
                        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                    except:
                        pass
                conn.close()

        except Exception as e:
            if not self.killed:
                self.error.emit(str(e))

    def kill(self):
        self.killed = True

    def estimate_file_size(self, conn, table_name):
        """Estimate the output file size in MB using GeoJSON feature collection structure"""
        try:
            # Get total row count
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            
            # Use a smaller sample size for large datasets
            sample_size = min(100, row_count)
            
            if sample_size > 0:
                # Create a proper GeoJSON FeatureCollection sample with all properties
                sample_query = f"""
                    WITH sample AS (
                        SELECT * FROM {table_name} LIMIT {sample_size}
                    )
                    SELECT AVG(LENGTH(
                        json_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON(geometry),
                            'properties', json_object(
                                {', '.join([
                                    f"'{col[0]}', COALESCE(CAST({col[0]} AS VARCHAR), 'null')"
                                    for col in conn.execute(f"DESCRIBE {table_name}").fetchall()
                                    if col[0] != 'geometry'
                                ])}
                            )
                        )::VARCHAR
                    )) as avg_feature_size
                    FROM sample;
                """
                
                # Get average feature size
                avg_feature_size = conn.execute(sample_query).fetchone()[0]
                
                if avg_feature_size:
                    # Account for GeoJSON overhead
                    collection_overhead = 50  # {"type":"FeatureCollection","features":[]}
                    comma_overhead = row_count - 1  # Commas between features
                    
                    total_estimated_bytes = (row_count * avg_feature_size) + collection_overhead + comma_overhead
                    return total_estimated_bytes / (1024 * 1024)  # Convert to MB
            return 0
            
        except Exception as e:
            print(f"Error estimating file size: {str(e)}")
            return 0

class ValidationWorker(QObject):
    finished = pyqtSignal(bool, str, dict)
    progress = pyqtSignal(str)
    needs_bbox_warning = pyqtSignal()

    def __init__(self, dataset_url, iface, extent):
        super().__init__()
        self.dataset_url = dataset_url
        self.iface = iface
        self.extent = extent
        self.killed = False

    def check_bbox_metadata(self, conn):
        """Check for bbox information in GeoParquet metadata"""
        metadata_query = f"SELECT key, value FROM parquet_kv_metadata('{self.dataset_url}')"
        metadata_results = conn.execute(metadata_query).fetchall()
        
        for key, value in metadata_results:
            if key == b'geo':
                try:
                    decoded_value = value.decode()
                    print("\nRaw metadata value:")
                    print(decoded_value)
                    
                    # Install and load JSON extension
                    conn.execute("INSTALL json;")
                    conn.execute("LOAD json;")
                    
                    # Create a table with the JSON string
                    conn.execute(f"CREATE TEMP TABLE temp_json AS SELECT '{decoded_value}' as json_str")
                    
                    # Extract the bbox column name using JSON path
                    # First get the geometry column info which contains the covering
                    result = conn.execute("""
                        SELECT json_str->'$.columns.geometry.covering.bbox.xmin[0]' as bbox_column
                        FROM temp_json
                    """).fetchone()
                    
                    print("\nExtracted bbox column name:")
                    print(result[0] if result else None)
                    
                    if result and result[0]:
                        # Remove quotes from the result if present
                        bbox_col = result[0].strip('"')
                        return bbox_col
                        
                except Exception as e:
                    print(f"\nError parsing geo metadata: {str(e)}")
                    print(f"Exception type: {type(e)}")
                    import traceback
                    print(traceback.format_exc())
                finally:
                    # Clean up temporary table
                    conn.execute("DROP TABLE IF EXISTS temp_json")
        return None

    def run(self):
        try:
            self.progress.emit("Connecting to data source...")
            conn = duckdb.connect()
            conn.execute("INSTALL spatial;")
            conn.execute("LOAD spatial;")
            conn.execute("INSTALL httpfs;")
            conn.execute("LOAD httpfs;")
            
            self.progress.emit("Checking data format...")
            try:
                # Try to read the schema to verify it's a valid parquet file
                schema_query = f"DESCRIBE SELECT * FROM read_parquet('{self.dataset_url}')"
                schema_result = conn.execute(schema_query).fetchall()
            except Exception as e:
                self.finished.emit(False, "Could not read the parquet file. Please check the URL and try again.", {})
                return
            
            # Store schema and check for BBOX
            validation_results = {
                'schema': schema_result,
                'has_bbox': False,
                'bbox_column': None
            }
            
            # Check for standard bbox column first
            if any(row[0].lower() == 'bbox' and 'struct' in row[1].lower() for row in schema_result):
                validation_results['has_bbox'] = True
                validation_results['bbox_column'] = 'bbox'
            else:
                # Check metadata for alternative bbox column
                bbox_column = self.check_bbox_metadata(conn)
                if bbox_column:
                    validation_results['has_bbox'] = True
                    validation_results['bbox_column'] = bbox_column
            
            if not validation_results['has_bbox']:
                # Emit signal for main thread to show warning
                self.needs_bbox_warning.emit()
                return

            # Try to verify data exists in the area
            self.progress.emit("Checking for data in the area...")
            try:
                where_clause = ""
                if validation_results['bbox_column']:
                    bbox = transform_bbox_to_4326(self.extent, self.iface.mapCanvas().mapSettings().destinationCrs())
                    where_clause = f"""
                    WHERE "{validation_results['bbox_column']}".xmin BETWEEN {bbox.xMinimum()} AND {bbox.xMaximum()}
                    AND "{validation_results['bbox_column']}".ymin BETWEEN {bbox.yMinimum()} AND {bbox.yMaximum()}
                    """
                
                test_query = f"""
                SELECT COUNT(*) FROM read_parquet('{self.dataset_url}')
                {where_clause}
                LIMIT 1
                """
                result = conn.execute(test_query).fetchone()
                if result[0] == 0:
                    layer_info = f" for {self.layer_name}" if hasattr(self, 'layer_name') else ""
                    self.finished.emit(False, f"No data found{layer_info} in the requested area. Check that your map extent overlaps with the data and/or expand your map extent.", {})
                    return
            except Exception as e:
                # If the query fails, continue anyway as the file might be valid but structured differently
                pass

            self.finished.emit(True, "Validation successful", validation_results)

        except Exception as e:
            self.finished.emit(False, f"Error validating source: {str(e)}", {})
        finally:
            conn.close()

    def needs_validation(self):
        """Determine if the dataset needs any validation"""
        # Check if URL matches any preset dataset
        for source in PRESET_DATASETS.values():
            for dataset in source.values():
                if isinstance(dataset.get('url'), str) and dataset['url'] in self.dataset_url:
                    return dataset.get('needs_validation', True)
                elif isinstance(dataset.get('url_template'), str) and dataset['url_template'].split('{')[0] in self.dataset_url:
                    return dataset.get('needs_validation', True)
        
        # All other datasets need validation
        return True

class DataSourceDialog(QDialog):
    validation_complete = pyqtSignal(bool, str, dict)
    
    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.validation_thread = None
        self.validation_worker = None
        self.progress_message = None
        self.requires_validation = True
        self.setWindowTitle("GeoParquet Data Source")
        self.setMinimumWidth(500)
        
        # Create main layout
        layout = QVBoxLayout()
        
        # Create horizontal layout for radio buttons
        radio_layout = QHBoxLayout()
        
        # Create radio buttons
        self.overture_radio = QRadioButton("Overture Maps")
        self.sourcecoop_radio = QRadioButton("Source Cooperative")
        self.other_radio = QRadioButton("Hugging Face")
        self.custom_radio = QRadioButton("Custom URL")

        # Add radio buttons to horizontal layout
        radio_layout.addWidget(self.overture_radio)
        radio_layout.addWidget(self.sourcecoop_radio)
        radio_layout.addWidget(self.other_radio)
        radio_layout.addWidget(self.custom_radio)

        # Connect to save state
        self.overture_radio.released.connect(self.save_radio_button_state)
        self.sourcecoop_radio.released.connect(self.save_radio_button_state)
        self.other_radio.released.connect(self.save_radio_button_state)
        self.custom_radio.released.connect(self.save_radio_button_state)

        # Add radio button layout to main layout
        layout.addLayout(radio_layout)
        
        # Add some spacing between radio buttons and content
        layout.addSpacing(10)
        
        # Create and setup the stacked widget for different options
        self.stack = QStackedWidget()
        
        # Custom URL page
        custom_page = QWidget()
        custom_layout = QVBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter URL to Parquet file or folder (s3:// or https://)")
        custom_layout.addWidget(self.url_input)
        custom_page.setLayout(custom_layout)
        
        # Overture Maps page
        self.setup_overture_page()
        
        # Source Cooperative page
        sourcecoop_page = QWidget()
        sourcecoop_layout = QVBoxLayout()
        self.sourcecoop_combo = QComboBox()
        self.sourcecoop_combo.addItems([
            dataset['display_name'] 
            for dataset in PRESET_DATASETS['source_cooperative'].values()
        ])
        sourcecoop_layout.addWidget(self.sourcecoop_combo)

        # Add link label
        self.sourcecoop_link = QLabel()
        self.sourcecoop_link.setOpenExternalLinks(True)
        self.sourcecoop_link.setWordWrap(True)
        sourcecoop_layout.addWidget(self.sourcecoop_link)

        # Connect combo box change to update link
        self.sourcecoop_combo.currentTextChanged.connect(self.update_sourcecoop_link)
        sourcecoop_page.setLayout(sourcecoop_layout)
        
        # Other sources page
        other_page = QWidget()
        other_layout = QVBoxLayout()
        self.other_combo = QComboBox()
        self.other_combo.addItems([
            dataset['display_name'] 
            for dataset in PRESET_DATASETS['other'].values()
        ])
        other_layout.addWidget(self.other_combo)
        
        # Add link label for other sources
        self.other_link = QLabel()
        self.other_link.setOpenExternalLinks(True)
        self.other_link.setWordWrap(True)
        other_layout.addWidget(self.other_link)
        
        # Connect combo box change to update link
        self.other_combo.currentTextChanged.connect(self.update_other_link)
        other_page.setLayout(other_layout)
        
        # Add initial link update for other sources
        self.update_other_link(self.other_combo.currentText())
        
        # Add pages to stack
        self.stack.addWidget(custom_page)
        self.stack.addWidget(self.setup_overture_page())
        self.stack.addWidget(sourcecoop_page)
        self.stack.addWidget(other_page)
        
        layout.addWidget(self.stack)
        
        # Buttons
        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # Connect signals
        self.custom_radio.toggled.connect(lambda: self.stack.setCurrentIndex(0))
        self.overture_radio.toggled.connect(lambda: self.stack.setCurrentIndex(1))
        self.sourcecoop_radio.toggled.connect(lambda: self.stack.setCurrentIndex(2))
        self.other_radio.toggled.connect(lambda: self.stack.setCurrentIndex(3))
        self.ok_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)
        
        # Add after setting up the sourcecoop_combo
        self.update_sourcecoop_link(self.sourcecoop_combo.currentText())
        
    def setup_overture_page(self):
        overture_page = QWidget()
        overture_layout = QVBoxLayout()
        
        # Create horizontal layout for main checkboxes
        checkbox_layout = QHBoxLayout()
        
        # Create a widget to hold checkboxes
        self.overture_checkboxes = {}
        for key in PRESET_DATASETS['overture'].keys():
            if key != 'base':  # Handle base separately
                checkbox = QCheckBox(key.title())
                self.overture_checkboxes[key] = checkbox
                checkbox_layout.addWidget(checkbox)
        
        # Add the horizontal checkbox layout to main layout
        overture_layout.addLayout(checkbox_layout)
        
        # Add base layer section
        base_group = QWidget()
        base_layout = QVBoxLayout()
        base_layout.setContentsMargins(0, 10, 0, 0)  # Add some top margin
        
        self.base_checkbox = QCheckBox("Base")
        self.overture_checkboxes['base'] = self.base_checkbox
        base_layout.addWidget(self.base_checkbox)
        
        # Add base subtype checkboxes
        self.base_subtype_widget = QWidget()
        base_subtype_layout = QHBoxLayout()  # Horizontal layout for subtypes
        base_subtype_layout.setContentsMargins(20, 0, 0, 0)  # Add left margin for indentation
        
        # Replace combo box with checkboxes
        self.base_subtype_checkboxes = {}
        # Dictionary to map internal names to display names
        subtype_display_names = {
            'infrastructure': 'Infrastructure',
            'land': 'Land',
            'land_cover': 'Land Cover',
            'land_use': 'Land Use',
            'water': 'Water',
            'bathymetry': 'Bathymetry'
        }
        
        for subtype in PRESET_DATASETS['overture']['base']['subtypes']:
            # Use the display name for the checkbox text
            checkbox = QCheckBox(subtype_display_names[subtype])
            self.base_subtype_checkboxes[subtype] = checkbox
            base_subtype_layout.addWidget(checkbox)
        
        self.base_subtype_widget.setLayout(base_subtype_layout)
        self.base_subtype_widget.hide()
        
        base_layout.addWidget(self.base_subtype_widget)
        base_group.setLayout(base_layout)
        overture_layout.addWidget(base_group)
        
        # Connect base checkbox to show/hide subtype checkboxes and resize dialog
        self.base_checkbox.toggled.connect(self.base_subtype_widget.setVisible)
        self.base_checkbox.toggled.connect(self.adjust_dialog_size)
        
        overture_page.setLayout(overture_layout)
        return overture_page

    def adjust_dialog_size(self, checked):
        """Adjust dialog size when base checkbox is toggled"""
        if checked:
            # Get current size
            current_size = self.size()
            # Add height for the base subtypes (adjust the 50 value if needed)
            self.resize(current_size.width() + 100, current_size.height())
        else:
            # Restore original size
            current_size = self.size()

            self.resize(current_size.width() - 100, current_size.height())

    def save_radio_button_state(self) -> None:
        if self.custom_radio.isChecked():
            button_name = self.custom_radio.text()
        elif self.overture_radio.isChecked():
            button_name = self.overture_radio.text()
        elif self.sourcecoop_radio.isChecked():
            button_name = self.sourcecoop_radio.text()
        elif self.other_radio.isChecked():
            button_name = self.other_radio.text()
        elif self.custom_radio.isChecked():
            button_name = self.custom_radio.text()

        QgsSettings().setValue(
            "gpq_downloader/radio_selection",
            button_name,
            section=QgsSettings.Plugins,
        )

    def validate_and_accept(self):
        """Validate the input and accept the dialog if valid"""
        urls = self.get_urls()
        if not urls:
            QMessageBox.warning(self, "Validation Error", "Please select at least one dataset")
            return
        
        # For Overture datasets, we know they're valid so we can skip validation
        if self.overture_radio.isChecked():
            self.selected_urls = urls
            self.accept()
            return
        
        # For custom URLs, do validation
        if self.custom_radio.isChecked():
            for url in urls:
                if not (url.startswith('http://') or url.startswith('https://') or 
                       url.startswith('s3://') or url.startswith('file://') or url.startswith('hf://')):
                    QMessageBox.warning(self, "Validation Error", 
                        "URL must start with http://, https://, s3://, hf://, or file://")
                    return
            
                # Create progress dialog for validation
                progress_dialog = QProgressDialog("Validating URL...", "Cancel", 0, 0, self)
                progress_dialog.setWindowModality(Qt.WindowModal)
                
                # Create validation worker
                self.validation_worker = ValidationWorker(url, self.iface, self.iface.mapCanvas().extent())
                self.validation_thread = QThread()
                self.validation_worker.moveToThread(self.validation_thread)
                
                # Connect signals
                self.validation_thread.started.connect(self.validation_worker.run)
                self.validation_worker.progress.connect(progress_dialog.setLabelText)
                self.validation_worker.finished.connect(
                    lambda success, message, results: self.handle_validation_result(
                        success, message, results, progress_dialog, urls
                    )
                )
                self.validation_worker.needs_bbox_warning.connect(self.show_bbox_warning)
                
                # Start validation
                self.validation_thread.start()
                progress_dialog.exec_()
                return
        
        # For other preset sources, we can skip validation
        self.selected_urls = urls
        self.accept()

    def handle_validation_result(self, success, message, results, progress_dialog, urls):
        """Handle the validation result"""
        progress_dialog.close()
        self.validation_thread.quit()
        self.validation_thread.wait()
        
        if success:
            self.selected_urls = urls
            self.accept()
        else:
            QMessageBox.warning(self, "Validation Error", 
                "Could not validate the URL. Please check that the URL points to a valid GeoParquet file and try again.")

    def get_urls(self):
        """Returns a list of URLs for selected datasets"""
        if self.custom_radio.isChecked():
            return [self.url_input.text().strip()]
        elif self.overture_radio.isChecked():
            urls = []
            for theme, checkbox in self.overture_checkboxes.items():
                if checkbox.isChecked():
                    dataset = PRESET_DATASETS['overture'][theme]
                    if theme == "transportation":
                        type_str = "segment"
                    elif theme == "divisions":
                        type_str = "division_area"
                    elif theme == "addresses":
                        type_str = "*"
                    elif theme == "base":
                        # Handle multiple base subtypes
                        for subtype, subtype_checkbox in self.base_subtype_checkboxes.items():
                            if subtype_checkbox.isChecked():
                                urls.append(dataset['url_template'].format(subtype=subtype))
                        continue  # Skip the normal URL append for base
                    else:
                        type_str = theme.rstrip('s')  # remove trailing 's' for singular form
                    urls.append(dataset['url_template'].format(subtype=type_str))
            return urls
        elif self.sourcecoop_radio.isChecked():
            selection = self.sourcecoop_combo.currentText()
            dataset = next((dataset for dataset in PRESET_DATASETS['source_cooperative'].values() 
                           if dataset['display_name'] == selection), None)
            return [dataset['url']] if dataset else []
        elif self.other_radio.isChecked():
            selection = self.other_combo.currentText()
            dataset = next((dataset for dataset in PRESET_DATASETS['other'].values() 
                           if dataset['display_name'] == selection), None)
            return [dataset['url']] if dataset else []
        return []

    def update_sourcecoop_link(self, selection):
        """Update the link based on the selected dataset"""
        if selection == "Planet EU Field Boundaries (2022)":
            self.sourcecoop_link.setText('<a href="https://source.coop/repositories/planet/eu-field-boundaries/description">View dataset info</a>')
        elif selection == "USDA Crop Sequence Boundaries":
            self.sourcecoop_link.setText('<a href="https://source.coop/fiboa/us-usda-cropland/description">View dataset info</a>')
        elif selection == "California Crop Mapping":
            self.sourcecoop_link.setText('<a href="https://source.coop/repositories/fiboa/us-ca-scm/description">View dataset info</a>')
        elif selection == "VIDA Google/Microsoft/OSM Buildings":
            self.sourcecoop_link.setText('<a href="https://source.coop/repositories/vida/google-microsoft-osm-open-buildings/description">View dataset info</a>')
        else:
            self.sourcecoop_link.setText('')

    def update_other_link(self, selection):
        """Update the link based on the selected dataset"""
        for dataset in PRESET_DATASETS['other'].values():
            if dataset['display_name'] == selection:
                self.other_link.setText(
                    f'<a href="{dataset["info_url"]}">View dataset info</a>'
                )
                return
        self.other_link.setText('')

    def show_bbox_warning(self):
        """Show bbox warning dialog in main thread"""
        # Close the progress dialog if it exists
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
            
        reply = QMessageBox.warning(
            self,
            "No bbox Column Detected",
            "This dataset doesn't have a bbox column, which means downloads will be slower. "
            "GeoParquet 1.1 files with a bbox column work much better - tell your data provider to upgrade!\n\n"
            "Do you want to continue with the download?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        validation_results = {'has_bbox': False, 'schema': None}
        if reply == QMessageBox.No:
            self.validation_complete.emit(False, "Download cancelled by user.", validation_results)
        else:
            self.validation_complete.emit(True, "Validation successful", validation_results)

class QgisPluginGeoParquet:
    def __init__(self, iface):
        self.iface = iface
        self.worker = None
        self.worker_thread = None
        self.action = None
        self.output_file = None
        # Create a default downloads directory in user's home directory
        self.download_dir = Path.home() / "Downloads" 
        # Create the directory if it doesn't exist
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def initGui(self):
        # Create the action with the icon and tooltip
        icon_path = ':/qgis_plugin_gpq_downloader/icons/parquet-download.svg'
        self.action = QAction(
            QIcon(icon_path),
            "Download GeoParquet Data", 
            self.iface.mainWindow()
        )
        self.action.setToolTip("Download GeoParquet Data")
        self.action.triggered.connect(self.run)

        # Add the actions to the toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        # Clean up worker and thread when plugin is unloaded
        self.cleanup_thread()
        # Remove all actions from the toolbar
        self.iface.removeToolBarIcon(self.action)

    def run(self, default_source=None):
        # Reset any existing worker
        self.worker = None
        self.worker_thread = None
        
        dialog = DataSourceDialog(self.iface.mainWindow(), self.iface)

        selected_name = QgsSettings().value("gpq_downloader/radio_selection", section=QgsSettings.Plugins)
        for button in [dialog.overture_radio, dialog.sourcecoop_radio, dialog.other_radio, dialog.custom_radio]:
            if button.text() == selected_name:
                button.setChecked(True)
        if not selected_name:
            dialog.overture_radio.setChecked(True)
        
        if dialog.exec_() == QDialog.Accepted:
            # Get the selected URLs from the dialog
            urls = dialog.selected_urls
            extent = self.iface.mapCanvas().extent()
            
            # First, collect all file locations from user
            download_queue = []
            for url in urls:
                # Get current date for filename
                current_date = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                
                # Generate filename based on the URL and source type
                if dialog.overture_radio.isChecked():
                    # Extract theme from URL
                    theme = url.split('theme=')[1].split('/')[0]
                    if 'type=' in url:
                        type_str = url.split('type=')[1].split('/')[0]
                        if theme == 'base':
                            filename = f"overture_base_{type_str}_{current_date}.parquet"
                        else:
                            # Use theme name directly for other types
                            filename = f"overture_{theme}_{current_date}.parquet"
                    else:
                        filename = f"overture_{theme}_{current_date}.parquet"
                elif dialog.sourcecoop_radio.isChecked():
                    # Get the selected dataset name from the combo box
                    dataset_name = dialog.sourcecoop_combo.currentText()
                    # Convert to snake case and clean up the name
                    clean_name = dataset_name.lower().replace(' ', '_').replace('/', '_').replace('(', '').replace(')', '')
                    filename = f"sourcecoop_{clean_name}_{current_date}.parquet"
                elif dialog.other_radio.isChecked():
                    # Get the selected dataset name from the combo box
                    dataset_name = dialog.other_combo.currentText()
                    # Convert to snake case and clean up the name
                    clean_name = dataset_name.lower().replace(' ', '_').replace('/', '_')
                    filename = f"other_{clean_name}_{current_date}.parquet"
                else:
                    # Custom URL case
                    filename = f"custom_download_{current_date}.parquet"

                default_save_path = str(self.download_dir / filename)
                
                # Show save file dialog
                output_file, selected_filter = QFileDialog.getSaveFileName(
                    self.iface.mainWindow(),
                    f"Save Data for {theme if dialog.overture_radio.isChecked() else 'dataset'}",
                    default_save_path,
                    "GeoParquet (*.parquet);;DuckDB Database (*.duckdb);;GeoPackage (*.gpkg);;FlatGeobuf (*.fgb);;GeoJSON (*.geojson)"
                )
                
                if output_file:
                    # Add to download queue instead of starting download immediately
                    download_queue.append((url, output_file))
                else:
                    # If user cancels any save dialog, abort the whole process
                    return
            
            # Now process downloads one at a time
            self.process_download_queue(download_queue, extent)

    def process_download_queue(self, download_queue, extent):
        """Process downloads sequentially"""
        if not download_queue:
            return
        
        # Get the next download
        url, output_file = download_queue[0]
        remaining_queue = download_queue[1:]
        
        # Extract layer name from URL for Overture data
        layer_name = None
        if 'overture' in url:
            if 'theme=' in url:
                theme = url.split('theme=')[1].split('/')[0]
                if theme == 'base':
                    # For base layers, include the subtype
                    subtype = url.split('type=')[1].split('/')[0]
                    layer_name = f"Overture {theme.title()} - {subtype.title()}"
                else:
                    layer_name = f"Overture {theme.title()}"
        
        # Create validation results (we know Overture URLs are valid)
        validation_results = {'has_bbox': True, 'bbox_column': 'bbox'}
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog(
            "Starting download..." if not layer_name else f"Starting {layer_name} download...",
            "Cancel", 0, 0, self.iface.mainWindow()
        )
        self.progress_dialog.setWindowTitle("Downloading Data")
        self.progress_dialog.setWindowModality(Qt.NonModal)
        self.progress_dialog.setMinimumDuration(0)
        
        # Create worker with layer name
        self.worker = Worker(url, extent, output_file, self.iface, validation_results, layer_name)
        self.worker.remaining_queue = remaining_queue  # Store remaining queue in worker
        self.worker_thread = QThread()
        
        # Move worker to thread
        self.worker.moveToThread(self.worker_thread)
        
        # Connect signals
        self.worker_thread.started.connect(self.worker.run)
        self.worker.error.connect(self.handle_error)
        self.worker.load_layer.connect(self.load_layer)
        self.worker.info.connect(self.show_info)
        self.worker.finished.connect(lambda: self.handle_download_complete(remaining_queue, extent))
        self.worker.progress.connect(self.update_progress)
        self.worker.file_size_warning.connect(self.handle_large_file_warning)
        self.progress_dialog.canceled.connect(self.cancel_download)
        
        # Show the progress dialog and start the thread
        self.progress_dialog.show()
        self.worker_thread.start()

    def handle_download_complete(self, remaining_queue, extent):
        """Handle completion of a download and start the next one if any"""
        self.cleanup_thread()
        if remaining_queue:
            # Start the next download
            self.process_download_queue(remaining_queue, extent)

    def handle_error(self, message):
        self.progress_dialog.close()
        QMessageBox.critical(self.iface.mainWindow(), "Error", message)

    def update_progress(self, message):
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.setLabelText(message)

    def cancel_download(self):
        if self.worker:
            self.worker.kill()
        self.cleanup_thread()

    def cleanup_thread(self):
        if self.worker_thread is not None:
            if self.worker:
                self.worker.kill()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None
        if hasattr(self, 'progress_dialog'):
            self.progress_dialog.close()

    def load_layer(self, output_file):
        """Load the layer into QGIS if GeoParquet is supported"""
        if output_file.lower().endswith('.parquet'):
            # Try to create a test layer to check GeoParquet support
            test_layer = QgsVectorLayer(output_file, "test", "ogr")
            if not test_layer.isValid():
                dialog = QDialog(self.iface.mainWindow())
                dialog.setWindowTitle("GeoParquet Support Not Available")
                dialog.setMinimumWidth(400)
                
                layout = QVBoxLayout()
                
                message = QLabel(
                    "Data has been successfully saved to GeoParquet file.\n\n"
                    "Note: Your current QGIS installation does not support reading GeoParquet files directly. You can select GeoPackage for your output format to view immediately.\n\n"
                    "To view GeoParquet files in QGIS, you'll need to install QGIS with GDAL 3.8 "
                    "or higher with 'libgdal-arrow-parquet'. You can find instructions at:"
                )
                message.setWordWrap(True)
                layout.addWidget(message)
                
                link = QLabel()
                link.setText('<a href="https://github.com/cholmes/qgis_plugin_gpq_downloader/wiki/Installing-GeoParquet-Support-in-QGIS">Installing GeoParquet Support in QGIS</a>')
                link.setOpenExternalLinks(True)
                layout.addWidget(link)
                
                button_box = QPushButton("OK")
                button_box.clicked.connect(dialog.accept)
                layout.addWidget(button_box)
                
                dialog.setLayout(layout)
                dialog.exec_()
                return

        layer_name = Path(output_file).stem  # Get filename without extension
        # Create the layer
        layer = QgsVectorLayer(output_file, layer_name, "ogr")
        if not layer.isValid():
            QMessageBox.critical(self.iface.mainWindow(), "Error", f"Failed to load the layer from {output_file}")
            return
        # Add the layer to the QGIS project
        QgsProject.instance().addMapLayer(layer)

    def show_info(self, message):
        """Show an information message to the user"""
        QMessageBox.information(self.iface.mainWindow(), "Success", message)

    def handle_large_file_warning(self, estimated_size):
        """Handle warning about large GeoJSON file size with a more streamlined UI"""
        # Store worker info before any cleanup
        if not hasattr(self, 'worker') or self.worker is None:
            QMessageBox.critical(self.iface.mainWindow(), "Error", "Download session lost. Please try again.")
            return

        # Store all necessary info before any cleanup
        worker_info = {
            'dataset_url': self.worker.dataset_url,
            'extent': self.worker.extent,
            'iface': self.worker.iface,
            'validation_results': self.worker.validation_results,
            'output_file': self.worker.output_file,
            'size_warning_accepted': False,
            'remaining_queue': getattr(self.worker, 'remaining_queue', [])  # Store remaining queue
        }
        
        # Now we can safely close the progress dialog
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.close()
        
        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle("Large File Warning")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout()

        # Format size for display
        if estimated_size >= 1024:
            size_str = f"{estimated_size/1024:.2f} GB"
        else:
            size_str = f"{estimated_size:.0f} MB"
        
        msg = QLabel(
            f"The estimated file size is {size_str}. Large GeoJSON files can be slow to process and load.\n\n"
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # Format selection
        format_group = QVBoxLayout()
        recommended_label = QLabel("Alternative formats (recommended for large datasets):")
        format_group.addWidget(recommended_label)
        
        # Create horizontal layout for dropdown and button
        format_row = QHBoxLayout()
        
        # Create dropdown instead of radio buttons
        format_combo = QComboBox()
        format_combo.addItems([
            "FlatGeobuf (*.fgb)",
            "GeoPackage (*.gpkg)",
            "GeoParquet (*.parquet)"
        ])
        format_row.addWidget(format_combo)
        
        # Add Save As button next to dropdown
        save_button = QPushButton("Save As...")
        format_row.addWidget(save_button)
        
        format_group.addLayout(format_row)
        layout.addLayout(format_group)

        # Buttons (now just Proceed and Cancel)
        button_box = QHBoxLayout()
        proceed_button = QPushButton("Proceed with GeoJSON anyway")
        cancel_button = QPushButton("Cancel")
        button_box.addWidget(proceed_button)
        button_box.addWidget(cancel_button)
        layout.addLayout(button_box)

        dialog.setLayout(layout)

        # Connect buttons
        cancel_button.clicked.connect(dialog.reject)
        save_button.clicked.connect(lambda: dialog.done(1))  # Custom return code for Save As
        proceed_button.clicked.connect(lambda: dialog.done(2))  # Custom return code for Proceed

        while True:
            result = dialog.exec_()
            if result == 1:  # Save As
                # Get selected format
                selected_format = format_combo.currentText()
                extension = selected_format.split("*")[1].rstrip(")")
                
                # Update output file path with new extension
                new_output_file = os.path.splitext(worker_info['output_file'])[0] + extension
                
                # Show save file dialog with selected format
                output_file, _ = QFileDialog.getSaveFileName(
                    self.iface.mainWindow(),
                    "Save Data",
                    new_output_file,
                    selected_format
                )
                
                if output_file:
                    # Create new progress dialog
                    self.progress_dialog = QProgressDialog("Starting download...", "Cancel", 0, 0, self.iface.mainWindow())
                    self.progress_dialog.setWindowTitle("Downloading Data")
                    self.progress_dialog.setWindowModality(Qt.NonModal)
                    self.progress_dialog.setMinimumDuration(0)
                    
                    # Update output file
                    self.output_file = output_file
                    
                    # Create new worker and thread
                    self.worker = Worker(
                        worker_info['dataset_url'],
                        worker_info['extent'],
                        output_file,
                        worker_info['iface'],
                        worker_info['validation_results']
                    )
                    self.worker.remaining_queue = worker_info['remaining_queue']  # Pass remaining queue
                    self.worker_thread = QThread()
                    self.worker.moveToThread(self.worker_thread)
                    
                    # Connect signals
                    self.worker_thread.started.connect(self.worker.run)
                    self.worker.error.connect(self.handle_error)
                    self.worker.load_layer.connect(self.load_layer)
                    self.worker.info.connect(self.show_info)
                    self.worker.file_size_warning.connect(self.handle_large_file_warning)
                    self.worker.finished.connect(lambda: self.handle_download_complete(worker_info['remaining_queue'], worker_info['extent']))
                    self.worker.progress.connect(self.update_progress)
                    self.progress_dialog.canceled.connect(self.cancel_download)
                    
                    # Show progress dialog and start thread
                    self.progress_dialog.show()
                    self.worker_thread.start()
                    return
                continue  # If Save As was cancelled, continue the loop
            
            elif result == 2:  # Proceed with GeoJSON
                # Create new progress dialog
                self.progress_dialog = QProgressDialog("Starting download...", "Cancel", 0, 0, self.iface.mainWindow())
                self.progress_dialog.setWindowTitle("Downloading Data")
                self.progress_dialog.setWindowModality(Qt.NonModal)
                self.progress_dialog.setMinimumDuration(0)
                
                # Create new worker with original settings
                self.worker = Worker(
                    worker_info['dataset_url'],
                    worker_info['extent'],
                    worker_info['output_file'],
                    worker_info['iface'],
                    worker_info['validation_results']
                )
                self.worker.remaining_queue = worker_info['remaining_queue']  # Pass remaining queue
                self.worker_thread = QThread()
                self.worker.moveToThread(self.worker_thread)
                
                # Connect signals
                self.worker_thread.started.connect(self.worker.run)
                self.worker.error.connect(self.handle_error)
                self.worker.load_layer.connect(self.load_layer)
                self.worker.info.connect(self.show_info)
                self.worker.file_size_warning.connect(self.handle_large_file_warning)
                self.worker.finished.connect(lambda: self.handle_download_complete(worker_info['remaining_queue'], worker_info['extent']))
                self.worker.progress.connect(self.update_progress)
                self.progress_dialog.canceled.connect(self.cancel_download)
                
                # Set size warning accepted
                self.worker.size_warning_accepted = True
                
                # Show progress dialog and start thread
                self.progress_dialog.show()
                self.worker_thread.start()
                return
            
            else:  # Cancel
                # Process next download in queue if available
                if worker_info['remaining_queue']:
                    self.process_download_queue(worker_info['remaining_queue'], worker_info['extent'])
                else:
                    self.cleanup_thread()
                return

def classFactory(iface):
    return QgisPluginGeoParquet(iface)
