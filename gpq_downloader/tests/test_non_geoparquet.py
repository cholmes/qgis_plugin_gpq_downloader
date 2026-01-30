"""Tests for handling non-GeoParquet compliant parquet files with geometry columns."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pyarrow.parquet as pq
import duckdb
import tempfile
import os

from gpq_downloader.utils import Worker, ValidationWorker


class TestNonGeoParquetHandling:
    """Test handling of parquet files with geometry but no geo metadata."""
    
    @pytest.fixture
    def test_data_path(self):
        """Path to test data directory."""
        return Path(__file__).parent / "data"
    
    @pytest.fixture
    def non_geoparquet_file(self, test_data_path):
        """Path to non-GeoParquet test file."""
        return test_data_path / "non_geoparquet_with_geometry.parquet"
    
    def test_non_geoparquet_file_exists(self, non_geoparquet_file):
        """Verify test file exists and has expected structure."""
        assert non_geoparquet_file.exists(), f"Test file not found: {non_geoparquet_file}"
        
        # Verify file structure
        pf = pq.ParquetFile(non_geoparquet_file)
        schema = pf.schema
        
        # Check expected columns
        column_names = [field.name for field in schema]
        assert "geometry" in column_names
        assert "id" in column_names
        assert "name" in column_names
        
        # Verify no geo metadata
        metadata = pf.metadata.metadata
        if metadata:
            metadata_dict = {k.decode(): v.decode() for k, v in metadata.items()}
            assert "geo" not in metadata_dict
    
    @patch('gpq_downloader.utils.transform_bbox_to_4326')
    @patch('gpq_downloader.utils.duckdb.connect')
    def test_worker_handles_non_geoparquet(self, mock_connect, mock_transform_bbox, non_geoparquet_file, tmp_path):
        """Test that Worker can process non-GeoParquet files with geometry."""
        # Mock connection
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        # Mock execute method to handle spatial extension loading
        def mock_execute(query):
            result = MagicMock()
            if "DESCRIBE SELECT" in query:
                # Schema query result
                result.fetchall.return_value = [
                    ('id', 'BIGINT', 'YES', None, None, None),
                    ('name', 'VARCHAR', 'YES', None, None, None), 
                    ('type', 'VARCHAR', 'YES', None, None, None),
                    ('length_m', 'DOUBLE', 'YES', None, None, None),
                    ('geometry', 'BLOB', 'YES', None, None, None)  # Geometry as BLOB, not WKB_BLOB
                ]
            elif "SELECT COUNT(*)" in query:
                # Count query result
                result.fetchone.return_value = (7,)
            else:
                # For other queries (INSTALL, LOAD, CREATE TABLE, etc.)
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result
        
        mock_conn.execute.side_effect = mock_execute
        
        # Mock transform_bbox_to_4326 to return a proper bbox for testing
        from qgis.core import QgsRectangle
        mock_bbox = QgsRectangle(-180, -90, 180, 90)  # Global extent
        mock_transform_bbox.return_value = mock_bbox
        
        # Create worker with temp output file
        output_file = tmp_path / "test_output.parquet"
        
        # Mock iface
        mock_iface = MagicMock()
        mock_iface.mapCanvas.return_value.mapSettings.return_value.destinationCrs.return_value = MagicMock()
        
        # Mock validation results
        validation_results = {
            'has_geometry': True,
            'geometry_type': 'BLOB',
            'total_features': 7
        }
        
        worker = Worker(
            dataset_url=f"file://{non_geoparquet_file}",
            extent=None,
            output_file=str(output_file),
            iface=mock_iface,
            validation_results=validation_results
        )
        
        # Mock signals
        worker.progress = MagicMock()
        worker.error = MagicMock()
        worker.finished = MagicMock()
        
        # Run worker
        worker.run()
        
        # Verify spatial extension was loaded
        execute_calls = [call[0][0] for call in mock_conn.execute.call_args_list]
        assert any("INSTALL spatial" in call for call in execute_calls)
        assert any("LOAD spatial" in call for call in execute_calls)
        
        # Verify no errors
        worker.error.emit.assert_not_called()
        
        # Verify finished signal was emitted
        worker.finished.emit.assert_called_once()
    
    @patch('gpq_downloader.utils.transform_bbox_to_4326')
    @patch('gpq_downloader.utils.duckdb.connect')
    def test_non_geoparquet_spatial_query(self, mock_connect, mock_transform_bbox, non_geoparquet_file, tmp_path):
        """Test spatial filtering works without bbox column."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        
        # Track all queries
        queries_executed = []
        
        def mock_execute(query):
            queries_executed.append(query)
            result = MagicMock()
            if "DESCRIBE SELECT" in query:
                result.fetchall.return_value = [
                    ('geometry', 'BLOB', 'YES', None, None, None), 
                    ('id', 'BIGINT', 'YES', None, None, None), 
                    ('name', 'VARCHAR', 'YES', None, None, None)
                ]
            elif "SELECT COUNT(*)" in query:
                result.fetchone.return_value = (5,)
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result
        
        mock_conn.execute.side_effect = mock_execute
        
        # Mock transform_bbox_to_4326 to return the extent
        from qgis.core import QgsRectangle
        mock_bbox = QgsRectangle(-122.5, 37.7, -122.4, 37.8)
        mock_transform_bbox.return_value = mock_bbox
        
        # Create worker with bbox filter
        output_file = tmp_path / "test_output.parquet"
        
        # Mock iface
        mock_iface = MagicMock()
        mock_iface.mapCanvas.return_value.mapSettings.return_value.destinationCrs.return_value = MagicMock()
        
        # Create extent for bbox filter
        from qgis.core import QgsRectangle
        extent = QgsRectangle(-122.5, 37.7, -122.4, 37.8)  # SF area
        
        # Mock validation results
        validation_results = {
            'has_geometry': True,
            'geometry_type': 'BLOB',
            'total_features': 5
        }
        
        worker = Worker(
            dataset_url=f"file://{non_geoparquet_file}",
            extent=extent,
            output_file=str(output_file),
            iface=mock_iface,
            validation_results=validation_results
        )
        
        # Mock signals
        worker.progress = MagicMock()
        worker.error = MagicMock()
        worker.finished = MagicMock()
        
        # Run worker
        worker.run()
        
        # For BLOB geometry columns, spatial filtering happens after conversion
        # So we should see the conversion happening in a separate step
        conversion_query = any(
            "ST_GeomFromWKB" in query and "CREATE TABLE" in query
            for query in queries_executed
        )
        assert conversion_query, f"Expected geometry conversion for BLOB column. Queries: {queries_executed}"
    
    def test_duckdb_reads_non_geoparquet(self, non_geoparquet_file):
        """Test that DuckDB can actually read the non-GeoParquet file with spatial extension."""
        conn = duckdb.connect()
        
        # Load spatial extension
        conn.execute("INSTALL spatial;")
        conn.execute("LOAD spatial;")
        
        # Read the file
        query = f"SELECT * FROM read_parquet('{non_geoparquet_file}')"
        result = conn.execute(query).fetchall()
        
        # Should have 7 rows
        assert len(result) == 7
        
        # Test geometry column can be converted from WKB
        geom_query = f"""
        SELECT 
            id, 
            name,
            ST_AsText(ST_GeomFromWKB(geometry)) as geom_wkt
        FROM read_parquet('{non_geoparquet_file}')
        LIMIT 1
        """
        geom_result = conn.execute(geom_query).fetchone()
        
        assert geom_result is not None
        assert geom_result[0] == 1  # id
        assert geom_result[1] == 'Market St'  # name
        assert 'LINESTRING' in geom_result[2]  # geometry as WKT
        
        conn.close()
    
    @pytest.mark.integration
    @pytest.mark.skipif(
        os.environ.get('SKIP_INTEGRATION_TESTS', 'false').lower() == 'true',
        reason="Skipping integration tests"
    )
    @patch('gpq_downloader.utils.transform_bbox_to_4326')
    def test_end_to_end_remote_non_geoparquet(self, mock_transform_bbox):
        """End-to-end test downloading and processing remote non-geoparquet file."""
        dataset_url = "https://data.source.coop/cholmes/aois/non_geoparquet_with_geometry.parquet"
        
        # Create a temporary directory for output
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = os.path.join(temp_dir, "test_output.parquet")
            
            # Mock iface
            mock_iface = MagicMock()
            mock_canvas = MagicMock()
            mock_settings = MagicMock()
            mock_crs = MagicMock()
            
            # Setup the chain of mocks
            mock_iface.mapCanvas.return_value = mock_canvas
            mock_canvas.mapSettings.return_value = mock_settings
            mock_settings.destinationCrs.return_value = mock_crs
            mock_crs.authid.return_value = "EPSG:4326"
            
            # Create extent for filtering (San Francisco area)
            from qgis.core import QgsRectangle
            extent = QgsRectangle(-122.5, 37.7, -122.4, 37.8)
            
            # Mock transform_bbox_to_4326 to return the same extent (already in 4326)
            mock_transform_bbox.return_value = extent
            
            # Run validation manually to get results
            # Since we can't easily test the actual ValidationWorker with signals,
            # we'll validate using duckdb directly
            conn = duckdb.connect()
            conn.execute("INSTALL spatial;")
            conn.execute("LOAD spatial;")
            
            # Get schema
            schema_query = f"DESCRIBE SELECT * FROM read_parquet('{dataset_url}')"
            schema = conn.execute(schema_query).fetchall()
            
            # Check for geometry column
            has_geometry = False
            geometry_column = None
            for col_name, col_type, _, _, _, _ in schema:
                if col_name == 'geometry' or 'geom' in col_name.lower():
                    has_geometry = True
                    geometry_column = col_name
                    break
            
            # Check for bbox metadata
            has_bbox = False
            bbox_column = None
            try:
                metadata_query = f"SELECT key, value FROM parquet_kv_metadata('{dataset_url}')"
                metadata_results = conn.execute(metadata_query).fetchall()
                for key, value in metadata_results:
                    if key == b"geo":
                        has_bbox = True  # Would need more parsing to get actual bbox column
                        break
            except:
                pass
            
            conn.close()
            
            # Create validation results based on our checks
            validation_results = {
                'has_geometry': has_geometry,
                'geometry_column': geometry_column,
                'has_bbox': has_bbox,
                'bbox_column': bbox_column,
                'schema': schema
            }
            
            # Now run the worker with validation results
            worker = Worker(
                dataset_url=dataset_url,
                extent=extent,
                output_file=output_file,
                iface=mock_iface,
                validation_results=validation_results
            )
            
            # Mock signals for worker
            worker.finished = MagicMock()
            worker.error = MagicMock()
            worker.progress = MagicMock()
            worker.percent = MagicMock()
            worker.info = MagicMock()
            worker.load_layer = MagicMock()
            worker.file_size_warning = MagicMock()
            
            # Run worker
            worker.run()
            
            # The worker should succeed or at least handle the spatial extension issue gracefully
            # For now, let's check if it tried to load the spatial extension
            if worker.error.emit.called:
                error_message = worker.error.emit.call_args[0][0]
                # This is actually revealing a bug - the spatial extension isn't being loaded properly
                print(f"Worker encountered error: {error_message}")
                # The test should now pass without this error
                assert False, f"Worker should not encounter spatial extension error: {error_message}"
            
            # Check finished signal was emitted
            worker.finished.emit.assert_called_once()
            
            # Verify output file was created
            assert os.path.exists(output_file)
            
            # Verify the output is valid GeoParquet
            conn = duckdb.connect()
            conn.execute("INSTALL spatial;")
            conn.execute("LOAD spatial;")
            
            # Check we can read the output file
            result = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{output_file}')").fetchone()
            assert result[0] > 0  # Should have filtered some features
            
            # Check geometry column exists and is valid
            schema_result = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{output_file}')").fetchall()
            column_names = [row[0] for row in schema_result]
            assert 'geometry' in column_names
            
            # Check we can read geometry
            # First check what type the geometry column is
            geom_col_type = None
            for row in schema_result:
                if row[0] == 'geometry':
                    geom_col_type = row[1]
                    break
            
            # If it's already GEOMETRY type, don't use ST_GeomFromWKB
            if geom_col_type and 'GEOMETRY' in geom_col_type.upper():
                geom_result = conn.execute(f"""
                    SELECT ST_AsText(geometry) as wkt
                    FROM read_parquet('{output_file}')
                    LIMIT 1
                """).fetchone()
            else:
                # It's still BLOB, so convert it
                geom_result = conn.execute(f"""
                    SELECT ST_AsText(ST_GeomFromWKB(geometry)) as wkt
                    FROM read_parquet('{output_file}')
                    LIMIT 1
                """).fetchone()
            
            assert geom_result is not None
            assert 'LINESTRING' in geom_result[0] or 'POINT' in geom_result[0] or 'POLYGON' in geom_result[0]
            
            conn.close()