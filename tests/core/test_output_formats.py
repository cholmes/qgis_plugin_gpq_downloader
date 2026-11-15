from unittest import mock

import pytest
from gpq_downloader.core import copy_options, output_formats as of


def test_the_dialog_filter_string_is_the_one_the_plugin_shows_today():
    assert of.dialog_filters() == (
        "GeoParquet (*.parquet);;DuckDB Database (*.duckdb);;GeoPackage (*.gpkg);;"
        "FlatGeobuf (*.fgb);;GeoJSON (*.geojson)"
    )


def test_each_name_maps_to_its_extension():
    assert [(f.name, f.extension) for f in of.FORMATS] == [
        ("GeoParquet", ".parquet"), ("DuckDB Database", ".duckdb"),
        ("GeoPackage", ".gpkg"), ("FlatGeobuf", ".fgb"), ("GeoJSON", ".geojson"),
    ]


def test_an_unknown_stored_name_falls_back_to_geoparquet():
    assert of.format_for("Shapefile").extension == ".parquet"
    assert of.format_for("").name == of.DEFAULT_FORMAT_NAME

@pytest.mark.parametrize("fmt", of.FORMATS, ids=lambda f: f.name)
def test_every_offered_format_can_actually_be_written(fmt):
    """A format in the dropdown must be one copy_options knows how to write.

    Adding a sixth format to FORMATS without teaching _GDAL_DRIVERS about it
    would offer it in the panel, name the file with it, run the whole
    download, and only then say "Unsupported file format."
    .duckdb is the one exception: the worker commits the connection instead
    of running a COPY, so copy_options is never asked for a clause.
    """
    options = copy_options.format_options(f"/tmp/out{fmt.extension}")
    if fmt.extension == ".duckdb":
        assert options is None
    else:
        assert options is not None and options.endswith(");")


@pytest.mark.parametrize("path, expected", [
    ("/tmp/a.parquet", "GeoParquet"),
    ("/tmp/a.duckdb", "DuckDB Database"),
    ("/tmp/a.gpkg", "GeoPackage"),
    ("/tmp/a.fgb", "FlatGeobuf"),
    ("/tmp/a.geojson", "GeoJSON"),
    # Typed by hand in the save dialog, and written: the plugin takes
    # uppercase extensions, so remembering them has to as well.
    ("/tmp/A.GPKG", "GeoPackage"),
    ("/tmp/My Data.GeoJSON", "GeoJSON"),
])
def test_a_written_file_is_matched_to_the_format_it_is(path, expected):
    assert of.format_for_path(path).name == expected


@pytest.mark.parametrize("path", ["/tmp/a.shp", "/tmp/a", "/tmp/parquet", ""])
def test_an_extension_we_do_not_write_is_not_a_format(path):
    """None, not a GeoParquet guess.

    format_for() falls back to GeoParquet because a stale *setting* should
    stay recoverable. This answers a different question - "what did we just
    write?" - where guessing would mean remembering a format that was never
    used, and preselecting it on the next download.
    """
    assert of.format_for_path(path) is None


def test_the_size_warnings_alternatives_are_the_binary_formats():
    """Every format but GeoJSON and DuckDB, in table order.

    Not GeoJSON, which is what triggered the warning, and not a DuckDB
    database, which is not an interchange file. Derived from FORMATS by
    exclusion, so a format added to the table shows up here on its own.
    """
    assert [f.name for f in of.large_dataset_alternatives()] == [
        "GeoParquet", "GeoPackage", "FlatGeobuf",
    ]
    assert [f.dialog_filter for f in of.large_dataset_alternatives()] == [
        "GeoParquet (*.parquet)", "GeoPackage (*.gpkg)", "FlatGeobuf (*.fgb)",
    ]


def test_a_format_added_to_the_table_reaches_the_alternatives_unasked():
    """The point of deriving by exclusion: no second list to forget."""
    geoarrow = of.OutputFormat("GeoArrow", ".arrow", "GeoArrow (*.arrow)")
    with mock.patch.object(of, "FORMATS", of.FORMATS + (geoarrow,)):
        assert geoarrow in of.large_dataset_alternatives()


def test_the_excluded_names_actually_name_table_rows():
    """A rename of GeoJSON or DuckDB in FORMATS must break here, not ship a
    warning dialog that offers GeoJSON as an alternative to itself."""
    table_names = {f.name for f in of.FORMATS}
    assert set(of.LARGE_DATASET_EXCLUDED) <= table_names
    assert of.large_dataset_alternatives()
