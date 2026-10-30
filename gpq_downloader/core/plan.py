"""Download planning that needs no QGIS: what to call the files a download writes.

Moved here from plugin.py (#195), which still imports custom_file_stem from
here, so gpq_downloader.plugin.custom_file_stem keeps working.
"""

import re


def custom_file_stem(url):
    """A file name stem for a custom URL: the file's name, or the folder a glob reads.

    https://host/data/rivers.parquet -> rivers
    s3://bucket/cadastre/ple/**/*.parquet -> ple
    s3://bucket/places/country=*/part-0.parquet -> part-0
    """
    path = url.split("?", 1)[0].split("#", 1)[0].split("://", 1)[-1].rstrip("/")
    for part in reversed(path.split("/")[1:]):  # [0] is the host or bucket
        if "*" in part:
            continue  # a glob says nothing about the data
        if "=" in part and not re.search(r"\.(geo)?parquet$|\.pq$", part, flags=re.IGNORECASE):
            continue  # a hive partition key like country=US, unless it is a file name
        stem = re.sub(r"\.(geo)?parquet$|\.pq$", "", part, flags=re.IGNORECASE)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
        if stem:
            return stem
    return "custom_download"


# Presets whose type is told apart from the rest of their theme by name:
# building_part shares theme=buildings with building, and the three divisions
# subtypes share theme=divisions, so naming by theme alone would give those
# downloads the same layer and file name - and two in the same second the
# same default path, the second overwriting the first.
_OVERTURE_TYPE_NAMES = {
    "building_part": "Building Parts",
    "division": "Divisions",
    "division_area": "Division Areas",
    "division_boundary": "Division Boundaries",
}


def _overture_theme_and_type(url):
    """(theme, type) from an Overture URL; either is None when the URL lacks it."""
    theme = url.split("theme=")[1].split("/")[0] if "theme=" in url else None
    type_str = url.split("type=")[1].split("/")[0] if theme and "type=" in url else None
    return theme, type_str


def overture_file_stem(url):
    """A file name stem for an Overture preset URL, without the date.

    .../theme=buildings/type=building/* -> overture_buildings
    .../theme=buildings/type=building_part/* -> overture_building_parts
    .../theme=base/type=water/* -> overture_base_water
    """
    theme, type_str = _overture_theme_and_type(url)
    if type_str in _OVERTURE_TYPE_NAMES:
        return "overture_" + _OVERTURE_TYPE_NAMES[type_str].lower().replace(" ", "_")
    if theme == "base" and type_str:
        return f"overture_base_{type_str}"
    if theme is None:
        return "overture"
    return f"overture_{theme}"


def overture_layer_name(url):
    """The QGIS layer name for an Overture preset URL, None if it has no theme.

    .../theme=buildings/type=building/* -> Overture Buildings
    .../theme=buildings/type=building_part/* -> Overture Building Parts
    .../theme=base/type=water/* -> Overture Base - Water
    """
    theme, type_str = _overture_theme_and_type(url)
    if theme is None:
        return None
    if type_str in _OVERTURE_TYPE_NAMES:
        return f"Overture {_OVERTURE_TYPE_NAMES[type_str]}"
    if theme == "base" and type_str:
        # For base layers, include the subtype
        return f"Overture {theme.title()} - {type_str.title()}"
    return f"Overture {theme.title()}"
