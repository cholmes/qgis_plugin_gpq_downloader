"""Real DuckDB runs of the bbox filter, column detection, clipping and exports.

data/geom_bbox_named.parquet was written by geoparquet-io (`gpio add bbox
--bbox-name geom_bbox`): its primary geometry column is `geom` and its bbox
covering column is `geom_bbox` (#163). Its polygons sit around the window
(10, 10)-(11, 11):

- contains_window: (0, 0)-(20, 20), larger than the window on every side (#2, #176)
- inside: (10.2, 10.2)-(10.4, 10.4)
- crosses_west_edge: (9.5, 10.5)-(10.5, 10.7), xmin west of the window (#120, #176)
- outside: (30, 30)-(31, 31)
"""

import os

import duckdb
import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsRectangle, QgsVectorLayer

from gpq_downloader.utils import (
    ValidationWorker,
    Worker,
    crs_from_geo,
    detect_columns,
    parse_geo_crs,
    parse_geo_metadata,
    sql_ident,
    sql_str,
    transform_rect,
)

DATA = os.path.join(os.path.dirname(__file__), "data", "geom_bbox_named.parquet")
WINDOW = QgsRectangle(10, 10, 11, 11)
IN_WINDOW = {"contains_window", "inside", "crosses_west_edge"}

# What the download queue passes for every source: preset defaults, which do
# not match this file's columns
QUEUE_DEFAULTS = {"has_bbox": True, "bbox_column": "bbox", "geometry_column": "geometry"}


def run_worker(iface, output, aoi=None, clip=False, url=DATA, results=None):
    worker = Worker(
        url, WINDOW, str(output), iface, dict(results or QUEUE_DEFAULTS),
        aoi_geometry=aoi, clip_to_area=clip,
    )
    worker.size_warning_accepted = True
    errors, infos = [], []
    worker.error.connect(errors.append)
    worker.info.connect(infos.append)
    worker.run()
    assert not errors, errors
    return worker, infos


def read_parquet(path, sql="SELECT name FROM t ORDER BY name"):
    conn = duckdb.connect()
    conn.execute("LOAD spatial")
    conn.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{path}')")
    return conn.execute(sql).fetchall()


def test_parse_geo_metadata():
    raw = (
        '{"version": "1.1.0", "primary_column": "geom", "columns": {"geom": {'
        '"encoding": "WKB", "covering": {"bbox": {"xmin": ["geom_bbox", "xmin"], '
        '"ymin": ["geom_bbox", "ymin"], "xmax": ["geom_bbox", "xmax"], "ymax": ["geom_bbox", "ymax"]}}}}}'
    )
    assert parse_geo_metadata(raw) == ("geom", "geom_bbox")
    # GeoParquet 1.0: no covering
    assert parse_geo_metadata('{"primary_column": "geometry", "columns": {"geometry": {}}}') == ("geometry", None)
    # an invalid covering, or junk, is ignored rather than raising
    assert parse_geo_metadata('{"columns": {"geometry": {"covering": {"bbox": {"xmin": [0]}}}}}') == ("geometry", None)
    assert parse_geo_metadata("not json") == (None, None)
    assert parse_geo_metadata("[]") == (None, None)


def test_parse_geo_crs():
    """#182: data in a projected CRS has to be filtered in its own units."""
    trimet = (
        '{"primary_column": "geometry", "columns": {"geometry": {"encoding": "WKB", '
        '"crs": {"id": {"authority": "EPSG", "code": 2913}, "name": "NAD83(HARN) / Oregon North (ft)"}}}}'
    )
    assert parse_geo_crs(trimet) == "EPSG:2913"
    # absent or null means the GeoParquet default, OGC:CRS84
    assert parse_geo_crs('{"columns": {"geometry": {}}}') is None
    assert parse_geo_crs('{"columns": {"geometry": {"crs": null}}}') is None
    assert parse_geo_crs("not json") is None
    # PROJJSON with no authority id is passed through for PROJ to read
    assert parse_geo_crs('{"columns": {"geometry": {"crs": {"name": "odd"}}}}') == '{"name": "odd"}'


def test_crs_from_geo(qgs_app):
    assert crs_from_geo("EPSG:2913").authid() == "EPSG:2913"
    assert crs_from_geo(None) is None
    assert crs_from_geo("EPSG:4326") is None  # already lon/lat: nothing to do
    assert crs_from_geo("EPSG:999999") is False  # present but unreadable


def test_transform_rect_into_the_files_units(qgs_app):
    """The Oregon window in degrees has to cover the TriMet file's bbox in feet."""
    window = QgsRectangle(-123.78146933459826, 45.229451024481, -121.65289549498178, 45.67616962908521)
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    feet = transform_rect(window, wgs84, QgsCoordinateReferenceSystem("EPSG:2913"))
    file_bbox = QgsRectangle(7563739.96, 607778.18, 7710256.64, 714400.32)
    assert feet.contains(file_bbox)


def projected_table(conn):
    conn.execute("INSTALL spatial; LOAD spatial")
    conn.execute(
        "CREATE TABLE t AS SELECT 'rail' AS name, ST_Point(7600000, 650000) AS geom, "
        "struct_pack(xmin := 7600000.0, ymin := 650000.0, xmax := 7600000.0, ymax := 650000.0) AS geom_bbox"
    )


def test_reproject_table_converts_to_4326(mock_iface, tmp_path):
    """#182: after filtering in the file's own units the table is converted, and
    the stale bbox covering is rebuilt from the new geometry."""
    conn = duckdb.connect()
    projected_table(conn)
    worker = Worker(DATA, WINDOW, str(tmp_path / "o.parquet"), mock_iface, dict(QUEUE_DEFAULTS))

    assert worker.reproject_table(conn, "t", "geom", "geom_bbox", "EPSG:2913")

    x, y, bx, by = conn.execute(
        "SELECT ST_X(geom), ST_Y(geom), geom_bbox.xmin, geom_bbox.ymin FROM t"
    ).fetchone()
    assert -123 < x < -122 and 45 < y < 46, "expected lon/lat near Portland, not lat/lon"
    assert (bx, by) == pytest.approx((x, y))


def test_reproject_table_reports_a_crs_duckdb_cannot_use(mock_iface, tmp_path):
    conn = duckdb.connect()
    projected_table(conn)
    worker = Worker(DATA, WINDOW, str(tmp_path / "o.parquet"), mock_iface, dict(QUEUE_DEFAULTS))
    errors = []
    worker.error.connect(errors.append)

    assert worker.reproject_table(conn, "t", "geom", "geom_bbox", "EPSG:NOPE") is False
    assert errors and "EPSG:NOPE" in errors[0]


def test_sql_quoting():
    assert sql_ident("geom") == '"geom"'
    assert sql_ident('a"b') == '"a""b"'
    assert sql_str("O'Brien") == "'O''Brien'"


def test_quoted_column_name_cannot_inject_sql(mock_iface, tmp_path):
    """A geometry column whose name contains a double quote (which a hostile
    remote file can choose) stays an identifier instead of becoming SQL."""
    evil = 'g" (SELECT 1); --'
    src = tmp_path / "quoted.parquet"
    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial")
    conn.execute(
        f"COPY (SELECT name, geom AS {sql_ident(evil)} FROM read_parquet({sql_str(DATA)})) "
        f"TO {sql_str(src)} (FORMAT parquet)"
    )
    out = tmp_path / "quoted_out.parquet"
    worker, _ = run_worker(mock_iface, out, url=str(src))
    assert worker.validation_results["geometry_column"] == evil
    assert {r[0] for r in read_parquet(out)} == IN_WINDOW


def test_detect_columns_prefers_metadata_then_hints_then_schema():
    schema = [
        ("name", "VARCHAR"),
        ("geom", "GEOMETRY"),
        ("geom_bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"),
    ]
    assert detect_columns(schema, "geom", "geom_bbox") == ("geom", "geom_bbox")
    # hints that don't exist in the file are ignored; the GEOMETRY type is found
    assert detect_columns(schema, hint_geometry="geometry", hint_bbox="bbox") == ("geom", None)
    # a plain struct called bbox is still found without metadata
    overture = [("geometry", "BLOB"), ("bbox", "STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)")]
    assert detect_columns(overture) == ("geometry", "bbox")
    # a covering that names a column that isn't a bbox struct is not used
    assert detect_columns(schema, "geom", "name") == ("geom", None)


def test_validation_finds_named_covering(mock_iface):
    """#163/#178: geom + geom_bbox is found from the metadata, with no bbox warning."""
    results, warned = [], []
    worker = ValidationWorker(DATA, mock_iface, WINDOW)
    worker.finished.connect(lambda ok, msg, r: results.append((ok, msg, r)))
    worker.needs_bbox_warning.connect(lambda: warned.append(True))
    worker.run()
    ok, msg, r = results[0]
    assert ok, msg
    assert (r["geometry_column"], r["bbox_column"], r["has_bbox"]) == ("geom", "geom_bbox", True)
    assert not warned


def test_worker_uses_metadata_columns_and_keeps_overlapping_features(mock_iface, tmp_path):
    """#177: the covering column from the metadata is used even when the caller says "bbox".
    #176/#120/#2: features larger than the window, or entering it from the west, are kept."""
    out = tmp_path / "out.parquet"
    worker, _ = run_worker(mock_iface, out)
    assert worker.validation_results["bbox_column"] == "geom_bbox"
    assert worker.validation_results["geometry_column"] == "geom"
    assert {r[0] for r in read_parquet(out)} == IN_WINDOW


def test_clip_cuts_features_and_refreshes_bbox(mock_iface, tmp_path):
    """#120: with clipping on, the large polygons are cut at the window edge."""
    out = tmp_path / "clipped.parquet"
    run_worker(mock_iface, out, clip=True)
    rows = read_parquet(
        out,
        "SELECT name, ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom), "
        "geom_bbox.xmin, geom_bbox.ymin, geom_bbox.xmax, geom_bbox.ymax FROM t ORDER BY name",
    )
    by_name = {r[0]: r[1:] for r in rows}
    assert set(by_name) == IN_WINDOW
    assert by_name["contains_window"][:4] == (10, 10, 11, 11)
    assert by_name["crosses_west_edge"][:4] == (10, 10.5, 10.5, 10.7)
    for values in by_name.values():  # the bbox column follows the clipped geometry
        assert values[:4] == pytest.approx(values[4:])


def test_aoi_polygon_filters_precisely(mock_iface, tmp_path):
    """An AOI is applied with ST_Intersects on top of the bbox pass."""
    aoi = QgsGeometry.fromWkt("POLYGON((10.1 10.1, 10.45 10.1, 10.45 10.45, 10.1 10.45, 10.1 10.1))")
    out = tmp_path / "aoi.parquet"
    run_worker(mock_iface, out, aoi=aoi)
    assert {r[0] for r in read_parquet(out)} == {"contains_window", "inside"}


def test_wkb_blob_column_with_aoi(mock_iface, tmp_path):
    """A WKB BLOB geometry with an AOI used to produce 'WHERE'-less '... AND ST_Intersects' SQL."""
    src = tmp_path / "blob.parquet"
    conn = duckdb.connect()
    conn.execute("LOAD spatial")
    conn.execute(
        f"COPY (SELECT name, ST_AsWKB(geom) AS the_geom FROM read_parquet('{DATA}')) TO '{src}' (FORMAT parquet)"
    )
    aoi = QgsGeometry.fromWkt("POLYGON((10.1 10.1, 10.45 10.1, 10.45 10.45, 10.1 10.45, 10.1 10.1))")
    out = tmp_path / "blob_out.parquet"
    worker, _ = run_worker(mock_iface, out, aoi=aoi, url=str(src))
    assert worker.validation_results["geometry_column"] == "the_geom"
    assert {r[0] for r in read_parquet(out)} == {"contains_window", "inside"}


@pytest.mark.parametrize("ext", ["gpkg", "fgb", "geojson"])
def test_exports_carry_epsg_4326(mock_iface, tmp_path, ext):
    """#179: GeoPackage (like FlatGeobuf and GeoJSON) is written with its CRS."""
    out = tmp_path / f"out.{ext}"
    run_worker(mock_iface, out)
    layer = QgsVectorLayer(str(out), "out", "ogr")
    assert layer.isValid()
    assert layer.featureCount() == 3
    assert layer.crs().authid() == "EPSG:4326"
