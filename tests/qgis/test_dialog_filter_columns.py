"""The filter box's guidance (#217): one dataset, its column list, completion
and the inline note.

The report: the filter box was a QLineEdit with a placeholder, so the user
typed `cg = cv` blind and learnt the column names from a DuckDB Binder Error
modal once the download had already started. Everything here is about saying
it sooner, and in the dialog rather than in a message box.

No test lets a real SchemaWorker run: the fetch is started by hand on a
patched SchemaWorker and submit(), the way the map-tool paths are tested
statically, so nothing here waits on DuckDB or the network.
"""

import gc
from unittest.mock import MagicMock, patch

import pytest
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent
from qgis.PyQt.QtWidgets import QLineEdit

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.gui import dialog as dialog_module

URL = "https://example.com/data/places.parquet"
OSM_POIS = "https://data.openstreetmap.us/layercake/pois.parquet"
SCHEMA = [
    ("id", "BIGINT"),
    ("geometry", "GEOMETRY"),
    ("bbox", "STRUCT(xmin FLOAT, ymin FLOAT)"),
    ("class", "VARCHAR"),
    ("subclass", "VARCHAR"),
    ("confidence", "DOUBLE"),
    ("names", "STRUCT(primary VARCHAR)"),
]


@pytest.fixture
def mock_iface():
    iface = MagicMock()
    iface.mainWindow.return_value = None
    return iface


@pytest.fixture(autouse=True)
def no_remembered_state():
    """Nothing here writes the remembered checkboxes.

    These tests tick datasets to count them, and a dialog built by the next
    test file would otherwise open with one of them already selected.
    """
    with patch("gpq_downloader.gui.settings_store.save_value"):
        yield


def new_dialog(mock_iface):
    """A dialog with nothing ticked, whatever the last session remembered."""
    dialog = DataSourceDialog(None, mock_iface)
    dialog.extent_group.setChecked(False)
    for checkbox in (
        list(dialog.overture_checkboxes.values())
        + list(dialog.base_subtype_checkboxes.values())
        + list(dialog.divisions_subtype_checkboxes.values())
        + list(dialog.osm_checkboxes.values())
    ):
        checkbox.setChecked(False)
    return dialog


@pytest.fixture
def dialog(qgs_app, mock_iface):
    dialog = new_dialog(mock_iface)
    yield dialog
    dialog.cancel_schema_fetch()
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def one_osm(dialog, *keys):
    """Tick exactly `keys` on the OpenStreetMap page."""
    dialog.osm_radio.setChecked(True)
    for key, checkbox in dialog.osm_checkboxes.items():
        checkbox.setChecked(key in keys)


def with_schema(dialog, schema=SCHEMA):
    """Hand the dialog a column list, as a finished fetch would."""
    dialog._schema_arrived(dialog._schema_key, list(schema))
    return dialog


# --- a filter is a single-dataset option -----------------------------------

def test_the_filter_box_is_enabled_only_for_exactly_one_dataset(dialog):
    one_osm(dialog)
    assert not dialog.filter_input.isEnabled()
    one_osm(dialog, "pois")
    assert dialog.filter_input.isEnabled()
    one_osm(dialog, "pois", "buildings")
    assert not dialog.filter_input.isEnabled()
    one_osm(dialog, "buildings")
    assert dialog.filter_input.isEnabled()


def test_the_label_is_greyed_out_with_the_box(dialog):
    one_osm(dialog, "pois", "buildings")
    assert not dialog.filter_label.isEnabled()
    one_osm(dialog, "pois")
    assert dialog.filter_label.isEnabled()


@pytest.mark.parametrize("keys, expected", [
    ((), "Select a dataset to filter it."),
    (("pois", "buildings"), "applies to one dataset at a time; 2 are selected"),
    (("pois", "buildings", "waterways"), "3 are selected"),
])
def test_the_inline_note_says_why_the_filter_is_off(dialog, keys, expected):
    one_osm(dialog, *keys)
    assert expected in dialog.filter_status.text()
    assert dialog.filter_status.isVisibleTo(dialog)


def test_a_filter_typed_for_one_dataset_does_not_carry_over_to_two(dialog):
    """The box is disabled, so the text cannot be corrected or even seen as
    applying; it must not quietly shrink both downloads either."""
    one_osm(dialog, "pois")
    dialog.filter_input.setText("confidence > 0.9")
    assert dialog.get_where_fragment() == "confidence > 0.9"
    one_osm(dialog, "pois", "buildings")
    assert dialog.get_where_fragment() is None
    one_osm(dialog, "pois")
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_a_custom_url_is_one_dataset_and_an_empty_one_is_none(dialog):
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText("")
    assert not dialog.filter_input.isEnabled()
    dialog.url_input.setText(URL)
    assert dialog.filter_input.isEnabled()


def test_source_cooperative_is_always_one_dataset(dialog):
    dialog.sourcecoop_radio.setChecked(True)
    assert len(dialog.selected_datasets()) == 1
    assert dialog.filter_input.isEnabled()


def test_overture_subtypes_each_count_as_a_dataset(dialog):
    dialog.overture_radio.setChecked(True)
    dialog.base_checkbox.setChecked(True)
    dialog.base_subtype_checkboxes["water"].setChecked(True)
    assert dialog.filter_input.isEnabled()
    dialog.base_subtype_checkboxes["land"].setChecked(True)
    assert not dialog.filter_input.isEnabled()
    assert "2 are selected" in dialog.filter_status.text()


def test_counting_the_selection_never_looks_up_the_overture_release(dialog):
    """It runs on every keystroke; the lookup blocks on the network."""
    dialog.overture_radio.setChecked(True)
    dialog.overture_checkboxes["places"].setChecked(True)
    with patch.object(dialog_module, "latest_overture_release") as lookup:
        urls = dialog.selected_datasets()
        assert dialog.filter_input.isEnabled()
    lookup.assert_not_called()
    assert urls == [
        "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    ]


def test_the_release_is_resolved_by_the_fetch_not_by_the_dialog(dialog):
    """_single_dataset() hands the task a callable that closes over strings
    only, so the blocking lookup happens on the task's own thread."""
    dialog.overture_radio.setChecked(True)
    dialog.overture_checkboxes["places"].setChecked(True)
    key, resolve_url = dialog._single_dataset()
    assert "{release}" in key  # the cache key outlives any one release
    with patch.object(dialog_module, "latest_overture_release", return_value="2026-09-23.1"):
        assert resolve_url() == (
            "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=places/type=place/*"
        )


# --- when the column list is fetched ---------------------------------------

def test_opening_the_dialog_reads_no_schema(qgs_app, mock_iface):
    """Including with a dataset remembered from last time: a dialog that is
    only opened must not start a DuckDB query against the network."""
    with patch.object(dialog_module, "SchemaWorker") as worker, \
            patch.object(dialog_module, "submit") as submit:
        second = new_dialog(mock_iface)
        try:
            one_osm(second, "pois")
            assert not second._schema_timer.isActive()
        finally:
            second.close()
            second.deleteLater()
    worker.assert_not_called()
    submit.assert_not_called()


def test_using_the_filter_box_asks_for_the_columns(dialog):
    one_osm(dialog, "pois")
    assert not dialog._schema_timer.isActive()
    dialog.request_filter_columns()
    assert dialog._schema_timer.isActive()  # debounced, not fired at once
    assert dialog.filter_status.text() == dialog_module.FILTER_READING_COLUMNS


def test_the_keyboard_arriving_in_the_filter_box_asks_for_the_columns(dialog):
    one_osm(dialog, "pois")
    dialog.eventFilter(dialog.filter_input, QEvent(QEvent.Type.FocusIn))
    assert dialog._schema_timer.isActive()


def test_the_fetch_waits_until_a_dataset_is_ticked(dialog):
    one_osm(dialog)
    dialog.request_filter_columns()
    assert not dialog._schema_timer.isActive()
    one_osm(dialog, "pois")
    assert dialog._schema_timer.isActive()  # the engaged filter box is served


def fetch(dialog):
    """Run _start_schema_fetch() with SchemaWorker and submit() patched."""
    with patch.object(dialog_module, "SchemaWorker") as worker_class, \
            patch.object(dialog_module, "submit") as submit:
        worker_class.return_value = MagicMock()
        dialog._start_schema_fetch()
    return worker_class, submit


def test_the_fetch_runs_on_a_task_and_is_dropped_when_that_task_ends(dialog):
    one_osm(dialog, "pois")
    worker_class, submit = fetch(dialog)
    worker_class.assert_called_once()
    assert worker_class.call_args.args[0]() == OSM_POIS  # the resolver
    submit.assert_called_once()
    task = submit.call_args.args[0]
    assert task is dialog.schema_task and task.worker is dialog.schema_worker

    dialog._schema_task_finished(task, True)
    assert dialog.schema_worker is None and dialog.schema_task is None


def test_cancelling_kills_the_worker_and_lets_go_of_the_task(dialog):
    one_osm(dialog, "pois")
    _, submit = fetch(dialog)
    task = submit.call_args.args[0]
    with patch.object(task, "cancel") as cancel:
        dialog.cancel_schema_fetch()
    cancel.assert_called_once()  # WorkerTask.cancel() calls worker.kill()
    assert dialog.schema_worker is None and dialog.schema_task is None


def test_a_report_for_a_dataset_no_longer_ticked_is_not_shown(dialog):
    """The fetch is keyed, not guarded on the worker still being the current
    one: nothing orders a queued emission against the task's finished()."""
    one_osm(dialog, "pois")
    one_osm(dialog, "buildings")
    dialog._schema_arrived(OSM_POIS, SCHEMA)
    assert dialog.filter_columns() == []
    dialog._schema_unavailable(OSM_POIS, "IO Error")
    assert dialog.filter_status.text() != dialog_module.FILTER_NO_COLUMNS
    # but it is kept, so re-ticking it needs no second DESCRIBE
    one_osm(dialog, "pois")
    assert dialog.filter_columns() == SCHEMA


@pytest.mark.parametrize("leave", ["close", "accept", "reject"])
def test_leaving_the_dialog_stops_a_running_fetch(qgs_app, mock_iface, leave):
    leaving = new_dialog(mock_iface)
    one_osm(leaving, "pois")
    _, submit = fetch(leaving)
    task = submit.call_args.args[0]
    try:
        with patch.object(task, "cancel") as cancel:
            getattr(leaving, leave)()
        cancel.assert_called_once()
        assert leaving.schema_worker is None and leaving.schema_task is None
        assert not leaving._schema_timer.isActive()
    finally:
        leaving.deleteLater()


def test_changing_the_selection_supersedes_the_fetch_and_the_columns(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    assert dialog.filter_columns() == SCHEMA
    one_osm(dialog, "buildings")
    assert dialog.filter_columns() == []


def test_a_dataset_read_once_is_not_read_again(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    one_osm(dialog, "buildings")
    one_osm(dialog, "pois")
    assert dialog.filter_columns() == SCHEMA
    worker_class, submit = fetch(dialog)
    worker_class.assert_not_called()
    submit.assert_not_called()


def test_a_schema_that_cannot_be_read_leaves_the_filter_usable(dialog):
    one_osm(dialog, "pois")
    dialog._schema_unavailable(
        dialog._schema_key, "IO Error: No files found that match the pattern"
    )
    assert dialog.filter_input.isEnabled()
    assert dialog.filter_columns() == []
    assert dialog.filter_status.text() == dialog_module.FILTER_NO_COLUMNS
    # and an unchecked name is not called wrong just because nothing is known
    dialog.filter_input.setText("cg = cv")
    assert dialog.filter_status.text() == dialog_module.FILTER_NO_COLUMNS


# --- completion ------------------------------------------------------------

def test_the_columns_reach_the_completer_with_their_types(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    model = dialog._completer_model
    assert model.rowCount() == len(SCHEMA)
    labels = [model.item(row).text() for row in range(model.rowCount())]
    names = [model.item(row).data(Qt.ItemDataRole.UserRole) for row in range(model.rowCount())]
    assert labels[3] == "class  VARCHAR"
    assert labels[2] == "bbox  STRUCT(xmin FLOAT, ymin FLOAT)"
    assert names == [name for name, _type in SCHEMA]


@pytest.mark.parametrize("text, cursor, expected", [
    ("cl", 2, "cl"),
    # mid-expression, which a QCompleter on the line edit cannot do
    ("confidence > 0.9 AND cl", 23, "cl"),
    ("(class = 'a' OR sub", 19, "sub"),
    ("confidence > 0.9 AND ", 21, ""),
    # not where a column name cannot be typed
    ("class = 'scho", 13, None),
    ("names.pri", 9, None),
])
def test_the_completer_takes_the_word_under_the_cursor(dialog, text, cursor, expected):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText(text)
    dialog.filter_input.setCursorPosition(cursor)
    assert dialog.filter_completion_prefix() == expected


def test_a_completion_replaces_only_the_word_under_the_cursor(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("confidence > 0.9 AND cl")
    dialog.filter_input.setCursorPosition(23)
    dialog._insert_completion("class")
    assert dialog.filter_input.text() == "confidence > 0.9 AND class"
    assert dialog.filter_input.cursorPosition() == 26


def test_a_completion_keeps_what_comes_after_the_cursor(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("cl = 'school'")
    dialog.filter_input.setCursorPosition(2)
    dialog._insert_completion("class")
    assert dialog.filter_input.text() == "class = 'school'"


def test_a_completed_name_that_needs_quoting_gets_it(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog, [("my column", "VARCHAR")])
    dialog.filter_input.setText("my")
    dialog.filter_input.setCursorPosition(2)
    dialog._insert_completion("my column")
    assert dialog.filter_input.text() == '"my column"'


def tab_event():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)


def test_tab_in_the_filter_box_is_spent_on_completion(dialog):
    """Without this the key just moves the keyboard out of the field."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("cl")
    dialog.filter_input.setCursorPosition(2)
    with patch.object(dialog, "complete_current_word", return_value=True) as complete:
        assert dialog.eventFilter(dialog.filter_input, tab_event()) is True
    complete.assert_called_once()


def test_tab_is_left_alone_when_there_is_nothing_to_complete(dialog):
    one_osm(dialog, "pois")  # no schema fetched
    assert dialog.complete_current_word() is False
    assert dialog.eventFilter(dialog.filter_input, tab_event()) is False


def test_tab_offers_the_columns_for_a_word_it_can_complete(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("confidence > 0.9 AND sub")
    dialog.filter_input.setCursorPosition(24)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is True
    show.assert_called_once()
    assert dialog._completer.completionPrefix() == "sub"
    assert dialog._completer.currentCompletion() == "subclass"


def test_tab_inside_a_literal_offers_nothing(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("class = 'scho")
    dialog.filter_input.setCursorPosition(13)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is False
    show.assert_not_called()


def test_a_word_matching_no_column_offers_nothing(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("zzz")
    dialog.filter_input.setCursorPosition(3)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is False
    show.assert_not_called()


# --- the inline "spell check" ----------------------------------------------

def test_an_unknown_column_is_named_inline_as_you_type(dialog):
    """The report, answered where it was typed rather than by a modal after
    the download had started."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    with patch.object(dialog_module, "QMessageBox") as boxes:
        dialog.filter_input.setText("cg = cv")
        assert dialog.filter_status.text() == 'Unknown column "cg" - did you mean "class"?'
        dialog.filter_input.setText("class = cv")
        assert dialog.filter_status.text() == 'Unknown column "cv" - did you mean "class"?'
        dialog.filter_input.setText("class = 'school'")
        assert dialog.filter_status.text() == dialog_module.FILTER_COLUMNS_READY.format(
            count=len(SCHEMA)
        )
    assert not boxes.method_calls, boxes.method_calls


@pytest.mark.parametrize("text", [
    "confidence > 0.9",
    "confidence > 0.9 AND class = 'school'",
    "bbox.xmin > 10",
    "names.primary ILIKE '%park%'",
    "CLASS = 'school'",
])
def test_a_filter_on_real_columns_draws_no_complaint(dialog, text):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText(text)
    assert "Unknown column" not in dialog.filter_status.text()


def test_the_lexical_check_runs_as_you_type_not_only_on_ok(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("class = 'a'; DROP TABLE x")
    assert "cannot contain ';'" in dialog.filter_status.text()
    assert dialog_module._STATUS_ERROR in dialog.filter_status.styleSheet()
    assert dialog.filter_input.styleSheet() == dialog_module._FIELD_ERROR
    dialog.filter_input.setText("class = 'a'")
    assert "cannot contain" not in dialog.filter_status.text()
    assert dialog.filter_input.styleSheet() == ""


def test_a_half_typed_literal_is_a_hint_not_an_error(dialog):
    """The closing quote has not been typed yet; a red field would be rude."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("class = 'scho")
    assert "unclosed string" in dialog.filter_status.text()
    assert dialog_module._STATUS_WARNING in dialog.filter_status.styleSheet()
    assert dialog.filter_input.styleSheet() == ""


def test_an_unknown_column_does_not_stop_ok(dialog):
    """It is a warning, not a refusal: the column list may be incomplete, and
    DuckDB has the last word either way."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("cg = cv")
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    with patch.object(dialog_module, "QMessageBox") as boxes:
        dialog.validate_and_accept()
    assert not boxes.method_calls, boxes.method_calls
    assert accepted == [True]


def test_ok_still_refuses_a_hard_error(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("WHERE class = 'a'")
    with patch.object(dialog_module.QMessageBox, "warning") as warning:
        dialog.validate_and_accept()
    warning.assert_called_once()
    assert "Leave out the WHERE keyword" in warning.call_args[0][2]


def test_a_filter_left_over_from_one_dataset_does_not_block_two(dialog):
    """The box is disabled, the text is not used, and OK must not stop for it."""
    one_osm(dialog, "pois")
    dialog.filter_input.setText("WHERE class = 'a'")
    one_osm(dialog, "pois", "buildings")
    with patch.object(dialog_module.QMessageBox, "warning") as warning:
        dialog.validate_and_accept()
    warning.assert_not_called()


def test_the_filter_row_is_still_a_plain_line_edit(dialog):
    """No QLineEdit subclass: the Tab key is taken with an event filter, so
    everything else about the field is Qt's own."""
    assert type(dialog.filter_input) is QLineEdit
