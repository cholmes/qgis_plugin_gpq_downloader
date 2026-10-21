import logging

from qgis.core import Qgis, QgsMessageLog


def message_level(name):
    """Qgis.Warning on QGIS 3, Qgis.MessageLevel.Warning where only the scoped
    name exists. Qt6 rejects the plain ints these used to be."""
    return getattr(Qgis, name) if hasattr(Qgis, name) else getattr(Qgis.MessageLevel, name)


def log(message: str, level_in: int = 0):
    if level_in == 0:
        level = Qgis.MessageLevel.Info
    elif level_in == 1:
        level = Qgis.MessageLevel.Warning
    elif level_in == 2:
        level = Qgis.MessageLevel.Critical
    else:
        level = Qgis.MessageLevel.Info

    QgsMessageLog.logMessage(str(message), "GeoParquet Downloader", level)


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
