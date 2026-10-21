"""Re-exports the DuckDB setup, which moved to gpq_downloader/qgis_io/duckdb_setup.py (#195).

Existing `from gpq_downloader.duckdb_setup import ...` uses keep working. The
running install task is module state of qgis_io.duckdb_setup, so patch that
module, not this one. The shim is deleted at 1.0 (#198); import from
qgis_io.duckdb_setup instead.
"""

from .qgis_io.duckdb_setup import (  # noqa: F401
    BELOW_VERSION,
    MIN_VERSION,
    DuckDBInstallerTask,
    activate,
    add_user_site_packages,
    detach,
    duckdb_status,
    failure_advice,
    install,
    is_installing,
    pip_command,
    python_executable,
)
