import pytest
from unittest.mock import MagicMock, patch
import os
from qgis.PyQt.QtCore import QObject

from gpq_downloader.utils import Worker

class MockResult:
    def __init__(self, data):
        self.data = data
    
    def fetchall(self):
        return self.data
    
    def fetchone(self):
        return self.data[0] if self.data else None

class MockConnection:
    def __init__(self, schema_data=None, count_result=1):
        self.schema_data = schema_data or []
        self.count_result = count_result
        self.executed_queries = []
    
    def execute(self, query):
        self.executed_queries.append(query)
        if "DESCRIBE" in query:
            return MockResult(self.schema_data)
        elif "COUNT" in query:
            return MockResult([(self.count_result,)])
        return MockResult([])
    
    def commit(self):
        pass
    
    def close(self):
        pass

@pytest.fixture
def schema_with_bbox():
    return [
        ("id", "INTEGER", "YES", None, None, None),
        ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", "YES", None, None, None),
        ("geometry", "GEOMETRY", "YES", None, None, None)
    ]

@pytest.fixture
def schema_without_bbox():
    return [
        ("id", "INTEGER", "YES", None, None, None),
        ("geometry", "GEOMETRY", "YES", None, None, None)
    ]

@patch("duckdb.connect")
def test_worker_run_with_bbox(mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results, schema_with_bbox):
    """Test Worker run method with a bbox column"""
    # Setup
    mock_conn = MockConnection(schema_data=schema_with_bbox)
    mock_connect.return_value = mock_conn
    
    # Create signals for testing
    progress_messages = []
    
    # Create worker
    worker = Worker(
        "https://example.com/test.parquet", 
        sample_bbox, 
        os.path.join(tmp_path, "output.gpkg"), 
        mock_iface, 
        sample_validation_results
    )
    
    # Connect to our test slots
    worker.progress.connect(lambda msg: progress_messages.append(msg))
    
    # Run the worker
    worker.run()
    
    # Check queries
    bbox_query_found = False
    for query in mock_conn.executed_queries:
        if '"bbox".xmin BETWEEN' in query:
            bbox_query_found = True
    
    assert bbox_query_found, "Should use bbox in the query"
    assert any("Downloading" in msg for msg in progress_messages)

@patch("duckdb.connect")
def test_worker_run_without_bbox(mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results_no_bbox, schema_without_bbox):
    """Test Worker run method without a bbox column"""
    # Setup
    mock_conn = MockConnection(schema_data=schema_without_bbox)
    mock_connect.return_value = mock_conn
    
    # Create signals for testing
    progress_messages = []
    
    # Create worker with no bbox
    worker = Worker(
        "https://example.com/test.parquet", 
        sample_bbox, 
        os.path.join(tmp_path, "output.gpkg"), 
        mock_iface, 
        sample_validation_results_no_bbox
    )
    
    # Connect to our test slots
    worker.progress.connect(lambda msg: progress_messages.append(msg))
    
    # Run the worker
    worker.run()
    
    # Check queries
    st_intersects_found = False
    for query in mock_conn.executed_queries:
        if 'ST_Intersects' in query:
            st_intersects_found = True
    
    assert st_intersects_found, "Should use ST_Intersects in the query when no bbox column"
    assert any("Downloading" in msg for msg in progress_messages) 