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

import contextlib
import os
import tempfile

from qgis.core import QgsApplication

from gpq_downloader import logger
from gpq_downloader.core.presets import load_presets
from gpq_downloader.core.user_sources import (
    default_config,
    parse_config,
    serialize_config,
    source_cooperative_group,
)


def user_config_path():
    """Where user_sources.json lives in this QGIS profile."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(), "gpq_downloader", "user_sources.json"
    )


def load_user_config():
    """The user's sanitized config, or a seeded/default one if there is none.

    A missing file is the ordinary case for a user who has never customized
    anything - first run - so it is seeded with the "Source Cooperative"
    custom group built from the bundled presets, rather than left empty:
    Source Cooperative is no longer a hardcoded built-in, and this is what
    gives it to a new user without a line of SC-specific code anywhere else
    (core/user_sources.py's source_cooperative_group()).

    A file that exists but cannot be read, decoded (invalid UTF-8 raises
    UnicodeDecodeError, a ValueError subclass), or parsed as a valid config is
    not silently discarded without a trace: it is logged once, then treated
    as plain default_config() - NOT reseeded - so a user who deleted the
    Source Cooperative group (or everything else) does not get it handed back
    by a corrupt read of their own file.
    """
    path = user_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return parse_config(text)
    except FileNotFoundError:
        config = default_config()
        config["custom_sources"] = [source_cooperative_group(load_presets())]
        return config
    except (OSError, ValueError) as exc:
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
        # The temp file may already be gone (e.g. os.fdopen() itself failed
        # to write it) - suppress that so the original exception propagates
        # rather than being masked by a FileNotFoundError from the cleanup.
        with contextlib.suppress(OSError):
            os.remove(temp_path)
        raise
