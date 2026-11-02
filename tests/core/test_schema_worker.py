"""SchemaWorker: the one DESCRIBE the dialog runs to learn a dataset's columns.

Headless, on a recording connection, like the probe tests in
test_attribute_filter.py. The worker's place in the dialog's thread lifetime
is in tests/qgis/test_dialog_filter_columns.py.
"""

import pytest

from core_support import RecordingConnection
from gpq_downloader.qgis_io import worker as worker_module
from gpq_downloader.qgis_io.worker import SchemaWorker

URL = "https://example.com/data/places.parquet"
SCHEMA_ROWS = [
    ("id", "BIGINT", "YES", None, None, None),
    ("class", "VARCHAR", "YES", None, None, None),
    ("geometry", "GEOMETRY", "YES", None, None, None),
]
SETUP = ["INSTALL spatial;", "LOAD spatial;", "INSTALL httpfs;", "LOAD httpfs;"]
DESCRIBE = f"DESCRIBE SELECT * FROM read_parquet('{URL}')"


def run_worker(monkeypatch, resolve_url, responder=None, before_run=None):
    """Run a SchemaWorker on a recording connection; returns (conn, signals)."""
    connections = []

    def responses(sql):
        if responder is not None:
            return responder(sql)
        return SCHEMA_ROWS if sql.startswith("DESCRIBE") else []

    def connect(*args):
        conn = RecordingConnection(responses)
        connections.append(conn)
        return conn

    monkeypatch.setattr(worker_module.duckdb, "connect", connect)
    worker = SchemaWorker(resolve_url)
    signals = []
    worker.schema_ready.connect(lambda schema: signals.append(("schema", list(schema))))
    worker.failed.connect(lambda message: signals.append(("failed", message)))
    if before_run is not None:
        before_run(worker)
    worker.run()
    return (connections[0] if connections else None), signals


def test_it_describes_the_url_and_reports_name_and_type(monkeypatch):
    conn, signals = run_worker(monkeypatch, lambda: URL)
    assert conn.statements == SETUP + [DESCRIBE]
    assert signals == [
        ("schema", [("id", "BIGINT"), ("class", "VARCHAR"), ("geometry", "GEOMETRY")])
    ]
    assert conn.closed


def test_the_url_is_resolved_on_the_worker_thread(monkeypatch):
    """The dialog hands over a callable, not a URL: an Overture URL needs the
    release, and looking that up blocks."""
    calls = []

    def resolve():
        calls.append("resolved")
        return URL

    conn, signals = run_worker(monkeypatch, resolve)
    assert calls == ["resolved"]
    assert signals[0][0] == "schema"


@pytest.mark.parametrize("url", [None, ""])
def test_no_url_is_no_work_and_no_report(monkeypatch, url):
    conn, signals = run_worker(monkeypatch, lambda: url)
    assert conn is None and signals == []


def test_a_source_it_cannot_read_is_reported_not_raised(monkeypatch):
    def responder(sql):
        if sql.startswith("DESCRIBE"):
            raise RuntimeError("IO Error: No files found that match the pattern")
        return []

    conn, signals = run_worker(monkeypatch, lambda: URL, responder)
    assert signals == [("failed", "IO Error: No files found that match the pattern")]
    assert conn.closed


def test_a_url_it_cannot_resolve_is_reported_not_raised(monkeypatch):
    def resolve():
        raise RuntimeError("Could not look up the latest Overture release")

    conn, signals = run_worker(monkeypatch, resolve)
    assert signals == [("failed", "Could not look up the latest Overture release")]


def test_a_killed_worker_reports_nothing(monkeypatch):
    conn, signals = run_worker(monkeypatch, lambda: URL, before_run=lambda w: w.kill())
    assert signals == []
    assert conn is None  # killed before it even connected


def test_killing_a_running_worker_interrupts_duckdb_and_stays_quiet(monkeypatch):
    killed = {}

    def responder(sql):
        if sql.startswith("DESCRIBE"):
            killed["worker"].kill()
            raise RuntimeError("INTERRUPT Error: Interrupted!")
        return []

    conn, signals = run_worker(
        monkeypatch, lambda: URL, responder, before_run=lambda w: killed.update(worker=w)
    )
    assert signals == []
    assert conn.interrupts == 1
    assert conn.closed


def test_the_filter_fragment_is_never_part_of_the_describe(monkeypatch):
    """The schema fetch runs while the user is still typing: it must not take
    a half-written condition anywhere near DuckDB."""
    conn, _ = run_worker(monkeypatch, lambda: URL)
    assert not any("WHERE" in str(statement) for statement in conn.statements)
