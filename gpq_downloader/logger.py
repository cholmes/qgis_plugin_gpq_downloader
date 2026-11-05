import logging

from qgis.core import Qgis, QgsMessageLog

# The threshold below which log()/debug() go silent (#93). This is plain
# module state, not a QgsSettings read: logger.py is imported (indirectly, via
# qgis_io/worker.py) by code that must not import gui/, so plugin.py and
# SettingsPage.apply() push the stored level in with set_level() instead of
# logger.py reading it for itself.
_LEVEL_RANKS = {"Debug": 0, "Info": 1, "Warning": 2, "Critical": 3}
# What _core_logger.setLevel() takes for each name; Critical maps to
# logging.ERROR, which _MessageLogHandler below already treats as Critical.
_LOGGING_LEVELS = {
    "Debug": logging.DEBUG,
    "Info": logging.INFO,
    "Warning": logging.WARNING,
    "Critical": logging.ERROR,
}

_level = "Info"


def set_level(name: str) -> None:
    """Set the threshold that log()/debug() and _core_logger obey together.

    An unknown name sets Info rather than raising: a settings file written by
    another version of the plugin must not break logging.
    """
    global _level
    _level = name if name in _LEVEL_RANKS else "Info"
    _core_logger.setLevel(_LOGGING_LEVELS[_level])


def current_level() -> str:
    return _level


def message_level(name):
    """Qgis.Warning on QGIS 3, Qgis.MessageLevel.Warning where only the scoped
    name exists. Qt6 rejects the plain ints these used to be."""
    return getattr(Qgis, name) if hasattr(Qgis, name) else getattr(Qgis.MessageLevel, name)


def _emit(message: str, rank: int, level) -> None:
    """Log message at the QGIS level `level`, unless rank is below the
    current threshold."""
    if rank < _LEVEL_RANKS[_level]:
        return
    QgsMessageLog.logMessage(str(message), "GeoParquet Downloader", level)


def log(message: str, level_in: int = 0):
    if level_in == 1:
        rank, level = _LEVEL_RANKS["Warning"], Qgis.MessageLevel.Warning
    elif level_in == 2:
        rank, level = _LEVEL_RANKS["Critical"], Qgis.MessageLevel.Critical
    else:
        rank, level = _LEVEL_RANKS["Info"], Qgis.MessageLevel.Info

    _emit(message, rank, level)


def debug(message: str) -> None:
    """The rung below Info. QGIS has no Debug level, so this emits at
    Qgis.MessageLevel.Info, but only when the threshold is Debug."""
    _emit(message, _LEVEL_RANKS["Debug"], Qgis.MessageLevel.Info)


class _MessageLogHandler(logging.Handler):
    """Hands the core package's log records to log() above.

    gpq_downloader.core cannot import this module (it imports qgis), so code
    moved there logs through the standard logging module instead (#195). This
    keeps those messages in the QGIS message log, at the same level, and only
    there.
    """

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            level_in = 2
        elif record.levelno >= logging.WARNING:
            level_in = 1
        else:
            level_in = 0
        log(record.getMessage(), level_in)


# The installed plugin's package is not called gpq_downloader, so the core
# logger's name is taken from this package's.
_core_logger = logging.getLogger(f"{__package__}.core")
_core_logger.setLevel(logging.INFO)
_core_logger.propagate = False
# A plugin reload runs this module again; replace the handler, don't add a second.
for _handler in list(_core_logger.handlers):
    if type(_handler).__name__ == _MessageLogHandler.__name__:
        _core_logger.removeHandler(_handler)
_core_logger.addHandler(_MessageLogHandler())
