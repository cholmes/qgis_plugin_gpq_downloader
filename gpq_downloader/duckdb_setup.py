"""Find DuckDB, or install it into QGIS's Python, without breaking plugin load.

The plugin's modules import without DuckDB (utils.py falls back to duckdb=None),
so QGIS always loads the plugin and shows its toolbar button. When DuckDB is
missing or too old, the user gets a message with an "Install DuckDB" button
instead of a ModuleNotFoundError at startup.
"""

import importlib
import os
import platform
import site
import subprocess
import sys

from qgis.core import QgsApplication, QgsTask

from . import logger

MIN_VERSION = (1, 1, 0)


def add_user_site_packages():
    """Put the user site-packages dir, where pip --user installs, on sys.path.

    QGIS on Windows and macOS can start Python without it, so a DuckDB that pip
    reported as installed still could not be imported (#75, #123).
    """
    try:
        user_site = site.getusersitepackages()
    except Exception:
        return
    if user_site and os.path.isdir(user_site) and user_site not in sys.path:
        site.addsitedir(user_site)


def _parse_version(text):
    parts = []
    for piece in str(text).split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def duckdb_status():
    """Return (module or None, version string or None, usable)."""
    add_user_site_packages()
    try:
        import duckdb
    except ImportError:
        return None, None, False
    version = getattr(duckdb, "__version__", "0")
    return duckdb, version, _parse_version(version) >= MIN_VERSION


def activate(module):
    """Give the plugin's worker module the DuckDB module, so no restart is needed."""
    from . import utils

    utils.duckdb = module


def python_executable():
    """The Python interpreter QGIS runs, to call pip with.

    Inside QGIS sys.executable is often the QGIS binary itself, not Python.
    """
    candidates = []
    name = os.path.basename(sys.executable).lower()
    if name.startswith("python"):
        candidates.append(sys.executable)
    if platform.system() == "Windows":
        candidates += [
            os.path.join(sys.exec_prefix, "python.exe"),
            os.path.join(os.path.dirname(sys.executable), "python.exe"),
            os.path.join(os.path.dirname(sys.executable), "python3.exe"),
        ]
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        exe_dir = os.path.dirname(sys.executable)
        candidates += [
            os.path.join(exe_dir, version),  # QGIS.app/Contents/MacOS/python3.12
            os.path.join(exe_dir, "python3"),
            os.path.join(sys.exec_prefix, "bin", version),
            os.path.join(sys.exec_prefix, "bin", "python3"),
            os.path.join(exe_dir, "bin", "python3"),
            os.path.join(exe_dir, "..", "Resources", "python", "bin", "python3"),
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return sys.executable


def pip_command(python):
    return [python, "-m", "pip", "install", "--user", "--upgrade", "duckdb>=%d.%d.%d" % MIN_VERSION]


def failure_advice(output):
    """What to tell the user after pip failed, based on what pip printed."""
    tips = []
    if "externally-managed-environment" in output:
        tips.append(
            "Your system Python is managed by the operating system, so pip may not "
            "install into it. Install the QDuckDB plugin, which bundles DuckDB, or run:\n"
            "    pip install --user --break-system-packages duckdb"
        )
    elif "No module named pip" in output:
        tips.append("This QGIS has no pip. Install the QDuckDB plugin, which bundles DuckDB.")
    else:
        tips.append("Install the QDuckDB plugin, which bundles DuckDB, then restart QGIS.")
    return "\n\n".join(tips)


class DuckDBInstallerTask(QgsTask):
    """Run pip in the background so QGIS stays responsive."""

    def __init__(self, on_done):
        flag = QgsTask.CanCancel if hasattr(QgsTask, "CanCancel") else QgsTask.Flag.CanCancel
        super().__init__("Installing DuckDB", flag)
        self.on_done = on_done
        self.output = ""
        self.command = []

    def run(self):
        self.command = pip_command(python_executable())
        logger.log(f"Installing DuckDB: {' '.join(self.command)}")
        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                self.command, capture_output=True, text=True, timeout=600, **kwargs
            )
        except Exception as e:
            self.output = str(e)
            return False
        self.output = (result.stdout or "") + (result.stderr or "")
        logger.log(self.output[-4000:])
        return result.returncode == 0

    def finished(self, result):
        importlib.invalidate_caches()
        self.on_done(bool(result), self.output, self.command)


_task = None  # keep a reference so the task is not garbage collected while it runs


def install(on_done):
    """Start installing DuckDB; on_done(ok, output, command) runs on the main thread."""
    global _task
    _task = DuckDBInstallerTask(on_done)
    QgsApplication.taskManager().addTask(_task)
    return _task
