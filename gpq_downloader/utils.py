import json

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import pyqtSignal, QObject
import os
import duckdb

from gpq_downloader import logger


def transform_bbox_to_4326(extent, source_crs):
    """
    Transform a bounding box to EPSG:4326 (WGS84)

    Args:
        extent (QgsRectangle): The input extent to transform
        source_crs (QgsCoordinateReferenceSystem): The source CRS of the extent

    Returns:
        QgsRectangle: The transformed extent in EPSG:4326
    """
    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    if source_crs != dest_crs:
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        return transform.transformBoundingBox(extent)
    return extent


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
        self.layer_name = layer_name  # Ensure this is included if needed
        self.size_warning_accepted = False  # Ensure this is False on initialization

    def get_bbox_info_from_metadata(self, conn):
        """Read GeoParquet metadata to find bbox column info"""
        self.progress.emit("Checking for bbox metadata...")
        metadata_query = (
            f"SELECT key, value FROM parquet_kv_metadata('{self.dataset_url}')"
        )
        metadata_results = conn.execute(metadata_query).fetchall()

        for key, value in metadata_results:
            if key == b"geo":
                try:
                    decoded_value = value.decode()
                    logger.log("\nRaw metadata value:")
                    logger.log(decoded_value)

                    # Parse JSON using DuckDB's JSON functions
                    json_query = (
                        f"SELECT json_parse('{decoded_value}'::VARCHAR) as json"
                    )
                    logger.log("\nExecuting JSON query:")
                    logger.log(json_query)

                    geo_metadata = conn.execute(json_query).fetchone()[0]
                    logger.log("\nParsed metadata:")
                    logger.log(geo_metadata)

                    if geo_metadata and "covering" in geo_metadata:
                        logger.log("\nFound covering:")
                        logger.log(geo_metadata["covering"])
                        if "bbox" in geo_metadata["covering"]:
                            bbox_info = geo_metadata["covering"]["bbox"]
                            logger.log("\nExtracted bbox info:")
                            logger.log(bbox_info)
                            return bbox_info
                except Exception as e:
                    logger.log(f"\nError parsing geo metadata: {str(e)}", 2)
                    logger.log(f"Exception type: {type(e)}", 2)
                    import traceback

                    logger.log(traceback.format_exc(), 2)
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
                self.progress.emit(f"Loading spatial extension{layer_info}...")

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

                self.progress.emit(f"Preparing query{layer_info}...")
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
                logger.log("Executing SQL query:")
                logger.log(base_query)
                conn.execute(base_query)
                
                # Add check for empty results
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if row_count == 0:
                    self.info.emit(f"No data found{layer_info} in the requested area. Check that your map extent overlaps with the data and/or expand your map extent. Skipping to next dataset if available.")
                    self.finished.emit()  # Ensure finished signal is emitted
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
                        if estimated_size > 4096 and not self.size_warning_accepted:  # 4GB warning threshold
                            self.file_size_warning.emit(estimated_size)
                            return

                    # Construct the COPY query with Hilbert sorting
                    copy_query = f"""
                    COPY (
                        SELECT * FROM {table_name}
                        ORDER BY ST_Hilbert(
                            geometry,
                            (SELECT ST_Extent(ST_Extent_Agg(COLUMNS(geometry)))::BOX_2D FROM {table_name})
                        )
                    ) TO '{self.output_file}'"""

                    if file_extension == "parquet":
                        format_options = "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 22);"
                    elif self.output_file.endswith(".gpkg"):
                        format_options = "(FORMAT GDAL, DRIVER 'GPKG');"
                    elif self.output_file.endswith(".fgb"):
                        format_options = "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');"
                    elif self.output_file.endswith(".geojson"):
                        format_options = "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');"
                    else:
                        self.error.emit("Unsupported file format.")
                    
                    logger.log("Executing SQL query:")
                    logger.log(copy_query + format_options)
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
                        self.finished.emit()  # Ensure finished signal is emitted
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
                    collection_overhead = (
                        50  # {"type":"FeatureCollection","features":[]}
                    )
                    comma_overhead = row_count - 1  # Commas between features

                    total_estimated_bytes = (
                        (row_count * avg_feature_size)
                        + collection_overhead
                        + comma_overhead
                    )
                    return total_estimated_bytes / (1024 * 1024)  # Convert to MB
            return 0

        except Exception as e:
            logger.log(f"Error estimating file size: {str(e)}", 2)
            return 0

    def process_schema_columns(self, schema_result):
        """Process schema columns and return formatted SELECT clause"""
        columns = []
        for row in schema_result:
            col_name = row[0]
            col_type = row[1]
            quoted_col_name = f'"{col_name}"'

            if "STRUCT" in col_type.upper() or "MAP" in col_type.upper():
                columns.append(f"TO_JSON({quoted_col_name}) AS {quoted_col_name}")
            elif "[]" in col_type:
                columns.append(
                    f"array_to_string({quoted_col_name}, ', ') AS {quoted_col_name}"
                )
            elif col_type.upper() == "UTINYINT":
                columns.append(
                    f"CAST({quoted_col_name} AS INTEGER) AS {quoted_col_name}"
                )
            else:
                columns.append(quoted_col_name)
        return columns


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

        base_path = os.path.dirname(os.path.abspath(__file__))
        presets_path = os.path.join(base_path, "data", "presets.json")
        with open(presets_path, "r") as f:
            self.PRESET_DATASETS = json.load(f)

    def check_bbox_metadata(self, conn):
        """Check for bbox information in GeoParquet metadata"""
        metadata_query = (
            f"SELECT key, value FROM parquet_kv_metadata('{self.dataset_url}')"
        )
        metadata_results = conn.execute(metadata_query).fetchall()

        for key, value in metadata_results:
            if key == b"geo":
                try:
                    decoded_value = value.decode()
                    logger.log("\nRaw metadata value:")
                    logger.log(decoded_value)

                    # Install and load JSON extension
                    conn.execute("INSTALL json;")
                    conn.execute("LOAD json;")

                    # Create a table with the JSON string
                    conn.execute(
                        f"CREATE TEMP TABLE temp_json AS SELECT '{decoded_value}' as json_str"
                    )

                    # Extract the bbox column name using JSON path
                    # First get the geometry column info which contains the covering
                    result = conn.execute("""
                        SELECT json_str->'$.columns.geometry.covering.bbox.xmin[0]' as bbox_column
                        FROM temp_json
                    """).fetchone()

                    logger.log("\nExtracted bbox column name:")
                    logger.log(result[0] if result else None)

                    if result and result[0]:
                        # Remove quotes from the result if present
                        bbox_col = result[0].strip('"')
                        return bbox_col

                except Exception as e:
                    logger.log(f"\nError parsing geo metadata: {str(e)}", 2)
                    logger.log(f"Exception type: {type(e)}", 2)
                    import traceback

                    logger.log(traceback.format_exc())
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

            if not self.needs_validation():
                self.finished.emit(
                    True,
                    "Validation successful",
                    {"has_bbox": True, "bbox_column": "bbox"},
                )
                return

            self.progress.emit("Checking data format...")
            schema_query = f"DESCRIBE SELECT * FROM read_parquet('{self.dataset_url}')"
            schema_result = conn.execute(schema_query).fetchall()

            # Store schema and check for BBOX
            validation_results = {
                "schema": schema_result,
                "has_bbox": False,
                "bbox_column": None,
            }

            # Check for standard bbox column first
            if any(
                row[0].lower() == "bbox" and "struct" in row[1].lower()
                for row in schema_result
            ):
                validation_results["has_bbox"] = True
                validation_results["bbox_column"] = "bbox"
            else:
                # Check metadata for alternative bbox column
                bbox_column = self.check_bbox_metadata(conn)
                if bbox_column:
                    validation_results["has_bbox"] = True
                    validation_results["bbox_column"] = bbox_column

            if not validation_results["has_bbox"]:
                # Emit signal for main thread to show warning
                self.needs_bbox_warning.emit()
                return

            self.finished.emit(True, "Validation successful", validation_results)

        except Exception as e:
            self.finished.emit(False, f"Error validating source: {str(e)}", {})
        finally:
            conn.close()

    def needs_validation(self):
        """Determine if the dataset needs any validation"""
        # Check if URL matches any preset dataset
        for source in self.PRESET_DATASETS.values():
            for dataset in source.values():
                if (
                    isinstance(dataset.get("url"), str)
                    and dataset["url"] in self.dataset_url
                ):
                    return dataset.get("needs_validation", True)
                elif (
                    isinstance(dataset.get("url_template"), str)
                    and dataset["url_template"].split("{")[0] in self.dataset_url
                ):
                    return dataset.get("needs_validation", True)

        # All other datasets need validation
        return True
