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
