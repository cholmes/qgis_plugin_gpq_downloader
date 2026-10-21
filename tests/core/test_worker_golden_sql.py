"""Golden SQL for Worker.run(): every statement it issues, in order.

Each test runs the Worker against a recording connection (passed in through
the connection_factory seam) that answers like DuckDB would for one source,
and compares the whole statement sequence with the one written out here. The
matrix is the four kinds of source (an Overture glob, Source Cooperative over
S3 and through its https proxy, a custom https file, MinIO) by the three
formats QGIS loads (GeoParquet, GeoPackage, FlatGeobuf), followed by the paths
off that matrix: no data, errors, clipping, GeoJSON's size check, DuckDB.

Statements are compared after squash(), which only collapses the whitespace
between tokens outside quotes, so the indentation of the multi-line f-strings
in utils.py is free to change and nothing else is.

The window is in EPSG:4326 and the data has no CRS of its own, so nothing here
goes through a QGIS transform; the AOI and projected-data paths do, and are
pinned in gpq_downloader/tests/test_worker_qgis_seams.py.
"""

import duckdb
import pytest

from core_support import squash
from worker_cases import (
    BBOX_DOUBLE,
    CUSTOM_URL,
    SOURCES,
    Source,
    describe,
    geo_metadata,
    overture_part,
    run_worker,
)

OUT = "/nonexistent/gpq/out"
WINDOW_WKT = "POLYGON((10.0 10.0, 11.0 10.0, 11.0 11.0, 10.0 11.0, 10.0 10.0))"

PREAMBLE = [
    "INSTALL httpfs;",
    "INSTALL spatial;",
    "LOAD httpfs;",
    "LOAD spatial;",
    "SET geometry_always_xy = true;",
]
SPATIAL_CHECK = "SELECT ST_AsText(ST_GeomFromText('POINT(0 0)'))"
COUNT = "SELECT COUNT(*) FROM download_data"
CLEANUP = "DROP TABLE IF EXISTS download_data"

FORMAT_OPTIONS = {
    "parquet": "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 22);",
    "gpkg": "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');",
    "fgb": "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');",
    "geojson": "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');",
}


def copy(geometry, output_file, options):
    return (
        f'COPY ( WITH bbox AS ( SELECT ST_Extent(ST_Extent_Agg("{geometry}"))::BOX_2D AS b '
        f"FROM download_data ) SELECT t.* FROM download_data AS t CROSS JOIN bbox "
        f'ORDER BY ST_Hilbert(t."{geometry}", bbox.b) ) TO \'{output_file}\' {options}'
    )


OVERTURE_GLOB = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*"
OVERTURE_PART_1 = overture_part(1)
OBM_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet"
OBM_HIT = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/building.122221.parquet"
PROXY_S3 = "s3://ondata/cadastral-italy-geospatial-data/ple/**/*.parquet"
MINIO_S3 = "s3://lake/roads/*.parquet"

OVERTURE_BBOX_FILTER = (
    'WHERE "bbox".xmin <= 11.0 AND "bbox".xmax >= 10.0 AND "bbox".ymin <= 11.0 AND "bbox".ymax >= 10.0'
)
GEOM_BBOX_FILTER = (
    'WHERE "geom_bbox".xmin <= 11.0 AND "geom_bbox".xmax >= 10.0 '
    'AND "geom_bbox".ymin <= 11.0 AND "geom_bbox".ymax >= 10.0'
)

# Per source: the statements up to the download, the download for a GeoParquet
# output and for any other output, what follows it, and the geometry column.
GOLDEN = {
    "overture": {
        "setup": [
            f"DESCRIBE SELECT * FROM read_parquet('{OVERTURE_GLOB}')",
            f"SELECT file FROM glob('{OVERTURE_GLOB}') LIMIT 1",
            f"SELECT value FROM parquet_kv_metadata('{overture_part(0)}') WHERE key = 'geo'::BLOB LIMIT 1",
            # pruned with the STAC: only part 1 overlaps the window
            f"SELECT file FROM glob('{OVERTURE_GLOB}')",
        ],
        "create_parquet": (
            f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet(['{OVERTURE_PART_1}']) "
            f"{OVERTURE_BBOX_FILTER} )"
        ),
        # Overture outputs other than GeoParquet get the primary name as a
        # first column, and structs and lists as JSON
        "create_other": (
            'CREATE TABLE download_data AS ( SELECT "names"."primary" as name,"id", "geometry", '
            'TO_JSON("bbox") AS "bbox", "version", TO_JSON("sources") AS "sources", '
            'TO_JSON("names") AS "names", "height" '
            f"FROM read_parquet(['{OVERTURE_PART_1}']) {OVERTURE_BBOX_FILTER} )"
        ),
        "after_create": [],
        "geometry": "geometry",
        "results": {"geometry_column": "geometry", "bbox_column": "bbox", "has_bbox": True},
    },
    "sourcecoop": {
        "setup": [
            "SET s3_region='us-west-2';",
            "SET s3_endpoint='s3.us-west-2.amazonaws.com';",
            "SET s3_url_style='path';",
            "__SPATIAL_CHECK__",
            f"DESCRIBE SELECT * FROM read_parquet('{OBM_GLOB}')",
            f"SELECT file FROM glob('{OBM_GLOB}') LIMIT 1",
            f"SELECT value FROM parquet_kv_metadata('{OBM_HIT}') WHERE key = 'geo'::BLOB LIMIT 1",
            # pruned by the quadkeys in the file names
            f"SELECT file FROM glob('{OBM_GLOB}')",
        ],
        "create_parquet": (
            f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet(['{OBM_HIT}']) {OVERTURE_BBOX_FILTER} )"
        ),
        "create_other": (
            'CREATE TABLE download_data AS ( SELECT "id", "height", "geometry", TO_JSON("bbox") AS "bbox" '
            f"FROM read_parquet(['{OBM_HIT}']) {OVERTURE_BBOX_FILTER} )"
        ),
        "after_create": [],
        "geometry": "geometry",
        "results": {"geometry_column": "geometry", "bbox_column": "bbox", "has_bbox": True},
    },
    "sourcecoop_proxy": {
        "setup": [
            "SET s3_endpoint='data.source.coop';",
            "SET s3_use_ssl=true;",
            "SET s3_url_style='path';",
            "__SPATIAL_CHECK__",
            f"DESCRIBE SELECT * FROM read_parquet('{PROXY_S3}')",
            f"SELECT file FROM glob('{PROXY_S3}') LIMIT 1",
            "SELECT value FROM parquet_kv_metadata("
            "'s3://ondata/cadastral-italy-geospatial-data/ple/region=x/part_0.parquet') "
            "WHERE key = 'geo'::BLOB LIMIT 1",
            # listed, but part_N.parquet carries no extent: the glob is read
            f"SELECT file FROM glob('{PROXY_S3}')",
        ],
        # WKB in a BLOB with no bbox: nothing is filtered remotely...
        "create_parquet": f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet('{PROXY_S3}') )",
        "create_other": (
            'CREATE TABLE download_data AS ( SELECT "name", "geometry", array_to_string("tags", \', \') AS "tags" '
            f"FROM read_parquet('{PROXY_S3}') )"
        ),
        # ...the geometry is converted and filtered locally
        "after_create": [
            'CREATE TABLE download_data_converted AS SELECT "name", ST_GeomFromWKB("geometry") AS "geometry", '
            '"tags" FROM download_data WHERE ST_Intersects(ST_GeomFromWKB("geometry"), '
            f"ST_GeomFromText('{WINDOW_WKT}'))",
            "DROP TABLE download_data",
            "ALTER TABLE download_data_converted RENAME TO download_data",
        ],
        "geometry": "geometry",
        "results": {"geometry_column": "geometry", "bbox_column": None, "has_bbox": False},
    },
    "custom_https": {
        "setup": [
            f"DESCRIBE SELECT * FROM read_parquet('{CUSTOM_URL}')",
            # one file: no glob, no pruning
            f"SELECT value FROM parquet_kv_metadata('{CUSTOM_URL}') WHERE key = 'geo'::BLOB LIMIT 1",
        ],
        "create_parquet": (
            f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet('{CUSTOM_URL}') {GEOM_BBOX_FILTER} )"
        ),
        "create_other": (
            'CREATE TABLE download_data AS ( SELECT "id", "name", "geom", TO_JSON("geom_bbox") AS "geom_bbox", '
            'array_to_string("tags", \', \') AS "tags", TO_JSON("props") AS "props", '
            'CAST("level" AS INTEGER) AS "level" '
            f"FROM read_parquet('{CUSTOM_URL}') {GEOM_BBOX_FILTER} )"
        ),
        "after_create": [],
        "geometry": "geom",
        "results": {"geometry_column": "geom", "bbox_column": "geom_bbox", "has_bbox": True},
    },
    "minio": {
        "setup": [
            (
                "CREATE OR REPLACE TEMPORARY SECRET gpq_minio ( TYPE s3, PROVIDER config, KEY_ID '', SECRET '', "
                "REGION 'us-east-1', ENDPOINT ?, URL_STYLE 'path', USE_SSL ?, SCOPE ?)",
                ["minio.example.org:9000", True, "s3://lake/"],
            ),
            "__SPATIAL_CHECK__",
            f"DESCRIBE SELECT * FROM read_parquet('{MINIO_S3}')",
            f"SELECT file FROM glob('{MINIO_S3}') LIMIT 1",
            "SELECT value FROM parquet_kv_metadata('s3://lake/roads/0.parquet') WHERE key = 'geo'::BLOB LIMIT 1",
            f"SELECT file FROM glob('{MINIO_S3}')",
        ],
        # No covering: the geometry itself is tested
        "create_parquet": (
            f"CREATE TABLE download_data AS ( SELECT * FROM read_parquet('{MINIO_S3}') "
            f"WHERE ST_Intersects(\"geometry\", ST_GeomFromText('{WINDOW_WKT}')) )"
        ),
        "create_other": (
            'CREATE TABLE download_data AS ( SELECT "id", "geometry" '
            f"FROM read_parquet('{MINIO_S3}') "
            f"WHERE ST_Intersects(\"geometry\", ST_GeomFromText('{WINDOW_WKT}')) )"
        ),
        "after_create": [],
        "geometry": "geometry",
        "results": {"geometry_column": "geometry", "bbox_column": None, "has_bbox": False},
    },
}


def setup_statements(source):
    """The statements before the download; the spatial check comes after any
    URL configuration, which the sources without any leave out."""
    setup = GOLDEN[source]["setup"]
    if "__SPATIAL_CHECK__" in setup:
        return [SPATIAL_CHECK if s == "__SPATIAL_CHECK__" else s for s in setup]
    return [SPATIAL_CHECK] + setup


def progress(layer=""):
    return [
        ("progress", f"Connecting to database{layer}..."),
        ("progress", f"Loading spatial extension{layer}..."),
        ("progress", f"Preparing query{layer}..."),
        ("progress", f"Downloading{layer} data..."),
        ("progress", f"Processing{layer} data to requested format..."),
    ]


def statements(conn):
    return [squash(s) for s in conn.statements]


@pytest.mark.parametrize("fmt", ["parquet", "gpkg", "fgb"])
@pytest.mark.parametrize("source", sorted(GOLDEN))
def test_golden_sql(source, fmt, monkeypatch):
    golden = GOLDEN[source]
    output_file = f"{OUT}.{fmt}"
    worker, conn, signals = run_worker(SOURCES[source], output_file, monkeypatch)

    expected = (
        PREAMBLE
        + setup_statements(source)
        + [golden["create_parquet" if fmt == "parquet" else "create_other"]]
        + golden["after_create"]
        + [COUNT, copy(golden["geometry"], output_file, FORMAT_OPTIONS[fmt]), CLEANUP]
    )
    assert statements(conn) == expected
    assert signals == progress() + [("load_layer", output_file), ("finished",)]
    assert worker.validation_results == golden["results"]
    assert conn.closed and conn.connect_args == () and conn.commits == 0
    assert worker.conn is None


def test_read_url_is_the_resolved_url(monkeypatch):
    expected = {
        "overture": OVERTURE_GLOB,
        "sourcecoop": OBM_GLOB,
        "sourcecoop_proxy": PROXY_S3,
        "custom_https": CUSTOM_URL,
        "minio": MINIO_S3,
    }
    for name, read_url in expected.items():
        worker, _, _ = run_worker(SOURCES[name], f"{OUT}.parquet", monkeypatch)
        assert worker.read_url == read_url
        assert worker.dataset_url == SOURCES[name].url


def test_layer_name_appears_in_progress(monkeypatch):
    _, _, signals = run_worker(SOURCES["overture"], f"{OUT}.gpkg", monkeypatch, layer_name="Overture Buildings")
    assert signals == progress(" for Overture Buildings") + [("load_layer", f"{OUT}.gpkg"), ("finished",)]


def test_metadata_overrides_the_callers_hints_and_updates_its_dict(monkeypatch):
    hints = {"geometry_column": "geometry", "bbox_column": "bbox", "has_bbox": True, "extra": 1}
    worker, _, _ = run_worker(SOURCES["custom_https"], f"{OUT}.parquet", monkeypatch, validation_results=hints)
    assert worker.validation_results == {
        "geometry_column": "geom", "bbox_column": "geom_bbox", "has_bbox": True, "extra": 1,
    }


def test_overture_with_blob_geometry_drops_the_name_column(monkeypatch):
    """Oddity: on a DuckDB that reads Overture's geometry as a BLOB, the WKB
    conversion selects the file's own columns again, so the "name" column the
    non-GeoParquet outputs add is dropped."""
    schema = describe(
        ("id", "VARCHAR"), ("geometry", "BLOB"), ("bbox", "STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)"),
        ("names", 'STRUCT("primary" VARCHAR)'),
    )
    base = SOURCES["overture"]
    source = Source(base.url, schema, geo=base.geo, files=base.files, stac=base.stac)
    _, conn, _ = run_worker(source, f"{OUT}.gpkg", monkeypatch)
    assert statements(conn)[10:14] == [
        'CREATE TABLE download_data AS ( SELECT "names"."primary" as name,"id", "geometry", '
        'TO_JSON("bbox") AS "bbox", TO_JSON("names") AS "names" '
        f"FROM read_parquet(['{OVERTURE_PART_1}']) {OVERTURE_BBOX_FILTER} )",
        # the bbox covering has filtered remotely, so no ST_Intersects here
        'CREATE TABLE download_data_converted AS SELECT "id", ST_GeomFromWKB("geometry") AS "geometry", '
        '"bbox", "names" FROM download_data',
        "DROP TABLE download_data",
        "ALTER TABLE download_data_converted RENAME TO download_data",
    ]


def test_spatial_check_failure_reloads_spatial(monkeypatch):
    source = SOURCES["custom_https"]

    def respond(sql):
        if sql == SPATIAL_CHECK:
            raise duckdb.CatalogException("Scalar Function with name st_geomfromtext does not exist!")
        return source.respond(sql)

    _, conn, signals = run_worker(source, f"{OUT}.parquet", monkeypatch, responder=respond)
    got = statements(conn)
    assert got[: len(PREAMBLE) + 2] == PREAMBLE + [SPATIAL_CHECK, "LOAD spatial;"]
    assert got[len(PREAMBLE) + 2] == f"DESCRIBE SELECT * FROM read_parquet('{CUSTOM_URL}')"
    assert signals[-1] == ("finished",)


def test_no_file_in_the_window(monkeypatch):
    """Pruning that keeps no file stops before the download, as "no data"."""
    base = SOURCES["sourcecoop"]
    source = Source(base.url, base.schema, geo=base.geo, files=base.files[1:])  # neither covers the window
    _, conn, signals = run_worker(source, f"{OUT}.parquet", monkeypatch)
    east = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/building.122230.parquet"
    assert statements(conn) == PREAMBLE + setup_statements("sourcecoop")[:6] + [
        f"SELECT value FROM parquet_kv_metadata('{east}') WHERE key = 'geo'::BLOB LIMIT 1",
        f"SELECT file FROM glob('{OBM_GLOB}')",
        CLEANUP,
    ]
    no_data = (
        "No data found in the requested area. Check that your map extent overlaps with the data "
        "and/or expand your map extent. Skipping to next dataset if available."
    )
    assert signals == progress()[:3] + [("info", no_data), ("finished",)]


def test_no_rows_in_the_window(monkeypatch):
    base = SOURCES["custom_https"]
    source = Source(base.url, base.schema, geo=base.geo, row_count=0)
    _, conn, signals = run_worker(source, f"{OUT}.gpkg", monkeypatch)
    assert statements(conn) == PREAMBLE + setup_statements("custom_https") + [
        GOLDEN["custom_https"]["create_other"], COUNT, CLEANUP,
    ]
    no_data = (
        "No data found in the requested area. Check that your map extent overlaps with the data "
        "and/or expand your map extent. Skipping to next dataset if available."
    )
    assert signals == progress()[:4] + [("info", no_data), ("finished",)]


def test_an_early_error_is_emitted_and_the_connection_closed(monkeypatch):
    """Oddity: an error before the table name is assigned makes the cleanup's
    DROP TABLE raise NameError, which is logged and swallowed, so no DROP is
    issued. Nothing was created, so nothing leaks."""
    source = SOURCES["custom_https"]

    def respond(sql):
        if isinstance(sql, str) and sql.startswith("DESCRIBE SELECT"):
            raise duckdb.HTTPException("HTTP Error: HTTP GET error on 'https://example.com/data/rivers.parquet' (HTTP 404)")
        return source.respond(sql)

    _, conn, signals = run_worker(source, f"{OUT}.parquet", monkeypatch, responder=respond)
    assert statements(conn) == PREAMBLE + [
        SPATIAL_CHECK, f"DESCRIBE SELECT * FROM read_parquet('{CUSTOM_URL}')",
    ]
    assert signals == progress()[:2] + [
        ("error", "HTTP Error: HTTP GET error on 'https://example.com/data/rivers.parquet' (HTTP 404)"),
    ]
    assert conn.closed


def test_a_no_data_error_becomes_info(monkeypatch):
    source = SOURCES["custom_https"]

    def respond(sql):
        if isinstance(sql, str) and squash(sql).startswith("CREATE TABLE download_data AS"):
            raise duckdb.IOException("IO Error: No data found")
        return source.respond(sql)

    _, conn, signals = run_worker(source, f"{OUT}.parquet", monkeypatch, responder=respond)
    assert statements(conn)[-1] == CLEANUP
    assert signals[-2:] == [
        ("info", f"No data found in the requested area for {CUSTOM_URL}. Skipping to next dataset if available."),
        ("finished",),
    ]


def test_an_invalid_minio_url_is_an_error(monkeypatch):
    source = Source("minio://host", describe())
    _, conn, signals = run_worker(source, f"{OUT}.parquet", monkeypatch)
    assert statements(conn) == PREAMBLE  # and no DROP: see the test above
    assert signals[-1] == (
        "error",
        "Invalid MinIO URL: minio://host\nExpected minio://host[:port]/bucket/path "
        "(or minio+http:// for a server without TLS)",
    )


def test_clip_to_area(monkeypatch):
    base = SOURCES["custom_https"]
    # after the download the covering is JSON, as it is for every non-GeoParquet output
    table = describe(("id", "BIGINT"), ("geom", "GEOMETRY"), ("geom_bbox", "JSON"))
    source = Source(base.url, base.schema, geo=base.geo, table_schema=table)
    _, conn, signals = run_worker(source, f"{OUT}.gpkg", monkeypatch, clip=True)
    area = f"ST_GeomFromText('{WINDOW_WKT}')"
    assert statements(conn) == PREAMBLE + setup_statements("custom_https") + [
        GOLDEN["custom_https"]["create_other"],
        f'DELETE FROM download_data WHERE "geom" IS NULL OR NOT ST_Intersects("geom", {area}) '
        f'OR (NOT ST_CoveredBy("geom", {area}) AND ST_Dimension(ST_Intersection("geom", {area})) '
        '< ST_Dimension("geom"))',
        f'UPDATE download_data SET "geom" = ST_Intersection("geom", {area}) WHERE NOT ST_CoveredBy("geom", {area})',
        "DESCRIBE download_data",
        'UPDATE download_data SET "geom_bbox" = to_json(struct_pack(xmin := ST_XMin("geom"), '
        'ymin := ST_YMin("geom"), xmax := ST_XMax("geom"), ymax := ST_YMax("geom")))',
        COUNT,
        copy("geom", f"{OUT}.gpkg", FORMAT_OPTIONS["gpkg"]),
        CLEANUP,
    ]
    assert signals == progress()[:4] + [
        ("progress", "Clipping features to the area..."),
        progress()[4],
        ("load_layer", f"{OUT}.gpkg"),
        ("finished",),
    ]


GEOJSON_ESTIMATE = (
    "WITH sample AS ( SELECT * FROM download_data LIMIT {n} ) SELECT AVG(LENGTH( json_object( "
    "'type', 'Feature', 'geometry', ST_AsGeoJSON(\"geom\"), 'properties', json_object("
    "'id', COALESCE(CAST(\"id\" AS VARCHAR), 'null'), 'geom_bbox', COALESCE(CAST(\"geom_bbox\" AS VARCHAR), 'null')"
    ") )::VARCHAR )) as avg_feature_size FROM sample;"
)


def geojson_source(row_count):
    base = SOURCES["custom_https"]
    table = describe(("id", "BIGINT"), ("geom", "GEOMETRY"), ("geom_bbox", "JSON"))
    return Source(base.url, base.schema, geo=base.geo, table_schema=table, row_count=row_count,
                  avg_feature_size=200.0)


def test_geojson_estimates_the_size_first(monkeypatch):
    _, conn, signals = run_worker(geojson_source(3), f"{OUT}.geojson", monkeypatch)
    assert statements(conn) == PREAMBLE + setup_statements("custom_https") + [
        GOLDEN["custom_https"]["create_other"],
        COUNT,
        COUNT,  # again, inside estimate_file_size()
        "DESCRIBE download_data",
        GEOJSON_ESTIMATE.format(n=3),
        copy("geom", f"{OUT}.geojson", FORMAT_OPTIONS["geojson"]),
        CLEANUP,
    ]
    assert signals[-2:] == [("load_layer", f"{OUT}.geojson"), ("finished",)]


def test_geojson_over_4gb_stops_with_a_warning(monkeypatch):
    rows = 30_000_000  # 200 bytes each: about 5.6 GB
    _, conn, signals = run_worker(geojson_source(rows), f"{OUT}.geojson", monkeypatch)
    assert statements(conn)[-3:] == ["DESCRIBE download_data", GEOJSON_ESTIMATE.format(n=100), CLEANUP]
    assert signals == progress() + [
        ("file_size_warning", (rows * 200.0 + 50 + rows - 1) / (1024 * 1024)),
    ]


def test_geojson_over_4gb_proceeds_once_accepted(monkeypatch):
    def accept(worker):
        worker.size_warning_accepted = True

    _, conn, signals = run_worker(geojson_source(30_000_000), f"{OUT}.geojson", monkeypatch, before_run=accept)
    assert statements(conn)[-2:] == [copy("geom", f"{OUT}.geojson", FORMAT_OPTIONS["geojson"]), CLEANUP]
    assert signals[-2:] == [("load_layer", f"{OUT}.geojson"), ("finished",)]


def test_duckdb_output(monkeypatch):
    """The .duckdb file is the connection's database, committed and kept.

    Oddity: the "saved to DuckDB" info is emitted twice."""
    output_file = f"{OUT}.duckdb"
    _, conn, signals = run_worker(SOURCES["custom_https"], output_file, monkeypatch)
    assert conn.connect_args == (output_file,)
    assert statements(conn) == PREAMBLE + setup_statements("custom_https") + [
        GOLDEN["custom_https"]["create_other"], COUNT,
    ]
    assert conn.commits == 1 and conn.closed
    saved = (
        "Data has been successfully saved to DuckDB database.\n\n"
        "Note: QGIS does not currently support loading DuckDB files directly."
    )
    assert signals == progress() + [("info", saved), ("info", saved), ("finished",)]


def test_upper_case_parquet_extension_converts_columns(monkeypatch):
    """Oddity: the GeoParquet check for the SELECT is case-sensitive but the
    format check is not, so OUT.PARQUET is written as GeoParquet with its
    structs and lists turned into JSON and strings."""
    output_file = f"{OUT}.PARQUET"
    _, conn, signals = run_worker(SOURCES["custom_https"], output_file, monkeypatch)
    got = statements(conn)
    assert got[-4] == GOLDEN["custom_https"]["create_other"]
    assert got[-2] == copy("geom", output_file, FORMAT_OPTIONS["parquet"])
    assert signals[-2:] == [("load_layer", output_file), ("finished",)]


@pytest.mark.parametrize("ext", ["GPKG", "FGB", "GeoJSON", "shp"])
def test_unsupported_or_upper_case_extension(monkeypatch, ext):
    """Oddity: the GDAL formats are matched case-sensitively, so OUT.GPKG is
    "Unsupported file format." -- and then a second error, because the COPY
    is still built from the format options that were never set."""
    output_file = f"{OUT}.{ext}"
    source = geojson_source(3) if ext.lower() == "geojson" else SOURCES["custom_https"]
    _, conn, signals = run_worker(source, output_file, monkeypatch)
    got = statements(conn)
    assert got[-1] == CLEANUP
    assert not any(s.startswith("COPY") for s in got)
    errors = [s for s in signals if s[0] == "error"]
    assert errors[0] == ("error", "Unsupported file format.")
    assert len(errors) == 2 and "format_options" in errors[1][1]
    assert ("finished",) not in signals and not any(s[0] == "load_layer" for s in signals)


def test_quotes_in_the_url_and_output_path_are_escaped(monkeypatch):
    url = "https://example.com/it's/data.parquet"
    output_file = "/nonexistent/o'neil/out.parquet"
    source = Source(url, describe(("geometry", "GEOMETRY"), ("bbox", BBOX_DOUBLE)), geo=geo_metadata("geometry", "bbox"))
    _, conn, _ = run_worker(source, output_file, monkeypatch)
    got = statements(conn)
    assert got[len(PREAMBLE) + 1] == "DESCRIBE SELECT * FROM read_parquet('https://example.com/it''s/data.parquet')"
    assert got[-2].endswith(
        "TO '/nonexistent/o''neil/out.parquet' (FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 22);"
    )


def test_the_name_column_is_only_added_for_overture_urls(monkeypatch):
    """Another source with a names struct gets no extra column."""
    source = Source(CUSTOM_URL, describe(("geometry", "GEOMETRY"), ("names", 'STRUCT("primary" VARCHAR)')))
    _, conn, _ = run_worker(source, f"{OUT}.gpkg", monkeypatch)
    assert statements(conn)[len(PREAMBLE) + 3] == (
        'CREATE TABLE download_data AS ( SELECT "geometry", TO_JSON("names") AS "names" '
        f"FROM read_parquet('{CUSTOM_URL}') WHERE ST_Intersects(\"geometry\", ST_GeomFromText('{WINDOW_WKT}')) )"
    )


def test_the_overture_name_column_test_is_by_substring(monkeypatch):
    """Oddity: "overture" anywhere in the URL and "names" anywhere in a column
    name are enough, so a file with a street_names column and no names struct
    is read with "names"."primary", which DuckDB would reject."""
    url = "https://example.com/overture-extract.parquet"
    source = Source(url, describe(("geometry", "GEOMETRY"), ("street_names", "VARCHAR")))
    _, conn, _ = run_worker(source, f"{OUT}.gpkg", monkeypatch)
    assert statements(conn)[len(PREAMBLE) + 3] == (
        'CREATE TABLE download_data AS ( SELECT "names"."primary" as name,"geometry", "street_names" '
        f"FROM read_parquet('{url}') WHERE ST_Intersects(\"geometry\", ST_GeomFromText('{WINDOW_WKT}')) )"
    )
