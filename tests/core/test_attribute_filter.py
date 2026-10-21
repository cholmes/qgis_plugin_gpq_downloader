"""The attribute filter (#16) against DuckDB: the dialog's probe in
ValidationWorker, and downloads whose filter real DuckDB evaluates.

The probe tests answer on a recording connection, like the golden-SQL tests.
The real runs read tests/qgis/data/geom_bbox_named.parquet (see the note in
tests/qgis/test_bbox_and_columns.py). Neither the headless job nor a sandbox
can be assumed to have DuckDB's spatial extension, so NoSpatialConnection
answers the extension setup itself and writes the result without the Hilbert
sort; the statement that reads the file, with the bbox and attribute filters
in it, is run by DuckDB as the Worker built it.
"""

import os

import duckdb
import pytest

from core_support import FakeIface, RecordingConnection, squash
from gpq_downloader.core.filters import validate_where_fragment
from gpq_downloader.core.sql import sql_str
from gpq_downloader.qgis_io import worker as worker_module
from gpq_downloader.utils import ValidationWorker, Worker
from worker_cases import CUSTOM_URL, SOURCES, WINDOW

# The probe tests patch duckdb.connect itself
_duckdb_connect = duckdb.connect

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(REPO_ROOT, "tests", "qgis", "data", "geom_bbox_named.parquet")
IN_WINDOW = {"contains_window", "inside", "crosses_west_edge"}

VALIDATION_SETUP = ["INSTALL spatial;", "LOAD spatial;", "INSTALL httpfs;", "LOAD httpfs;"]
OVERTURE_URL = SOURCES["overture"].url


def probe(url, fragment):
    return f"SELECT 1 FROM read_parquet({sql_str(url)}) WHERE ({fragment}) LIMIT 0"


def run_validation(monkeypatch, url, fragment, responder, before_run=None):
    """Run a ValidationWorker on a recording connection.

    Returns (worker, connection, signals): signals is every emission in order.
    """
    connections = []

    def connect(*args):
        conn = RecordingConnection(responder)
        connections.append(conn)
        return conn

    monkeypatch.setattr(worker_module.duckdb, "connect", connect)
    worker = ValidationWorker(url, FakeIface(), WINDOW, where_fragment=fragment)
    signals = []
    worker.finished.connect(lambda ok, msg, results: signals.append(("finished", ok, msg, dict(results))))
    worker.needs_bbox_warning.connect(lambda: signals.append(("needs_bbox_warning",)))
    worker.progress.connect(lambda msg: signals.append(("progress", msg)))
    if before_run:
        before_run(worker)
    worker.run()
    return worker, connections[0], signals


def custom_responder(fail_probe=None):
    source = SOURCES["custom_https"]

    def respond(sql):
        if sql.startswith("SELECT 1 FROM read_parquet("):
            if fail_probe:
                raise fail_probe
            return []
        return source.respond(sql)

    return respond


CUSTOM_RESULTS = {"has_bbox": True, "bbox_column": "geom_bbox", "geometry_column": "geom", "crs": None}


def test_no_filter_issues_no_probe(monkeypatch):
    _, conn, signals = run_validation(monkeypatch, CUSTOM_URL, None, custom_responder())
    assert not any(s.startswith("SELECT 1") for s in conn.statements)
    assert signals[-1] == ("finished", True, "Validation successful", CUSTOM_RESULTS)


def test_the_filter_is_probed_after_the_existing_checks(monkeypatch):
    _, conn, signals = run_validation(monkeypatch, CUSTOM_URL, "level >= 2", custom_responder())
    assert [squash(s) for s in conn.statements] == VALIDATION_SETUP + [
        f"DESCRIBE SELECT * FROM read_parquet('{CUSTOM_URL}')",
        f"SELECT value FROM parquet_kv_metadata('{CUSTOM_URL}') WHERE key = 'geo'::BLOB LIMIT 1",
        probe(CUSTOM_URL, "level >= 2"),
    ]
    assert signals == [
        ("progress", "Connecting to data source..."),
        ("progress", "Checking data format..."),
        ("progress", "Checking the attribute filter..."),
        ("finished", True, "Validation successful", CUSTOM_RESULTS),
    ]
    assert conn.closed


def test_a_filter_duckdb_rejects_fails_validation_with_its_message(monkeypatch):
    error = duckdb.BinderException('Binder Error: Referenced column "confidence" not found in FROM clause!')
    _, conn, signals = run_validation(monkeypatch, CUSTOM_URL, "confidence > 0.9", custom_responder(error))
    assert signals[-1] == (
        "finished", False,
        "The attribute filter does not work on this data:\n\n"
        'Binder Error: Referenced column "confidence" not found in FROM clause!',
        CUSTOM_RESULTS,
    )
    assert conn.closed


def test_a_rejected_filter_comes_before_the_bbox_warning(monkeypatch):
    """The warning's Yes accepts the dialog, so it must not be shown for a
    source whose filter fails: only the failure is emitted."""
    source = SOURCES["minio"]  # no covering: would warn
    url = "https://example.com/no_bbox.parquet"

    def respond(sql):
        if sql.startswith("SELECT 1 FROM read_parquet("):
            raise duckdb.ParserException("Parser Error: syntax error at or near \"=\"")
        return source.respond(sql)

    _, _, signals = run_validation(monkeypatch, url, "= 1", respond)
    assert ("needs_bbox_warning",) not in signals
    assert signals[-1][:3] == (
        "finished", False, "The attribute filter does not work on this data:\n\nParser Error: syntax error at or near \"=\"",
    )


def test_the_filter_is_probed_for_a_url_that_skips_validation(monkeypatch):
    """A preset URL skips the schema checks, but the filter is still tried."""
    def respond(sql):
        return [] if sql.startswith("SELECT 1") else SOURCES["overture"].respond(sql)

    _, conn, signals = run_validation(monkeypatch, OVERTURE_URL, "confidence > 0.9", respond)
    assert conn.statements == VALIDATION_SETUP + [probe(OVERTURE_URL, "confidence > 0.9")]
    assert signals[-1] == (
        "finished", True, "Validation successful",
        {"has_bbox": True, "bbox_column": "bbox", "geometry_column": "geometry"},
    )


def test_a_cancel_during_the_probe_emits_nothing(monkeypatch):
    holder = {}

    def respond(sql):
        if sql.startswith("SELECT 1 FROM read_parquet("):
            holder["worker"].kill()
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return SOURCES["custom_https"].respond(sql)

    def keep(worker):
        holder["worker"] = worker

    _, conn, signals = run_validation(monkeypatch, CUSTOM_URL, "level >= 2", respond, before_run=keep)
    assert not any(s[0] == "finished" for s in signals)
    assert conn.interrupts == 1 and conn.closed


# --- Real DuckDB -----------------------------------------------------------------

class NoSpatialConnection:
    """A real DuckDB connection, minus what needs the spatial extension.

    The extension setup and the Worker's spatial check are answered here; the
    final COPY (ST_Hilbert, ST_Extent_Agg) becomes a plain parquet COPY of the
    same table. Every other statement goes to DuckDB as issued.
    """

    def __init__(self, *args):
        self._conn = _duckdb_connect(*args)
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        text = squash(sql)
        if text.startswith(("INSTALL ", "LOAD ", "SET geometry_always_xy")):
            return self._conn.execute("SELECT 1")
        if text == "SELECT ST_AsText(ST_GeomFromText('POINT(0 0)'))":
            return self._conn.execute("SELECT 'POINT (0 0)'")
        if text.startswith("COPY ("):
            target = text.split(") TO ", 1)[1].split(" (FORMAT", 1)[0]
            return self._conn.execute(f"COPY download_data TO {target} (FORMAT parquet)")
        return self._conn.execute(sql) if params is None else self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def download(tmp_path, fragment):
    output_file = str(tmp_path / "out.parquet")
    connections = []

    def connect(*args):
        connections.append(NoSpatialConnection(*args))
        return connections[-1]

    worker = Worker(DATA, WINDOW, output_file, FakeIface(), {}, connection_factory=connect,
                    where_fragment=fragment)
    errors, infos = [], []
    worker.error.connect(errors.append)
    worker.info.connect(infos.append)
    worker.run()
    assert not errors, errors
    if infos:  # "No data found"
        return set(), connections[0]
    names = duckdb.connect().execute(
        f"SELECT name FROM read_parquet({sql_str(output_file)})"
    ).fetchall()
    return {row[0] for row in names}, connections[0]


@pytest.mark.parametrize("fragment, expected", [
    (None, IN_WINDOW),
    ("   ", IN_WINDOW),
    # the spatial filter already drops "outside"
    ("name <> 'outside'", IN_WINDOW),
    ("name <> 'inside'", {"contains_window", "crosses_west_edge"}),
    ("name LIKE '%window%' OR name = 'inside'", {"contains_window", "inside"}),
    # an attribute of the covering itself
    ("geom_bbox.xmin >= 9.0", {"inside", "crosses_west_edge"}),
    # an OR cannot reach past the spatial filter: "outside" stays out
    ("name = 'outside' OR name = 'inside'", {"inside"}),
    ("name = 'nothing'", set()),
])
def test_real_duckdb_downloads_the_filtered_subset(tmp_path, fragment, expected):
    names, conn = download(tmp_path, fragment)
    assert names == expected
    create = next(squash(s) for s in conn.statements if squash(s).startswith("CREATE TABLE download_data AS"))
    if fragment and fragment.strip():
        assert create.endswith(f") AND ({fragment}) )")
    else:
        assert "AND (" not in create


def test_real_duckdb_rejects_a_missing_column_in_the_probe(monkeypatch):
    """The probe's error is DuckDB's own, naming the column."""
    def connect(*args):
        return NoSpatialConnection(*args)

    monkeypatch.setattr(worker_module.duckdb, "connect", connect)
    results = []
    worker = ValidationWorker(DATA, FakeIface(), WINDOW, where_fragment="confidence > 0.9")
    worker.finished.connect(lambda ok, msg, r: results.append((ok, msg)))
    worker.run()
    ok, msg = results[-1]
    assert not ok
    assert msg.startswith("The attribute filter does not work on this data:\n\nBinder Error")
    assert '"confidence"' in msg


def test_real_duckdb_accepts_a_good_filter_in_the_probe(monkeypatch):
    monkeypatch.setattr(worker_module.duckdb, "connect", NoSpatialConnection)
    results = []
    worker = ValidationWorker(DATA, FakeIface(), WINDOW, where_fragment="name = 'inside'")
    worker.finished.connect(lambda ok, msg, r: results.append((ok, msg, r["bbox_column"])))
    worker.run()
    assert results == [(True, "Validation successful", "geom_bbox")]


# --- The Worker and ValidationWorker check the fragment themselves ------------

@pytest.mark.parametrize("fragment", ["a = 1; DROP TABLE x", "a = 1 -- rest", "(a = 1"])
def test_the_worker_refuses_a_malformed_filter_before_connecting(fragment):
    connections = []
    worker = Worker(CUSTOM_URL, WINDOW, "/nonexistent/gpq/out.parquet", FakeIface(), {},
                    connection_factory=lambda *a: connections.append(a), where_fragment=fragment)
    errors, finished = [], []
    worker.error.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert errors == [validate_where_fragment(fragment)] and errors[0]
    assert connections == [] and finished == []


def test_the_validation_worker_refuses_a_malformed_filter_without_probing(monkeypatch):
    _, conn, signals = run_validation(monkeypatch, CUSTOM_URL, "a = 1; DROP TABLE x", custom_responder())
    assert not any(s.startswith("SELECT 1") for s in conn.statements)
    assert signals[-1] == ("finished", False, validate_where_fragment("a = 1; DROP TABLE x"), CUSTOM_RESULTS)
