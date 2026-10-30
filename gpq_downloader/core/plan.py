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
