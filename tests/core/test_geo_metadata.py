"""Characterization of the GeoParquet metadata readers, column detection and
the SQL quoting and predicate helpers.

parquet_kv_metadata() and glob() are DuckDB core, so the readers run against
a real connection on the fixture files where that is possible; a recording
connection pins the exact statements and the error handling.
"""

import json
import os
import struct

import duckdb
import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from core_support import RecordingConnection, Rect
from gpq_downloader.qgis_io.crs import QgsCoordinateReferenceSystem
from gpq_downloader.utils import (
    bbox_overlap_condition,
    bbox_polygon_wkt,
    crs_from_geo,
    detect_columns,
    first_parquet_file,
    is_bbox_struct,
    parse_geo_crs,
    parse_geo_metadata,
    read_geo_metadata,
    sql_ident,
    sql_str,
    transform_bbox_to_4326,
    use_lon_lat_axis_order,
)

DATA = os.path.join(os.path.dirname(__file__), "..", "qgis", "data")
DATA = os.path.normpath(DATA)

COVERING = {"bbox": {
    "xmin": ["geom_bbox", "xmin"], "ymin": ["geom_bbox", "ymin"],
    "xmax": ["geom_bbox", "xmax"], "ymax": ["geom_bbox", "ymax"],
}}


def geo(**doc):
    return json.dumps(doc)


# --- parse_geo_metadata -----------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (geo(version="1.1.0", primary_column="geom", columns={"geom": {"encoding": "WKB", "covering": COVERING}}),
     ("geom", "geom_bbox")),
    # GeoParquet 1.0: no covering
    (geo(primary_column="geometry", columns={"geometry": {}}), ("geometry", None)),
    # The primary column is not described: the first column that is
    (geo(primary_column="x", columns={"g": {}, "h": {"covering": COVERING}}), ("g", None)),
    (geo(columns={"g": {"covering": COVERING}}), ("g", "geom_bbox")),
    (geo(primary_column=None, columns={"g": {}}), ("g", None)),
    # No columns at all: the primary column name is returned anyway
    (geo(primary_column="x"), ("x", None)),
    (geo(primary_column="x", columns=[]), ("x", None)),
    (geo(), (None, None)),
    # Only the xmin path of the covering is read, and only its column name
    (geo(columns={"g": {"covering": {"bbox": {"xmin": ["b", "xmin"], "ymin": ["other", "ymin"]}}}}), ("g", "b")),
    (geo(columns={"g": {"covering": {"bbox": {"xmin": ["b"]}}}}), ("g", None)),
    (geo(columns={"g": {"covering": {"bbox": {"xmin": "b.xmin"}}}}), ("g", None)),
    (geo(columns={"g": {"covering": {"bbox": ["b"]}}}), ("g", None)),
    (geo(columns={"g": {"covering": "bbox"}}), ("g", None)),
    (geo(columns={"g": "WKB"}), ("g", None)),
    # bytes are read as JSON too
    (geo(columns={"g": {}}).encode(), ("g", None)),
    ("not json", (None, None)),
    ("[]", (None, None)),
    ("null", (None, None)),
    ("1", (None, None)),
    ("", (None, None)),
    (None, (None, None)),
])
def test_parse_geo_metadata(raw, expected):
    assert parse_geo_metadata(raw) == expected


# --- parse_geo_crs ----------------------------------------------------------

WGS84_PROJJSON = {"type": "GeographicCRS", "name": "WGS 84", "id": {"authority": "EPSG", "code": 4326}}


@pytest.mark.parametrize("raw, expected", [
    (geo(primary_column="geometry", columns={"geometry": {"crs": {
        "id": {"authority": "EPSG", "code": 2913}, "name": "NAD83(HARN) / Oregon North (ft)"}}}), "EPSG:2913"),
    (geo(columns={"geometry": {"crs": WGS84_PROJJSON}}), "EPSG:4326"),
    (geo(columns={"geometry": {"crs": {"id": {"authority": "OGC", "code": "CRS84"}}}}), "OGC:CRS84"),
    (geo(columns={"geometry": {"crs": {"id": {"authority": "EPSG", "code": 0}}}}), "EPSG:0"),
    # absent or null: the GeoParquet default, OGC:CRS84
    (geo(columns={"geometry": {}}), None),
    (geo(columns={"geometry": {"crs": None}}), None),
    # A string is passed through
    (geo(columns={"geometry": {"crs": "EPSG:3857"}}), "EPSG:3857"),
    # PROJJSON with no usable id is passed through as JSON for PROJ to read
    (geo(columns={"geometry": {"crs": {"name": "odd"}}}), '{"name": "odd"}'),
    (geo(columns={"geometry": {"crs": {"id": {"authority": "", "code": 1}, "name": "x"}}}),
     '{"id": {"authority": "", "code": 1}, "name": "x"}'),
    (geo(columns={"geometry": {"crs": {"id": {"authority": "EPSG", "code": None}}}}),
     '{"id": {"authority": "EPSG", "code": null}}'),
    (geo(columns={"geometry": {"crs": {"ids": [{"authority": "EPSG", "code": 1}]}}}),
     '{"ids": [{"authority": "EPSG", "code": 1}]}'),
    # Anything else is ignored
    (geo(columns={"geometry": {"crs": 4326}}), None),
    # Only the primary column's CRS counts
    (geo(primary_column="a", columns={"a": {}, "b": {"crs": "EPSG:3857"}}), None),
    (geo(primary_column="missing", columns={"a": {"crs": "EPSG:3857"}}), "EPSG:3857"),
    ("not json", None),
    ("[]", None),
    (None, None),
])
def test_parse_geo_crs(raw, expected):
    assert parse_geo_crs(raw) == expected


@pytest.mark.parametrize("value", [None, ""])
def test_crs_from_geo_without_a_crs_needs_no_qgis(value):
    assert crs_from_geo(value) is None


# --- is_bbox_struct and detect_columns --------------------------------------

@pytest.mark.parametrize("col_type, expected", [
    ("STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", True),
    ("STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)", True),
    ("struct(xmin double, ymin double, xmax double, ymax double)", True),
    ("STRUCT(minx DOUBLE, miny DOUBLE, maxx DOUBLE, maxy DOUBLE)", False),
    ("STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE)", False),
    ("VARCHAR", False),
    ("MAP(VARCHAR, DOUBLE)", False),
    # Oddities: the field names are substrings, and a list of structs counts
    ("STRUCT(a_xmin DOUBLE, a_ymin DOUBLE, a_xmax DOUBLE, a_ymax DOUBLE)", True),
    ("STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)[]", True),
])
def test_is_bbox_struct(col_type, expected):
    assert is_bbox_struct(col_type) is expected


BBOX = "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"


@pytest.mark.parametrize("schema, args, kwargs, expected", [
    # Metadata wins when it names columns that exist
    ([("geom", "GEOMETRY"), ("geom_bbox", BBOX)], ("geom", "geom_bbox"), {}, ("geom", "geom_bbox")),
    # Then the caller's hints
    ([("g", "BLOB"), ("b", BBOX)], (), {"hint_geometry": "g", "hint_bbox": "b"}, ("g", "b")),
    ([("g", "BLOB"), ("b", BBOX)], ("missing", "missing"), {"hint_geometry": "g", "hint_bbox": "b"}, ("g", "b")),
    # Metadata over hints
    ([("g", "GEOMETRY"), ("h", "GEOMETRY")], ("h",), {"hint_geometry": "g"}, ("h", None)),
    # Hints that do not exist are ignored; a GEOMETRY-typed column is found
    ([("name", "VARCHAR"), ("geom", "GEOMETRY"), ("geom_bbox", BBOX)], (),
     {"hint_geometry": "geometry", "hint_bbox": "bbox"}, ("geom", None)),
    # GEOGRAPHY too, and the type is matched as a substring, case-insensitively
    ([("shape", "geography")], (), {}, ("shape", None)),
    ([("shape", "GEOMETRY('EPSG:4326')")], (), {}, ("shape", None)),
    # The first GEOMETRY column in schema order
    ([("a", "GEOMETRY"), ("b", "GEOMETRY")], (), {}, ("a", None)),
    # Then the usual names, case-insensitively
    ([("id", "INTEGER"), ("WKB_Geometry", "BLOB")], (), {}, ("WKB_Geometry", None)),
    ([("the_geom", "BLOB")], (), {}, ("the_geom", None)),
    ([("geom", "BLOB"), ("geometry", "BLOB")], (), {}, ("geom", None)),
    # Nothing recognisable: "geometry", even though it does not exist
    ([("id", "INTEGER"), ("wkt", "VARCHAR")], (), {}, ("geometry", None)),
    ([], (), {}, ("geometry", None)),
    # A struct called bbox is found without metadata, case-insensitively
    ([("geometry", "BLOB"), ("bbox", "STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)")], (), {},
     ("geometry", "bbox")),
    ([("geometry", "BLOB"), ("BBOX", BBOX)], (), {}, ("geometry", "BBOX")),
    # ...but not one under another name, nor a bbox that is not a struct
    ([("geometry", "BLOB"), ("extent", BBOX)], (), {}, ("geometry", None)),
    ([("geometry", "BLOB"), ("bbox", "VARCHAR")], (), {}, ("geometry", None)),
    # A covering that names a column that is not a bbox struct is not used
    ([("geom", "GEOMETRY"), ("name", "VARCHAR"), ("bbox", BBOX)], ("geom", "name"), {}, ("geom", "bbox")),
    # Full DESCRIBE rows work as well as (name, type) pairs
    ([("geometry", "GEOMETRY", "YES", None, None, None)], (), {}, ("geometry", None)),
])
def test_detect_columns(schema, args, kwargs, expected):
    assert detect_columns(schema, *args, **kwargs) == expected


# --- Quoting and predicates ---------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("a", "'a'"),
    ("O'Brien", "'O''Brien'"),
    ("''", "''''''"),
    ("back\\slash", "'back\\slash'"),
    ("s3://b/x/*.parquet", "'s3://b/x/*.parquet'"),
    (3, "'3'"),
    (None, "'None'"),
])
def test_sql_str(value, expected):
    assert sql_str(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("geom", '"geom"'),
    ('a"b', '"a""b"'),
    ('g" (SELECT 1); --', '"g"" (SELECT 1); --"'),
    ("names.primary", '"names.primary"'),
    (None, '"None"'),
])
def test_sql_ident(value, expected):
    assert sql_ident(value) == expected


@pytest.mark.parametrize("rect, expected", [
    (Rect(1, 2, 3, 4), "POLYGON((1.0 2.0, 3.0 2.0, 3.0 4.0, 1.0 4.0, 1.0 2.0))"),
    (Rect(-122.5, 37.7, -122.3, 37.9),
     "POLYGON((-122.5 37.7, -122.3 37.7, -122.3 37.9, -122.5 37.9, -122.5 37.7))"),
    # Python's float formatting goes into the WKT as it is, exponents included
    (Rect(0.00001, -1e20, 3, 4), "POLYGON((1e-05 -1e+20, 3.0 -1e+20, 3.0 4.0, 1e-05 4.0, 1e-05 -1e+20))"),
])
def test_bbox_polygon_wkt(rect, expected):
    assert bbox_polygon_wkt(rect) == expected


@pytest.mark.parametrize("column, rect, expected", [
    ("bbox", Rect(1, 2, 3, 4),
     '"bbox".xmin <= 3.0 AND "bbox".xmax >= 1.0 AND "bbox".ymin <= 4.0 AND "bbox".ymax >= 2.0'),
    ("geom_bbox", Rect(-10.5, -5.25, 10.5, 5.25),
     '"geom_bbox".xmin <= 10.5 AND "geom_bbox".xmax >= -10.5 AND "geom_bbox".ymin <= 5.25 AND "geom_bbox".ymax >= -5.25'),
    ('a"b', Rect(0, 0, 1, 1),
     '"a""b".xmin <= 1.0 AND "a""b".xmax >= 0.0 AND "a""b".ymin <= 1.0 AND "a""b".ymax >= 0.0'),
])
def test_bbox_overlap_condition(column, rect, expected):
    assert bbox_overlap_condition(column, rect) == expected


# --- use_lon_lat_axis_order -------------------------------------------------

def test_use_lon_lat_axis_order_statement():
    conn = RecordingConnection()
    use_lon_lat_axis_order(conn)
    assert conn.statements == ["SET geometry_always_xy = true;"]


def test_use_lon_lat_axis_order_tolerates_a_duckdb_without_the_setting():
    # Without the spatial extension DuckDB does not know the setting
    conn = duckdb.connect()
    try:
        use_lon_lat_axis_order(conn)
    finally:
        conn.close()


# --- transform_bbox_to_4326 without a transform ------------------------------

def test_transform_bbox_to_4326_passes_through():
    rect = Rect(1, 2, 3, 4)
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    assert transform_bbox_to_4326(rect, wgs84) is rect
    assert transform_bbox_to_4326(None, wgs84) is None
    assert transform_bbox_to_4326(rect, None) is None


# --- first_parquet_file -------------------------------------------------------

def test_first_parquet_file_without_a_glob_lists_nothing():
    conn = RecordingConnection()
    assert first_parquet_file(conn, "s3://b/x.parquet") == "s3://b/x.parquet"
    assert conn.statements == []


def test_first_parquet_file_statement():
    conn = RecordingConnection(lambda sql: [("s3://b/it's/0.parquet",)])
    assert first_parquet_file(conn, "s3://b/it's/*.parquet") == "s3://b/it's/0.parquet"
    assert conn.statements == ["SELECT file FROM glob('s3://b/it''s/*.parquet') LIMIT 1"]


def test_first_parquet_file_real_glob():
    conn = duckdb.connect()
    try:
        assert first_parquet_file(conn, f"{DATA}/*.parquet") == f"{DATA}/geom_bbox_named.parquet"
        # No match: the glob itself
        assert first_parquet_file(conn, "/nonexistent/*.parquet") == "/nonexistent/*.parquet"
    finally:
        conn.close()


def test_first_parquet_file_error_returns_the_url():
    def fail(sql):
        raise duckdb.IOException("403")

    assert first_parquet_file(RecordingConnection(fail), "s3://b/*.parquet") == "s3://b/*.parquet"


def test_first_parquet_file_reraises_a_cancel():
    def interrupted(sql):
        raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")

    with pytest.raises(duckdb.InterruptException):
        first_parquet_file(RecordingConnection(interrupted), "s3://b/*.parquet")


# --- read_geo_metadata ------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("geom_bbox_named.parquet", ("geom", "geom_bbox", None)),
    ("geoparquet_with_metadata.parquet", ("geometry", None, "EPSG:4326")),
    ("non_geoparquet_with_geometry.parquet", (None, None, None)),
    # A glob reads the first file it lists
    ("*.parquet", ("geom", "geom_bbox", None)),
])
def test_read_geo_metadata_of_the_fixtures(name, expected):
    conn = duckdb.connect()
    try:
        assert read_geo_metadata(conn, f"{DATA}/{name}") == expected
    finally:
        conn.close()


def test_read_geo_metadata_of_a_projected_file(tmp_path):
    path = tmp_path / "projected.parquet"
    # DuckDB checks the 'geo' metadata when it opens the file, so it has to be complete
    point = struct.pack("<BIdd", 1, 1, 7600000.0, 650000.0)
    table = pa.table({"geometry": [point]}).replace_schema_metadata({
        b"geo": geo(version="1.1.0", primary_column="geometry", columns={"geometry": {
            "encoding": "WKB", "geometry_types": ["Point"],
            "crs": {"id": {"authority": "EPSG", "code": 2913}}}}).encode(),
    })
    pq.write_table(table, path)
    conn = duckdb.connect()
    try:
        assert read_geo_metadata(conn, str(path)) == ("geometry", None, "EPSG:2913")
    finally:
        conn.close()


def test_read_geo_metadata_of_a_missing_file():
    conn = duckdb.connect()
    try:
        assert read_geo_metadata(conn, "/nonexistent/x.parquet") == (None, None, None)
    finally:
        conn.close()


def test_read_geo_metadata_statements():
    raw = geo(primary_column="geom", columns={"geom": {"covering": COVERING, "crs": "EPSG:3857"}})

    def respond(sql):
        if "glob(" in sql:
            return [("s3://b/0.parquet",)]
        return [(raw.encode(),)]

    conn = RecordingConnection(respond)
    assert read_geo_metadata(conn, "s3://b/*.parquet") == ("geom", "geom_bbox", "EPSG:3857")
    assert conn.statements == [
        "SELECT file FROM glob('s3://b/*.parquet') LIMIT 1",
        "SELECT value FROM parquet_kv_metadata('s3://b/0.parquet') WHERE key = 'geo'::BLOB LIMIT 1",
    ]


@pytest.mark.parametrize("value, expected", [
    # A str value as well as bytes
    (geo(columns={"g": {}}), ("g", None, None)),
    # Bytes that are not UTF-8 are decoded with replacement, then fail as JSON
    (b"\xff\xfe", (None, None, None)),
])
def test_read_geo_metadata_value_types(value, expected):
    conn = RecordingConnection(lambda sql: [(value,)])
    assert read_geo_metadata(conn, "s3://b/x.parquet") == expected


def test_read_geo_metadata_without_geo_key():
    assert read_geo_metadata(RecordingConnection(), "s3://b/x.parquet") == (None, None, None)


def test_read_geo_metadata_error():
    def fail(sql):
        raise duckdb.IOException("403")

    assert read_geo_metadata(RecordingConnection(fail), "s3://b/x.parquet") == (None, None, None)


def test_read_geo_metadata_reraises_a_cancel():
    def interrupted(sql):
        raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")

    with pytest.raises(duckdb.InterruptException):
        read_geo_metadata(RecordingConnection(interrupted), "s3://b/x.parquet")
