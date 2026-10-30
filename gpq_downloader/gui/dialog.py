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
from qgis.PyQt.QtCore import pyqtSignal, Qt, QUrl

from ..qgis_io.manager import QUEUED, WorkerTask, submit
from ..qgis_io.worker import ValidationWorker
from ..utils import MINIO_URL_FORMAT
from ..core.filters import validate_where_fragment
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
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Attribute filter (SQL):")
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("confidence > 0.9 AND class = 'school'")
        filter_tooltip = (
            "Optional. A DuckDB SQL condition on the data's own columns, without WHERE. "
            "Only features that match it and fall in the area are downloaded "
            "(it is ANDed with the spatial filter). Leave empty to download everything in the area. "
            "The condition runs as SQL in your local DuckDB, so only paste filters from sources you trust."
        )
        filter_label.setToolTip(filter_tooltip)
        self.filter_input.setToolTip(filter_tooltip)
        filter_label.setBuddy(self.filter_input)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.filter_input)
        layout.addLayout(filter_layout)

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
        """The attribute filter as typed, stripped, or None when it is empty."""
        return self.filter_input.text().strip() or None

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
        filter_error = validate_where_fragment(self.filter_input.text())
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
            box = QMessageBox(
                QMessageBox.Icon.Warning, "Validation Error", message,
                QMessageBox.StandardButton.Ok, self,
            )
            # DuckDB's message can quote remote metadata; never render it as HTML
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.exec()
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

    def get_urls(self):
        """Returns a list of URLs for selected datasets"""
        urls = []
        if self.custom_radio.isChecked():
            return [self.url_input.text().strip()]
        elif self.overture_radio.isChecked():
            # One lookup per dialog: validate_and_accept() and the plugin's
            # accepted handler both call get_urls() on the same OK, and a
            # network drop between the two would otherwise raise out of a slot
            if self._latest_release is None:
                self._latest_release = latest_overture_release()
            latest_release = self._latest_release

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
