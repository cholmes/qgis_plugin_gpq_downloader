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
    """Tick the filter on and hand it a column list, as a fetch would."""
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_arrived(dialog._schema_key, list(schema))
    return dialog


# --- a filter is a single-dataset option -----------------------------------

def test_the_filter_is_offered_only_for_exactly_one_dataset(dialog):
    one_osm(dialog)
    assert not dialog.filter_is_available()
    one_osm(dialog, "pois")
    assert dialog.filter_is_available()
    one_osm(dialog, "pois", "buildings")
    assert not dialog.filter_is_available()
    one_osm(dialog, "buildings")
    assert dialog.filter_is_available()


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
    """The field is put away, so the text cannot be corrected or even seen as
    applying; it must not quietly shrink both downloads either."""
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    assert dialog.get_where_fragment() == "confidence > 0.9"
    one_osm(dialog, "pois", "buildings")
    assert dialog.get_where_fragment() is None
    one_osm(dialog, "pois")
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_a_custom_url_is_one_dataset_and_an_empty_one_is_none(dialog):
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText("")
    assert not dialog.filter_is_available()
    dialog.url_input.setText(URL)
    assert dialog.filter_is_available()


def test_source_cooperative_is_always_one_dataset(dialog):
    dialog.sourcecoop_radio.setChecked(True)
    assert len(dialog.selected_datasets()) == 1
    assert dialog.filter_is_available()


def test_overture_subtypes_each_count_as_a_dataset(dialog):
    dialog.overture_radio.setChecked(True)
    dialog.base_checkbox.setChecked(True)
    dialog.base_subtype_checkboxes["water"].setChecked(True)
    assert dialog.filter_is_available()
    dialog.base_subtype_checkboxes["land"].setChecked(True)
    assert not dialog.filter_is_available()
    assert "2 are selected" in dialog.filter_status.text()


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


def test_the_fetch_is_debounced_rather_than_fired_at_once(dialog):
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    assert dialog._schema_timer.isActive()
    assert dialog.schema_worker is None  # nothing submitted yet


def test_the_fetch_waits_until_a_dataset_is_ticked(dialog):
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
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_unavailable(
        dialog._schema_key, "IO Error: No files found that match the pattern"
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


# --- the "Attribute filter" checkbox (#217) --------------------------------
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
    assert dialog.schema_worker is None


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


def test_the_checkbox_itself_carries_the_single_dataset_rule(dialog):
    one_osm(dialog, "pois")
    assert dialog.filter_checkbox.isEnabled()
    one_osm(dialog, "pois", "buildings")
    assert not dialog.filter_checkbox.isEnabled()
    assert "2 are selected" in dialog.filter_status.text()
    one_osm(dialog)
    assert not dialog.filter_checkbox.isEnabled()
    assert dialog.filter_status.text() == dialog_module.FILTER_NEEDS_A_DATASET


def test_a_ticked_filter_is_put_away_while_several_are_selected(dialog):
    """The tick is kept, so going back to one dataset brings the field back,
    but nothing filtered is downloaded meanwhile."""
    one_osm(dialog, "pois")
    dialog.filter_checkbox.setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    one_osm(dialog, "pois", "buildings")
    assert dialog.filter_checkbox.isChecked()
    assert not dialog.filter_row.isVisibleTo(dialog)
    assert dialog.get_where_fragment() is None
    one_osm(dialog, "pois")
    assert dialog.filter_row.isVisibleTo(dialog)
    assert dialog.get_where_fragment() == "confidence > 0.9"


def test_the_tick_is_not_remembered_between_sessions(qgs_app, mock_iface):
    """Like the filter text: a filter restored out of sight would shrink a
    download the user had not asked to filter."""
    dialog = new_dialog(mock_iface)
    try:
        one_osm(dialog, "pois")
        dialog.filter_checkbox.setChecked(True)
        with patch("gpq_downloader.gui.settings_store.QgsSettings") as settings:
            dialog.save_checkbox_states()
        saved = [call.args[0] for call in settings.return_value.setValue.call_args_list]
        assert not any("filter" in str(key).lower() for key in saved)
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
    dialog.filter_checkbox.setChecked(True)
    dialog._schema_arrived(dialog._schema_key, list(schema))
    return dialog


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
