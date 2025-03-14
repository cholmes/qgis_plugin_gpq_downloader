import pytest
from unittest.mock import MagicMock, patch
import json
import os

from gpq_downloader.utils import ValidationWorker

@patch("duckdb.connect")
def test_validation_worker_with_bbox(mock_connect, mock_iface, sample_bbox):
    """Test the validation worker with a dataset that has a bbox column"""
    # Setup mock connection
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        ("id", "INTEGER", "YES", None, None, None),
        ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", "YES", None, None, None),
        ("geometry", "GEOMETRY", "YES", None, None, None)
    ]
    mock_connect.return_value = mock_conn
    
    # Setup validation signals
    finished_signal_received = False
    validation_results = None
    
    def on_finished(success, message, results):
        nonlocal finished_signal_received, validation_results
        finished_signal_received = True
        validation_results = results
    
    # Create worker
    worker = ValidationWorker("https://example.com/test.parquet", mock_iface, sample_bbox)
    worker.finished.connect(on_finished)
    
    # Mock presets.json to return empty dict
    with patch.object(worker, 'PRESET_DATASETS', {}):
        worker.run()
    
    # Check results
    assert finished_signal_received
    assert validation_results["has_bbox"] is True
    assert validation_results["bbox_column"] == "bbox"

@patch("duckdb.connect")
def test_validation_worker_without_bbox(mock_connect, mock_iface, sample_bbox):
    """Test the validation worker with a dataset that has no bbox column"""
    # Setup mock connection
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [
        ("id", "INTEGER", "YES", None, None, None),
        ("geometry", "GEOMETRY", "YES", None, None, None)
    ]
    mock_connect.return_value = mock_conn
    
    # Setup validation signals
    warning_signal_received = False
    finished_signal_received = False
    validation_results = None
    
    def on_finished(success, message, results):
        nonlocal finished_signal_received, validation_results
        finished_signal_received = True
        validation_results = results
        print(f"Received validation results: {results}")  # Add debug print
    
    def on_warning():
        nonlocal warning_signal_received
        warning_signal_received = True
        print("Warning signal received")  # Add debug print
    
    # Create worker
    worker = ValidationWorker("https://example.com/test.parquet", mock_iface, sample_bbox)
    worker.finished.connect(on_finished)
    worker.needs_bbox_warning.connect(on_warning)
    
    # Mock presets.json to return empty dict
    with patch.object(worker, 'PRESET_DATASETS', {}):
        worker.run()
    
    # Check results
    assert finished_signal_received, "Finished signal was not emitted"
    assert validation_results is not None, "No validation results received"
    assert "has_bbox" in validation_results, f"has_bbox not in validation_results: {validation_results}"
    assert validation_results["has_bbox"] is False
    assert validation_results["bbox_column"] is None
    assert warning_signal_received, "Warning signal was not emitted" 