def classFactory(iface):
    """Load the plugin class.

    Nothing imported here needs DuckDB, so the plugin loads, and shows its
    toolbar button, even when DuckDB is missing. The plugin then offers to
    install it (see duckdb_setup.py).
    """
    from .duckdb_setup import add_user_site_packages

    add_user_site_packages()
    from .plugin import QgisPluginGeoParquet

    return QgisPluginGeoParquet(iface)
