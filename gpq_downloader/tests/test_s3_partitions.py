from unittest.mock import MagicMock

from qgis.core import QgsRectangle

from gpq_downloader.utils import (
    configure_s3_for_url,
    extent_from_file_name,
    parquet_source_for_bbox,
    quadkey_bounds,
)

SOURCE_COOP_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet"
PREFIX = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/"
GBA_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/*.parquet"
GBA_PREFIX = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/"
NAN_THAILAND = (100.76, 18.77, 100.79, 18.80)


def executed_sql(conn):
    return [c.args[0] for c in conn.execute.call_args_list]


def test_configure_s3_dotted_bucket_uses_path_style():
    conn = MagicMock()
    configure_s3_for_url(conn, SOURCE_COOP_GLOB)
    sql = executed_sql(conn)
    assert "SET s3_url_style='path';" in sql
    assert "SET s3_endpoint='s3.us-west-2.amazonaws.com';" in sql


def test_configure_s3_leaves_other_urls_alone():
    for url in [
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*",
        "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
        "",
        None,
    ]:
        conn = MagicMock()
        configure_s3_for_url(conn, url)
        conn.execute.assert_not_called()


def contains(extent, x, y):
    xmin, ymin, xmax, ymax = extent
    return xmin <= x <= xmax and ymin <= y <= ymax


def test_quadkey_bounds():
    # Seattle and Nan, Thailand fall in these zoom-6 tiles
    assert contains(quadkey_bounds("021230"), -122.33, 47.61)
    assert contains(quadkey_bounds("132201"), 100.77, 18.78)
    assert not contains(quadkey_bounds("132201"), -122.33, 47.61)
    # Zoom-1 tile "3" is the south-east quarter of the world
    xmin, ymin, xmax, ymax = quadkey_bounds("3")
    assert (xmin, xmax) == (0.0, 180.0)
    assert ymax == 0.0 and ymin < -85


def test_extent_from_grid_file_name():
    assert extent_from_file_name("e100_n20_e105_n15.parquet") == (100, 15, 105, 20)
    assert extent_from_file_name("w170_n65_w165_n60.parquet") == (-170, 60, -165, 65)
    assert extent_from_file_name("e100_n00_e105_s05.parquet") == (100, -5, 105, 0)


def test_extent_from_file_name_ignores_other_names():
    for name in ["0.parquet", "part_1.parquet", "THA.parquet", "building.012.parquet",
                 "part-00000-170892fe-0fe0-43c1-999d-f0911ce43365-c000.zstd.parquet"]:
        assert extent_from_file_name(name) is None


def mock_glob(files):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(f,) for f in files]
    return conn


def test_parquet_source_prunes_quadkey_files():
    conn = mock_glob([PREFIX + f"building.{k}.parquet" for k in ("132201", "132203", "021230")])
    source = parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(100.76, 18.77, 100.79, 18.80))
    assert source == f"['{PREFIX}building.132201.parquet']"


def test_parquet_source_prunes_grid_files():
    names = ["e100_n20_e105_n15", "e095_n20_e100_n15", "e100_n25_e105_n20", "w170_n65_w165_n60"]
    conn = mock_glob([GBA_PREFIX + f"{n}.parquet" for n in names])
    source = parquet_source_for_bbox(conn, GBA_GLOB, QgsRectangle(*NAN_THAILAND))
    assert source == f"['{GBA_PREFIX}e100_n20_e105_n15.parquet']"


def test_parquet_source_includes_neighbour_within_buffer():
    # An extent touching the 100° E cell edge also reads the western neighbour
    names = ["e100_n20_e105_n15", "e095_n20_e100_n15"]
    conn = mock_glob([GBA_PREFIX + f"{n}.parquet" for n in names])
    source = parquet_source_for_bbox(conn, GBA_GLOB, QgsRectangle(100.005, 18.0, 100.1, 18.1))
    assert source.count(".parquet") == 2


def test_parquet_source_falls_back_for_mixed_quadkey_lengths():
    conn = mock_glob([PREFIX + "building.1322.parquet", PREFIX + "building.132201.parquet"])
    assert parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(*NAN_THAILAND)) == f"'{SOURCE_COOP_GLOB}'"


def test_parquet_source_no_overlap_returns_none():
    conn = mock_glob([PREFIX + "building.021230.parquet"])
    assert parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(100.76, 18.77, 100.79, 18.80)) is None


def test_parquet_source_falls_back_for_other_file_names():
    url = "s3://bucket/by_country/*/*.parquet"
    conn = mock_glob(["s3://bucket/by_country/THA/THA.parquet", "s3://bucket/by_country/LAO/LAO.parquet"])
    assert parquet_source_for_bbox(conn, url, QgsRectangle(100, 18, 101, 19)) == f"'{url}'"


def test_parquet_source_skips_listing_for_single_files():
    conn = MagicMock()
    url = "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet"
    assert parquet_source_for_bbox(conn, url, QgsRectangle(0, 0, 1, 1)) == f"'{url}'"
    conn.execute.assert_not_called()
