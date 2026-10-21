"""Find DuckDB, or install it into QGIS's Python, without breaking plugin load.

The plugin's modules import without DuckDB (utils.py falls back to duckdb=None),
so QGIS always loads the plugin and shows its toolbar button. When DuckDB is
missing or too old, the user gets a message with an "Install DuckDB" button
instead of a ModuleNotFoundError at startup.
"""

from __future__ import annotations

import importlib
import os
import platform
import re
import site
import subprocess
import sys

from qgis.core import QgsApplication, QgsTask

from . import logger

MIN_VERSION = (1, 1, 0)

# DuckDB 2.0, out since 2026-10-21, segfaults the QGIS 4 test run at shutdown
# once the plugin has used it, so an install started from here stays on 1.x
# until that is understood (#203). A DuckDB the user already has is still used
# whatever its version -- 2.0 works, as far as anything before shutdown goes --
# so this caps what the plugin installs, not what it accepts.
BELOW_VERSION = (2, 0)


def add_user_site_packages() -> None:
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
        # addsitedir appends, so a too-old DuckDB bundled in QGIS's own
        # site-packages would still win over the pip --user upgrade. Move the
        # user dir ahead of it, matching normal Python's path order.
        if user_site in sys.path:
            sys.path.remove(user_site)
            sys.path.insert(1, user_site)


def _parse_version(text) -> tuple[int, int, int]:
    parts = []
    for piece in str(text).split(".")[:3]:
        m = re.match(r"\d+", piece)  # "0rc1" counts as 0, not 1
        parts.append(int(m.group()) if m else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def duckdb_status():
    """Return (module or None, version string or None, usable)."""
    add_user_site_packages()
    try:
        import duckdb
    except ImportError:
        return None, None, False
    except Exception as e:
        # A broken install (half-upgraded, corrupt) must not stop plugin load
        logger.log(f"DuckDB import failed: {e}", 2)
        return None, None, False
    version = getattr(duckdb, "__version__", "0")
    return duckdb, version, _parse_version(version) >= MIN_VERSION


def activate(module) -> None:
    """Give the plugin's worker module the DuckDB module, so no restart is needed."""
    from . import utils

    utils.duckdb = module


def python_executable() -> str:
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


def pip_command(python: str) -> list[str]:
    # --user fails inside a venv-based QGIS (some Linux distros, conda)
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    user_flag = [] if in_venv else ["--user"]
    spec = (
        "duckdb>=" + ".".join(str(n) for n in MIN_VERSION)
        + ",<" + ".".join(str(n) for n in BELOW_VERSION)
    )
    return [python, "-m", "pip", "install", *user_flag, "--upgrade", spec]


def failure_advice(output: str) -> str:
    """What to tell the user after pip failed, based on what pip printed."""
    if "externally-managed-environment" in output:
        return (
            "Your system Python is managed by the operating system, so pip may not "
            "install into it. Install the QDuckDB plugin, which bundles DuckDB, or run:\n"
            "    pip install --user --break-system-packages duckdb"
        )
    if "No module named pip" in output:
        return "This QGIS has no pip. Install the QDuckDB plugin, which bundles DuckDB."
    if "not visible in this virtualenv" in output:
        return (
            "This QGIS runs Python in a virtual environment that pip --user cannot "
            "install into. Run 'pip install duckdb' in that environment, or install "
            "the QDuckDB plugin, which bundles DuckDB."
        )
    return "Install the QDuckDB plugin, which bundles DuckDB, then restart QGIS."


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
        global _task
        importlib.invalidate_caches()
        try:
            self.on_done(bool(result), self.output, self.command)
        finally:
            if _task is self:
                _task = None


_task = None  # keep a reference so the task is not garbage collected while it runs


def is_installing() -> bool:
    """True while an install started by install() is still queued or running."""
    return _task is not None and _task.isActive()


def install(on_done):
    """Start installing DuckDB; on_done(ok, output, command) runs on the main thread.

    A second call while an install is running returns the running task instead
    of racing a second pip process against it.
    """
    global _task
    if is_installing():
        return _task
    _task = DuckDBInstallerTask(on_done)
    QgsApplication.taskManager().addTask(_task)
    return _task


def detach() -> None:
    """Keep a running install from calling back into an unloaded plugin."""
    if _task is not None:
        _task.on_done = lambda ok, output, command: None
