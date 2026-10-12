from unittest.mock import MagicMock

from qgis.core import QgsRectangle

from gpq_downloader.utils import (
    configure_s3_for_url,
    parquet_source_for_bbox,
    quadkeys_for_bbox,
)

SOURCE_COOP_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet"
PREFIX = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/"


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


def test_quadkeys_for_point():
    # Seattle and Nan, Thailand at zoom 6
    assert quadkeys_for_bbox(-122.33, 47.61, -122.33, 47.61, 6) == {"021230"}
    assert quadkeys_for_bbox(100.76, 18.77, 100.79, 18.80, 6) == {"132201"}


def test_quadkeys_for_bbox_spanning_tiles():
    # The zoom-1 tiles meet at (0, 0)
    assert quadkeys_for_bbox(-1, -1, 1, 1, 1) == {"0", "1", "2", "3"}


def mock_glob(files):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(f,) for f in files]
    return conn


def test_parquet_source_prunes_quadkey_files():
    conn = mock_glob([PREFIX + f"building.{k}.parquet" for k in ("132201", "132203", "021230")])
    source = parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(100.76, 18.77, 100.79, 18.80))
    assert source == f"['{PREFIX}building.132201.parquet']"


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
