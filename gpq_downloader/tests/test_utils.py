import pytest
import os
from qgis.core import QgsRectangle, QgsCoordinateReferenceSystem
from unittest.mock import patch

from gpq_downloader.utils import transform_bbox_to_4326, Worker, ValidationWorker

def test_transform_bbox_to_4326(qgs_app):
    """Test transforming a bounding box to EPSG:4326"""
    # Create test bbox in EPSG:3857
    source_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    input_bbox = QgsRectangle(1000000, 2000000, 1010000, 2010000)
    
    # Transform
    result_bbox = transform_bbox_to_4326(input_bbox, source_crs)
    
    # Check result is in 4326
    assert isinstance(result_bbox, QgsRectangle)
    assert result_bbox.xMinimum() != input_bbox.xMinimum()  # Values should change after transform
    
    # Test when already in 4326 (no transformation needed)
    already_4326 = QgsRectangle(1, 2, 3, 4)
    result = transform_bbox_to_4326(already_4326, QgsCoordinateReferenceSystem("EPSG:4326"))
    assert result.xMinimum() == already_4326.xMinimum()

def test_worker_initialization(mock_iface, sample_bbox, tmp_path, sample_validation_results):
    """Test Worker initialization"""
    # Create test parameters
    dataset_url = "https://example.com/test.parquet"
    output_file = os.path.join(tmp_path, "output.gpkg")
    
    # Initialize worker
    worker = Worker(dataset_url, sample_bbox, output_file, mock_iface, sample_validation_results)
    
    # Check properties
    assert worker.dataset_url == dataset_url
    assert worker.extent == sample_bbox
    assert worker.output_file == output_file
    assert worker.validation_results == sample_validation_results
    assert worker.killed is False

def test_validation_worker_initialization(mock_iface, sample_bbox):
    """Test ValidationWorker initialization"""
    dataset_url = "https://example.com/test.parquet"
    
    # Initialize validation worker
    worker = ValidationWorker(dataset_url, mock_iface, sample_bbox)
    
    # Check properties
    assert worker.dataset_url == dataset_url
    assert worker.extent == sample_bbox
    assert worker.killed is False

def test_transform_bbox_with_none(qgs_app):
    """Test transform_bbox_to_4326 with None input"""
    result = transform_bbox_to_4326(None, None)
    assert result is None

@patch('duckdb.connect')
def test_worker_error_handling(mock_connect, mock_iface, sample_bbox, tmp_path):
    """Test Worker error handling"""
    mock_connect.side_effect = Exception("Test error")
    
    # Create signals for testing
    error_message = None
    def on_error(msg):
        nonlocal error_message
        error_message = msg
    
    # Create worker
    worker = Worker(
        "https://example.com/test.parquet",
        sample_bbox,
        str(tmp_path / "test.parquet"),
        mock_iface,
        {"has_bbox": True, "bbox_column": "bbox"}
    )
    worker.error.connect(on_error)
    
    # Run worker
    worker.run()
    
    assert error_message is not None
    assert "Test error" in error_message 