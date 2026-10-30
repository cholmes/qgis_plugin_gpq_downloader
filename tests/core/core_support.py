"""Make gpq_downloader.utils and qgis_io importable without QGIS, and the test
doubles the headless tests share. conftest.py imports this before any test
module, so the stand-ins are in place before gpq_downloader is first imported.

utils.py and qgis_io/ import qgis.core and qgis.PyQt.QtCore at module level.
The logic these tests pin (URL resolution, partition pruning, metadata
parsing, the SQL the Worker issues) does not need QGIS, so when qgis cannot be
imported a few minimal stand-ins are put in sys.modules before gpq_downloader
is imported.

When the real qgis is importable (the QGIS CI containers) nothing is stubbed,
and the same tests run against it. So the stubs only provide what these tests
touch, and behave the way QGIS does for it: anything else raises, so a test
that starts leaning on a stub fails here instead of passing for the wrong
reason.
"""

import os
import re
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _install_qgis_stubs():
    class _BoundSignal:
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def disconnect(self, slot=None):
            if slot is None:
                self._slots.clear()
            else:
                self._slots.remove(slot)

        def emit(self, *args):
            for slot in list(self._slots):
                slot(*args)

    class pyqtSignal:
        """Class attribute that hands each instance its own connect/emit signal."""

        def __init__(self, *types_):
            self._name = None

        def __set_name__(self, owner, name):
            self._name = name

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            key = f"_stub_signal_{self._name}"
            if key not in obj.__dict__:
                obj.__dict__[key] = _BoundSignal()
            return obj.__dict__[key]

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class _Unavailable:
        """A QGIS class the stubbed tests must not reach."""

        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                f"{type(self).__name__} needs the real QGIS; this test belongs in gpq_downloader/tests"
            )

        @classmethod
        def instance(cls):
            raise NotImplementedError(f"{cls.__name__} needs the real QGIS")

        @classmethod
        def fromWkt(cls, *args):
            raise NotImplementedError(f"{cls.__name__} needs the real QGIS")

    class QgsCoordinateReferenceSystem:
        """Only construction from an auth id and comparison are supported."""

        def __init__(self, definition=""):
            if not isinstance(definition, str):
                raise NotImplementedError("only an auth id string is stubbed")
            self._authid = definition

        def authid(self):
            return self._authid

        def isValid(self):
            return bool(self._authid)

        def __eq__(self, other):
            return isinstance(other, QgsCoordinateReferenceSystem) and other._authid == self._authid

        def __ne__(self, other):
            return not self == other

        def __hash__(self):
            return hash(self._authid)

    class QgsCoordinateTransform(_Unavailable):
        pass

    class QgsProject(_Unavailable):
        pass

    class QgsGeometry(_Unavailable):
        pass

    class _MessageLevel:
        Info, Warning, Critical, Success = 0, 1, 2, 3

    class Qgis:
        MessageLevel = _MessageLevel

    class QgsMessageLog:
        @staticmethod
        def logMessage(message, tag="", level=None):
            pass

    qgis = types.ModuleType("qgis")
    qgis.__path__ = []  # a package, so the submodules below can be imported
    core = types.ModuleType("qgis.core")
    for cls in (
        QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
        QgsGeometry, Qgis, QgsMessageLog,
    ):
        setattr(core, cls.__name__, cls)
    pyqt = types.ModuleType("qgis.PyQt")
    pyqt.__path__ = []
    qtcore = types.ModuleType("qgis.PyQt.QtCore")
    qtcore.QObject = QObject
    qtcore.pyqtSignal = pyqtSignal
    qgis.core, qgis.PyQt, pyqt.QtCore = core, pyqt, qtcore
    sys.modules.update({
        "qgis": qgis, "qgis.core": core, "qgis.PyQt": pyqt, "qgis.PyQt.QtCore": qtcore,
    })


try:
    import qgis.core  # noqa: F401
    import qgis.PyQt.QtCore  # noqa: F401

    QGIS_STUBBED = False
except ImportError:
    _install_qgis_stubs()
    QGIS_STUBBED = True


from gpq_downloader.qgis_io.crs import QgsCoordinateReferenceSystem  # noqa: E402


class Rect:
    """What the Worker reads from a QgsRectangle. QgsRectangle stores doubles,
    so the coordinates are floats here too: they are formatted into the SQL."""

    def __init__(self, xmin, ymin, xmax, ymax):
        self._coords = tuple(float(v) for v in (xmin, ymin, xmax, ymax))

    def xMinimum(self):
        return self._coords[0]

    def yMinimum(self):
        return self._coords[1]

    def xMaximum(self):
        return self._coords[2]

    def yMaximum(self):
        return self._coords[3]


class FakeIface:
    """iface whose map canvas is in EPSG:4326, so the Worker transforms nothing."""

    def __init__(self):
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        settings = types.SimpleNamespace(destinationCrs=lambda: crs)
        canvas = types.SimpleNamespace(mapSettings=lambda: settings)
        self.mapCanvas = lambda: canvas


class Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class RecordingConnection:
    """Stands in for a DuckDB connection and records every statement.

    `responder(sql)` returns the rows for a statement; a statement recorded with
    parameters is stored as a (sql, params) tuple.
    """

    def __init__(self, responder=None):
        self.statements = []
        self.responder = responder or (lambda sql: [])
        self.closed = False
        self.commits = 0
        self.interrupts = 0

    def execute(self, sql, params=None):
        self.statements.append(sql if params is None else (sql, params))
        return Result(self.responder(sql))

    def commit(self):
        self.commits += 1

    def interrupt(self):
        self.interrupts += 1

    def close(self):
        self.closed = True


_QUOTED = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")")


def squash(sql):
    """Collapse whitespace outside quoted literals and identifiers.

    The golden tests compare every token of the SQL; only the indentation of the
    multi-line f-strings in qgis_io/worker.py is not pinned.
    """
    if isinstance(sql, tuple):
        return (squash(sql[0]), sql[1])
    parts = _QUOTED.split(sql)
    out = []
    for i, part in enumerate(parts):
        out.append(part if i % 2 else re.sub(r"\s+", " ", part))
    return "".join(out).strip()
