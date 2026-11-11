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


# -- info_url (Task C / #84 follow-up) ---------------------------------------
# info_url is checked only after the data checks succeed, and never fails the
# probe on its own - a broken info link is a soft warning (success stays
# True, with a non-empty message), never a reason to refuse the URL.


_DESCRIBE_ROWS = [("id", "INTEGER"), ("geometry", "GEOMETRY")]


class _FakeResponse:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_probe_with_a_reachable_info_url_calls_the_opener_and_stays_clean():
    conn = FakeConnection(_DESCRIBE_ROWS, geo_metadata=None)
    calls = []
    response = _FakeResponse()

    def opener(url, timeout=None):
        calls.append((url, timeout))
        return response

    worker = DatasetProbeWorker(
        "https://example.com/data.parquet",
        "https://example.com/info",
        connection_factory=lambda: conn,
        url_opener=opener,
    )

    success, message, results = _run(worker)

    assert success is True
    assert message == ""
    assert calls == [("https://example.com/info", 10)]
    assert response.closed is True
    assert results == {
        "has_bbox": False,
        "bbox_column": None,
        "geometry_column": "geometry",
    }


def test_probe_with_an_unreachable_info_url_still_succeeds_with_a_soft_warning():
    conn = FakeConnection(_DESCRIBE_ROWS, geo_metadata=None)

    def raising_opener(url, timeout=None):
        raise RuntimeError("404 Not Found")

    worker = DatasetProbeWorker(
        "https://example.com/data.parquet",
        "https://example.com/bad-info",
        connection_factory=lambda: conn,
        url_opener=raising_opener,
    )

    success, message, results = _run(worker)

    assert success is True
    assert "info URL" in message
    assert "404 Not Found" in message
    assert results == {
        "has_bbox": False,
        "bbox_column": None,
        "geometry_column": "geometry",
    }


def test_probe_with_no_info_url_never_calls_the_opener():
    conn = FakeConnection(_DESCRIBE_ROWS, geo_metadata=None)
    calls = []
    worker = DatasetProbeWorker(
        "https://example.com/data.parquet",
        "",
        connection_factory=lambda: conn,
        url_opener=lambda *a, **k: calls.append((a, k)),
    )

    success, message, results = _run(worker)

    assert success is True
    assert message == ""
    assert calls == []


def test_probe_data_failure_never_calls_the_info_url_opener():
    calls = []

    def raising_factory():
        raise RuntimeError("could not connect")

    worker = DatasetProbeWorker(
        "https://example.com/unreachable.parquet",
        "https://example.com/info",
        connection_factory=raising_factory,
        url_opener=lambda *a, **k: calls.append((a, k)),
    )

    success, message, results = _run(worker)

    assert success is False
    assert results == {}
    assert calls == []
