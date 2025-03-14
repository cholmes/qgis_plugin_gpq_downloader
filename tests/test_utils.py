import pytest
from unittest.mock import MagicMock, patch
import os
from qgis.core import QgsRectangle, QgsCoordinateReferenceSystem
from pathlib import Path

from gpq_downloader.utils import (
    transform_bbox_to_4326, 
    Worker, 
    ValidationWorker
)

# Add new test for file size estimation
def test_estimate_file_size(mock_iface, sample_bbox, tmp_path):
    """Test file size estimation for GeoJSON output"""
    # Create mock connection and cursor
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.side_effect = [
        (1000,),  # row count
        (2000.0,)  # avg feature size
    ]
    
    # Create worker
    worker = Worker(
        "https://example.com/test.parquet",
        sample_bbox,
        str(tmp_path / "test.geojson"),
        mock_iface,
        {"has_bbox": True, "bbox_column": "bbox"}
    )
    
    # Test size estimation
    estimated_size = worker.estimate_file_size(mock_conn, "test_table")
    assert estimated_size > 0
    assert isinstance(estimated_size, float)

# Add test for process_schema_columns
def test_process_schema_columns():
    """Test schema column processing for different data types"""
    # Create worker
    worker = Worker(
        "https://example.com/test.parquet",
        QgsRectangle(0, 0, 1, 1),
        "test.parquet",
        MagicMock(),
        {"has_bbox": True}
    )
    
    # Test different column types
    schema_result = [
        ("id", "INTEGER", "YES", None, None, None),
        ("tags", "MAP(VARCHAR, VARCHAR)", "YES", None, None, None),
        ("names", "STRUCT(primary VARCHAR)", "YES", None, None, None),
        ("categories", "VARCHAR[]", "YES", None, None, None),
        ("small_num", "UTINYINT", "YES", None, None, None),
        ("geometry", "GEOMETRY", "YES", None, None, None)
    ]
    
    columns = worker.process_schema_columns(schema_result)
    
    assert len(columns) == 6
    assert 'TO_JSON("tags")' in columns[1]
    assert 'TO_JSON("names")' in columns[2]
    assert 'array_to_string("categories"' in columns[3]
    assert 'CAST("small_num" AS INTEGER)' in columns[4]

# Add test for ValidationWorker metadata parsing
@patch('duckdb.connect')
def test_validation_worker_metadata_parsing(mock_connect, mock_iface):
    """Test GeoParquet metadata parsing in ValidationWorker"""
    # Mock connection with metadata
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        (b"geo", b'{"columns":{"geometry":{"encoding":"WKB","geometry_types":["Point"],"covering":{"bbox":{"xmin":[0],"ymin":[0],"xmax":[1],"ymax":[1]}}}}}')
    ]
    mock_connect.return_value = mock_conn
    
    worker = ValidationWorker(
        "https://example.com/test.parquet",
        mock_iface,
        QgsRectangle(0, 0, 1, 1)
    )
    
    # Test metadata parsing
    bbox_column = worker.check_bbox_metadata(mock_conn)
    assert bbox_column is not None

# Add test for needs_validation method
def test_validation_worker_needs_validation():
    """Test needs_validation logic for different URLs"""
    worker = ValidationWorker(
        "https://example.com/test.parquet",
        MagicMock(),
        QgsRectangle(0, 0, 1, 1)
    )
    
    # Test custom URL
    assert worker.needs_validation() == True
    
    # Test Overture URL
    worker.dataset_url = "s3://overturemaps-us-west-2/release/2024/theme=buildings"
    assert worker.needs_validation() == True
    
    # Test Source Cooperative URL with validation flag
    worker.PRESET_DATASETS = {
        "source_cooperative": {
            "test_dataset": {
                "url": "https://example.com/test.parquet",
                "needs_validation": False
            }
        }
    }
    worker.dataset_url = "https://example.com/test.parquet"
    assert worker.needs_validation() == False

# Add test for transform_bbox_to_4326 with invalid inputs
def test_transform_bbox_invalid_inputs(qgs_app):
    """Test bbox transformation with invalid inputs"""
    # Test with None extent
    assert transform_bbox_to_4326(None, QgsCoordinateReferenceSystem("EPSG:4326")) is None
    
    # Test with None CRS
    assert transform_bbox_to_4326(QgsRectangle(0, 0, 1, 1), None) is None
    
    # Test with invalid CRS
    invalid_crs = QgsCoordinateReferenceSystem()
    assert not invalid_crs.isValid()
    result = transform_bbox_to_4326(QgsRectangle(0, 0, 1, 1), invalid_crs)
    assert isinstance(result, QgsRectangle)

# Add test for Worker initialization with layer name
def test_worker_initialization_with_layer_name(mock_iface, sample_bbox, tmp_path):
    """Test Worker initialization with optional layer name"""
    worker = Worker(
        "https://example.com/test.parquet",
        sample_bbox,
        str(tmp_path / "test.parquet"),
        mock_iface,
        {"has_bbox": True},
        layer_name="Test Layer"
    )
    
    assert worker.layer_name == "Test Layer"
    assert not worker.size_warning_accepted
    assert not worker.killed