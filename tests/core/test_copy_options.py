import pytest

from gpq_downloader.core import copy_options as co


def test_the_parquet_default_is_zstd_at_level_15():
    assert co.format_options("/out/x.parquet") == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15);"
    )


@pytest.mark.parametrize("codec", ["SNAPPY", "GZIP", "BROTLI", "LZ4", "UNCOMPRESSED"])
def test_no_compression_level_clause_for_a_non_zstd_codec(codec):
    options = co.format_options("/out/x.parquet", codec=codec, compression_level=15)
    assert options == f"(FORMAT 'parquet', COMPRESSION '{codec}');"
    assert "COMPRESSION_LEVEL" not in options


def test_an_unknown_codec_falls_back_to_zstd():
    assert co.format_options("/out/x.parquet", codec="SQUEEZE") == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15);"
    )


@pytest.mark.parametrize("size", [None, 0, -1])
def test_no_row_group_size_clause_when_it_is_unset_or_not_positive(size):
    assert "ROW_GROUP_SIZE" not in co.format_options("/out/x.parquet", row_group_size=size)


def test_a_positive_row_group_size_is_emitted_last():
    assert co.format_options("/out/x.parquet", row_group_size=15000) == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15, ROW_GROUP_SIZE 15000);"
    )


@pytest.mark.parametrize("path, expected", [
    ("/out/x.gpkg", "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');"),
    ("/out/x.fgb", "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');"),
    ("/out/x.geojson", "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');"),
    ("/out/X.GPKG", "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');"),
])
def test_the_gdal_formats_take_no_compression_options(path, expected):
    assert co.format_options(path, codec="SNAPPY", row_group_size=15000) == expected


def test_an_extension_the_plugin_cannot_write_has_no_options():
    assert co.format_options("/out/x.shp") is None


def test_a_compression_error_is_reported_with_the_codec_and_where_to_change_it():
    advice = co.codec_error_advice("Binder Error: Unsupported compression codec BROTLI", "BROTLI")
    assert "BROTLI" in advice and "Settings" in advice


def test_an_unrelated_error_is_left_alone():
    assert co.codec_error_advice("IO Error: no such file", "ZSTD") is None


@pytest.mark.parametrize("path, expected", [
    ("/tmp/out.parquet", ".parquet"),
    ("/tmp/OUT.GeoJSON", ".geojson"),
    # The point of splitting the basename off first: rfind(".") over the
    # whole path reads these as extensions of ".data/output" and ".5/out".
    ("/my.data/output", ""),
    ("/projects/v2.5/out", ""),
    ("/my.data/out.gpkg", ".gpkg"),
    ("/tmp/plain", ""),
])
def test_the_extension_comes_from_the_file_name_not_the_path(path, expected):
    assert co._extension(path) == expected
