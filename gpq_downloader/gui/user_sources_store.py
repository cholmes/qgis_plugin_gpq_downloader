"""Where the user's custom-sources config lives on disk, and how it is
loaded and saved.

user_config_path() points at user_sources.json inside this QGIS profile's
gpq_downloader folder (QgsApplication.qgisSettingsDirPath()) rather than at
QgsSettings: a hand-editable, shareable file suits custom URLs and hidden
datasets better than a registry key does. core.user_sources owns the shape
of the config and never touches disk; this module is the one place that
does, so load_user_config()/save_user_config() are the only functions here.

tests/qgis/conftest.py's autouse isolated_user_sources fixture monkeypatches
user_config_path() for every qgis test, so none of them ever reads or writes
the developer's real profile.
"""

import os
import tempfile

from qgis.core import QgsApplication

from gpq_downloader import logger
from gpq_downloader.core.user_sources import (
    default_config,
    parse_config,
    serialize_config,
)


def user_config_path():
    """Where user_sources.json lives in this QGIS profile."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(), "gpq_downloader", "user_sources.json"
    )


def load_user_config():
    """The user's sanitized config, or default_config() if there is none.

    A missing file is the ordinary case for a user who has never customized
    anything, so that is silent. A file that exists but cannot be read or
    decoded as a valid config is not silently discarded without a trace: it
    is logged once, then treated the same as a missing file, so the dialog
    can still open.
    """
    path = user_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return default_config()
    except OSError as exc:
        logger.log(f"Could not read user sources config at {path}: {exc}")
        return default_config()

    try:
        return parse_config(text)
    except ValueError as exc:
        logger.log(f"Could not read user sources config at {path}: {exc}")
        return default_config()


def save_user_config(config):
    """Write config to user_config_path(), creating its folder if needed.

    The write lands in a temp file in the same directory first and is then
    os.replace()'d into place, so a crash mid-write cannot leave a
    half-written, unparseable user_sources.json behind for the next load.
    """
    path = user_config_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialize_config(config))
        os.replace(temp_path, path)
    except Exception:
        os.remove(temp_path)
        raise
