"""Quoting for the SQL the plugin builds as f-strings.

DuckDB's Python API cannot parameterise a table name, a column name or the
path read_parquet() reads, so the queries interpolate them, and every value
that did not come from the plugin's own source goes through sql_str() or
sql_ident() first. The B608 suppressions on those f-strings each assert that
a line has been checked against that rule (see the note in utils.py).

Moved here from utils.py (#195), which still re-exports both.
"""


def sql_str(value) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_ident(name) -> str:
    """Quote a name as a SQL identifier.

    Column names come from the remote file's own schema and GeoParquet
    metadata, so a name containing a double quote must not be able to
    terminate the identifier and smuggle SQL into the query.
    """
    return '"' + str(name).replace('"', '""') + '"'
