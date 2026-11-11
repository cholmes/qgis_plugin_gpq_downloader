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

from gpq_downloader.core.filters import validate_where_fragment
from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.gui import dialog as dialog_module
from gpq_downloader.gui import settings_store
from gpq_downloader.gui.dialog import FilterHelpDialog

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
    """Tick the filter on and give every selected dataset `schema`."""
    dialog.filter_checkbox.setChecked(True)
    for key in dialog._schema_keys:
        dialog._schema_arrived(key, list(schema))
    return dialog


# --- a filter is a single-dataset option -----------------------------------

def test_a_filter_needs_a_dataset_but_not_only_one(dialog):
    """A filter covers whatever is selected; what it may use is what they all
    have (test_filter_across_datasets.py has the schema rules)."""
    one_osm(dialog)
    assert not dialog.filter_is_available()
    one_osm(dialog, "pois")
    assert dialog.filter_is_available()
    one_osm(dialog, "pois", "buildings")
    assert dialog.filter_is_available()
    one_osm(dialog, "pois", "buildings", "waterways")
    assert dialog.filter_is_available()


# --- the note beside the checkbox, not on a line of its own ---------------

@pytest.mark.parametrize("keys, expected", [
    ((), "Select a dataset to filter it."),
    (("pois",), ""),
    (("pois", "buildings"), "applies to all 2 selected datasets"),
    (("pois", "buildings", "waterways"), "applies to all 3 selected datasets"),
])
def test_the_availability_note_sits_on_the_checkbox_line(dialog, keys, expected):
    one_osm(dialog, *keys)
    assert dialog.filter_note.text() == expected
    assert dialog.filter_note.isVisibleTo(dialog) == bool(expected)
    # and not underneath, where the typing feedback goes
    assert expected not in dialog.filter_status.text() or not expected


def test_the_note_is_on_the_same_row_as_the_checkbox(dialog):
    """Rather than popping out below it."""
    assert dialog.filter_note.parentWidget() is dialog.filter_checkbox.parentWidget()
    row = dialog.layout().indexOf(dialog.filter_checkbox)
    assert row == dialog.layout().indexOf(dialog.filter_note)


def test_the_note_does_not_stretch_the_dialog(dialog):
    """However long it gets, it takes the room left on the row."""
    one_osm(dialog, "pois")
    narrow = dialog.minimumSizeHint().width()
    one_osm(dialog, "pois", "buildings", "waterways")
    assert dialog.filter_note.text()
    assert dialog.minimumSizeHint().width() == narrow
    dialog.filter_note.setText("a note far longer than any of these messages " * 4)
    assert dialog.minimumSizeHint().width() == narrow


def test_a_filter_typed_for_one_dataset_carries_over_to_two(dialog):
    """The owner's case: Buildings and Building Parts filtered together. The
    plugin hands the fragment to every job, so it applies to both."""
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    assert dialog.get_where_fragment() == "confidence > 0.9"
    one_osm(dialog, "pois", "buildings")
    assert dialog.filter_row.isVisibleTo(dialog)
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_a_custom_url_is_one_dataset_and_an_empty_one_is_none(dialog):
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText("")
    assert not dialog.filter_is_available()
    dialog.url_input.setText(URL)
    assert dialog.filter_is_available()


def test_overture_subtypes_each_count_as_a_dataset(dialog):
    dialog.overture_radio.setChecked(True)
    dialog.base_checkbox.setChecked(True)
    dialog.base_subtype_checkboxes["water"].setChecked(True)
    assert len(dialog.selected_datasets()) == 1
    assert dialog.filter_note.text() == ""
    dialog.base_subtype_checkboxes["land"].setChecked(True)
    assert len(dialog.selected_datasets()) == 2
    assert dialog.filter_note.text() == "applies to all 2 selected datasets"


def test_counting_the_selection_never_looks_up_the_overture_release(dialog):
    """It runs on every keystroke; the lookup blocks on the network."""
    dialog.overture_radio.setChecked(True)
    dialog.overture_checkboxes["places"].setChecked(True)
    with patch.object(dialog_module, "latest_overture_release") as lookup:
        urls = dialog.selected_datasets()
        assert dialog.filter_is_available()
    lookup.assert_not_called()
    assert urls == [
        "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    ]


def test_the_release_is_resolved_by_the_fetch_not_by_the_dialog(dialog):
    """selected_sources() hands each task a callable that closes over strings
    only, so the blocking lookup happens on the task's own thread."""
    dialog.overture_radio.setChecked(True)
    dialog.overture_checkboxes["places"].setChecked(True)
    (key, resolve_url), = dialog.selected_sources()
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


def test_the_fetch_is_debounced_rather_than_fired_at_once(dialog):
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    assert dialog._schema_timer.isActive()
    assert dialog._schema_workers == {}  # nothing submitted yet


def test_the_fetch_waits_until_a_dataset_is_selected(dialog):
    """A filter asked for with nothing selected is served the moment one is."""
    one_osm(dialog)
    dialog.filter_checkbox.setChecked(True)
    assert not dialog._schema_timer.isActive()
    one_osm(dialog, "pois")
    assert dialog._schema_timer.isActive()


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
    assert dialog._schema_tasks == {OSM_POIS: task}
    assert set(dialog._schema_workers) == {OSM_POIS}

    dialog._schema_task_finished(task, True)
    assert dialog._schema_tasks == {} and dialog._schema_workers == {}


def test_one_fetch_per_selected_dataset(dialog):
    one_osm(dialog, "pois", "buildings", "waterways")
    _, submit = fetch(dialog)
    assert submit.call_count == 3
    assert set(dialog._schema_tasks) == set(dialog.selected_datasets())


def test_a_dataset_already_read_is_not_fetched_again_with_the_others(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    one_osm(dialog, "pois", "buildings")
    worker_class, submit = fetch(dialog)
    assert submit.call_count == 1  # buildings only
    assert set(dialog._schema_tasks) == {
        "https://data.openstreetmap.us/layercake/buildings.parquet"
    }


def test_cancelling_kills_every_worker_and_lets_go_of_every_task(dialog):
    one_osm(dialog, "pois", "buildings")
    _, submit = fetch(dialog)
    tasks = [call.args[0] for call in submit.call_args_list]
    with patch.object(tasks[0], "cancel") as first, \
            patch.object(tasks[1], "cancel") as second:
        dialog.cancel_schema_fetch()
    first.assert_called_once()  # WorkerTask.cancel() calls worker.kill()
    second.assert_called_once()
    assert dialog._schema_tasks == {} and dialog._schema_workers == {}


def test_a_report_for_a_dataset_no_longer_selected_is_not_shown(dialog):
    """The fetch is keyed, not guarded on the worker still being a current
    one: nothing orders a queued emission against the task's finished()."""
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    one_osm(dialog, "buildings")
    dialog._schema_arrived(OSM_POIS, SCHEMA)
    assert dialog.filter_columns() == []
    dialog._schema_unavailable(OSM_POIS, "IO Error")
    assert dialog.filter_status.text() != dialog_module.FILTER_NO_COLUMNS
    # but it is kept, so reselecting it needs no second DESCRIBE
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
        assert leaving._schema_tasks == {} and leaving._schema_workers == {}
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
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_unavailable(
        OSM_POIS, "IO Error: No files found that match the pattern"
    )
    assert dialog.filter_row.isVisibleTo(dialog)
    assert dialog.filter_columns() == []
    assert dialog.filter_status.text() == dialog_module.FILTER_NO_COLUMNS
    # and an unchecked name is not called wrong just because nothing is known
    dialog.filter_input.setText("cg = cv")
    assert dialog.filter_status.text() == dialog_module.FILTER_NO_COLUMNS


# --- completion ------------------------------------------------------------

def test_the_columns_reach_the_completer_with_their_types(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog._set_completion_candidates(())
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
    # a struct field is the word, with the struct as its path
    ("names.pri", 9, "pri"),
    # not where a column name cannot be typed at all
    ("class = 'scho", 13, None),
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
    one_osm(dialog, "pois")  # no schema fetched, and the filter not asked for
    assert dialog.complete_current_word() is False
    assert dialog.eventFilter(dialog.filter_input, tab_event()) is False


# Tab completes shell-style (#231): one match is typed out, several type what
# they share and then list what is left.

def test_tab_finishes_a_word_when_only_one_column_matches(dialog):
    """The report: `sources[1].up` matched one field and still opened a popup
    with a single row in it, waiting for a second Tab."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("confidence > 0.9 AND sub")
    dialog.filter_input.setCursorPosition(24)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is True
    show.assert_not_called()
    assert dialog.filter_input.text() == "confidence > 0.9 AND subclass"
    assert dialog.filter_input.cursorPosition() == len("confidence > 0.9 AND subclass")


def test_tab_finishes_a_struct_field_inside_a_list(dialog):
    """The owner's own example, as Overture really spells it."""
    one_osm(dialog, "pois")
    with_schema(dialog, [
        ("id", "VARCHAR"),
        ("sources", "STRUCT(property VARCHAR, update_time VARCHAR)[]"),
    ])
    dialog.filter_input.setText("sources[1].up")
    dialog.filter_input.setCursorPosition(13)
    assert dialog.complete_current_word() is True
    assert dialog.filter_input.text() == "sources[1].update_time"


def test_tab_types_as_much_as_several_names_share_then_lists_them(dialog):
    one_osm(dialog, "pois")
    with_schema(dialog, [
        ("update_time", "VARCHAR"),
        ("update_type", "VARCHAR"),
        ("id", "VARCHAR"),
    ])
    dialog.filter_input.setText("up")
    dialog.filter_input.setCursorPosition(2)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is True
    assert dialog.filter_input.text() == "update_t"
    show.assert_called_once()


def test_tab_only_lists_when_the_matches_share_nothing_more(dialog):
    """`c` matches class and confidence, which agree on no more than the c
    already typed: there is nothing to type, so the names are shown."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("c")
    dialog.filter_input.setCursorPosition(1)
    with patch.object(dialog._completer, "complete") as show:
        assert dialog.complete_current_word() is True
    assert dialog.filter_input.text() == "c"
    show.assert_called_once()
    assert dialog._completer.completionPrefix() == "c"


def test_tab_with_the_popup_open_still_takes_the_highlighted_name(dialog):
    """Unchanged: once a list is on screen Tab accepts what is selected."""
    one_osm(dialog, "pois")
    with_schema(dialog)
    dialog.filter_input.setText("c")
    dialog.filter_input.setCursorPosition(1)
    dialog.complete_current_word()  # opens the list
    popup = dialog._completer.popup()
    with patch.object(popup, "isVisible", return_value=True):
        popup.setCurrentIndex(dialog._completer.completionModel().index(0, 0))
        assert dialog.complete_current_word() is True
    assert dialog.filter_input.text() in ("class", "confidence")


def test_a_half_typed_shared_opening_is_never_quoted(dialog):
    """`select` out of select/selector would be quoted as a finished name,
    which would close a word the next keystroke means to carry on."""
    one_osm(dialog, "pois")
    with_schema(dialog, [("select", "VARCHAR"), ("selector", "VARCHAR")])
    dialog.filter_input.setText("sel")
    dialog.filter_input.setCursorPosition(3)
    with patch.object(dialog._completer, "complete"):
        dialog.complete_current_word()
    assert dialog.filter_input.text() == "select"


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


# --- the "Filter data by attribute" checkbox (#217) --------------------------------
#
# The owner asked for the filter to work like "Clip features to the area":
# off by default, and ticking it is what reveals the field and reads the
# column list. Nothing is fetched or shown until then.

def test_the_filter_starts_off_with_no_field_and_no_fetch(dialog):
    one_osm(dialog, "pois")
    assert not dialog.filter_checkbox.isChecked()
    assert not dialog.filter_row.isVisibleTo(dialog)
    assert not dialog._schema_timer.isActive()
    assert dialog.filter_status.text() == ""


def test_ticking_it_reveals_the_field_and_asks_for_the_columns(dialog):
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    assert dialog.filter_row.isVisibleTo(dialog)
    assert dialog._schema_timer.isActive()
    assert dialog.filter_status.text() == dialog_module.FILTER_READING_COLUMNS


def test_unticking_it_hides_the_field_and_stops_the_fetch(dialog):
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_checkbox.setChecked(False)
    assert not dialog.filter_row.isVisibleTo(dialog)
    assert not dialog._schema_timer.isActive()
    assert dialog._schema_workers == {}


def test_an_unticked_filter_never_reaches_a_download(dialog):
    """An invisible filter must not quietly shrink anything; the text is kept
    so that re-ticking restores what was typed."""
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    assert dialog.get_where_fragment() == "confidence > 0.9"
    dialog.filter_checkbox.setChecked(False)
    assert dialog.get_where_fragment() is None
    assert dialog.filter_input.text() == "confidence > 0.9"
    dialog.filter_checkbox.setChecked(True)
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_an_unticked_filter_cannot_block_ok(dialog):
    one_osm(dialog, "pois")
    dialog.filter_input.setText("WHERE class = 'a'")
    with patch.object(dialog_module.QMessageBox, "warning") as warning:
        dialog.validate_and_accept()
    warning.assert_not_called()


def test_the_checkbox_says_it_needs_a_dataset_rather_than_greying(dialog):
    """Never disabled, like "Clip features to the area".

    The owner asked for the two to behave alike (#231). With nothing
    selected the note carries the reason, where a grey box gave none, and
    the tick survives until a dataset makes it mean something.
    """
    one_osm(dialog, "pois")
    assert dialog.filter_checkbox.isEnabled()
    one_osm(dialog, "pois", "buildings")
    assert dialog.filter_checkbox.isEnabled()
    one_osm(dialog)
    assert dialog.filter_checkbox.isEnabled()
    assert dialog.filter_note.text() == dialog_module.FILTER_NEEDS_A_DATASET


def test_both_option_checkboxes_stay_tickable_with_nothing_selected(dialog):
    """The pair the owner compared: neither greys out."""
    one_osm(dialog)
    assert dialog.clip_checkbox.isEnabled()
    assert dialog.filter_checkbox.isEnabled()


def test_ticking_with_no_dataset_shows_no_field_and_fetches_nothing(dialog):
    """Enabled is not the same as active: an unbacked filter stays inert and
    cannot reach a download."""
    one_osm(dialog)
    dialog.filter_checkbox.setChecked(True)
    assert not dialog.filter_row.isVisibleTo(dialog)
    assert not dialog._schema_timer.isActive()
    assert dialog.get_where_fragment() is None
    assert dialog.filter_note.text() == dialog_module.FILTER_NEEDS_A_DATASET


def test_a_filter_left_behind_by_a_deselected_dataset_cannot_shrink_a_download(dialog):
    """The hazard the grey-out used to cover.

    Typing a filter and then unticking the dataset leaves a ticked box, kept
    text and no field on screen. It must not reach a download; re-selecting
    brings it back.
    """
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    assert dialog.get_where_fragment() == "confidence > 0.9"
    one_osm(dialog)
    assert not dialog.filter_row.isVisibleTo(dialog)
    assert dialog.get_where_fragment() is None
    one_osm(dialog, "pois")
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_a_tick_made_early_takes_effect_once_a_dataset_is_picked(dialog):
    one_osm(dialog)
    dialog.filter_checkbox.setChecked(True)
    one_osm(dialog, "pois")
    assert dialog.filter_row.isVisibleTo(dialog)
    assert dialog._schema_timer.isActive()


def test_the_tick_is_not_remembered_between_sessions(qgs_app, mock_iface):
    """The text is remembered now (#231); the tick is what must not be.

    A filter restored out of sight would shrink a download the user had not
    asked to filter, and the field is only on screen once the box is ticked.
    """
    dialog = new_dialog(mock_iface)
    try:
        one_osm(dialog, "pois")
        dialog.filter_checkbox.setChecked(True)
        # save_value, not QgsSettings: the autouse fixture above patches
        # save_value, so a QgsSettings mock would see no calls at all and any
        # "nothing was saved" assertion would hold whatever the code did.
        with patch("gpq_downloader.gui.settings_store.save_value") as save:
            dialog.save_checkbox_states()
        saved = [str(call.args[0]) for call in save.call_args_list]
        assert settings_store.ATTRIBUTE_FILTER in saved
        assert not any("checkbox" in key.lower() and "filter" in key.lower()
                       for key in saved)
    finally:
        dialog.cancel_schema_fetch()
        dialog.close()
        dialog.deleteLater()
    second = new_dialog(mock_iface)
    try:
        assert not second.filter_checkbox.isChecked()
    finally:
        second.close()
        second.deleteLater()


# --- struct field completion (#217) ---------------------------------------

DIVISIONS = [
    ("id", "VARCHAR"),
    ("subtype", "VARCHAR"),
    ("bbox", "STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE)"),
    ("names", 'STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR), '
              "rules STRUCT(variant VARCHAR)[])"),
    ("sources", "STRUCT(property VARCHAR, dataset VARCHAR)[]"),
]


def ticked(dialog, schema=DIVISIONS):
    """One dataset, the filter ticked, and its column list in place."""
    one_osm(dialog, "pois")
    return with_schema(dialog, schema)


@pytest.mark.parametrize("text, cursor, expected", [
    ("names.", 6, ["primary", "common", "rules"]),
    ("names.pri", 9, ["primary"]),
    ("bbox.", 5, ["xmin", "xmax", "ymin", "ymax"]),
    ("bbox.xm", 7, ["xmin", "xmax"]),
    ("subtype = 'a' AND names.", 24, ["primary", "common", "rules"]),
    # the indexed form a list of structs needs
    ("sources[1].", 11, ["property", "dataset"]),
    # a bare dot on a list offers nothing: dot access is a binder error there
    ("sources.", 8, []),
    ("names.rules.", 12, []),
    # a MAP's keys are not in the schema to offer
    ("names.common.", 13, []),
    # the top level, for comparison
    ("", 0, ["id", "subtype", "bbox", "names", "sources"]),
])
def test_the_completer_offers_the_fields_of_the_struct_in_front_of_the_cursor(
    dialog, text, cursor, expected
):
    ticked(dialog)
    dialog.filter_input.setText(text)
    dialog.filter_input.setCursorPosition(cursor)
    assert dialog.completions_at_cursor() == expected


def test_a_field_completion_shows_its_type(dialog):
    ticked(dialog)
    dialog.filter_input.setText("names.")
    dialog.filter_input.setCursorPosition(6)
    dialog._show_completions(offer_all=True)
    model = dialog._completer_model
    labels = {
        model.item(row).data(Qt.ItemDataRole.UserRole): model.item(row).text()
        for row in range(model.rowCount())
    }
    assert labels["primary"] == "primary  VARCHAR"
    assert labels["common"] == "common  MAP(VARCHAR, VARCHAR)"


def test_completing_from_inside_a_word_replaces_all_of_it(dialog):
    """The report: type `classification`, press Left five times, press Tab.
    current_word() reads only what is in front of the cursor, so the chosen
    name used to go in ahead of the tail that was still there.
    """
    ticked(dialog)
    dialog.filter_input.setText("classification")
    dialog.filter_input.setCursorPosition(5)
    dialog._insert_completion("classification")
    assert dialog.filter_input.text() == "classification"


def test_completing_inside_a_word_keeps_the_rest_of_the_condition(dialog):
    """Only the word is replaced; what follows it is not a name and stays."""
    ticked(dialog)
    dialog.filter_input.setText("classif = 'x' AND height > 1")
    dialog.filter_input.setCursorPosition(4)
    dialog._insert_completion("class")
    assert dialog.filter_input.text() == "class = 'x' AND height > 1"


def test_completing_a_field_replaces_only_the_last_part(dialog):
    ticked(dialog)
    dialog.filter_input.setText("names.pri")
    dialog.filter_input.setCursorPosition(9)
    dialog._insert_completion("primary")
    assert dialog.filter_input.text() == "names.primary"


def test_a_field_is_completed_unquoted_even_when_it_is_a_reserved_word(dialog):
    """names.primary binds in DuckDB; "primary" everywhere would be noise."""
    ticked(dialog)
    dialog.filter_input.setText("names.")
    dialog.filter_input.setCursorPosition(6)
    dialog._insert_completion("primary")
    assert dialog.filter_input.text() == "names.primary"


@pytest.mark.parametrize("field, expected", [
    ("a AND 1=1", 'names."a AND 1=1"'),  # would otherwise go in as syntax
    ('odd"name', 'names."odd""name"'),  # the stray quote is doubled, not loose
    ("from", 'names."from"'),  # names."from" binds; bare FROM is refused
])
def test_a_field_whose_name_is_not_a_plain_word_is_quoted(dialog, field, expected):
    """A field name is read off a remote file's type string, so it is as
    untrusted as the rest of it. Typing one in bare would let the data put
    syntax in the user's filter, or a quote that leaves the box
    unvalidatable with nothing on screen to say where it came from.
    """
    ticked(dialog)
    dialog.filter_input.setText("names.")
    dialog.filter_input.setCursorPosition(6)
    dialog._insert_completion(field)
    assert dialog.filter_input.text() == expected
    assert validate_where_fragment(dialog.filter_input.text()) is None


def test_the_candidate_list_follows_the_path_as_the_cursor_moves(dialog):
    ticked(dialog)
    dialog.filter_input.setText("bbox.xmin > 1 AND names.pri")
    dialog.filter_input.setCursorPosition(27)
    assert dialog.completions_at_cursor() == ["primary"]
    dialog.filter_input.setCursorPosition(9)
    assert dialog.completions_at_cursor() == ["xmin"]


def test_a_new_dataset_drops_the_old_fields(dialog):
    ticked(dialog)
    dialog.filter_input.setText("names.")
    dialog.filter_input.setCursorPosition(6)
    assert dialog.completions_at_cursor() == ["primary", "common", "rules"]
    one_osm(dialog, "buildings")
    assert dialog.completions_at_cursor() == []


# --- the quote trap, and paths, said inline -------------------------------

def test_the_double_quoted_value_trap_is_named(dialog):
    """What the owner hit: `names.primary = "Jack Park"` used to come back as
    `Unknown column "Jack Park"`, which is true and no help at all."""
    ticked(dialog)
    with patch.object(dialog_module, "QMessageBox") as boxes:
        dialog.filter_input.setText('names.primary = "Jack Park"')
        assert dialog.filter_status.text() == (
            '"Jack Park" is not a column. '
            "For a text value use single quotes: 'Jack Park'"
        )
        dialog.filter_input.setText("names.primary = 'Jack Park'")
        assert "is not a column" not in dialog.filter_status.text()
    assert not boxes.method_calls, boxes.method_calls


@pytest.mark.parametrize("text, expected", [
    ("names.primry = 'x'", 'Unknown field "names.primry" - did you mean "names.primary"?'),
    ("sources.property = 'p'",
     '"sources" is a list - use sources[1].property, not sources.property.'),
    ("sources[1].property = 'p'", dialog_module.FILTER_COLUMNS_READY.format(count=5)),
    ("names.common.en = 'x'", dialog_module.FILTER_COLUMNS_READY.format(count=5)),
])
def test_a_struct_path_is_checked_inline_too(dialog, text, expected):
    ticked(dialog)
    dialog.filter_input.setText(text)
    assert dialog.filter_status.text() == expected


def test_a_path_problem_does_not_stop_ok(dialog):
    ticked(dialog)
    dialog.filter_input.setText('names.primary = "Jack Park"')
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    with patch.object(dialog_module, "QMessageBox") as boxes:
        dialog.validate_and_accept()
    assert not boxes.method_calls, boxes.method_calls
    assert accepted == [True]


# --- the help button (#217) ------------------------------------------------

def test_the_help_button_opens_a_non_modal_window(dialog):
    """Never a QMessageBox instance and never exec(): a real modal in CI
    wedges the run with no timeout."""
    with patch.object(dialog_module, "FilterHelpDialog") as help_class:
        dialog.show_filter_help()
        dialog.show_filter_help()  # and again: one window, raised, not two
    help_class.assert_called_once_with(dialog)
    assert help_class.return_value.show.call_count == 2
    help_class.return_value.exec.assert_not_called()
    help_class.return_value.exec_.assert_not_called()


def test_the_help_button_is_wired_to_it(dialog):
    with patch.object(dialog_module, "FilterHelpDialog") as help_class:
        dialog.filter_help_button.click()
    help_class.assert_called_once_with(dialog)
    help_class.return_value.show.assert_called_once()


def test_the_help_window_is_closed_with_the_dialog(qgs_app, mock_iface):
    leaving = new_dialog(mock_iface)
    try:
        with patch.object(dialog_module, "FilterHelpDialog") as help_class:
            leaving.show_filter_help()
            leaving.close()
        help_class.return_value.close.assert_called_once()
        assert leaving._filter_help is None
    finally:
        leaving.deleteLater()


@pytest.mark.parametrize("expected", [
    "names.primary = 'Jack Park'",
    "names.primary = \"Jack Park\"",
    "bbox.xmin &gt; 7",
    "sources[1].property",
    "sources.property",
    "LIKE 'Jack%'",
    "ILIKE '%park%'",
    "IN ('locality', 'county')",
    "IS NOT NULL",
    "Jack''s Park",
    "confidence &gt; 0.9",
    "is_land = true",
    "&lt;&gt; 'locality'",
    "NOT IN ('locality', 'county')",
    "sources[1].update_time &gt; '2018'",
    "AND",
    "OR",
    "WHERE",
    "Subqueries",
    "read_text()",
    "semicolon",
    "Tab",
])
def test_the_help_text_covers_what_the_owner_asked_for(expected):
    assert expected in dialog_module.FILTER_HELP_HTML


def test_the_help_leads_with_the_quote_trap():
    """It is what actually bit, so it comes before the rest."""
    html = dialog_module.FILTER_HELP_HTML
    assert html.index("single quotes") < html.index("bbox.xmin")
    assert html.index("single quotes") < html.index("sources[1]")


def test_the_help_reaches_no_network():
    """Static plugin text, so the rich text is safe; it stays that way."""
    assert "http" not in dialog_module.FILTER_HELP_HTML
    assert "<img" not in dialog_module.FILTER_HELP_HTML


def test_the_help_window_is_not_modal_and_shows_the_text(qgs_app):
    help_dialog = FilterHelpDialog(None)
    try:
        assert not help_dialog.isModal()
        assert not help_dialog.browser.openExternalLinks()
        assert "single quotes" in help_dialog.browser.toPlainText()
    finally:
        help_dialog.deleteLater()


def test_a_half_typed_name_the_popup_is_offering_is_not_called_unknown(dialog):
    """`names.pri` is three letters of names.primary, not a mistake; the
    message waits until the word is finished or really wrong."""
    ticked(dialog)
    dialog.filter_input.setText("names.pri")
    dialog.filter_input.setCursorPosition(9)
    assert "Unknown" not in dialog.filter_status.text()
    assert dialog.completions_at_cursor() == ["primary"]

    # finished, and wrong: now it is reported
    dialog.filter_input.setText("names.primry")
    dialog.filter_input.setCursorPosition(12)
    assert dialog.filter_status.text() == (
        'Unknown field "names.primry" - did you mean "names.primary"?'
    )

    # and a name nothing matches is reported while it is still being typed
    dialog.filter_input.setText("names.zq")
    dialog.filter_input.setCursorPosition(8)
    assert "Unknown field" in dialog.filter_status.text()


def test_a_problem_elsewhere_is_still_reported_while_a_word_is_typed(dialog):
    """Only the name under the cursor is given the benefit of the doubt."""
    ticked(dialog)
    dialog.filter_input.setText("zzzz = 1 AND names.pri")
    dialog.filter_input.setCursorPosition(22)
    assert dialog.filter_status.text() == (
        'Unknown column "zzzz" - the data has no column by that name.'
    )


# --- a filter over several datasets (#217) ---------------------------------
#
# The owner asked for Buildings and Building Parts to be filterable together.
# They share 23 of their columns and differ in four, so the rule is the
# schemas themselves rather than a hardcoded pair: completion comes from the
# intersection, and a column only some of them have is a warning naming the
# one without it.

BUILDINGS = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("names", 'STRUCT("primary" VARCHAR)'),
    ("height", "DOUBLE"),
    ("class", "VARCHAR"),
]
BUILDING_PARTS = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("names", 'STRUCT("primary" VARCHAR)'),
    ("height", "DOUBLE"),
    ("building_id", "VARCHAR"),
]
OSM_BUILDINGS = "https://data.openstreetmap.us/layercake/buildings.parquet"


def two_datasets(dialog, first=BUILDINGS, second=BUILDING_PARTS):
    """pois and buildings selected, the filter ticked, a schema each."""
    one_osm(dialog, "pois", "buildings")
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_arrived(OSM_POIS, list(first))
    dialog._schema_arrived(OSM_BUILDINGS, list(second))
    return dialog


def test_completion_offers_only_the_columns_both_datasets_have(dialog):
    two_datasets(dialog)
    assert [name for name, _type in dialog.filter_columns()] == [
        "id", "geometry", "names", "height"
    ]
    dialog.filter_input.setText("")
    dialog.filter_input.setCursorPosition(0)
    assert dialog.completions_at_cursor() == ["id", "geometry", "names", "height"]


def test_a_struct_shared_by_both_still_completes_its_fields(dialog):
    two_datasets(dialog)
    dialog.filter_input.setText("names.")
    dialog.filter_input.setCursorPosition(6)
    assert dialog.completions_at_cursor() == ["primary"]


@pytest.mark.parametrize("text, expected", [
    ("class = 'school'",
     'Column "class" is not in every selected dataset (missing from "buildings").'),
    ("building_id IS NOT NULL",
     'Column "building_id" is not in every selected dataset (missing from "pois").'),
])
def test_a_column_only_one_of_them_has_names_the_one_without(dialog, text, expected):
    two_datasets(dialog)
    dialog.filter_input.setText(text)
    assert dialog.filter_status.text() == expected


def test_a_column_neither_has_keeps_the_unknown_message(dialog):
    two_datasets(dialog)
    dialog.filter_input.setText("zzzzzz = 1")
    assert dialog.filter_status.text() == (
        'Unknown column "zzzzzz" - the data has no column by that name.'
    )


def test_a_shared_column_draws_no_complaint(dialog):
    two_datasets(dialog)
    dialog.filter_input.setText("height > 10 AND names.primary = 'Flatiron'")
    assert dialog.filter_status.text() == dialog_module.FILTER_SHARED_COLUMNS.format(
        count=4, datasets=2
    )


def test_the_status_says_how_many_columns_the_datasets_share(dialog):
    two_datasets(dialog)
    assert dialog.filter_status.text() == (
        "4 columns shared by all 2 datasets - press Tab to complete a name."
    )


def test_datasets_with_nothing_in_common_say_so(dialog):
    """The honest result where the intersection is empty, rather than a field
    that looks broken."""
    two_datasets(dialog, [("a", "VARCHAR")], [("b", "VARCHAR")])
    assert dialog.filter_columns() == []
    assert dialog.filter_status.text() == (
        dialog_module.FILTER_NO_SHARED_COLUMNS.format(datasets=2)
    )
    assert "no columns in common" in dialog.filter_status.text()


def test_a_thin_intersection_is_explained_by_the_count(dialog):
    """Six Base subtypes share seven plumbing columns; the line says so."""
    two_datasets(dialog, [("id", "VARCHAR"), ("class", "VARCHAR")], [("id", "VARCHAR")])
    assert dialog.filter_status.text() == (
        "1 columns shared by all 2 datasets - press Tab to complete a name."
    )


def test_nothing_is_offered_until_every_dataset_has_reported(dialog):
    """The intersection of the ones that have could offer a column the next
    one has not got."""
    one_osm(dialog, "pois", "buildings")
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_arrived(OSM_POIS, BUILDINGS)
    assert dialog.filter_columns() == []
    assert dialog.filter_status.text() == dialog_module.FILTER_READING_COLUMNS
    dialog._schema_arrived(OSM_BUILDINGS, BUILDING_PARTS)
    assert [name for name, _ in dialog.filter_columns()] == [
        "id", "geometry", "names", "height"
    ]


def test_a_dataset_whose_columns_could_not_be_read_is_left_out(dialog):
    """Not taken as having no columns, which would make everything look
    missing from it."""
    one_osm(dialog, "pois", "buildings")
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_arrived(OSM_POIS, BUILDINGS)
    dialog._schema_unavailable(OSM_BUILDINGS, "IO Error")
    dialog.filter_input.setText("class = 'school'")
    assert "not in every selected dataset" not in dialog.filter_status.text()
    assert "Unknown" not in dialog.filter_status.text()


def test_dropping_back_to_one_dataset_offers_its_own_columns_again(dialog):
    two_datasets(dialog)
    assert len(dialog.filter_columns()) == 4
    one_osm(dialog, "pois")
    assert dialog.filter_columns() == BUILDINGS
    dialog.filter_input.setText("class = 'school'")
    assert dialog.filter_status.text() == dialog_module.FILTER_COLUMNS_READY.format(
        count=5
    )


def test_a_mixed_column_does_not_stop_ok(dialog):
    """A warning, not a refusal: the download reports what DuckDB says."""
    two_datasets(dialog)
    dialog.filter_input.setText("class = 'school'")
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    with patch.object(dialog_module, "QMessageBox") as boxes:
        dialog.validate_and_accept()
    assert not boxes.method_calls, boxes.method_calls
    assert accepted == [True]


OVERTURE_BUILDING = (
    "s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*"
)
OVERTURE_PART = (
    "s3://overturemaps-us-west-2/release/{release}/"
    "theme=buildings/type=building_part/*"
)


def test_the_dataset_names_in_a_message_come_off_the_urls(dialog):
    """Named from the URL, not from the widgets, so nothing here depends on
    how the Overture page is built - #230 is reshaping it. The keys are set
    directly for the same reason; the message itself is exercised through the
    OpenStreetMap page, whose names are stable."""
    dialog._schema_keys = (OVERTURE_BUILDING, OVERTURE_PART)
    dialog._schema_arrived(OVERTURE_BUILDING, BUILDINGS)
    dialog._schema_arrived(OVERTURE_PART, BUILDING_PARTS)
    assert [label for label, _columns in dialog.filter_schemas()] == [
        "Building", "Building Part"
    ]


def test_every_checkbox_on_the_source_pages_updates_the_filter(dialog):
    """Found rather than named: the pages keep gaining presets, subtypes and
    modifiers, and one this did not know about would leave the filter
    describing a selection the user had already changed."""
    from qgis.PyQt.QtWidgets import QCheckBox

    checkboxes = dialog.stack.findChildren(QCheckBox)
    assert len(checkboxes) > 10
    dialog.osm_radio.setChecked(True)
    for checkbox in checkboxes:
        if checkbox not in dialog.osm_checkboxes.values():
            continue
        before = len(dialog.selected_datasets())
        checkbox.setChecked(not checkbox.isChecked())
        # the note is recomputed, so the selection was noticed
        assert len(dialog.selected_datasets()) != before
        assert dialog.filter_note.text() == dialog.filter_availability_message()
    # and the Clip checkbox, which is not a dataset, is left out of it
    assert dialog.clip_checkbox not in checkboxes


# --- the help text register ------------------------------------------------

def help_text():
    """The help with its line wrapping collapsed, so a phrase can be looked
    for without caring where the source happens to break."""
    return " ".join(dialog_module.FILTER_HELP_HTML.split())


@pytest.mark.parametrize("phrase", [
    "This is the one that catches everyone",
    "For an apostrophe inside a value",
    "a dot will not follow one",
    "Give it an element number first",
    "has a value at all",
    "any of these",
    "Reach into a struct",
    "your own DuckDB",
    "could do more than filter",
])
def test_the_help_drops_the_chatty_phrasing(phrase):
    assert phrase not in help_text()


@pytest.mark.parametrize("phrase", [
    "To include an apostrophe in a value, double it",
    "Dot access does not apply to a list; index an element first",
    "A dot reads a field of a struct",
    "the local DuckDB",
])
def test_the_help_states_it_plainly_instead(phrase):
    assert phrase in help_text()


def test_the_help_shows_not_equal_and_the_null_it_leaves_out():
    """Asked for by the owner (#231). <> is worth little without the warning
    that a null row matches neither it nor =."""
    html = help_text()
    assert "&lt;&gt; 'locality'" in html
    assert "not equal to" in html
    assert "<code>!=</code> means the same" in html
    assert "matches neither <code>=</code> nor" in html


def test_the_help_shows_filtering_by_overture_update_time():
    """The owner's example. update_time is VARCHAR in Overture, not a
    timestamp, so the help has to say why comparing it with text works at
    all, or the next person writes a cast that is not needed."""
    html = help_text()
    assert "sources[1].update_time &gt; '2018'" in html
    assert "ISO-8601" in html
    assert "sort in the same order as the dates" in html
    # sources is a list, so it reads one entry and says so
    assert "reads the first source of each feature" in html


def test_the_help_explains_filtering_several_datasets():
    html = help_text()
    assert "A filter applies to every selected dataset" in html
    assert "Only the columns they all have can be used" in html
    assert "Buildings and Building Parts" in html
