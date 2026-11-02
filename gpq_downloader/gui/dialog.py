"""The data source dialog: which data to download, and from where.

The source pages (Overture, Source Cooperative, OpenStreetMap, a custom URL),
the custom URL's validation, and remembering the checkboxes between sessions.
The Area of Interest group is AreaOfInterestMixin, in extent.py. Moved here
from gpq_downloader/dialog.py (#195), which re-exports its names until 1.0
(#198).
"""

import json

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QCompleter,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QProgressDialog,
    QRadioButton,
    QStackedWidget,
    QWidget,
    QCheckBox,
    QGridLayout,
)
from qgis.PyQt.QtGui import QStandardItem, QStandardItemModel
from qgis.PyQt.QtCore import pyqtSignal, QEvent, Qt, QTimer, QUrl

from .. import logger
from ..qgis_io.manager import QUEUED, WorkerTask, submit
from ..qgis_io.worker import SchemaWorker, ValidationWorker
from ..utils import MINIO_URL_FORMAT
from ..core.filters import (
    column_feedback,
    completion_candidates,
    completion_text,
    current_word,
    dataset_label,
    shared_columns,
    validate_where_fragment,
)
from ..core.schema import field_candidates
from ..core.presets import load_presets
from . import settings_store
from .extent import AoiHighlighter, AreaOfInterestMixin

# How many OpenStreetMap preset checkboxes go on a row before wrapping.
OSM_CHECKBOX_COLUMNS = 5

# How many Overture preset checkboxes go on a row before wrapping.
OVERTURE_CHECKBOX_COLUMNS = 3

# Overture presets whose checkbox label is not just the key title-cased
OVERTURE_DISPLAY_NAMES = {"building_parts": "Building Parts"}

# Overture's STAC catalog, whose "latest" names the newest release
OVERTURE_CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
RELEASE_LOOKUP_TIMEOUT_MS = 30000

# Left in an Overture URL in place of the release while a selection is only
# being counted and keyed: putting the real release in is a network lookup
# (latest_overture_release), which the schema task does on its own thread.
RELEASE_PLACEHOLDER = "{release}"

# How long after the selection last changed the column list is read. Ticking
# three checkboxes in a row is one selection worth a DESCRIBE, not three.
SCHEMA_FETCH_DELAY_MS = 500

# Beside the checkbox: whether a filter can be had, and what it covers. A
# filter applies to every selected dataset, so only the columns they all have
# can be used - a condition on a column one of them lacks is not a condition
# that one can run (core/filters.shared_columns).
FILTER_NEEDS_A_DATASET = "Select a dataset to filter it."
FILTER_ACROSS_DATASETS = "applies to all {count} selected datasets"

# Under the field: what is wrong with the condition, or what it can complete.
FILTER_READING_COLUMNS = "Reading the column list..."
FILTER_COLUMNS_READY = "{count} columns available - press Tab to complete a name."
FILTER_SHARED_COLUMNS = (
    "{count} columns shared by all {datasets} datasets - press Tab to complete a name."
)
FILTER_NO_SHARED_COLUMNS = (
    "The {datasets} selected datasets have no columns in common, so no "
    "condition applies to all of them."
)
FILTER_NO_COLUMNS = "Could not read the column list, so column names are not checked."

# A half-typed fragment trips the lexical check on its way through: the
# closing quote or parenthesis simply has not been typed yet. Those read as
# "not finished", not as the "this will not run" of a ';' or a SELECT.
_INCOMPLETE_HINTS = ("unclosed", "without a matching")

# The in-plugin help the owner asked for, in the order that matters: the
# quote trap first, because that is the one that actually bit. Plugin-authored
# static text, with nothing interpolated into it, so the rich text is safe;
# it carries no links and reaches no network.
FILTER_HELP_HTML = """
<h3>Writing an attribute filter</h3>
<p>A filter is one SQL condition on the dataset&rsquo;s own columns.
<b>Tab</b> in the field lists the column names, and <b>Tab</b> after a
<code>.</code> lists the fields of a struct.</p>

<h4>Single quotes for text, not double</h4>
<p>In SQL <b>'single quotes' are a text value</b> and <b>"double quotes" are
a column name</b>. A value written in double quotes is read as a column, and
the column does not exist:</p>
<table cellpadding="4">
<tr><td><code>names.primary = 'Jack Park'</code></td><td>&nbsp;correct</td></tr>
<tr><td><code>names.primary = "Jack Park"</code></td>
    <td>&nbsp;wrong: <i>Referenced column "Jack Park" not found</i></td></tr>
</table>
<p>To include an apostrophe in a value, double it:
<code>names.primary = 'Jack''s Park'</code>.</p>

<h4>Sub-properties</h4>
<p>A dot reads a field of a struct:</p>
<ul>
<li><code>names.primary = 'Jack Park'</code></li>
<li><code>bbox.xmin &gt; 7</code></li>
<li><code>cartography.min_zoom &lt;= 10</code></li>
</ul>

<h4>Lists need an index</h4>
<p>A column whose type ends in <code>[]</code> is a list. Dot access does not
apply to a list; index an element first:</p>
<table cellpadding="4">
<tr><td><code>sources[1].property = 'name'</code></td><td>&nbsp;correct</td></tr>
<tr><td><code>sources.property = 'name'</code></td>
    <td>&nbsp;wrong: <i>Cannot extract field ... it is not a struct</i></td></tr>
</table>

<h4>Matching text</h4>
<ul>
<li><code>subtype = 'locality'</code> &mdash; equal to</li>
<li><code>names.primary LIKE 'Jack%'</code> &mdash; starts with Jack
    (<code>%</code> matches any run of characters)</li>
<li><code>names.primary ILIKE '%park%'</code> &mdash; contains park,
    disregarding case</li>
<li><code>subtype IN ('locality', 'county')</code> &mdash; equal to any of
    the listed values</li>
<li><code>names.primary IS NOT NULL</code> &mdash; not null</li>
</ul>

<h4>Numbers, true/false, and joining conditions</h4>
<ul>
<li><code>confidence &gt; 0.9</code>, <code>population &gt;= 10000</code>,
    <code>min_zoom &lt;&gt; 0</code></li>
<li><code>is_land = true</code></li>
<li><code>subtype = 'locality' AND population &gt; 10000</code></li>
<li><code>subtype = 'locality' OR subtype = 'county'</code></li>
<li><code>(a OR b) AND c</code> &mdash; parentheses group conditions</li>
</ul>
<p><code>WHERE</code> is not part of the filter: the field takes the
condition alone. It is combined with the area of interest, so a feature is
downloaded when it matches the condition <i>and</i> falls in the area.</p>

<h4>Several datasets</h4>
<p>A filter applies to every selected dataset. Only the columns they all have
can be used: <code>height</code> works across Buildings and Building Parts,
<code>class</code> exists only on Buildings. The line under the field says
how many columns the selected datasets share.</p>

<h4>What is refused</h4>
<p>The condition runs as SQL in the local DuckDB, where a pasted filter could
reach further than the data. Subqueries (<code>SELECT</code>,
<code>TABLE</code>), the functions that read files or the network
(<code>read_text()</code>, <code>read_csv()</code>, <code>glob()</code>,
<code>current_setting()</code>), a semicolon, and SQL comments
(<code>--</code>, <code>/*</code>) are refused, which keeps the filter to one
condition on this data.</p>
"""

_STATUS_ERROR = "color: #c0392b;"
_STATUS_WARNING = "color: #b9770e;"
_STATUS_HINT = "color: gray;"
_FIELD_ERROR = "QLineEdit { border: 1px solid #c0392b; }"


def _is_unfinished(error):
    """Is `error` only the lexical check seeing a fragment mid-typing?"""
    return any(hint in error for hint in _INCOMPLETE_HINTS)


class FilterHelpDialog(QDialog):
    """How to write an attribute filter, in a window of its own.

    Deliberately not a QMessageBox instance and never exec()ed: the tests
    patch the *static* QMessageBox methods, so an instance - or any modal
    event loop - would wait for a click nobody can give and hang CI with no
    timeout. This is shown with show(), parented to the dialog that opened
    it so it goes when that goes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Attribute filter help")
        self.setModal(False)
        self.resize(560, 620)
        layout = QVBoxLayout()
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(FILTER_HELP_HTML)
        layout.addWidget(self.browser)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        self.setLayout(layout)


class ReleaseLookupError(Exception):
    """The latest Overture release could not be looked up."""


def latest_overture_release():
    """The newest Overture release, from its STAC catalog.

    Fetched through QGIS's network stack, so the proxy and authentication
    set in QGIS apply. Like requests.get it blocks until the catalog
    arrives or the timeout runs out: on the main thread,
    QgsBlockingNetworkRequest runs the request on a thread of its own and
    turns the event loop only to answer an authentication or SSL prompt.
    Raises ReleaseLookupError when the catalog cannot be read.
    """
    request = QNetworkRequest(QUrl(OVERTURE_CATALOG_URL))
    if hasattr(request, "setTransferTimeout"):  # Qt 5.15 and newer
        request.setTransferTimeout(RELEASE_LOOKUP_TIMEOUT_MS)
    fetch = QgsBlockingNetworkRequest()
    # forceRefresh: never a cached catalog, which could name an old release
    if fetch.get(request, True) != QgsBlockingNetworkRequest.ErrorCode.NoError:
        raise ReleaseLookupError(
            f"Could not look up the latest Overture release: {fetch.errorMessage()}"
        )
    try:
        content = bytes(fetch.reply().content())
        if len(content) > 2_000_000:  # the catalog is ~1KB; don't parse a hostile reply
            raise ValueError(f"the catalog is implausibly large ({len(content)} bytes)")
        release = json.loads(content)["latest"]
        if not isinstance(release, str):
            raise ValueError(f"'latest' is a {type(release).__name__}, not a release id")
        return release
    except (ValueError, KeyError, TypeError) as e:
        raise ReleaseLookupError(
            f"Could not read the latest Overture release from {OVERTURE_CATALOG_URL}: {e!r}"
        ) from e


class DataSourceDialog(AreaOfInterestMixin, QDialog):
    validation_complete = pyqtSignal(bool, str, dict)

    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.validation_task = None
        self.validation_worker = None
        self._bbox_warning_open = False  # the bbox warning holds a queued finished() (see show_bbox_warning)
        self._held_validation = None
        # One fetch per selected dataset, all cancelled and superseded together
        self._schema_tasks = {}  # key -> the WorkerTask reading it
        self._schema_workers = {}  # key -> its SchemaWorker
        self._schema_columns = []  # the columns every selected dataset has
        self._schema_keys = ()  # the datasets _schema_columns are shared by
        self._schema_cache = {}  # key -> columns, so reselecting does not refetch
        self._schema_errors = {}  # key -> why its column list could not be read
        self._completer_path = None  # the struct path the completer is filled for
        self._filter_help = None  # the help window, while it is open
        self._latest_release = None  # looked up once per dialog; OK uses it twice, back to back
        self.progress_message = None
        self.requires_validation = True
        self.extent_group = None
        self.extent_button = None
        self.extent_display = None
        self.current_extent = None
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.polygon_tool = None
        self.bbox_button = None
        self.bbox_group = None
        self.xmin_spin = None
        self.ymin_spin = None
        self.xmax_spin = None
        self.ymax_spin = None
        self.rectangle_tool = None
        self._in_feature_select_mode = False
        self._in_polygon_draw_mode = False
        self._canvas_key_filter = None
        # Layer the plugin made a Select Features selection on, if any
        self._selection_layer = None

        # Create the AOI highlighter
        self.aoi_highlighter = None
        if self.iface and self.iface.mapCanvas():
            self.aoi_highlighter = AoiHighlighter(self.iface.mapCanvas())
            
            # Connect to layer changes
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.connect(self.on_layers_changed)
            QgsProject.instance().layersRemoved.connect(self.on_layers_changed)
            QgsProject.instance().layerWasAdded.connect(self.on_layers_changed)
            QgsProject.instance().layerWillBeRemoved.connect(self.on_layers_changed)
            
            # Connect to layer order changes
            if self.iface.layerTreeView():
                self.iface.layerTreeView().model().rowsMoved.connect(self.on_layers_changed)
                self.iface.layerTreeView().model().rowsInserted.connect(self.on_layers_changed)
                self.iface.layerTreeView().model().rowsRemoved.connect(self.on_layers_changed)
            
        self.setWindowTitle("GeoParquet Data Source")
        self.setMinimumWidth(500)


        self.PRESET_DATASETS = load_presets()

        # Create main layout
        layout = QVBoxLayout()

        # Create horizontal layout for radio buttons
        radio_layout = QHBoxLayout()

        # Create radio buttons
        self.overture_radio = QRadioButton("Overture Maps")
        self.sourcecoop_radio = QRadioButton("Source Cooperative")
        self.osm_radio = QRadioButton("OpenStreetMap")
        self.custom_radio = QRadioButton("Custom URL")

        # Add radio buttons to horizontal layout
        radio_layout.addWidget(self.overture_radio)
        radio_layout.addWidget(self.sourcecoop_radio)
        radio_layout.addWidget(self.osm_radio)
        radio_layout.addWidget(self.custom_radio)

        # Connect to save state
        self.overture_radio.released.connect(self.save_radio_button_state)
        self.sourcecoop_radio.released.connect(self.save_radio_button_state)
        self.osm_radio.released.connect(self.save_radio_button_state)
        self.custom_radio.released.connect(self.save_radio_button_state)

        # Add radio button layout to main layout
        layout.addLayout(radio_layout)

        # Add some spacing between radio buttons and content
        layout.addSpacing(10)

        # Create and setup the stacked widget for different options
        self.stack = QStackedWidget()

        # Custom URL page
        custom_page = QWidget()
        custom_layout = QVBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Enter URL to Parquet file or folder (s3://, https:// or minio://host/bucket/path)"
        )
        custom_layout.addWidget(self.url_input)
        custom_page.setLayout(custom_layout)

        # Overture Maps page
        overture_page = QWidget()
        overture_layout = QVBoxLayout()

        # A grid, not a row: each new Overture preset would otherwise raise the
        # dialog's minimum width (#80 made it six)
        checkbox_layout = QGridLayout()

        # Create a widget to hold checkboxes
        self.overture_checkboxes = {}
        for key in self.PRESET_DATASETS["overture"].keys():
            if key not in ["base", "divisions"]:  # Handle base and divisions separately
                checkbox = QCheckBox(OVERTURE_DISPLAY_NAMES.get(key) or key.title())
                self.overture_checkboxes[key] = checkbox

        # Add Divisions to the first block (before Base which has subtypes)
        self.divisions_checkbox = QCheckBox("Divisions")
        self.overture_checkboxes["divisions"] = self.divisions_checkbox

        for index, checkbox in enumerate(self.overture_checkboxes.values()):
            checkbox_layout.addWidget(
                checkbox, index // OVERTURE_CHECKBOX_COLUMNS, index % OVERTURE_CHECKBOX_COLUMNS
            )

        # Add the checkbox grid to main layout
        overture_layout.addLayout(checkbox_layout)

        # Divisions subtype checkboxes (right after first row, shown when Divisions is checked)
        self.divisions_subtype_widget = QWidget()
        divisions_subtype_layout = QHBoxLayout()
        divisions_subtype_layout.setContentsMargins(20, 0, 0, 0)  # Indent subtypes

        self.divisions_subtype_checkboxes = {}
        divisions_subtype_display_names = {
            "division": "Division",
            "division_area": "Division Area",
            "division_boundary": "Division Boundary",
        }

        for subtype in self.PRESET_DATASETS["overture"]["divisions"]["subtypes"]:
            checkbox = QCheckBox(divisions_subtype_display_names[subtype])
            self.divisions_subtype_checkboxes[subtype] = checkbox
            divisions_subtype_layout.addWidget(checkbox)

        self.divisions_subtype_widget.setLayout(divisions_subtype_layout)
        self.divisions_subtype_widget.hide()
        overture_layout.addWidget(self.divisions_subtype_widget)

        # Connect divisions checkbox to show/hide subtype checkboxes
        self.divisions_checkbox.toggled.connect(
            self.divisions_subtype_widget.setVisible
        )
        self.divisions_checkbox.toggled.connect(
            lambda checked: self.adjust_dialog_width(checked, 100)
        )

        # Second row: Base checkbox
        second_row = QHBoxLayout()
        second_row.setContentsMargins(0, 5, 0, 0)

        self.base_checkbox = QCheckBox("Base")
        self.overture_checkboxes["base"] = self.base_checkbox
        second_row.addWidget(self.base_checkbox)
        second_row.addStretch()
        overture_layout.addLayout(second_row)

        # Base subtype checkboxes (shown when Base is checked)
        self.base_subtype_widget = QWidget()
        base_subtype_layout = QHBoxLayout()
        base_subtype_layout.setContentsMargins(20, 0, 0, 0)  # Indent subtypes

        self.base_subtype_checkboxes = {}
        base_subtype_display_names = {
            "infrastructure": "Infrastructure",
            "land": "Land",
            "land_cover": "Land Cover",
            "land_use": "Land Use",
            "water": "Water",
            "bathymetry": "Bathymetry",
        }

        for subtype in self.PRESET_DATASETS["overture"]["base"]["subtypes"]:
            checkbox = QCheckBox(base_subtype_display_names[subtype])
            self.base_subtype_checkboxes[subtype] = checkbox
            base_subtype_layout.addWidget(checkbox)

        self.base_subtype_widget.setLayout(base_subtype_layout)
        self.base_subtype_widget.hide()
        overture_layout.addWidget(self.base_subtype_widget)

        # Connect base checkbox to show/hide subtype checkboxes
        self.base_checkbox.toggled.connect(self.base_subtype_widget.setVisible)
        self.base_checkbox.toggled.connect(
            lambda checked: self.adjust_dialog_width(checked, 100)
        )

        overture_page.setLayout(overture_layout)

        # Source Cooperative page
        sourcecoop_page = QWidget()
        sourcecoop_layout = QVBoxLayout()
        self.sourcecoop_combo = QComboBox()
        self.sourcecoop_combo.addItems(
            sorted(
                [
                    dataset["display_name"]
                    for dataset in self.PRESET_DATASETS["source_cooperative"].values()
                ],
                key=str.lower,
            )
        )
        sourcecoop_layout.addWidget(self.sourcecoop_combo)

        # Add link label
        self.sourcecoop_link = QLabel()
        self.sourcecoop_link.setOpenExternalLinks(True)
        self.sourcecoop_link.setWordWrap(True)
        sourcecoop_layout.addWidget(self.sourcecoop_link)

        # Connect combo box change to update link
        self.sourcecoop_combo.currentTextChanged.connect(self.update_sourcecoop_link)
        sourcecoop_page.setLayout(sourcecoop_layout)

        # OpenStreetMap page
        osm_page = QWidget()
        osm_layout = QVBoxLayout()

        # A grid, not a row: LayerCake keeps publishing datasets, and one row of
        # them set the dialog's minimum width (#193)
        osm_checkbox_layout = QGridLayout()

        # Create checkboxes for OSM datasets
        self.osm_checkboxes = {}
        for index, (key, dataset) in enumerate(self.PRESET_DATASETS["openstreetmap"].items()):
            # key.title() reads wrong for an acronym, so a preset may name itself
            checkbox = QCheckBox(dataset.get("display_name") or key.title())
            self.osm_checkboxes[key] = checkbox
            osm_checkbox_layout.addWidget(
                checkbox, index // OSM_CHECKBOX_COLUMNS, index % OSM_CHECKBOX_COLUMNS
            )

        # Add the checkbox grid to main layout
        osm_layout.addLayout(osm_checkbox_layout)

        # Add link label for LayerCake info
        self.osm_link = QLabel()
        self.osm_link.setText(
            'Data from <a href="https://openstreetmap.us/our-work/layercake/">LayerCake GeoParquet files</a>'
        )
        self.osm_link.setOpenExternalLinks(True)
        self.osm_link.setWordWrap(True)
        osm_layout.addWidget(self.osm_link)

        osm_page.setLayout(osm_layout)

        # Add pages to stack
        self.stack.addWidget(custom_page)
        self.stack.addWidget(overture_page)
        self.stack.addWidget(sourcecoop_page)
        self.stack.addWidget(osm_page)

        layout.addWidget(self.stack)

        # Add Area of Interest group
        layout.addWidget(self.setup_area_of_interest())

        # Attribute filter (#16). Deliberately not saved to QgsSettings like the
        # checkboxes: a filter left over from a past session would silently
        # shrink every later download, so it lasts only as long as this dialog.
        # Off by default and asked for with a tick, like "Clip features to
        # the area": an empty field invited a condition typed blind, and
        # ticking the box is now what both shows the field and reads the
        # column list. Not saved to QgsSettings, for the same reason the text
        # is not: a filter restored out of sight would shrink a download
        # nobody had asked to filter.
        self.filter_checkbox = QCheckBox("Filter data by attribute")
        self.filter_checkbox.setToolTip(
            "Download only the features matching an SQL condition, as well as "
            "falling in the area. Unchecked, everything in the area is "
            "downloaded."
        )
        # Beside the checkbox rather than on a line of its own: why a filter
        # cannot be had, or how many datasets it would cover
        self.filter_note = QLabel()
        self.filter_note.setStyleSheet(_STATUS_HINT)
        # Ignored: the note takes the room left on the row and never adds to
        # the width the dialog asks for, however long it is
        self.filter_note.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        checkbox_row = QHBoxLayout()
        checkbox_row.addWidget(self.filter_checkbox)
        checkbox_row.addWidget(self.filter_note)
        checkbox_row.addStretch()
        layout.addLayout(checkbox_row)

        self.filter_row = QWidget()
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(20, 0, 0, 0)  # indented under the box
        self.filter_label = QLabel("Condition (SQL):")
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("confidence > 0.9 AND class = 'school'")
        filter_tooltip = (
            "Optional. A DuckDB SQL condition on the data's own columns, without WHERE. "
            "Only features that match it and fall in the area are downloaded "
            "(it is ANDed with the spatial filter). Leave empty to download everything in the area. "
            "Press Tab for the column names, and again after a '.' for the fields of a struct. "
            "With several datasets selected, only the columns they all have can be used. "
            "The condition runs as SQL in your local DuckDB, so only paste filters from sources you trust."
        )
        self.filter_label.setToolTip(filter_tooltip)
        self.filter_input.setToolTip(filter_tooltip)
        self.filter_label.setBuddy(self.filter_input)
        filter_layout.addWidget(self.filter_label)
        filter_layout.addWidget(self.filter_input)

        self.filter_help_button = QToolButton()
        self.filter_help_button.setText("?")
        self.filter_help_button.setToolTip(
            "How to write an attribute filter, with examples"
        )
        self.filter_help_button.clicked.connect(self.show_filter_help)
        filter_layout.addWidget(self.filter_help_button)

        self.filter_row.setLayout(filter_layout)
        self.filter_row.hide()
        layout.addWidget(self.filter_row)

        # What is wrong with the filter, or what it can complete, said inline:
        # a modal after the download has started is how "cg = cv" used to be
        # answered, and by then it was a DuckDB Binder Error (#217).
        self.filter_status = QLabel()
        self.filter_status.setWordWrap(True)
        self.filter_status.setStyleSheet(_STATUS_HINT)
        self.filter_status.hide()
        layout.addWidget(self.filter_status)

        # Completes column names mid-expression. Not setCompleter(): that
        # completes the whole line, which is no use after the first AND, so
        # the popup is driven by hand from current_word() instead.
        self._completer_model = QStandardItemModel(self)
        self._completer = QCompleter(self._completer_model, self)
        self._completer.setCompletionRole(Qt.ItemDataRole.UserRole)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setWidget(self.filter_input)
        self._completer.activated[str].connect(self._insert_completion)

        self._schema_timer = QTimer(self)
        self._schema_timer.setSingleShot(True)
        self._schema_timer.timeout.connect(self._start_schema_fetch)

        # Buttons
        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Connect signals
        self.custom_radio.toggled.connect(lambda: self.stack.setCurrentIndex(0))
        self.overture_radio.toggled.connect(lambda: self.stack.setCurrentIndex(1))
        self.sourcecoop_radio.toggled.connect(lambda: self.stack.setCurrentIndex(2))
        self.osm_radio.toggled.connect(lambda: self.stack.setCurrentIndex(3))
        self.ok_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)

        # Add after setting up the sourcecoop_combo
        self.update_sourcecoop_link(self.sourcecoop_combo.currentText())

        # Load checkbox states during initialization
        self.load_checkbox_states()

        # Connect each checkbox to save its state when toggled
        for checkbox in self.overture_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)
        for checkbox in self.base_subtype_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)
        for checkbox in self.divisions_subtype_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)
        for checkbox in self.osm_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)

        # Ensure to call save_checkbox_states when the dialog is accepted
        self.ok_button.clicked.connect(self.save_checkbox_states)

        # Also after load_checkbox_states(), so restoring the remembered
        # checkboxes does not read as the user changing the selection
        self._connect_filter_signals()

    def save_radio_button_state(self) -> None:
        if self.custom_radio.isChecked():
            button_name = self.custom_radio.text()
        elif self.overture_radio.isChecked():
            button_name = self.overture_radio.text()
        elif self.sourcecoop_radio.isChecked():
            button_name = self.sourcecoop_radio.text()
        elif self.osm_radio.isChecked():
            button_name = self.osm_radio.text()
        else:
            button_name = self.custom_radio.text()

        settings_store.save_value(settings_store.RADIO_SELECTION, button_name)

    def handle_overture_selection(self, text):
        """Show/hide base subtype combo based on selection"""
        self.base_subtype_widget.setVisible(text == "Base")

    def get_where_fragment(self):
        """The attribute filter as typed, stripped, or None when it is empty.

        Also None whenever the field is not on screen (#217): an invisible
        filter must never shrink a download, since the text cannot be seen,
        corrected, or even known to apply. The text itself is kept, so
        re-ticking restores what was typed.

        That means filter_is_active(), not just the tick. The box no longer
        greys out when nothing is selected (#231), so a filter typed against
        a dataset and left behind when that dataset is unticked would
        otherwise still be returned here, with no field on screen to show it.

        A filter does carry across several selected datasets - the plugin
        passes it to every job - which is what the shared-column completion
        and the per-dataset warnings are for.
        """
        if not self.filter_is_active():
            return None
        return self.filter_input.text().strip() or None

    # --- the filter box's column knowledge (#217) -------------------------
    #
    # The filter box used to be a QLineEdit with a placeholder and nothing
    # else, so the only way to learn what a dataset's columns were called was
    # to get the filter wrong and read DuckDB's answer after the fact: `cg =
    # cv` came back as a Binder Error modal listing candidate bindings. The
    # dialog now reads the one selected dataset's column list in the
    # background (SchemaWorker), completes names from it, and says under the
    # field what is wrong with what is typed.
    #
    # One dataset, because that is the only selection a condition can be
    # offered and checked against; a filter across datasets that do not share
    # a column is not a filter either of them can run.

    def _connect_filter_signals(self):
        """Wire the filter box up, once the remembered checkboxes are restored.

        Every checkbox on the source pages, found rather than named: the
        pages keep gaining presets, subtypes and modifiers, and one that this
        did not know about would leave the filter describing a selection the
        user had already changed. Nothing outside self.stack is reached, so
        the Area of Interest box and its Clip checkbox are not included.
        """
        for checkbox in self.stack.findChildren(QCheckBox):
            checkbox.toggled.connect(self._selection_changed)
        for radio in (
            self.custom_radio,
            self.overture_radio,
            self.sourcecoop_radio,
            self.osm_radio,
        ):
            radio.toggled.connect(self._selection_changed)
        self.sourcecoop_combo.currentTextChanged.connect(self._selection_changed)
        self.url_input.textChanged.connect(self._selection_changed)
        self.filter_checkbox.toggled.connect(self._filter_asked_for)
        self.filter_input.textChanged.connect(self._update_filter_status)
        self.filter_input.textEdited.connect(self._filter_text_edited)
        # After QCompleter.setWidget(), which installs a filter of its own:
        # the last one installed is called first, so Tab is taken here
        self.filter_input.installEventFilter(self)
        self._selection_changed()

    def selected_datasets(self):
        """The URLs ticked now, one per download, with {release} left in place.

        get_urls() with a release given reaches no network, so this is cheap
        enough to run on every keystroke and every checkbox toggle.
        """
        try:
            urls = self.get_urls(release=RELEASE_PLACEHOLDER)
        except Exception as e:  # a preset this build cannot build a URL for
            logger.log(f"Could not list the selected datasets: {e}")
            return []
        return [url for url in urls if url]

    def selected_sources(self):
        """[(key, resolve_url)] for every dataset selected, one per download.

        `key` identifies a dataset for the schema cache and never depends on
        the Overture release, which changes monthly while the columns do not.
        `resolve_url` is called by a schema task on its own thread, so it
        closes over strings only and never touches a widget.
        """
        sources = []
        for url in self.selected_datasets():

            def resolve_url(url=url):
                if RELEASE_PLACEHOLDER in url:
                    return url.replace(RELEASE_PLACEHOLDER, latest_overture_release())
                return url

            sources.append((url, resolve_url))
        return sources

    def filter_is_available(self):
        """Can a filter be offered at all? Whenever a dataset is selected.

        A filter covers every selected dataset, so what it may use is what
        they all have: the completion comes from the intersection of their
        schemas and a column only some of them have is a warning naming the
        one without it. Buildings and Building Parts share 23 of their
        columns and are filterable together; the six Base subtypes share
        seven, and the honest result there is that almost nothing completes.
        """
        return bool(self.selected_datasets())

    def filter_is_active(self):
        """Is a filter being asked for, and can it be?"""
        return self.filter_checkbox.isChecked() and self.filter_is_available()

    def filter_columns(self):
        """[(column name, DuckDB type)] for the ticked dataset, or []."""
        return list(self._schema_columns)

    def _selection_changed(self, *args):
        """What is selected changed: the filter covers all of it."""
        keys = tuple(key for key, _resolve in self.selected_sources())
        if keys != self._schema_keys:
            self._schema_keys = keys
            self._schema_errors = {}
            self.cancel_schema_fetch()
            self._refresh_filter_columns()
        # Never disabled, so that it matches "Clip features to the area",
        # which stays tickable whatever is selected. With no dataset the note
        # beside it reads "Select a dataset to filter it.", which says more
        # than a grey box does, and a tick made early takes effect as soon as
        # a dataset is picked. filter_is_active() is what actually keeps an
        # unbacked filter out of a download.
        self._update_filter_visibility()

    def _filter_asked_for(self, checked):
        """The "Filter data by attribute" box was ticked or unticked.

        Ticking it is the one thing that starts a column fetch: a dialog
        merely opened, and a remembered selection merely restored, read
        nothing. Unticking stops any fetch still running - the schema already
        read stays in the cache, so re-ticking costs nothing.
        """
        if not checked:
            self.cancel_schema_fetch()
        self._update_filter_visibility()
        if checked and self.filter_is_available():
            self.filter_input.setFocus()

    def _update_filter_visibility(self):
        """Show the field only while a filter is on offer, and ask if it is."""
        active = self.filter_is_active()
        self.filter_row.setVisible(active)
        if active and not self._schema_columns:
            self.request_filter_columns()
        self._update_filter_status()

    def _filter_text_edited(self, _text=""):
        """The user typed in the filter box: offer the matching names."""
        self._show_completions()

    def request_filter_columns(self, delay_ms=None):
        """Ask for the selected datasets' column lists, shortly.

        Debounced, so that ticking the box and then changing the selection
        twice is one round of DESCRIBEs rather than three.
        """
        if not self.filter_checkbox.isChecked() or not self._schema_keys:
            return
        if not self._schemas_outstanding():
            return
        self._schema_timer.start(
            SCHEMA_FETCH_DELAY_MS if delay_ms is None else delay_ms
        )
        self._update_filter_status()

    def _schemas_outstanding(self):
        """The selected datasets whose column list is neither read nor refused."""
        return [
            key
            for key in self._schema_keys
            if key not in self._schema_cache and key not in self._schema_errors
        ]

    def _start_schema_fetch(self):
        """Read each selected dataset's columns, one task per dataset.

        Six Base subtypes means six DESCRIBEs in the background. They are
        started, cancelled and superseded together: anything already read, or
        already running, is left alone.
        """
        for key, resolve_url in self.selected_sources():
            if key in self._schema_cache or key in self._schema_errors:
                continue
            if key in self._schema_workers:
                continue  # already on its way
            worker = SchemaWorker(resolve_url)
            self._schema_workers[key] = worker

            # It emits from the task's thread; queued, these run on the main
            # thread. Both carry the key they are about rather than being
            # guarded on the worker still being a current one, the way the
            # validation signals are (_unless_superseded): nothing here
            # orders the queued emission against the task's own finished(),
            # which lets go of the worker, and a column list dropped for that
            # would be a completer that silently never fills.
            worker.schema_ready.connect(
                lambda schema, key=key: self._schema_arrived(key, list(schema)),
                QUEUED,
            )
            worker.failed.connect(
                lambda message, key=key: self._schema_unavailable(key, message),
                QUEUED,
            )
            task = WorkerTask(
                f"Reading the columns of {key}", worker, self._schema_task_finished
            )
            self._schema_tasks[key] = task
            submit(task)
        self._update_filter_status()

    def _schema_arrived(self, key, schema):
        """`key`'s columns, from a fetch that may since have been superseded.

        A killed worker reports nothing (SchemaWorker.run checks), so what
        arrives is always a real column list and worth keeping, whether or not
        `key` is still selected.
        """
        self._schema_cache[key] = list(schema)
        self._schema_errors.pop(key, None)
        if key not in self._schema_keys:
            return
        self._refresh_filter_columns()
        self._update_filter_status()

    def _schema_unavailable(self, key, message):
        """No column list for `key`: the filter box goes on without one.

        Which is how it worked before there was one, so this is a line under
        the field and nothing more. The download reports whatever is really
        wrong with the source.
        """
        self._schema_errors[key] = message
        if key not in self._schema_keys:
            return
        self._refresh_filter_columns()
        self._update_filter_status()

    def _schema_task_finished(self, task, result):
        """A task ended: let go of it, whether or not its worker reported."""
        for key, running in list(self._schema_tasks.items()):
            if running is task:
                self._schema_tasks.pop(key, None)
                self._schema_workers.pop(key, None)
        self._update_filter_status()

    def cancel_schema_fetch(self):
        """Stop every running column fetch and let go of its worker and task."""
        self._schema_timer.stop()
        tasks, self._schema_tasks = self._schema_tasks, {}
        workers, self._schema_workers = self._schema_workers, {}
        for task in tasks.values():
            task.cancel()  # kills the worker
        for key, worker in workers.items():
            if key not in tasks:
                worker.kill()
        # A killed worker may still be returning from DuckDB; its task holds
        # it until then, and the task manager deletes the task

    def filter_schemas(self):
        """[(a name for the dataset, its columns)] for what has been read.

        The names come off the URLs (core/filters.dataset_label), so a
        message can say which dataset has not got a column without this
        depending on how the source pages are built.
        """
        return [
            (dataset_label(key), self._schema_cache[key])
            for key in self._schema_keys
            if key in self._schema_cache
        ]

    def _refresh_filter_columns(self):
        """The columns a filter may use: those every selected dataset has.

        Nothing until every selected dataset has reported, because the
        intersection of the ones that have could offer a column the next one
        has not got.
        """
        if self._schemas_outstanding():
            self._set_filter_columns([])
            return
        self._schema_timer.stop()  # every selected dataset has reported
        self._set_filter_columns(
            shared_columns([columns for _label, columns in self.filter_schemas()])
        )

    def _set_filter_columns(self, schema):
        """Hold `schema` as the columns the selected datasets share."""
        self._schema_columns = list(schema)
        self._completer_model.clear()
        self._completer_path = None

    def candidates_for_path(self, path):
        """[(name, popup label)] for `path`: its fields, or the columns.

        An empty path is the top level. A path into a plain STRUCT gives its
        fields; a LIST gives nothing, because a bare dot on one is a binder
        error, and a MAP gives nothing, because its keys are nowhere in the
        schema (core/schema.py has the DuckDB rules that were probed).
        """
        if not self._schema_columns:
            return []
        if not path:
            return completion_candidates(self._schema_columns)
        return field_candidates(self._schema_columns, path)

    def _set_completion_candidates(self, path):
        """Fill the completer for `path`, unless it is already filled for it."""
        if self._completer_path == path and self._completer_model.rowCount():
            return
        self._completer_path = path
        self._completer_model.clear()
        for name, label in self.candidates_for_path(path):
            item = QStandardItem(label)
            # The popup shows the type beside the name; the completer matches
            # on and inserts the name alone, which is what UserRole holds
            item.setData(name, Qt.ItemDataRole.UserRole)
            item.setEditable(False)
            self._completer_model.appendRow(item)

    def _word_at_cursor(self):
        """The Word being typed in the filter box, or None."""
        return current_word(
            self.filter_input.text(), self.filter_input.cursorPosition()
        )

    def filter_completion_prefix(self):
        """The word the completer should complete, or None where none can be."""
        word = self._word_at_cursor()
        return None if word is None else word.text

    def completions_at_cursor(self):
        """The names the popup would offer where the cursor is, in its order.

        The candidates for the path in front of the cursor, narrowed to the
        word typed so far - which is what QCompleter does with the prefix.
        """
        word = self._word_at_cursor()
        if word is None:
            return []
        prefix = word.text.lower()
        return [
            name
            for name, _label in self.candidates_for_path(word.path)
            if name.lower().startswith(prefix)
        ]

    def _show_completions(self, offer_all=False):
        """Offer the names matching the word under the cursor; did it show?"""
        if not self._schema_columns or not self.filter_is_active():
            return False
        word = self._word_at_cursor()
        if word is None or (not word.text and not offer_all):
            self._hide_completions()
            return False
        self._set_completion_candidates(word.path)
        if not self._completer_model.rowCount():
            self._hide_completions()
            return False
        self._completer.setCompletionPrefix(word.text)
        if self._completer.completionCount() == 0:
            self._hide_completions()
            return False
        self._completer.complete()
        return True

    def _hide_completions(self):
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            popup.hide()

    def complete_current_word(self):
        """Tab in the filter box: take the completion, or offer the names.

        Returns True when Tab was used for completion, so that the key is not
        also spent moving the keyboard out of the field.
        """
        if not self._schema_columns or not self.filter_is_active():
            return False
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            index = popup.currentIndex()
            name = (
                index.data(Qt.ItemDataRole.UserRole)
                if index.isValid()
                else self._completer.currentCompletion()
            )
            popup.hide()
            if name:
                self._insert_completion(name)
                return True
            return False
        return self._show_completions(offer_all=True)

    def _insert_completion(self, name):
        """Put `name` in place of the word under the cursor.

        Only the last part of a dotted path is replaced, so names.pri becomes
        names.primary. A field goes in unquoted even when it is a reserved
        word, which is what DuckDB accepts there (names.primary binds).
        """
        text = self.filter_input.text()
        cursor = self.filter_input.cursorPosition()
        word = current_word(text, cursor)
        start = cursor if word is None else word.start
        inserted = name if word is not None and word.path else completion_text(name)
        self.filter_input.setText(text[:start] + inserted + text[cursor:])
        self.filter_input.setCursorPosition(start + len(inserted))

    def filter_status_message(self):
        """(the line to put under the filter box, the stylesheet for it).

        Nothing here opens a dialog: a filter being typed is corrected where
        it is typed, and only OK still stops for a message box.
        """
        if not self.filter_is_active() or not self.filter_checkbox.isChecked():
            return "", _STATUS_HINT
        fragment = self.filter_input.text()
        error = validate_where_fragment(fragment)
        if error:
            return error, (_STATUS_WARNING if _is_unfinished(error) else _STATUS_ERROR)
        # Every selected dataset, with its whole schema: the types are what
        # tell a misspelled struct field from a list that needs an index, and
        # a column only some of them have is its own warning
        unknown = column_feedback(fragment, self.filter_schemas())
        if unknown and self._still_typing(unknown):
            unknown = None
        if unknown:
            return unknown, _STATUS_WARNING
        if self._schema_workers or self._schema_timer.isActive():
            return FILTER_READING_COLUMNS, _STATUS_HINT
        datasets = len(self._schema_keys)
        if self._schema_columns:
            if datasets > 1:
                return (
                    FILTER_SHARED_COLUMNS.format(
                        count=len(self._schema_columns), datasets=datasets
                    ),
                    _STATUS_HINT,
                )
            return (
                FILTER_COLUMNS_READY.format(count=len(self._schema_columns)),
                _STATUS_HINT,
            )
        if self._schema_errors:
            return FILTER_NO_COLUMNS, _STATUS_HINT
        if datasets > 1 and self.filter_schemas():
            return FILTER_NO_SHARED_COLUMNS.format(datasets=datasets), _STATUS_HINT
        return "", _STATUS_HINT

    def _still_typing(self, unknown):
        """Is `unknown` about the half-typed name under the cursor?

        `names.pri` is not a mistake, it is three letters of names.primary,
        and the popup is offering it at that moment. Calling it unknown while
        the completion is on screen is just nagging, so the message waits
        until the word is either finished or really wrong.
        """
        word = self._word_at_cursor()
        if word is None or not word.text:
            return False
        typed = ".".join(word.path + (word.text,))
        return f'"{typed}"' in unknown and bool(self.completions_at_cursor())

    def filter_availability_message(self):
        """The line beside the checkbox: why no filter, or what it would cover."""
        count = len(self.selected_datasets())
        if count == 0:
            return FILTER_NEEDS_A_DATASET
        if count > 1:
            return FILTER_ACROSS_DATASETS.format(count=count)
        return ""

    def _update_filter_status(self, *args):
        note = self.filter_availability_message()
        self.filter_note.setText(note)
        self.filter_note.setVisible(bool(note))
        text, style = self.filter_status_message()
        self.filter_status.setText(text)
        self.filter_status.setStyleSheet(style)
        self.filter_status.setVisible(bool(text))
        self.filter_input.setStyleSheet(_FIELD_ERROR if style == _STATUS_ERROR else "")

    def show_filter_help(self):
        """Open the filter help, or raise the window already open.

        A window, not a QMessageBox instance and never an exec(): the tests
        patch the static QMessageBox methods, and a real modal would hang CI
        with no timeout (see FilterHelpDialog).
        """
        if self._filter_help is None:
            self._filter_help = FilterHelpDialog(self)
        self._filter_help.show()
        self._filter_help.raise_()
        self._filter_help.activateWindow()

    def close_filter_help(self):
        """Take the help window down with the dialog that opened it."""
        if self._filter_help is not None:
            self._filter_help.close()
            self._filter_help = None

    def eventFilter(self, obj, event):
        """Tab completes a column name instead of leaving the filter box."""
        if (
            obj is self.filter_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            and self.complete_current_word()
        ):
            return True
        return super().eventFilter(obj, event)

    def validate_and_accept(self):
        """Validate the input and accept the dialog if valid"""
        try:
            urls = self.get_urls()
        except ReleaseLookupError as e:
            QMessageBox.warning(self, "Validation Error", str(e))
            return
        if not urls:
            QMessageBox.warning(
                self, "Validation Error", "Please select at least one dataset"
            )
            return

        # The cheap lexical check runs for every source. Only a custom URL is
        # also tried against DuckDB below; a preset's DuckDB error comes from
        # the download itself.
        filter_error = validate_where_fragment(self.get_where_fragment())
        if filter_error:
            QMessageBox.warning(self, "Validation Error", filter_error)
            return
            
        # Check if the user selected an Area of Interest
        # Only warn if AOI checkbox is checked but no extent is selected
        if self.extent_group.isChecked() and not self.current_extent:
            reply = QMessageBox.warning(
                self,
                "No Area of Interest Selected",
                "You enabled 'Area of Interest' but haven't selected one. The current map canvas extent will be used instead.\n\n"
                "Do you want to continue using the current map canvas extent?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # For Overture and OSM datasets, we know they're valid so we can skip validation
        if self.overture_radio.isChecked() or self.osm_radio.isChecked():
            self.accept()
            return

        # For custom URLs, do validation
        if self.custom_radio.isChecked():
            for url in urls:
                if not url.startswith(
                    ("http://", "https://", "s3://", "minio://", "minio+http://", "file://", "hf://")
                ):
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        "URL must start with http://, https://, s3://, minio://, hf://, or file://\n\n"
                        f"MinIO and other S3-compatible servers: {MINIO_URL_FORMAT}",
                    )
                    return

                # Create progress dialog for validation
                self.progress_dialog = QProgressDialog(
                    "Validating URL...", "Cancel", 0, 0, self
                )
                self.progress_dialog.setWindowModality(Qt.WindowModality.NonModal)
                self.progress_dialog.canceled.connect(self.cancel_validation)

                # Use custom extent if set, otherwise use canvas extent
                extent = self.current_extent if self.current_extent else self.iface.mapCanvas().extent()

                # Create validation worker
                worker = ValidationWorker(
                    url, self.iface, extent, where_fragment=self.get_where_fragment()
                )
                self.validation_worker = worker

                # It emits from the task's thread; queued, these run on the
                # main thread
                worker.progress.connect(self.progress_dialog.setLabelText, QUEUED)
                worker.finished.connect(
                    lambda success, message, results: self._unless_superseded(
                        worker, self.handle_validation_result, success, message, results
                    ),
                    QUEUED,
                )
                worker.needs_bbox_warning.connect(
                    lambda: self._unless_superseded(worker, self.show_bbox_warning),
                    QUEUED,
                )

                # Start validation
                self.validation_task = WorkerTask(
                    f"Validating {url}", worker, self._validation_task_finished
                )
                submit(self.validation_task)
                self.progress_dialog.show()
                return

        # For other preset sources, we can skip validation
        self.accept()

    def handle_validation_result(self, success, message, validation_results):
        """Handle validation result in the dialog"""
        if self._bbox_warning_open:
            # The warning's modal event loop delivered the queued finished();
            # hold it until the user has answered (see show_bbox_warning)
            self._held_validation = (success, message, validation_results)
            return
        self.cleanup_validation()

        if success:
            self.validation_complete.emit(True, message, validation_results)
            self.accept()
        else:
            QMessageBox.warning(self, "Validation Error", message)
            self.validation_complete.emit(False, message, validation_results)

    def _unless_superseded(self, worker, slot, *args):
        """Call slot(*args) only while `worker` is still the current validation.

        Cleanup no longer waits for the task, so a cancelled worker can still
        be returning from DuckDB when its signals arrive. After Cancel or
        closeEvent (validation_worker is None), or once OK has started the
        next validation (a different worker), its report is not an answer to
        anything the user is still asking - acting on it would accept a
        dialog the user had dismissed and start the download anyway.
        """
        if self.validation_worker is worker:
            slot(*args)

    def _validation_task_finished(self, task, result):
        """The task ended without the worker reporting (cancelled from the
        QGIS task manager): take the progress dialog down with it."""
        if task.worker is self.validation_worker:
            self.cleanup_validation()

    def cancel_validation(self):
        """Handle validation cancellation"""
        if self.validation_task is not None:
            self.validation_task.cancel()  # kills the worker
        elif self.validation_worker:
            self.validation_worker.kill()
        self.cleanup_validation()

    def cleanup_validation(self):
        """Clean up validation resources"""
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # A killed worker may still be returning from DuckDB; its task holds
        # it until then, and the task manager deletes the task
        self.validation_worker = None
        self.validation_task = None

    def closeEvent(self, event):
        """Handle dialog close event"""
        self._in_feature_select_mode = False
        self._remove_canvas_key_filter()

        # Clean up validation if running
        if self.validation_task is not None:
            self.cancel_validation()
        self.cancel_schema_fetch()
        self.close_filter_help()

        # Disconnect from layer changes
        if self.iface:
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.disconnect(self.on_layers_changed)
            QgsProject.instance().layersRemoved.disconnect(self.on_layers_changed)
            QgsProject.instance().layerWasAdded.disconnect(self.on_layers_changed)
            QgsProject.instance().layerWillBeRemoved.disconnect(self.on_layers_changed)

        # Reset the map tool to default
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None

        # Clear any AOI highlighting
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()

        # Clear the Select Features selection (a user's own selection is kept)
        self._release_selection_layer()

        # Restore the default map tool
        if self.iface:
            self.iface.actionPan().trigger()

        super().closeEvent(event)

    def get_urls(self, release=None):
        """Returns a list of URLs for selected datasets

        `release` names the Overture release to build the URLs from, skipping
        the lookup: selected_datasets() passes RELEASE_PLACEHOLDER so that
        counting what is ticked never reaches the network (#217).
        """
        urls = []
        if self.custom_radio.isChecked():
            return [self.url_input.text().strip()]
        elif self.overture_radio.isChecked():
            # One lookup per dialog: validate_and_accept() and the plugin's
            # accepted handler both call get_urls() on the same OK, and a
            # network drop between the two would otherwise raise out of a slot
            if release is None:
                if self._latest_release is None:
                    self._latest_release = latest_overture_release()
                release = self._latest_release
            latest_release = release

            for theme, checkbox in self.overture_checkboxes.items():
                if checkbox.isChecked():
                    dataset = self.PRESET_DATASETS["overture"][theme]
                    if theme == "transportation":
                        type_str = "segment"
                    elif theme == "divisions":
                        # Handle multiple divisions subtypes
                        for (
                            subtype,
                            subtype_checkbox,
                        ) in self.divisions_subtype_checkboxes.items():
                            if subtype_checkbox.isChecked():
                                urls.append(
                                    dataset["url_template"].format(
                                        subtype=subtype, release=latest_release
                                    )
                                )
                        continue  # Skip the normal URL append for divisions
                    elif theme == "addresses":
                        type_str = "*"
                    elif theme == "base":
                        # Handle multiple base subtypes
                        for (
                            subtype,
                            subtype_checkbox,
                        ) in self.base_subtype_checkboxes.items():
                            if subtype_checkbox.isChecked():
                                urls.append(
                                    dataset["url_template"].format(
                                        subtype=subtype, release=latest_release
                                    )
                                )
                        continue  # Skip the normal URL append for base
                    else:
                        type_str = theme.rstrip(
                            "s"
                        )  # remove trailing 's' for singular form
                    urls.append(
                        dataset["url_template"].format(
                            subtype=type_str, release=latest_release
                        )
                    )
        elif self.sourcecoop_radio.isChecked():
            selection = self.sourcecoop_combo.currentText()
            dataset = next(
                (
                    dataset
                    for dataset in self.PRESET_DATASETS["source_cooperative"].values()
                    if dataset["display_name"] == selection
                ),
                None,
            )
            return [dataset["url"]] if dataset else []
        elif self.osm_radio.isChecked():
            for layer, checkbox in self.osm_checkboxes.items():
                if checkbox.isChecked():
                    dataset = self.PRESET_DATASETS["openstreetmap"][layer]
                    urls.append(dataset["url"])
        return urls

    def update_sourcecoop_link(self, selection):
        """Update the link based on the selected dataset"""
        # Find the dataset by display_name
        dataset = next(
            (
                dataset
                for dataset in self.PRESET_DATASETS["source_cooperative"].values()
                if dataset["display_name"] == selection
            ),
            None,
        )
        if dataset and "info_url" in dataset:
            self.sourcecoop_link.setText(
                f'<a href="{dataset["info_url"]}">View dataset info</a>'
            )
        else:
            self.sourcecoop_link.setText("")

    def show_bbox_warning(self):
        """Show bbox warning dialog in main thread"""
        # Close the progress dialog if it exists
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # The worker emits finished() right behind needs_bbox_warning. The
        # modal's nested event loop would deliver it mid-exec and accept the
        # dialog under the warning - a No answered afterwards could not stop
        # the download - so handle_validation_result holds it until here.
        self._bbox_warning_open = True
        self._held_validation = None
        try:
            reply = QMessageBox.warning(
                self,
                "No bbox Column Detected",
                "This dataset doesn't have a bbox column, which means downloads will be slower. "
                "GeoParquet 1.1 files with a bbox column work much better - tell your data provider to upgrade!\n\n"
                "Do you want to continue with the download?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        finally:
            self._bbox_warning_open = False
        held, self._held_validation = self._held_validation, None

        validation_results = {
            "has_bbox": False,
            "schema": None,
            "bbox_column": None,
        }
        if reply == QMessageBox.StandardButton.No:
            # Drops the held result too: whatever the worker said, the user
            # does not want this download
            self.cleanup_validation()
            self.validation_complete.emit(
                False, "Download cancelled by user.", validation_results
            )
        elif held is not None:
            self.handle_validation_result(*held)
        else:
            # finished() has not arrived yet; cleanup first, so when it does,
            # _unless_superseded drops it instead of accepting a second time
            self.cleanup_validation()
            self.validation_complete.emit(
                True, "Validation successful", validation_results
            )
            self.accept()

    def adjust_dialog_width(self, checked, width):
        """Adjust the dialog width based on the base checkbox state."""
        if checked:
            self.resize(self.width() + width, self.height())
        else:
            self.resize(self.width() - width, self.height())

    def save_checkbox_states(self) -> None:
        # Saved on accept, like the other checkboxes, so cancelling the dialog
        # keeps the remembered setting
        if hasattr(self, "clip_checkbox"):
            settings_store.save_value(settings_store.CLIP_TO_AREA, self.clip_checkbox.isChecked())

        # Save main checkboxes
        for key, checkbox in self.overture_checkboxes.items():
            settings_store.save_value(settings_store.CHECKBOX.format(key=key), checkbox.isChecked())

        # Save base subtype checkboxes
        for key, checkbox in self.base_subtype_checkboxes.items():
            settings_store.save_value(settings_store.BASE_SUBTYPE_CHECKBOX.format(key=key), checkbox.isChecked())

        # Save divisions subtype checkboxes
        for key, checkbox in self.divisions_subtype_checkboxes.items():
            settings_store.save_value(settings_store.DIVISIONS_SUBTYPE_CHECKBOX.format(key=key), checkbox.isChecked())

        # Save OSM checkboxes
        for key, checkbox in self.osm_checkboxes.items():
            settings_store.save_value(settings_store.OSM_CHECKBOX.format(key=key), checkbox.isChecked())

        # Save AOI checkbox state
        settings_store.save_value(settings_store.AOI_ENABLED, self.extent_group.isChecked())

    def load_checkbox_states(self) -> None:
        # Load main checkboxes
        for key, checkbox in self.overture_checkboxes.items():
            checked = settings_store.load_flag(settings_store.CHECKBOX.format(key=key))
            checkbox.setChecked(checked)

        # Load base subtype checkboxes
        for key, checkbox in self.base_subtype_checkboxes.items():
            checked = settings_store.load_flag(settings_store.BASE_SUBTYPE_CHECKBOX.format(key=key))
            checkbox.setChecked(checked)

        # Load divisions subtype checkboxes
        for key, checkbox in self.divisions_subtype_checkboxes.items():
            checked = settings_store.load_flag(settings_store.DIVISIONS_SUBTYPE_CHECKBOX.format(key=key))
            checkbox.setChecked(checked)

        # Load OSM checkboxes
        for key, checkbox in self.osm_checkboxes.items():
            checked = settings_store.load_flag(settings_store.OSM_CHECKBOX.format(key=key))
            checkbox.setChecked(checked)

        # Update base subtype widget visibility based on base checkbox state
        self.base_subtype_widget.setVisible(self.base_checkbox.isChecked())

        # Update divisions subtype widget visibility based on divisions checkbox state
        self.divisions_subtype_widget.setVisible(self.divisions_checkbox.isChecked())

        # Load AOI checkbox state
        aoi_enabled = settings_store.load_flag(settings_store.AOI_ENABLED)
        self.extent_group.setChecked(aoi_enabled)


    def accept(self):
        """Override accept to store the current extent"""
        # QDialog.accept() hides the dialog without a closeEvent, so the
        # column fetch is stopped here too rather than left running
        self.cancel_schema_fetch()
        self.close_filter_help()

        # Store the extent to be used by the plugin
        if hasattr(self, 'current_extent') and self.current_extent:
            settings_store.save_last_used_extent(self.current_extent.toString())

        # Reset the map tool to default when accepting dialog
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None

        # Clear any AOI highlighting when dialog is accepted
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()

        # Clear the Select Features selection (a user's own selection is kept)
        self._release_selection_layer()

        super().accept()

    def reject(self):
        """Override reject to clean up resources"""
        self.cancel_schema_fetch()
        self.close_filter_help()

        # Reset the map tool to default when rejecting dialog
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None

        # Clear any AOI highlighting when dialog is rejected
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()

        # Clear the Select Features selection (a user's own selection is kept)
        self._release_selection_layer()

        # Restore the default map tool
        if self.iface:
            self.iface.actionPan().trigger()

        super().reject()
