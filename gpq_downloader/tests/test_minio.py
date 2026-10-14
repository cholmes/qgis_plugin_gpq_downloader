import duckdb
import pytest

from gpq_downloader.utils import resolve_dataset_url


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs;")
    yield c
    c.close()


def minio_secret(conn):
    rows = conn.execute(
        "SELECT secret_string, scope FROM duckdb_secrets() WHERE name = 'gpq_minio'"
    ).fetchall()
    return rows[0] if rows else None


def test_minio_url_reads_bucket_from_that_server(conn):
    url = resolve_dataset_url(conn, "minio://data.example.org/cadencemaps/release/x/*")
    assert url == "s3://cadencemaps/release/x/*"
    secret, scope = minio_secret(conn)
    assert "endpoint=data.example.org" in secret
    assert "url_style=path" in secret
    assert "use_ssl=true" in secret
    assert scope == ["s3://cadencemaps/"]


@pytest.mark.parametrize("url", ["minio://host:80/b/k.parquet", "minio+http://host:9000/b/k.parquet"])
def test_minio_without_tls(conn, url):
    assert resolve_dataset_url(conn, url) == "s3://b/k.parquet"
    assert "use_ssl=false" in minio_secret(conn)[0]


def test_minio_on_other_port_uses_tls(conn):
    resolve_dataset_url(conn, "minio://host:9000/b/k.parquet")
    assert "use_ssl=true" in minio_secret(conn)[0]


@pytest.mark.parametrize("url", [
    "minio://host",
    "minio://host/",
    "minio://host:abc/b/k.parquet",
    "minio://h';CREATE TABLE pwned AS SELECT 1;--/b/k.parquet",
])
def test_invalid_minio_urls_are_rejected(conn, url):
    with pytest.raises(ValueError, match="Invalid MinIO URL"):
        resolve_dataset_url(conn, url)
    assert conn.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'pwned'").fetchone()[0] == 0


def test_other_urls_are_unchanged(conn):
    for url in ["https://example.com/a.parquet", "s3://overturemaps-us-west-2/release/x/*"]:
        assert resolve_dataset_url(conn, url) == url
    assert minio_secret(conn) is None
