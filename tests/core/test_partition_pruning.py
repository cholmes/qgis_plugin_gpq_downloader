"""Characterization of parquet_source_for_bbox(): which files a download reads.

This is the completeness hazard of partition pruning. Every path that returns
the plain glob (the safe fallback) is pinned as well as every path that prunes,
because a refactor that prunes where it used to fall back silently drops data.
"""

import duckdb
import pytest

from core_support import RecordingConnection, Rect
from gpq_downloader import utils
from gpq_downloader.utils import parquet_source_for_bbox

WINDOW = Rect(10, 10, 11, 11)

OBM = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/"
OBM_GLOB = OBM + "*.parquet"
# zoom-6 tiles: 122221 holds the window, 122230 is its eastern neighbour
# (starting at 11.25 E), 021230 is Seattle
HIT, EAST, SEATTLE = (OBM + f"building.{k}.parquet" for k in ("122221", "122230", "021230"))

OVERTURE = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/"
OVERTURE_GLOB = OVERTURE + "*"
OVERTURE_KEY = ("2026-09-23.1", "buildings", "building")
OVERTURE_EXTENTS = {
    0: (-180.0, -84.29, -85.87, 13.99),   # the Americas
    1: (8.99, 48.69, 11.07, 51.36),       # Germany
    2: (140.01, 39.66, 179.68, 77.11),    # Japan
}
FRANKFURT = Rect(9.1252, 50.2389, 9.1514, 50.2439)


def part(number):
    return f"{OVERTURE}part-{number:05d}-23bda295-5d48-57ef-aa31-bce815c998ac-c000.zstd.parquet"


def lister(files):
    """A connection whose glob() lists `files`."""
    def respond(sql):
        assert sql.startswith("SELECT file FROM glob("), sql
        return [(f,) for f in files]
    return RecordingConnection(respond)


def glob_sql(url):
    return f"SELECT file FROM glob({utils.sql_str(url)})"


def quoted(url):
    return "'" + url.replace("'", "''") + "'"


def listing(*files):
    return "[" + ", ".join(quoted(f) for f in files) + "]"


@pytest.fixture
def overture_stac():
    """The STAC result for the buildings collection, already cached."""
    utils._overture_extent_cache[OVERTURE_KEY] = dict(OVERTURE_EXTENTS)


# --- When nothing is listed -------------------------------------------------

@pytest.mark.parametrize("url, bbox", [
    (OBM_GLOB, None),                                   # no window
    ("https://example.com/a.parquet", WINDOW),          # one file
    (OBM + "building.122221.parquet", WINDOW),
    (OVERTURE + "part-00001-x.zstd.parquet", WINDOW),   # an Overture file, not a glob
    ("s3://bucket/data/*", WINDOW),                     # a glob that does not end in .parquet
    ("s3://bucket/data/**", WINDOW),
    # Overture addresses: type=* cannot be looked up in the STAC, and the glob
    # does not end in .parquet
    ("s3://overturemaps-us-west-2/release/2026-09-23.1/theme=addresses/type=*/*", WINDOW),
])
def test_returns_the_url_without_listing(url, bbox):
    conn = lister([])
    assert parquet_source_for_bbox(conn, url, bbox) == quoted(url)
    assert conn.statements == []


def test_overture_without_stac_falls_back_without_listing(monkeypatch):
    def unreachable(url, timeout=None):
        raise OSError("no network")

    monkeypatch.setattr(utils.urllib.request, "urlopen", unreachable)
    conn = lister([part(1)])
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, FRANKFURT) == quoted(OVERTURE_GLOB)
    assert conn.statements == []


# --- Listing fails or finds nothing -----------------------------------------

@pytest.mark.parametrize("error", [
    RuntimeError("HTTP 403"),
    duckdb.IOException("no such bucket"),
    # Oddity: a cancel that lands in the listing is swallowed here too; the
    # Worker's next execute() is what raises it
    duckdb.InterruptException("INTERRUPT Error: Interrupted!"),
])
def test_listing_error_falls_back(error):
    def respond(sql):
        raise error

    conn = RecordingConnection(respond)
    assert parquet_source_for_bbox(conn, OBM_GLOB, WINDOW) == quoted(OBM_GLOB)
    assert conn.statements == [glob_sql(OBM_GLOB)]


def test_empty_listing_falls_back():
    conn = lister([])
    assert parquet_source_for_bbox(conn, OBM_GLOB, WINDOW) == quoted(OBM_GLOB)
    assert conn.statements == [glob_sql(OBM_GLOB)]


# --- Pruning by file name ---------------------------------------------------

@pytest.mark.parametrize("files, bbox, kwargs, expected", [
    # Only the tile under the window
    ([HIT, EAST, SEATTLE], WINDOW, {}, listing(HIT)),
    # The 0.01 degree buffer pulls in a tile the window only approaches...
    ([HIT, EAST, SEATTLE], Rect(11.245, 10, 11.249, 10.5), {}, listing(HIT, EAST)),
    # ...unless the buffer is turned off
    ([HIT, EAST, SEATTLE], Rect(11.245, 10, 11.249, 10.5), {"buffer_deg": 0}, listing(HIT)),
    # The selection is sorted, whatever order the listing came in
    ([SEATTLE, EAST, HIT], Rect(11.245, 10, 11.249, 10.5), {}, listing(HIT, EAST)),
    # Every file overlaps: the glob itself, not a list of every file
    ([HIT, EAST], Rect(11.245, 10, 11.249, 10.5), {}, quoted(OBM_GLOB)),
    # No file overlaps: None, which the Worker reports as "No data found"
    ([EAST, SEATTLE], WINDOW, {}, None),
])
def test_quadkey_pruning(files, bbox, kwargs, expected):
    conn = lister(files)
    assert parquet_source_for_bbox(conn, OBM_GLOB, bbox, **kwargs) == expected
    assert conn.statements == [glob_sql(OBM_GLOB)]


@pytest.mark.parametrize("files", [
    # Two zoom levels
    [OBM + "building.12222.parquet", HIT],
    # Zoom below 5
    [OBM + "building.1222.parquet", OBM + "building.0212.parquet"],
    # Two prefixes
    [HIT, OBM + "house.021230.parquet"],
    # The same name in two folders (country=*/part_00000.parquet style)
    ["s3://b/x/a/building.122221.parquet", "s3://b/x/b/building.122221.parquet"],
    # One name that decodes and one that does not
    [HIT, OBM + "building.extra.parquet"],
    # Names like years are not quadkeys
    [f"s3://bucket/data/data_{y}.parquet" for y in (2020, 2021, 2022, 2023)],
    # Plain numbered files
    ["s3://bucket/data/0.parquet", "s3://bucket/data/1.parquet"],
])
def test_names_that_are_not_one_tiling_fall_back(files):
    conn = lister(files)
    assert parquet_source_for_bbox(conn, OBM_GLOB, WINDOW) == quoted(OBM_GLOB)


def test_quadkeys_and_grid_cells_in_one_glob_are_pruned_together():
    """Oddity: the one-tiling check only looks at the names that are quadkeys,
    so grid-cell names alongside them are decoded and pruned as well."""
    grid_hit = OBM + "e010_n15_e015_n10.parquet"
    grid_miss = OBM + "e100_n20_e105_n15.parquet"
    conn = lister([HIT, SEATTLE, grid_hit, grid_miss])
    assert parquet_source_for_bbox(conn, OBM_GLOB, WINDOW) == listing(HIT, grid_hit)


GBA = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/"


def test_grid_pruning():
    names = ["e010_n15_e015_n10", "e005_n15_e010_n10", "e010_n20_e015_n15", "w170_n65_w165_n60"]
    conn = lister([GBA + f"{n}.parquet" for n in names])
    # Exactly on the corner of four cells, the buffer reaches three of them; the
    # fourth (e005_n10...) is not listed
    assert parquet_source_for_bbox(conn, GBA + "*.parquet", Rect(10, 15, 10.5, 15.5)) == listing(
        GBA + "e005_n15_e010_n10.parquet", GBA + "e010_n15_e015_n10.parquet", GBA + "e010_n20_e015_n15.parquet",
    )


VIDA = "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country_s2/"


def test_s2_pruning():
    tha = VIDA + "country_iso=THA/3544332906740580352.parquet"
    lao = VIDA + "country_iso=LAO/3746994889972252672.parquet"
    per = VIDA + "country_iso=PER/10466365534009032704.parquet"
    nzl = VIDA + "country_iso=NZL/-6052837899185946624.parquet"
    conn = lister([tha, lao, per, nzl])
    nan_thailand = Rect(100.76, 18.77, 100.79, 18.80)
    assert parquet_source_for_bbox(conn, VIDA + "*/*.parquet", nan_thailand) == listing(lao, tha)


def test_quotes_in_file_names_are_escaped():
    url = "s3://b/it's/*.parquet"
    hit, miss = "s3://b/it's/building.122221.parquet", "s3://b/it's/building.021230.parquet"
    conn = lister([hit, miss])
    assert parquet_source_for_bbox(conn, url, WINDOW) == "['s3://b/it''s/building.122221.parquet']"
    assert conn.statements == ["SELECT file FROM glob('s3://b/it''s/*.parquet')"]


def test_real_duckdb_glob_on_local_files(tmp_path):
    """The same pruning against DuckDB's own glob() of a local folder."""
    for key in ("122221", "122230", "021230"):
        (tmp_path / f"building.{key}.parquet").write_bytes(b"")
    conn = duckdb.connect()
    try:
        source = parquet_source_for_bbox(conn, f"{tmp_path}/*.parquet", WINDOW)
    finally:
        conn.close()
    assert source == f"['{tmp_path}/building.122221.parquet']"


# --- Pruning by the Overture STAC -------------------------------------------

@pytest.mark.parametrize("files, bbox, expected", [
    ([part(0), part(1), part(2)], FRANKFURT, listing(part(1))),
    # Every file overlaps: the glob
    ([part(0), part(1), part(2)], Rect(-180, -90, 180, 90), quoted(OVERTURE_GLOB)),
    # None overlaps (the Indian Ocean)
    ([part(0), part(1), part(2)], Rect(70.0, -30.0, 71.0, -29.0), None),
    # A file the collection does not describe must not be skipped
    ([part(1), part(400)], FRANKFURT, quoted(OVERTURE_GLOB)),
    # Neither may one whose name is not part-NNNNN-...
    ([part(1), OVERTURE + "_SUCCESS"], FRANKFURT, quoted(OVERTURE_GLOB)),
    ([part(1), OVERTURE + "part-1-short.parquet"], FRANKFURT, quoted(OVERTURE_GLOB)),
    # The listing order does not matter
    ([part(2), part(1), part(0)], Rect(9, 49, 141, 50), listing(part(1), part(2))),
])
def test_overture_pruning(overture_stac, files, bbox, expected):
    conn = lister(files)
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, bbox) == expected
    assert conn.statements == [glob_sql(OVERTURE_GLOB)]


def test_overture_pruning_ignores_file_name_extents(overture_stac):
    """Overture URLs are pruned only by part number: a quadkey-looking name is
    not decoded, and with no part number it makes the whole glob the answer."""
    conn = lister([part(1), OVERTURE + "building.122221.parquet"])
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, FRANKFURT) == quoted(OVERTURE_GLOB)


def test_overture_pruning_buffer(overture_stac):
    # Germany's file ends at 11.07 E; a window starting 0.005 degrees east of it
    # is still within the 0.01 degree buffer, so that file is read
    conn = lister([part(0), part(1), part(2)])
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, Rect(11.075, 50, 11.08, 50.1)) == listing(part(1))
    conn = lister([part(0), part(1), part(2)])
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, Rect(11.085, 50, 11.09, 50.1)) is None
