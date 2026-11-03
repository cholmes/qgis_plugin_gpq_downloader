"""The trailing options of a DuckDB ``COPY ... TO`` statement.

worker.py used to hardcode ``"(FORMAT 'parquet', COMPRESSION 'ZSTD',
COMPRESSION_LEVEL 22);"`` as a literal string. That hid two things a settings
panel needs to change: the codec and its level. It also hid a DuckDB
constraint that is easy to violate once those become configurable:
COMPRESSION_LEVEL is only accepted alongside the ZSTD codec. DuckDB raises
``Binder Error: Compression level is only supported for the ZSTD compression
codec`` for any other codec, so this module omits the clause rather than
emit it unconditionally.

The default level dropped from 22 to 15: measured against level 19, level 22
bought 0.04 MB at a cost of 1.6 extra seconds, which is not a trade worth
making the default.
"""

from typing import Optional

CODECS = ("ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4", "UNCOMPRESSED")
DEFAULT_CODEC = "ZSTD"
DEFAULT_COMPRESSION_LEVEL = 15
MIN_COMPRESSION_LEVEL = 1
MAX_COMPRESSION_LEVEL = 22
DEFAULT_SPATIAL_SORT = True

# Extensions DuckDB writes through its GDAL copy function rather than its
# native Parquet writer, and the OGR driver name each one needs.
_GDAL_DRIVERS = {
    ".gpkg": "GPKG",
    ".fgb": "FlatGeobuf",
    ".geojson": "GeoJSON",
}


def format_options(
    output_file: str,
    *,
    codec: str = DEFAULT_CODEC,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    row_group_size: Optional[int] = None,
) -> Optional[str]:
    """The ``(...)`` clause and trailing ``;`` for a ``COPY ... TO`` of this file.

    Returns None for an extension this function is not asked to write for:
    ``.duckdb`` (the worker commits the connection instead of running a
    COPY) and anything else the plugin cannot produce, such as ``.shp``.
    """
    extension = _extension(output_file)

    if extension == ".parquet":
        return _parquet_options(codec, compression_level, row_group_size)

    driver = _GDAL_DRIVERS.get(extension)
    if driver is not None:
        return f"(FORMAT GDAL, DRIVER '{driver}', SRS 'EPSG:4326');"

    return None


def _extension(output_file: str) -> str:
    dot = output_file.rfind(".")
    if dot == -1:
        return ""
    return output_file[dot:].lower()


def _parquet_options(
    codec: str, compression_level: int, row_group_size: Optional[int]
) -> str:
    if codec not in CODECS:
        codec = DEFAULT_CODEC

    clauses = ["FORMAT 'parquet'", f"COMPRESSION '{codec}'"]

    if codec == "ZSTD":
        level = max(MIN_COMPRESSION_LEVEL, min(MAX_COMPRESSION_LEVEL, compression_level))
        clauses.append(f"COMPRESSION_LEVEL {level}")

    if row_group_size is not None and row_group_size > 0:
        clauses.append(f"ROW_GROUP_SIZE {row_group_size}")

    return f"({', '.join(clauses)});"


def codec_error_advice(error_text: str, codec: str) -> Optional[str]:
    """A readable replacement for a DuckDB codec/compression error.

    Returns None when ``error_text`` is not about compression at all, so the
    caller can fall back to showing DuckDB's own message unchanged.
    """
    if "compression" not in error_text.lower() and codec not in error_text:
        return None

    return (
        f"DuckDB could not write with the {codec} compression codec: "
        f"{error_text}. Choose a different codec under "
        f"Settings → Options → GeoParquet Downloader."
    )
