"""DatasetProbeWorker: the dataset-add/edit URL probe (Task B / #84
follow-up), exercised only through connection_factory fakes - a real DuckDB
extension load cannot happen in this project's local test process, so no
test here drives duckdb.connect() for real (see the worktree's test-env
notes and tests/qgis/test_worker_qgis_seams.py, which uses the same seam).
"""

import json

from gpq_downloader.qgis_io.worker import DatasetProbeWorker


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    """Enough of a DuckDB connection for DatasetProbeWorker.run(): answers
    DESCRIBE and parquet_kv_metadata queries, no-ops everything else
    (INSTALL/LOAD, SET ...)."""

    def __init__(self, describe_rows, geo_metadata=None):
        self.describe_rows = describe_rows
        self.geo_metadata = geo_metadata
        self.queries = []

    def execute(self, query, *args, **kwargs):
        self.queries.append(query)
        if "DESCRIBE" in query:
            return FakeResult(self.describe_rows)
        if "parquet_kv_metadata" in query:
            rows = [(self.geo_metadata,)] if self.geo_metadata is not None else []
            return FakeResult(rows)
        return FakeResult([])

    def interrupt(self):
        pass

    def close(self):
        pass


def _run(worker):
    outcomes = []
    worker.finished.connect(lambda *a: outcomes.append(a))
    worker.run()
    assert outcomes, "finished was never emitted"
    return outcomes[0]


def test_probe_success_uses_geoparquet_metadata_for_the_bbox_column():
    geo = json.dumps(
        {
            "primary_column": "geometry",
            "columns": {
                "geometry": {"covering": {"bbox": {"xmin": ["bbox", "xmin"]}}}
            },
        }
    )
    describe_rows = [
        ("id", "INTEGER"),
        ("geometry", "GEOMETRY"),
        ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"),
    ]
    conn = FakeConnection(describe_rows, geo_metadata=geo)
    worker = DatasetProbeWorker(
        "https://example.com/data.parquet", connection_factory=lambda: conn
    )

    success, message, results = _run(worker)

    assert success is True
    assert message == ""
    assert results == {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geometry",
    }


def test_probe_with_no_geoparquet_metadata_falls_back_to_the_describe_schema():
    describe_rows = [("id", "INTEGER"), ("geometry", "GEOMETRY")]
    conn = FakeConnection(describe_rows, geo_metadata=None)
    worker = DatasetProbeWorker(
        "https://example.com/no-bbox.parquet", connection_factory=lambda: conn
    )

    success, message, results = _run(worker)

    assert success is True
    assert results == {
        "has_bbox": False,
        "bbox_column": None,
        "geometry_column": "geometry",
    }


def test_probe_finds_an_unnamed_bbox_struct_without_geoparquet_metadata():
    describe_rows = [
        ("id", "INTEGER"),
        ("geom", "GEOMETRY"),
        ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"),
    ]
    conn = FakeConnection(describe_rows, geo_metadata=None)
    worker = DatasetProbeWorker(
        "https://example.com/plain-bbox.parquet", connection_factory=lambda: conn
    )

    success, message, results = _run(worker)

    assert success is True
    assert results == {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geom",
    }


def test_probe_failure_emits_false_with_the_message_and_no_results():
    def raising_factory():
        raise RuntimeError("could not connect")

    worker = DatasetProbeWorker(
        "https://example.com/unreachable.parquet", connection_factory=raising_factory
    )

    success, message, results = _run(worker)

    assert success is False
    assert "could not connect" in message
    assert results == {}
