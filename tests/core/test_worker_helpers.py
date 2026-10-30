"""Characterization of the Worker's table helpers and of cancellation.

clip_table(), reproject_table() and estimate_file_size() take the connection
as an argument, so their exact statements are pinned on a recording
connection. reproject_table() itself needs no QGIS (it is handed the CRS as
text); deciding whether to call it does, and that is pinned in
tests/qgis/test_worker_qgis_seams.py.
"""

import os

import duckdb
import pytest

from core_support import FakeIface, RecordingConnection, Rect, squash
from gpq_downloader.utils import CancellableConnection, Worker
from worker_cases import SOURCES, describe, run_worker

AREA_WKT = "POLYGON((10.0 10.0, 11.0 10.0, 11.0 11.0, 10.0 11.0, 10.0 10.0))"
AREA = f"ST_GeomFromText('{AREA_WKT}')"
BBOX_STRUCT = "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"


def make_worker(output_file="/nonexistent/gpq/out.parquet"):
    return Worker("https://example.com/a.parquet", Rect(10, 10, 11, 11), output_file, FakeIface(), {})


def table(*columns):
    """A connection whose DESCRIBE of the table returns `columns`."""
    return RecordingConnection(lambda sql: describe(*columns) if sql.startswith("DESCRIBE") else [])


# --- clip_table ---------------------------------------------------------------

DELETE_OUTSIDE = (
    f'DELETE FROM t WHERE "geom" IS NULL OR NOT ST_Intersects("geom", {AREA}) '
    f'OR (NOT ST_CoveredBy("geom", {AREA}) AND ST_Dimension(ST_Intersection("geom", {AREA})) < ST_Dimension("geom"))'
)
CUT_AT_EDGE = f'UPDATE t SET "geom" = ST_Intersection("geom", {AREA}) WHERE NOT ST_CoveredBy("geom", {AREA})'
NEW_BBOX = 'struct_pack(xmin := ST_XMin("geom"), ymin := ST_YMin("geom"), xmax := ST_XMax("geom"), ymax := ST_YMax("geom"))'


@pytest.mark.parametrize("bbox_column, columns, expected_tail", [
    # No bbox column: delete and cut only
    (None, [("geom", "GEOMETRY")], []),
    # A struct covering is rebuilt as a struct...
    ("geom_bbox", [("geom", "GEOMETRY"), ("geom_bbox", BBOX_STRUCT)],
     ["DESCRIBE t", f'UPDATE t SET "geom_bbox" = {NEW_BBOX}']),
    # ...and one already turned into JSON (any output but GeoParquet) as JSON
    ("geom_bbox", [("geom", "GEOMETRY"), ("geom_bbox", "JSON")],
     ["DESCRIBE t", f'UPDATE t SET "geom_bbox" = to_json({NEW_BBOX})']),
    ("geom_bbox", [("geom", "GEOMETRY"), ("geom_bbox", "VARCHAR")],
     ["DESCRIBE t", f'UPDATE t SET "geom_bbox" = to_json({NEW_BBOX})']),
    # The type is compared upper-cased
    ("geom_bbox", [("geom", "GEOMETRY"), ("geom_bbox", "struct(xmin double)")],
     ["DESCRIBE t", f'UPDATE t SET "geom_bbox" = {NEW_BBOX}']),
    # A bbox column that is not in the table is left alone
    ("bbox", [("geom", "GEOMETRY")], ["DESCRIBE t"]),
])
def test_clip_table(bbox_column, columns, expected_tail):
    conn = table(*columns)
    assert make_worker().clip_table(conn, "t", "geom", bbox_column, AREA_WKT) is None
    assert [squash(s) for s in conn.statements] == [DELETE_OUTSIDE, CUT_AT_EDGE] + expected_tail


def test_clip_table_quotes_names_and_the_area():
    conn = table(('g"x', "GEOMETRY"))
    make_worker().clip_table(conn, "t", 'g"x', None, "POLYGON((0 0, 1 0, 1 1, 0 0))")
    assert conn.statements[1] == (
        "UPDATE t SET \"g\"\"x\" = ST_Intersection(\"g\"\"x\", ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 0))'))"
        " WHERE NOT ST_CoveredBy(\"g\"\"x\", ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 0))'))"
    )


# --- reproject_table ------------------------------------------------------------

TRANSFORMED = "ST_Transform(\"geom\", 'EPSG:2913', 'EPSG:4326', always_xy := true)"


def test_reproject_table_statements():
    conn = table(("name", "VARCHAR"), ("geom", "GEOMETRY"), ("geom_bbox", BBOX_STRUCT))
    assert make_worker().reproject_table(conn, "t", "geom", "geom_bbox", "EPSG:2913") is True
    assert conn.statements == [
        "DESCRIBE t",
        f'CREATE TABLE t_4326 AS SELECT "name", {TRANSFORMED} AS "geom", '
        f"struct_pack(xmin := ST_XMin({TRANSFORMED}), ymin := ST_YMin({TRANSFORMED}), "
        f'xmax := ST_XMax({TRANSFORMED}), ymax := ST_YMax({TRANSFORMED})) AS "geom_bbox" FROM t',
        "DROP TABLE t",
        "ALTER TABLE t_4326 RENAME TO t",
    ]


def test_reproject_table_without_bbox_and_with_projjson():
    projjson = '{"type": "ProjectedCRS", "name": "it\'s odd"}'
    conn = table(("geom", "GEOMETRY"), ("bbox", BBOX_STRUCT))
    assert make_worker().reproject_table(conn, "t", "geom", None, projjson) is True
    # The bbox struct is copied unchanged when it is not named as the covering
    assert conn.statements[1] == (
        "CREATE TABLE t_4326 AS SELECT ST_Transform(\"geom\", "
        "'{\"type\": \"ProjectedCRS\", \"name\": \"it''s odd\"}', 'EPSG:4326', always_xy := true) AS \"geom\", "
        '"bbox" FROM t'
    )


def test_reproject_table_failure_emits_an_error():
    def respond(sql):
        if sql.startswith("CREATE"):
            raise duckdb.InvalidInputException("Invalid Input Error: unknown CRS")
        return describe(("geom", "GEOMETRY"))

    worker = make_worker()
    errors = []
    worker.error.connect(errors.append)
    conn = RecordingConnection(respond)
    assert worker.reproject_table(conn, "t", "geom", None, "EPSG:NOPE") is False
    assert errors == [
        "This data is in EPSG:NOPE and DuckDB could not convert it to EPSG:4326 "
        "(Invalid Input Error: unknown CRS). Reproject the data to EPSG:4326 and try again."
    ]
    assert len(conn.statements) == 2  # no DROP or ALTER


def test_reproject_table_reraises_a_cancel():
    def respond(sql):
        if sql.startswith("CREATE"):
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return describe(("geom", "GEOMETRY"))

    worker = make_worker()
    errors = []
    worker.error.connect(errors.append)
    with pytest.raises(duckdb.InterruptException):
        worker.reproject_table(RecordingConnection(respond), "t", "geom", None, "EPSG:2913")
    assert errors == []


# --- estimate_file_size -----------------------------------------------------------

def estimating(row_count, avg, columns=(("id", "BIGINT"), ("geom", "GEOMETRY"), ("name", "VARCHAR"))):
    def respond(sql):
        if sql.startswith("SELECT COUNT(*)"):
            return [(row_count,)]
        if sql.startswith("DESCRIBE"):
            return describe(*columns)
        return [(avg,)]
    return RecordingConnection(respond)


def test_estimate_file_size_statements_and_formula():
    conn = estimating(1000, 250.0)
    size = make_worker().estimate_file_size(conn, "t", "geom")
    assert size == (1000 * 250.0 + 50 + 999) / (1024 * 1024)
    assert [squash(s) for s in conn.statements] == [
        "SELECT COUNT(*) FROM t",
        "DESCRIBE t",
        "WITH sample AS ( SELECT * FROM t LIMIT 100 ) SELECT AVG(LENGTH( json_object( 'type', 'Feature', "
        "'geometry', ST_AsGeoJSON(\"geom\"), 'properties', json_object('id', COALESCE(CAST(\"id\" AS VARCHAR), "
        "'null'), 'name', COALESCE(CAST(\"name\" AS VARCHAR), 'null')) )::VARCHAR )) as avg_feature_size "
        "FROM sample;",
    ]


def test_estimate_file_size_samples_at_most_the_rows_there_are():
    conn = estimating(7, 100.0)
    make_worker().estimate_file_size(conn, "t", "geom")
    assert "LIMIT 7 )" in squash(conn.statements[2])


@pytest.mark.parametrize("row_count, avg, expected_statements", [
    (0, 100.0, 1),     # an empty table is 0 MB without sampling
    (5, None, 3),      # no average (all NULL): 0
    (5, 0, 3),
])
def test_estimate_file_size_zero(row_count, avg, expected_statements):
    conn = estimating(row_count, avg)
    assert make_worker().estimate_file_size(conn, "t", "geom") == 0
    assert len(conn.statements) == expected_statements


def test_estimate_file_size_error_is_zero():
    def respond(sql):
        raise duckdb.CatalogException("Table with name t does not exist!")

    assert make_worker().estimate_file_size(RecordingConnection(respond), "t", "geom") == 0


def test_estimate_file_size_with_only_a_geometry_column():
    """Oddity: with no other columns the properties are json_object() with no
    arguments."""
    conn = estimating(3, 50.0, columns=(("geom", "GEOMETRY"),))
    make_worker().estimate_file_size(conn, "t", "geom")
    assert "'properties', json_object() )" in squash(conn.statements[2])



# --- Cancellation -----------------------------------------------------------------

def test_cancellable_connection_raises_after_kill():
    worker = make_worker()
    inner = RecordingConnection(lambda sql: [(1,)])
    conn = CancellableConnection(inner, worker)
    assert conn.execute("SELECT 1").fetchone() == (1,)
    assert conn.execute("SELECT ?", [2]).fetchall() == [(1,)]
    worker.kill()
    with pytest.raises(duckdb.InterruptException, match="Cancelled"):
        conn.execute("SELECT 2")
    # The statement never reached the connection
    assert inner.statements == ["SELECT 1", ("SELECT ?", [2])]
    # Everything else is passed through
    conn.close()
    assert inner.closed


def test_cancellable_connection_with_real_duckdb():
    worker = make_worker()
    conn = CancellableConnection(duckdb.connect(), worker)
    try:
        assert conn.execute("SELECT 42").fetchone() == (42,)
        worker.kill()
        with pytest.raises(duckdb.InterruptException):
            conn.execute("SELECT 43")
    finally:
        conn.close()


def test_kill_interrupts_the_running_query():
    worker = make_worker()
    worker.kill()  # before run(): nothing to interrupt
    assert worker.killed
    worker.conn = RecordingConnection()
    worker.kill()
    assert worker.conn.interrupts == 1


def test_kill_tolerates_a_failing_interrupt():
    worker = make_worker()

    class Closed:
        def interrupt(self):
            raise duckdb.ConnectionException("Connection already closed!")

    worker.conn = Closed()
    worker.kill()
    assert worker.killed


def test_kill_mid_run_stops_at_the_next_statement(monkeypatch):
    """A cancel that lands while the download runs: that query finishes (the
    stub does not raise), the next execute() raises, nothing is emitted after
    the progress so far, and the cleanup DROP is refused too."""
    source = SOURCES["custom_https"]
    state = {}

    def respond(sql):
        if squash(sql).startswith("CREATE TABLE download_data AS"):
            state["worker"].kill()
        return source.respond(sql)

    def keep(worker):
        state["worker"] = worker

    worker, conn, signals = run_worker(
        source, "/nonexistent/gpq/out.gpkg", monkeypatch, responder=respond, before_run=keep,
    )
    assert squash(conn.statements[-1]).startswith("CREATE TABLE download_data AS")
    assert conn.interrupts == 1 and conn.closed
    assert [s[0] for s in signals] == ["progress"] * 4
    assert worker.conn is None


def test_kill_during_export_removes_the_partial_files(monkeypatch, tmp_path):
    output_file = str(tmp_path / "out.parquet")
    temp_file = str(tmp_path / "tmp_out.parquet")
    source = SOURCES["custom_https"]
    state = {}

    def respond(sql):
        if squash(sql).startswith("COPY"):
            for path in (output_file, temp_file):
                with open(path, "w") as f:
                    f.write("partial")
            state["worker"].kill()
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return source.respond(sql)

    def keep(worker):
        state["worker"] = worker

    _, conn, signals = run_worker(source, output_file, monkeypatch, responder=respond, before_run=keep)
    assert not os.path.exists(output_file) and not os.path.exists(temp_file)
    assert [s[0] for s in signals] == ["progress"] * 5
    assert conn.closed


def test_kill_before_export_keeps_an_existing_file(monkeypatch, tmp_path):
    output = tmp_path / "out.parquet"
    output.write_text("previous export")
    source = SOURCES["custom_https"]
    state = {}

    def respond(sql):
        if squash(sql).startswith("CREATE TABLE download_data AS"):
            state["worker"].kill()
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return source.respond(sql)

    def keep(worker):
        state["worker"] = worker

    run_worker(source, str(output), monkeypatch, responder=respond, before_run=keep)
    assert output.read_text() == "previous export"


def test_kill_never_removes_a_duckdb_output(monkeypatch, tmp_path):
    output = tmp_path / "out.duckdb"
    output.write_text("the user's database")
    source = SOURCES["custom_https"]
    state = {}

    def respond(sql):
        if sql.startswith("SELECT COUNT(*)"):
            state["worker"].kill()
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return source.respond(sql)

    def keep(worker):
        state["worker"] = worker

    _, conn, signals = run_worker(source, str(output), monkeypatch, responder=respond, before_run=keep)
    assert output.read_text() == "the user's database"
    assert conn.commits == 0
