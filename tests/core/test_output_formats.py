from gpq_downloader.core import output_formats as of


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
