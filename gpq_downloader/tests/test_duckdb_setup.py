"""The plugin loads without DuckDB (#180, #165, #75, #123) and offers to install it."""

import os
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

from gpq_downloader import duckdb_setup
from gpq_downloader.plugin import QgisPluginGeoParquet, custom_file_stem

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_plugin_loads_and_adds_its_button_without_duckdb():
    """Import the plugin with `import duckdb` failing, as on a fresh QGIS install."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.modules["duckdb"] = None  # makes `import duckdb` raise ImportError
        sys.path.insert(0, {REPO!r})
        sys.path.insert(0, {os.path.join(REPO, "gpq_downloader", "tests")!r})
        from unittest.mock import MagicMock
        from qgis.core import QgsApplication
        app = QgsApplication([], False)
        app.initQgis()
        from conftest import MockIface
        import gpq_downloader
        iface = MockIface()
        iface.messageBar = MagicMock()
        plugin = gpq_downloader.classFactory(iface)
        plugin.initGui()
        from gpq_downloader import utils
        assert utils.duckdb is None
        assert len(iface.toolbar_icons) == 1, "toolbar button missing"
        assert iface.messageBar.return_value.pushWidget.called, "no install offer shown"
        print("OK")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert "OK" in result.stdout, result.stdout + result.stderr


def test_check_duckdb_passes_with_duckdb(mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    assert plugin.check_duckdb() is True


def test_duckdb_status_and_version_parsing():
    module, version, usable = duckdb_setup.duckdb_status()
    assert module is not None and usable
    assert duckdb_setup._parse_version("1.1.0") == (1, 1, 0)
    assert duckdb_setup._parse_version("1.0.0") < duckdb_setup.MIN_VERSION
    assert duckdb_setup._parse_version("1.5.0.dev123") == (1, 5, 0)


def test_python_executable_is_a_python():
    path = duckdb_setup.python_executable()
    assert os.path.isfile(path)
    out = subprocess.run([path, "-c", "import sys; print(sys.version_info[:2])"], capture_output=True, text=True)
    assert out.stdout.strip() == str(tuple(sys.version_info[:2]))


def test_pip_command_upgrades_into_user_site():
    cmd = duckdb_setup.pip_command("/x/python3")
    assert cmd[:6] == ["/x/python3", "-m", "pip", "install", "--user", "--upgrade"]
    assert cmd[-1] == "duckdb>=1.1.0"


def test_failure_advice():
    assert "--break-system-packages" in duckdb_setup.failure_advice("error: externally-managed-environment")
    assert "QDuckDB" in duckdb_setup.failure_advice("No module named pip")
    assert "QDuckDB" in duckdb_setup.failure_advice("something else")


def test_install_finished_failure_shows_advice(mock_iface, monkeypatch):
    plugin = QgisPluginGeoParquet(mock_iface)
    mock_iface.messageBar = MagicMock()
    shown = []
    monkeypatch.setattr(duckdb_setup, "duckdb_status", lambda: (None, None, False))
    monkeypatch.setattr(
        "gpq_downloader.plugin.QMessageBox.exec", lambda box: shown.append((box.text(), box.informativeText()))
    )
    plugin.duckdb_install_finished(False, "error: externally-managed-environment", ["python", "-m", "pip"])
    assert shown and "--break-system-packages" in shown[0][1]


def test_custom_file_stem():
    assert custom_file_stem("https://host/data/rivers.parquet") == "rivers"
    assert custom_file_stem("s3://ondata/cadastral-italy/ple/**/*.parquet") == "ple"
    assert custom_file_stem("s3://bucket/places/country=*/part-0.parquet") == "part-0"
    assert custom_file_stem("s3://bucket/a/b.geoparquet") == "b"
    assert custom_file_stem("https://host/x.parquet?token=1") == "x"
    assert custom_file_stem("s3://bucket/*.parquet") == "custom_download"
    assert custom_file_stem("https://host/my file (1).parquet") == "my_file_1"
