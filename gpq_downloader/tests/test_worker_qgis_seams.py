"""The Worker SQL that depends on QGIS: the seams the core/qgis_io split cuts.

tests/core/test_worker_golden_sql.py pins every statement Worker.run() issues
when the window is already in EPSG:4326 and the data has no CRS of its own.
These are the paths it cannot reach without real QGIS classes, each pinned on
a recording connection passed in through the connection_factory seam:

- an AOI geometry: QgsGeometry, and its transform from the canvas CRS
- a canvas in another CRS: transform_bbox_to_4326() and QgsCoordinateTransform
- data in a projected CRS: crs_from_geo() and transform_rect() decide the
  filter window, then reproject_table() converts the result

Numbers that come out of PROJ, and QGIS's WKT of them, are compared to 1e-6
relative, so a PROJ upgrade's last-digit differences are not failures;
everything else in the SQL is compared exactly.
"""

import json
import re

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsRectangle

from gpq_downloader.utils import Worker, crs_from_geo, transform_bbox_to_4326

URL = "https://example.com/data/rivers.parquet"
OUT = "/nonexistent/gpq/out.parquet"
WINDOW = QgsRectangle(10, 10, 11, 11)
SPATIAL_CHECK = "SELECT ST_AsText(ST_GeomFromText('POINT(0 0)'))"
PREAMBLE = [
    "INSTALL httpfs;", "INSTALL spatial;", "LOAD httpfs;", "LOAD spatial;", "SET geometry_always_xy = true;",
    SPATIAL_CHECK,
    f"DESCRIBE SELECT * FROM read_parquet('{URL}')",
    f"SELECT value FROM parquet_kv_metadata('{URL}') WHERE key = 'geo'::BLOB LIMIT 1",
]
COPY_PARQUET = (
    'COPY ( WITH bbox AS ( SELECT ST_Extent(ST_Extent_Agg("geom"))::BOX_2D AS b FROM download_data ) '
    'SELECT t.* FROM download_data AS t CROSS JOIN bbox ORDER BY ST_Hilbert(t."geom", bbox.b) ) '
    f"TO '{OUT}' (FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 22);"
)
BBOX = "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"

_QUOTED = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")")
# A number on its own, not digits inside a name such as download_data_4326
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e[+-]?\d+)?(?![\w.])")


def squash(sql):
    """Collapse whitespace outside quoted text (as tests/core does)."""
    if isinstance(sql, tuple):
        return (squash(sql[0]), sql[1])
    parts = _QUOTED.split(sql)
    return "".join(p if i % 2 else re.sub(r"\s+", " ", p) for i, p in enumerate(parts)).strip()


def shape(sql):
    """The SQL with every number replaced by #."""
    return _NUMBER.sub("#", squash(sql))


def numbers(sql):
    return [float(n) for n in _NUMBER.findall(squash(sql))]


def assert_sql(got, expected):
    """Compare statements exactly, except numbers, which are compared to 1e-6."""
    assert [shape(s) for s in got] == [shape(s) for s in expected]
    for g, e in zip(got, expected):
        assert numbers(g) == pytest.approx(numbers(e), rel=1e-6, abs=1e-9), g


class Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class Recorder:
    def __init__(self, schema, geo, table_schema=None):
        self.statements = []
        self.schema = schema
        self.geo = geo
        self.table_schema = table_schema or schema
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if sql == SPATIAL_CHECK:
            return Result([("POINT (0 0)",)])
        if sql.startswith("DESCRIBE SELECT"):
            return Result(self.schema)
        if sql == "DESCRIBE download_data":
            return Result(self.table_schema)
        if "parquet_kv_metadata" in sql:
            return Result([(self.geo,)])
        if sql.startswith("SELECT COUNT(*)"):
            return Result([(3,)])
        return Result([])

    def interrupt(self):
        pass

    def close(self):
        self.closed = True


def describe(*columns):
    return [(name, t, "YES", None, None, None) for name, t in columns]


def geo(primary="geom", bbox_column=None, crs=None):
    column = {"encoding": "WKB", "geometry_types": []}
    if bbox_column:
        column["covering"] = {"bbox": {k: [bbox_column, k] for k in ("xmin", "ymin", "xmax", "ymax")}}
    if crs:
        column["crs"] = crs
    return json.dumps({"version": "1.1.0", "primary_column": primary, "columns": {primary: column}}).encode()


class Iface:
    def __init__(self, authid="EPSG:4326"):
        crs = QgsCoordinateReferenceSystem(authid)
        settings = type("S", (), {"destinationCrs": lambda self: crs})()
        canvas = type("C", (), {"mapSettings": lambda self: settings})()
        self.mapCanvas = lambda: canvas


def run(recorder, extent=WINDOW, aoi=None, canvas="EPSG:4326", output=OUT):
    worker = Worker(URL, extent, output, Iface(canvas), {}, aoi_geometry=aoi,
                    connection_factory=lambda *args: recorder)
    signals = []
    for name in ("progress", "info", "error", "load_layer", "finished"):
        getattr(worker, name).connect(lambda *args, _n=name: signals.append((_n, *args)))
    worker.run()
    return [squash(s) for s in recorder.statements], signals


def create(select_where):
    return f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet('{URL}') {select_where} )"


GEOM_BBOX_WINDOW = (
    'WHERE "geom_bbox".xmin <= 11.0 AND "geom_bbox".xmax >= 10.0 '
    'AND "geom_bbox".ymin <= 11.0 AND "geom_bbox".ymax >= 10.0'
)
AOI_WKT = "POLYGON((10.1 10.1, 10.45 10.1, 10.45 10.45, 10.1 10.45, 10.1 10.1))"


def test_aoi_in_the_canvas_crs(qgs_app):
    """The AOI is tested with ST_Intersects on top of the bbox pass, as QGIS writes its WKT."""
    rec = Recorder(describe(("geom", "GEOMETRY"), ("geom_bbox", BBOX)), geo("geom", "geom_bbox"))
    aoi = QgsGeometry.fromWkt(AOI_WKT)
    got, signals = run(rec, aoi=aoi)
    # QGIS's own WKT goes into the SQL: "Polygon ((10.09999999999999964 ...", on 3.34
    aoi_wkt = aoi.asWkt()
    assert aoi_wkt.startswith("Polygon ((")
    assert_sql(got, PREAMBLE + [
        create(f"{GEOM_BBOX_WINDOW} AND ST_Intersects(\"geom\", ST_GeomFromText('{aoi_wkt}'))"),
        "SELECT COUNT(*) FROM download_data",
        COPY_PARQUET,
        "DROP TABLE IF EXISTS download_data",
    ])
    assert signals[-2:] == [("load_layer", OUT), ("finished",)]
    # The caller's geometry is not transformed in place
    assert aoi.asWkt() == aoi_wkt


def test_aoi_from_a_web_mercator_canvas(qgs_app):
    """The canvas extent and the AOI are both converted to EPSG:4326 first."""
    rec = Recorder(describe(("geom", "GEOMETRY")), geo("geom"))
    # About (10, 10)-(11, 11) in EPSG:3857
    extent = QgsRectangle(1113194.9079327357, 1118889.9748579597, 1224514.3987260093, 1232106.8017379127)
    aoi = QgsGeometry.fromWkt(
        "POLYGON((1113194.9079327357 1118889.9748579597, 1224514.3987260093 1118889.9748579597, "
        "1224514.3987260093 1232106.8017379127, 1113194.9079327357 1118889.9748579597))"
    )
    got, signals = run(rec, extent=extent, aoi=aoi, canvas="EPSG:3857")
    create_sql = got[len(PREAMBLE)]
    # No covering: only the AOI test, in degrees
    assert shape(create_sql) == shape(create(
        "WHERE ST_Intersects(\"geom\", ST_GeomFromText('Polygon ((1.0 1.0, 1.0 1.0, 1.0 1.0, 1.0 1.0))'))"
    ))
    assert numbers(create_sql) == pytest.approx([10, 10, 11, 10, 11, 11, 10, 10], abs=1e-6)
    assert signals[-1] == ("finished",)


def test_a_web_mercator_canvas_extent(qgs_app):
    rec = Recorder(describe(("geom", "GEOMETRY"), ("geom_bbox", BBOX)), geo("geom", "geom_bbox"))
    extent = QgsRectangle(1113194.9079327357, 1118889.9748579597, 1224514.3987260093, 1232106.8017379127)
    got, _ = run(rec, extent=extent, canvas="EPSG:3857")
    assert shape(got[len(PREAMBLE)]) == shape(create(GEOM_BBOX_WINDOW))
    assert numbers(got[len(PREAMBLE)]) == pytest.approx([11, 10, 11, 10], abs=1e-6)


def test_transform_bbox_to_4326(qgs_app):
    extent = QgsRectangle(1113194.9079327357, 1118889.9748579597, 1224514.3987260093, 1232106.8017379127)
    out = transform_bbox_to_4326(extent, QgsCoordinateReferenceSystem("EPSG:3857"))
    assert (out.xMinimum(), out.yMinimum(), out.xMaximum(), out.yMaximum()) == pytest.approx(
        (10, 10, 11, 11), abs=1e-6)
    same = QgsRectangle(1, 2, 3, 4)
    assert transform_bbox_to_4326(same, QgsCoordinateReferenceSystem("EPSG:4326")) is same


# The TriMet case (#182): EPSG:2913 is Oregon North in international feet
OREGON = QgsRectangle(-123.0, 45.0, -122.0, 46.0)
EPSG_2913 = {"id": {"authority": "EPSG", "code": 2913}, "name": "NAD83(HARN) / Oregon North (ft)"}


# The window in EPSG:2913 as transform_rect() computes it (on QGIS 3.34)
OREGON_IN_FEET = [7555562.360839133, 489694.1279963993, 7820908.240836881, 860531.9048522356]
# and its corners, each converted on its own, as the AOI polygon is
OREGON_CORNERS_IN_FEET = [
    7555562.360839133, 496096.5623164205, 7814139.131725055, 489694.1279963993,
    7820908.240836881, 854241.1776035023, 7566843.064758781, 860531.9048522472,
    7555562.360839133, 496096.5623164205,
]


def test_projected_data_is_filtered_in_its_units_then_reprojected(qgs_app):
    rec = Recorder(
        describe(("name", "VARCHAR"), ("geom", "GEOMETRY"), ("geom_bbox", BBOX)),
        geo("geom", "geom_bbox", crs=EPSG_2913),
    )
    got, signals = run(rec, extent=OREGON)
    transformed = "ST_Transform(\"geom\", 'EPSG:2913', 'EPSG:4326', always_xy := true)"
    assert [shape(s) for s in got[len(PREAMBLE):]] == [shape(s) for s in [
        create('WHERE "geom_bbox".xmin <= 1.0 AND "geom_bbox".xmax >= 1.0 '
               'AND "geom_bbox".ymin <= 1.0 AND "geom_bbox".ymax >= 1.0'),
        "DESCRIBE download_data",
        f'CREATE TABLE download_data_4326 AS SELECT "name", {transformed} AS "geom", '
        f"struct_pack(xmin := ST_XMin({transformed}), ymin := ST_YMin({transformed}), "
        f'xmax := ST_XMax({transformed}), ymax := ST_YMax({transformed})) AS "geom_bbox" FROM download_data',
        "DROP TABLE download_data",
        "ALTER TABLE download_data_4326 RENAME TO download_data",
        "SELECT COUNT(*) FROM download_data",
        COPY_PARQUET,
        "DROP TABLE IF EXISTS download_data",
    ]]
    xmax, xmin, ymax, ymin = numbers(got[len(PREAMBLE)])
    assert [xmin, ymin, xmax, ymax] == pytest.approx(OREGON_IN_FEET, rel=1e-6)
    assert ("progress", "Reprojecting to EPSG:4326...") in signals
    assert signals[-2:] == [("load_layer", OUT), ("finished",)]


def test_projected_data_without_a_covering_tests_the_area_in_its_units(qgs_app):
    rec = Recorder(describe(("geom", "GEOMETRY")), geo("geom", crs=EPSG_2913))
    got, _ = run(rec, extent=OREGON)
    create_sql = got[len(PREAMBLE)]
    assert shape(create_sql) == shape(create(
        "WHERE ST_Intersects(\"geom\", ST_GeomFromText('Polygon ((1.0 1.0, 1.0 1.0, 1.0 1.0, 1.0 1.0, 1.0 1.0))'))"
    ))
    # The window's corners, each converted on its own: not the bounding box
    assert numbers(create_sql) == pytest.approx(OREGON_CORNERS_IN_FEET, rel=1e-6)


def test_an_unknown_crs_is_an_error_before_anything_is_downloaded(qgs_app):
    rec = Recorder(describe(("geom", "GEOMETRY")), geo("geom", crs="EPSG:999999"))
    got, signals = run(rec)
    # Returned before the table name is set, so not even the cleanup DROP
    assert got == PREAMBLE
    assert signals[-1] == ("error", (
        "This file says it is in EPSG:999999, which QGIS does not recognise, so the area you asked for "
        "cannot be matched against it. Reproject the data to EPSG:4326 and try again."
    ))
    assert rec.closed


@pytest.mark.parametrize("text, expected", [
    (None, None),
    ("EPSG:4326", None),
    ("EPSG:2913", "EPSG:2913"),
    ("EPSG:999999", False),
    ("not a crs", False),
])
def test_crs_from_geo(qgs_app, text, expected):
    crs = crs_from_geo(text)
    if expected in (None, False):
        assert crs is expected
    else:
        assert crs.authid() == expected


def test_crs_from_geo_ogc_crs84(qgs_app):
    """OGC:CRS84 is GeoParquet's default, written out. QGIS does not count it
    as EPSG:4326, so data that says so is passed through ST_Transform."""
    crs = crs_from_geo("OGC:CRS84")
    assert crs is not None and crs is not False
    assert crs.authid() == "OGC:CRS84"
