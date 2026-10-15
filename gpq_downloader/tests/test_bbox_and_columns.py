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
from qgis.core import QgsGeometry, QgsRectangle, QgsVectorLayer

from gpq_downloader.utils import (
    ValidationWorker,
    Worker,
    detect_columns,
    parse_geo_metadata,
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
