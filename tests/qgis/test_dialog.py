import pytest
from unittest.mock import MagicMock, patch
from qgis.core import QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsVectorLayer
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QCheckBox, QHBoxLayout

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.logger import message_level

@pytest.fixture
def mock_iface():
    """Create a mock iface with all required attributes"""
    iface = MagicMock()
    
    # Mock mapCanvas
    map_canvas = MagicMock()
    iface.mapCanvas.return_value = map_canvas
    
    # Mock layerTreeView
    layer_tree_view = MagicMock()
    layer_tree_model = MagicMock()
    layer_tree_view.model.return_value = layer_tree_model
    iface.layerTreeView.return_value = layer_tree_view
    
    # Mock activeLayer
    active_layer = MagicMock()
    iface.activeLayer.return_value = active_layer
    
    # Mock messageBar
    message_bar = MagicMock()
    iface.messageBar.return_value = message_bar
    
    # Mock actionPan
    pan_action = MagicMock()
    iface.actionPan.return_value = pan_action
    
    # Mock actionSelectRectangle
    select_action = MagicMock()
    iface.actionSelectRectangle.return_value = select_action
    
    return iface

def test_dialog_initialization(qgs_app, mock_iface):
    """Test dialog initialization"""
    dialog = DataSourceDialog(None, mock_iface)
    assert dialog is not None
    assert dialog.iface == mock_iface

def test_dialog_radio_buttons(qgs_app, mock_iface):
    """Test radio button functionality"""
    dialog = DataSourceDialog(None, mock_iface)
    
    # Set Overture radio to checked (since it might not be default)
    dialog.overture_radio.setChecked(True)
    
    # Check state after explicitly setting
    assert dialog.overture_radio.isChecked()
    assert not dialog.sourcecoop_radio.isChecked()
    assert not dialog.osm_radio.isChecked()

    # Test switching radio buttons
    dialog.sourcecoop_radio.setChecked(True)
    assert not dialog.overture_radio.isChecked()
    assert dialog.sourcecoop_radio.isChecked()
    assert not dialog.osm_radio.isChecked()

@patch('gpq_downloader.gui.settings_store.QgsSettings')
def test_dialog_settings_saved(mock_settings, qgs_app, mock_iface):
    """Test that settings are saved"""
    dialog = DataSourceDialog(None, mock_iface)
    dialog.save_checkbox_states()
    mock_settings.assert_called()


def test_dialog_aoi_checkbox_default_unchecked(qgs_app, mock_iface):
    """Test that AOI checkbox is unchecked by default"""
    dialog = DataSourceDialog(None, mock_iface)
    assert not dialog.extent_group.isChecked()


def test_dialog_aoi_checkbox_toggle(qgs_app, mock_iface):
    """Test AOI checkbox toggle functionality"""
    dialog = DataSourceDialog(None, mock_iface)

    # Initially unchecked
    assert not dialog.extent_group.isChecked()

    # Check it
    dialog.extent_group.setChecked(True)
    assert dialog.extent_group.isChecked()

    # Uncheck it
    dialog.extent_group.setChecked(False)
    assert not dialog.extent_group.isChecked()


@patch('gpq_downloader.gui.settings_store.QgsSettings')
def test_dialog_aoi_checkbox_state_saved(mock_settings, qgs_app, mock_iface):
    """Test that AOI checkbox state is saved"""
    mock_settings_instance = MagicMock()
    mock_settings.return_value = mock_settings_instance

    dialog = DataSourceDialog(None, mock_iface)
    dialog.extent_group.setChecked(True)
    dialog.save_checkbox_states()

    # Check that setValue was called with the AOI setting
    calls = mock_settings_instance.setValue.call_args_list
    aoi_call = [c for c in calls if 'aoi_enabled' in str(c)]
    assert len(aoi_call) > 0


def _layer_with_features(mock_iface):
    """Real memory layer with two polygons, set as the iface's active layer."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "aoi_source", "memory")
    feats = []
    for wkt in ["POLYGON((0 0,1 0,1 1,0 1,0 0))", "POLYGON((2 2,3 2,3 3,2 3,2 2))"]:
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    # A real canvas, so the AOI highlighter and CRS transforms behave as in QGIS
    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    mock_iface.mapCanvas = MagicMock(return_value=canvas)
    mock_iface.activeLayer = MagicMock(return_value=layer)
    for name in ("actionPan", "actionSelectRectangle", "messageBar", "layerTreeView"):
        setattr(mock_iface, name, MagicMock())
    return layer, canvas


def test_disconnect_selection_changed_removes_the_handler(qgs_app, mock_iface):
    """The one thing the helper has to do: take our slot off the signal."""
    dialog = DataSourceDialog(None, mock_iface)
    layer = MagicMock()

    dialog._disconnect_selection_changed(layer)

    layer.selectionChanged.disconnect.assert_called_once_with(dialog.on_selection_changed)


@pytest.mark.parametrize(
    "layer, case",
    [
        (None, "no active layer"),
        (object(), "a layer with no selection to change, such as a raster"),
        (MagicMock(**{"selectionChanged.disconnect.side_effect": RuntimeError}), "deleted layer"),
    ],
)
def test_disconnect_selection_changed_tolerates(qgs_app, mock_iface, layer, case):
    """Callers disconnect before connecting and while tearing an AOI mode down,
    so none of these states is an error."""
    dialog = DataSourceDialog(None, mock_iface)

    dialog._disconnect_selection_changed(layer)  # must not raise


def test_disconnect_selection_changed_tolerates_a_slot_never_connected(qgs_app, mock_iface):
    """PyQt itself raises TypeError here, which a MagicMock layer cannot show."""
    dialog = DataSourceDialog(None, mock_iface)
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "never_connected", "memory")

    dialog._disconnect_selection_changed(layer)  # must not raise

    # And it is a real disconnect once there is something to disconnect
    layer.selectionChanged.connect(dialog.on_selection_changed)
    dialog._disconnect_selection_changed(layer)
    dialog._disconnect_selection_changed(layer)  # now off again, still fine


def test_finish_feature_selection_clears_selection_keeps_aoi(qgs_app, mock_iface):
    """Select Features: the selection highlight is cleared, the AOI is kept."""
    layer, _canvas = _layer_with_features(mock_iface)
    dialog = DataSourceDialog(None, mock_iface)
    dialog.start_feature_selection()
    layer.selectByIds([f.id() for f in layer.getFeatures()][:1])
    assert dialog.aoi_geometry is not None

    dialog.finish_feature_selection()

    assert layer.selectedFeatureCount() == 0
    assert dialog.aoi_geometry is not None
    mock_iface.actionPan.return_value.trigger.assert_called()


def test_reject_clears_selection_made_by_plugin(qgs_app, mock_iface):
    """Canceling the dialog mid-selection clears the plugin's selection."""
    layer, _canvas = _layer_with_features(mock_iface)
    dialog = DataSourceDialog(None, mock_iface)
    dialog.start_feature_selection()
    layer.selectByIds([f.id() for f in layer.getFeatures()])

    dialog.reject()

    assert layer.selectedFeatureCount() == 0


def test_closing_dialog_keeps_users_own_selection(qgs_app, mock_iface):
    """A selection the user made without Select Features must survive accept/reject."""
    layer, _canvas = _layer_with_features(mock_iface)
    for close in ("accept", "reject"):
        layer.selectByIds([f.id() for f in layer.getFeatures()])
        dialog = DataSourceDialog(None, mock_iface)
        getattr(dialog, close)()
        assert layer.selectedFeatureCount() == 2, close


def test_dialog_divisions_checkbox_in_first_row(qgs_app, mock_iface):
    """Test that Divisions checkbox exists and is functional"""
    dialog = DataSourceDialog(None, mock_iface)

    # Divisions checkbox should exist
    assert hasattr(dialog, 'divisions_checkbox')
    assert dialog.divisions_checkbox is not None

    # Should be in overture_checkboxes
    assert 'divisions' in dialog.overture_checkboxes

    # Toggle should work
    dialog.divisions_checkbox.setChecked(True)
    assert dialog.divisions_checkbox.isChecked()


def test_dialog_divisions_subtypes_visibility(qgs_app, mock_iface):
    """Test that Divisions subtypes appear when checkbox is checked"""
    dialog = DataSourceDialog(None, mock_iface)

    # Ensure checkbox is unchecked to start from known state
    dialog.divisions_checkbox.setChecked(False)
    assert dialog.divisions_subtype_widget.isHidden()

    # Check divisions checkbox
    dialog.divisions_checkbox.setChecked(True)

    # Subtypes should now be visible
    assert not dialog.divisions_subtype_widget.isHidden()

    # Uncheck should hide
    dialog.divisions_checkbox.setChecked(False)
    assert dialog.divisions_subtype_widget.isHidden()


def test_dialog_base_subtypes_visibility(qgs_app, mock_iface):
    """Test that Base subtypes appear when checkbox is checked"""
    dialog = DataSourceDialog(None, mock_iface)

    # Ensure checkbox is unchecked to start from known state
    dialog.base_checkbox.setChecked(False)
    assert dialog.base_subtype_widget.isHidden()

    # Check base checkbox
    dialog.base_checkbox.setChecked(True)

    # Subtypes should now be visible
    assert not dialog.base_subtype_widget.isHidden()

    # Uncheck should hide
    dialog.base_checkbox.setChecked(False)
    assert dialog.base_subtype_widget.isHidden()


def test_message_level_returns_an_enum_not_an_int():
    """Qt6 rejects a plain int where a Qgis.MessageLevel is expected."""
    for name in ("Info", "Warning", "Success", "Critical"):
        level = message_level(name)
        assert type(level) is not int, f"{name} resolved to a plain int"


@pytest.mark.parametrize("module", ["gui/dialog", "gui/extent", "plugin"])
def test_no_bare_int_message_levels_in_source(module):
    """Every pushMessage/pushWidget passes a real message level. A bare int here
    raised TypeError in QGIS 4 but worked in QGIS 3, so only Qt6 catches it."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "gpq_downloader" / f"{module}.py"
    tree = ast.parse(src.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("pushMessage", "pushWidget"):
            continue
        # pushMessage(title, text, level, duration); pushWidget(widget, level)
        index = 2 if node.func.attr == "pushMessage" else 1
        levels = [node.args[index]] if len(node.args) > index else []
        levels += [kw.value for kw in node.keywords if kw.arg == "level"]
        for arg in levels:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                offenders.append(f"{module}.py:{node.lineno}")
    assert not offenders, f"bare int message level at {offenders}"


def test_standard_icons_the_dialog_asks_for_exist():
    """The dialog's icons resolve on this Qt. Qt6 moved QStyle's SP_* names
    under QStyle.StandardPixmap, and the bare form raised AttributeError
    before the dialog could open at all (#157)."""
    from qgis.PyQt.QtWidgets import QStyle

    for name in ("SP_FileDialogListView", "SP_DialogResetButton"):
        assert hasattr(QStyle.StandardPixmap, name), f"QStyle.StandardPixmap.{name} is missing"


def test_no_bare_qstyle_pixmaps_in_source():
    """QStyle.SP_* without the StandardPixmap scope works on Qt5 and raises on
    Qt6, so only a QGIS 4 run would catch a reintroduction (#157)."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "gpq_downloader"
    offenders = [
        f"{name}:{i}"
        for name in ("gui/dialog.py", "gui/extent.py")
        for i, line in enumerate((root / name).read_text().splitlines(), 1)
        if re.search(r"QStyle\.SP_", line)
    ]
    assert not offenders, f"unscoped QStyle pixmap at {offenders}"


def test_show_extent_menu_opens_the_menu(qgs_app, mock_iface):
    """The extent menu pops up. PyQt6 dropped QMenu.exec_(), so calling it
    raised AttributeError in QGIS 4 (#181 follow-up)."""
    dialog = DataSourceDialog(None, mock_iface)

    menu = MagicMock()
    with patch.object(dialog.extent_button, "menu", return_value=menu):
        dialog.show_extent_menu()

    menu.exec.assert_called_once()
    assert not menu.exec_.called, "exec_() does not exist in PyQt6"


def test_osm_presets_include_layercake_pois():
    """The LayerCake POIs dataset is offered (#166)."""
    import json
    import pathlib

    presets_path = pathlib.Path(__file__).resolve().parents[2] / "gpq_downloader" / "data" / "presets.json"
    pois = json.loads(presets_path.read_text())["openstreetmap"]["pois"]
    assert pois["url"] == "https://data.openstreetmap.us/layercake/pois.parquet"
    assert pois["display_name"] == "POIs"


def test_osm_checkbox_labels_use_a_preset_display_name(qgs_app, mock_iface):
    """key.title() turns "pois" into "Pois", so a preset may name itself."""
    dialog = DataSourceDialog(None, mock_iface)
    labels = {key: box.text() for key, box in dialog.osm_checkboxes.items()}
    assert labels["pois"] == "POIs"
    # The presets without a display_name still fall back to the key
    assert labels["buildings"] == "Buildings"
    dialog.close()


def test_osm_selection_returns_the_preset_url(qgs_app, mock_iface):
    dialog = DataSourceDialog(None, mock_iface)
    dialog.osm_radio.setChecked(True)
    for key, box in dialog.osm_checkboxes.items():
        box.setChecked(key == "pois")
    assert dialog.get_urls() == ["https://data.openstreetmap.us/layercake/pois.parquet"]
    dialog.close()


@pytest.mark.parametrize(
    "key,url",
    [
        ("addresses", "https://data.openstreetmap.us/layercake/addresses.parquet"),
        ("waterways", "https://data.openstreetmap.us/layercake/waterways.parquet"),
        ("wetlands", "https://data.openstreetmap.us/layercake/wetlands.parquet"),
    ],
)
def test_osm_presets_include_the_newer_layercake_datasets(key, url):
    """#193 asked for addresses, waterways and wetlands."""
    import json
    import pathlib

    presets_path = pathlib.Path(__file__).resolve().parents[2] / "gpq_downloader" / "data" / "presets.json"
    assert json.loads(presets_path.read_text())["openstreetmap"][key]["url"] == url


def test_osm_checkboxes_wrap_instead_of_widening_the_dialog(qgs_app, mock_iface):
    """All the presets on one row set the dialog's minimum width: nine of them
    demanded 825px, where six had demanded 536px. A grid keeps the page within
    the width the dialog already asks for (#193)."""
    dialog = DataSourceDialog(None, mock_iface)
    assert len(dialog.osm_checkboxes) >= 9
    osm_page = next(iter(dialog.osm_checkboxes.values())).parentWidget()
    assert osm_page.minimumSizeHint().width() <= dialog.minimumWidth()
    dialog.close()


BUILDINGS_URL_BASE = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings"


@pytest.fixture
def unsaved_checkboxes():
    """Keep these tests out of the settings QGIS is really running on.

    Toggling any checkbox saves every checkbox state, so a test that ticks
    boxes to build a URL would otherwise overwrite what the person running the
    suite has chosen in their own QGIS profile.
    """
    with patch("gpq_downloader.gui.settings_store.save_value"):
        yield


def overture_dialog(mock_iface):
    """An Overture dialog with every preset checkbox off, whatever is remembered."""
    dialog = DataSourceDialog(None, mock_iface)
    dialog.overture_radio.setChecked(True)
    for checkbox in dialog.overture_checkboxes.values():
        checkbox.setChecked(False)
    for checkbox in dialog.buildings_modifier_checkboxes.values():
        checkbox.setChecked(False)
    for checkbox in dialog.divisions_subtype_checkboxes.values():
        checkbox.setChecked(False)
    for checkbox in dialog.base_subtype_checkboxes.values():
        checkbox.setChecked(False)
    return dialog


# The Overture presets that are a category: a choice of layers, which sit on
# that preset's own line. Buildings is not one of them - it is a single layer
# with a modifier beside it.
OVERTURE_CATEGORIES = ["divisions", "base"]


def overture_page_rows(dialog):
    """The Overture page's rows: its top-level layout items, top to bottom."""
    page_layout = dialog.buildings_checkbox.parentWidget().layout()
    return [page_layout.itemAt(i) for i in range(page_layout.count())]


def row_checkboxes(row):
    """Every checkbox a row holds, in order, however deeply it is nested.

    A category's layers are a widget of their own inside the row, and wrap onto
    further lines inside that, so this has to recurse rather than look one
    level down.
    """
    widget = row.widget()
    if widget is not None:
        if isinstance(widget, QCheckBox):
            return [widget]
        row = widget.layout()
        if row is None:
            return []
    else:
        row = row.layout()
        if row is None:
            return []
    found = []
    for i in range(row.count()):
        found.extend(row_checkboxes(row.itemAt(i)))
    return found


def preset_tops(dialog):
    """Every preset checkbox's y on the page, once the page has been laid out."""
    page = dialog.buildings_checkbox.parentWidget()
    page.layout().activate()
    return {
        key: checkbox.mapTo(page, checkbox.rect().topLeft()).y()
        for key, checkbox in dialog.overture_checkboxes.items()
    }


@pytest.mark.parametrize("width", [560, 640, 820])
def test_what_follows_a_preset_starts_at_the_same_column(
    qgs_app, mock_iface, unsaved_checkboxes, width
):
    """The modifier and the layer groups line up under each other.

    They drift apart if the preset checkbox is free to grow, because then it
    takes the spare width instead of the stretch at the end of the line - and
    how much is spare depends on how wide the dialog is.
    """
    dialog = overture_dialog(mock_iface)
    for checkbox in dialog.overture_checkboxes.values():
        checkbox.setChecked(True)
    dialog.resize(width, dialog.height())
    dialog.show()
    qgs_app.processEvents()
    page = dialog.buildings_checkbox.parentWidget()
    page.layout().activate()

    def starts_at(checkbox):
        return checkbox.mapTo(page, checkbox.rect().topLeft()).x()

    first_of_each = [
        starts_at(next(iter(group.values())))
        for group in (
            dialog.buildings_modifier_checkboxes,
            dialog.divisions_subtype_checkboxes,
            dialog.base_subtype_checkboxes,
        )
    ]
    assert len(set(first_of_each)) == 1, f"groups start at {first_of_each}"
    dialog.close()


def test_only_a_line_with_presets_below_it_holds_its_room_open(
    qgs_app, mock_iface, unsaved_checkboxes
):
    """How the invariant below is actually held, and what it is allowed to cost.

    A category whose layers wrap makes its line taller and pushes everything
    below it down, so a line with presets under it keeps its room whether or
    not its layers are showing. The last line on the page pushes nothing, so
    it grows with its layers instead of holding a blank band open - which is
    the whole reason for drawing the distinction rather than reserving
    everywhere.
    """
    dialog = DataSourceDialog(None, mock_iface)
    last_line = [row for row in overture_page_rows(dialog) if row_checkboxes(row)][-1]

    for category in OVERTURE_CATEGORIES:
        layers = getattr(dialog, f"{category}_subtype_widget")
        on_last_line = dialog.overture_checkboxes[category] in row_checkboxes(last_line)
        assert layers.sizePolicy().retainSizeWhenHidden() is not on_last_line, category
    dialog.close()


@pytest.mark.parametrize("width", [500, 560, 640, 820])
def test_toggling_a_preset_never_moves_another_preset(
    qgs_app, mock_iface, unsaved_checkboxes, width
):
    """A checkbox the user is aiming at must not shift under the cursor.

    A category whose layers wrap grows its line, and everything below it moves:
    Base jumped whenever Divisions wrapped. Checked across widths because
    whether a group wraps depends on the width it is given and on the font.
    """
    dialog = overture_dialog(mock_iface)
    dialog.resize(width, dialog.height())
    dialog.show()
    qgs_app.processEvents()

    at_rest = preset_tops(dialog)
    for key, checkbox in dialog.overture_checkboxes.items():
        checkbox.setChecked(True)
        qgs_app.processEvents()
        ticked = preset_tops(dialog)
        assert {k: v for k, v in ticked.items() if k != key} == {
            k: v for k, v in at_rest.items() if k != key
        }, f"ticking {key} at {width}px moved another preset"

        checkbox.setChecked(False)
        qgs_app.processEvents()
        assert preset_tops(dialog) == at_rest, f"unticking {key} at {width}px moved a preset"
    dialog.close()


@pytest.mark.parametrize("category", OVERTURE_CATEGORIES)
def test_a_category_keeps_its_layers_on_its_own_line(
    qgs_app, mock_iface, unsaved_checkboxes, category
):
    """A category's layers belong to its line, to the right of it.

    Which is what keeps them attached to the right preset: they are inside its
    row, not a separate block below it that has to be read as belonging to
    whatever happens to sit above (#230).
    """
    dialog = DataSourceDialog(None, mock_iface)
    parent = dialog.overture_checkboxes[category]
    layers = getattr(dialog, f"{category}_subtype_widget")
    rows = overture_page_rows(dialog)

    holding = [i for i, row in enumerate(rows) if parent in row_checkboxes(row)]
    assert len(holding) == 1, f"{category} is on {len(holding)} rows"
    row = rows[holding[0]]

    # Its own layers are on that line, and no other preset's are
    assert row_checkboxes(row) == [parent, *layers.findChildren(QCheckBox)]
    # The category starts that line rather than floating in the middle of it,
    # which is what it would do once its layers wrap onto a second line
    assert row.layout().itemAt(0).alignment() & Qt.AlignmentFlag.AlignTop
    other_categories = [c for c in OVERTURE_CATEGORIES if c != category]
    for other in other_categories:
        assert dialog.overture_checkboxes[other] not in row_checkboxes(row)
    dialog.close()


@pytest.mark.parametrize("category", OVERTURE_CATEGORIES)
def test_a_category_shows_its_layers_only_while_it_is_checked(
    qgs_app, mock_iface, unsaved_checkboxes, category
):
    dialog = DataSourceDialog(None, mock_iface)
    parent = dialog.overture_checkboxes[category]
    layers = getattr(dialog, f"{category}_subtype_widget")

    parent.setChecked(False)
    assert layers.isHidden()
    parent.setChecked(True)
    assert not layers.isHidden()
    parent.setChecked(False)
    assert layers.isHidden()
    dialog.close()


def layer_line_tops(dialog, category):
    """The distinct line tops the category's layer checkboxes are laid out on."""
    boxes = getattr(dialog, f"{category}_subtype_checkboxes")
    return sorted({checkbox.geometry().top() for checkbox in boxes.values()})


def test_a_long_line_of_layers_wraps_instead_of_widening_the_dialog(
    qgs_app, mock_iface, unsaved_checkboxes
):
    """Base has six layers, wider together than the dialog. They wrap onto a
    continuation line inside Base's own block rather than run off the edge or
    push the dialog wider (#80, #193 guard the same thing for the presets).

    Read off the laid-out geometry. An earlier version of this test walked the
    FlowLayout's items as though each one were a line; they are the individual
    checkboxes, so all it ever asserted was that a single checkbox fits across
    the dialog, and it passed just the same with the flow layout swapped for a
    plain QHBoxLayout.
    """
    dialog = overture_dialog(mock_iface)
    dialog.base_checkbox.setChecked(True)
    dialog.resize(dialog.minimumWidth(), dialog.height())
    dialog.show()
    qgs_app.processEvents()

    wrapped = layer_line_tops(dialog, "base")
    assert len(wrapped) > 1, "Base's six layers stayed on one line at the narrowest width"
    block = dialog.base_subtype_widget
    for key, checkbox in dialog.base_subtype_checkboxes.items():
        assert checkbox.geometry().right() <= block.width(), f"{key} ran off the edge"

    dialog.resize(1200, dialog.height())
    qgs_app.processEvents()
    assert layer_line_tops(dialog, "base") == wrapped[:1], "they wrapped with room to spare"
    dialog.close()


@pytest.mark.parametrize("point_size", [9, 14])
def test_no_layer_checkbox_is_clipped_by_its_block(
    qgs_app, mock_iface, unsaved_checkboxes, point_size
):
    """A block given one line's height clips the lines that wrapped, and a
    checkbox showing 7px of its 20px can be neither read nor ticked - the
    layer is gone. A parent QBoxLayout takes how short a child may be from
    minimumSize(), which is one line for a FlowLayout by design, so the block
    has to ask for the wrapped height once it knows its width (#230).

    At a 14pt UI font this went wrong with no resizing at all: Base lost three
    layers, Bathymetry to nothing, the moment the dialog opened.
    """
    font = qgs_app.font()
    was = font.pointSize()
    font.setPointSize(point_size)
    qgs_app.setFont(font)
    try:
        dialog = overture_dialog(mock_iface)
        dialog.divisions_checkbox.setChecked(True)
        dialog.base_checkbox.setChecked(True)
        dialog.show()
        dialog.resize(dialog.minimumWidth(), dialog.minimumSizeHint().height())
        qgs_app.processEvents()
        for category in OVERTURE_CATEGORIES:
            block = getattr(dialog, f"{category}_subtype_widget")
            needed = block.layout().heightForWidth(block.width())
            assert needed <= block.height(), (
                f"{category}'s block is {block.height()}px tall for {needed}px of layers"
            )
            for key, checkbox in getattr(dialog, f"{category}_subtype_checkboxes").items():
                assert checkbox.geometry().bottom() <= block.height(), (
                    f"{category}.{key} is cut off at {point_size}pt"
                )
        dialog.close()
    finally:
        font.setPointSize(was)
        qgs_app.setFont(font)


def test_no_preset_resizes_the_dialog(qgs_app, mock_iface, unsaved_checkboxes):
    """Nothing on this page widens the dialog any more. A line of layers that
    would not fit wraps instead, so there is no width to claw back and the
    dialog no longer jumps as presets are ticked."""
    dialog = overture_dialog(mock_iface)
    width = dialog.width()
    for key, checkbox in dialog.overture_checkboxes.items():
        checkbox.setChecked(True)
        assert dialog.width() == width, f"{key} resized the dialog"
    for checkbox in dialog.overture_checkboxes.values():
        checkbox.setChecked(False)
    assert dialog.width() == width
    dialog.close()


def test_building_parts_is_a_modifier_on_the_buildings_line(
    qgs_app, mock_iface, unsaved_checkboxes
):
    """Buildings is one layer, not a category: Building Parts modifies that
    download rather than being a layer picked from a list. So it reads as a
    sentence, sits beside Buildings on its line, and is not a preset of its own.
    """
    dialog = DataSourceDialog(None, mock_iface)
    assert "building_parts" not in dialog.overture_checkboxes
    assert list(dialog.overture_checkboxes)[0] == "buildings"

    modifier = dialog.buildings_modifier_checkboxes["building_parts"]
    assert modifier.text() == "include building parts"

    rows = overture_page_rows(dialog)
    holding = [
        i for i, row in enumerate(rows)
        if dialog.buildings_checkbox in row_checkboxes(row)
    ]
    assert len(holding) == 1
    assert row_checkboxes(rows[holding[0]]) == [dialog.buildings_checkbox, modifier]
    dialog.close()


def test_the_buildings_modifier_shows_only_while_buildings_is_checked(
    qgs_app, mock_iface, unsaved_checkboxes
):
    dialog = DataSourceDialog(None, mock_iface)
    modifier = dialog.buildings_modifier_checkboxes["building_parts"]

    dialog.buildings_checkbox.setChecked(False)
    assert modifier.isHidden()

    dialog.buildings_checkbox.setChecked(True)
    assert not modifier.isHidden()

    dialog.buildings_checkbox.setChecked(False)
    assert modifier.isHidden()
    dialog.close()


def test_the_overture_page_reads_each_preset_then_what_belongs_to_it(
    qgs_app, mock_iface, unsaved_checkboxes
):
    """The whole page, line by line: each preset, then whatever belongs to it.

    Pinned so that adding a preset is a decision about where it goes rather
    than something that quietly reshuffles the page. A category's layers may
    wrap onto a continuation line within its own block, which does not change
    the line they belong to.
    """
    dialog = DataSourceDialog(None, mock_iface)
    rows = [[c.text() for c in row_checkboxes(row)] for row in overture_page_rows(dialog)]
    assert [row for row in rows if row] == [
        ["Buildings", "include building parts"],
        ["Places", "Transportation", "Addresses"],
        ["Divisions", "Division Points", "Division Area", "Division Boundary"],
        ["Base", "Infrastructure", "Land", "Land Cover", "Land Use", "Water", "Bathymetry"],
    ]
    dialog.close()


def test_the_division_subtype_is_labelled_division_points(qgs_app, mock_iface, unsaved_checkboxes):
    """The point type reads "Division Points", so it is not confused with the
    Divisions theme that holds all three. Its key, which the URL and the saved
    setting use, stays "division"."""
    dialog = DataSourceDialog(None, mock_iface)
    assert dialog.divisions_checkbox.text() == "Divisions"
    assert list(dialog.divisions_subtype_checkboxes) == [
        "division", "division_area", "division_boundary",
    ]
    assert [c.text() for c in dialog.divisions_subtype_checkboxes.values()] == [
        "Division Points", "Division Area", "Division Boundary",
    ]
    dialog.close()


def test_division_points_still_downloads_type_division(qgs_app, mock_iface, unsaved_checkboxes):
    """The renamed label must not change the URL the subtype builds."""
    dialog = overture_dialog(mock_iface)
    dialog.divisions_checkbox.setChecked(True)
    for key, checkbox in dialog.divisions_subtype_checkboxes.items():
        checkbox.setChecked(key == "division")
    with patch("gpq_downloader.gui.dialog.latest_overture_release", return_value="2026-09-23.1"):
        urls = dialog.get_urls()
    assert urls == [
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=divisions/type=division/*"
    ]
    dialog.close()


def test_overture_checkboxes_wrap_instead_of_widening_the_dialog(qgs_app, mock_iface):
    """Six presets on one row would set the dialog's minimum width (#80)."""
    dialog = DataSourceDialog(None, mock_iface)
    overture_page = dialog.overture_checkboxes["buildings"].parentWidget()
    assert overture_page.minimumSizeHint().width() <= dialog.minimumWidth()
    dialog.close()


def test_buildings_and_building_parts_are_two_urls(qgs_app, mock_iface, unsaved_checkboxes):
    """Checking both downloads two datasets, one URL each, not a type=building* glob."""
    dialog = overture_dialog(mock_iface)
    dialog.buildings_checkbox.setChecked(True)
    dialog.buildings_modifier_checkboxes["building_parts"].setChecked(True)
    with patch("gpq_downloader.gui.dialog.latest_overture_release", return_value="2026-09-23.1"):
        urls = dialog.get_urls()
    assert urls == [
        f"{BUILDINGS_URL_BASE}/type=building/*",
        f"{BUILDINGS_URL_BASE}/type=building_part/*",
    ]
    dialog.close()


def test_buildings_without_building_parts_is_the_buildings_url_alone(qgs_app, mock_iface, unsaved_checkboxes):
    """Buildings is not a pure container like Base and Divisions: on its own it
    still downloads type=building."""
    dialog = overture_dialog(mock_iface)
    dialog.buildings_checkbox.setChecked(True)
    with patch("gpq_downloader.gui.dialog.latest_overture_release", return_value="2026-09-23.1"):
        urls = dialog.get_urls()
    assert urls == [f"{BUILDINGS_URL_BASE}/type=building/*"]
    dialog.close()


def test_building_parts_without_buildings_downloads_nothing(qgs_app, mock_iface, unsaved_checkboxes):
    """Nesting Building Parts under Buildings means it is only reached through
    Buildings; a ticked child with its parent off is not a download."""
    dialog = overture_dialog(mock_iface)
    dialog.buildings_modifier_checkboxes["building_parts"].setChecked(True)
    with patch("gpq_downloader.gui.dialog.latest_overture_release", return_value="2026-09-23.1"):
        urls = dialog.get_urls()
    assert urls == []
    dialog.close()


def test_every_preset_line_stays_left(qgs_app, mock_iface, unsaved_checkboxes):
    """A preset line ends with a stretch, so its presets start at the left.

    The last line on the page holds no room open for its hidden layers, so
    without a stretch its row is one fixed-width checkbox in a full-width box
    and QHBoxLayout centres it: Base sat mid-page while every preset above it
    stayed left (#230). Asserted on the layout rather than on x positions,
    because an offscreen page is not wide enough to spread a row and the
    positional form of this test cannot fail. Only QHBoxLayout rows are
    checked: a FlowLayout starts every line at its left edge already.
    """
    dialog = DataSourceDialog(None, mock_iface)
    rows = [
        row
        for row in overture_page_rows(dialog)
        if isinstance(row.layout(), QHBoxLayout) and row_checkboxes(row)
    ]
    assert rows, "no preset rows found"
    for row in rows:
        layout = row.layout()
        last = layout.itemAt(layout.count() - 1)
        labels = [box.text() for box in row_checkboxes(row)]
        assert last.spacerItem() is not None, (
            f"the row holding {labels} does not end in a stretch, "
            "so its presets will centre once there is room to spread"
        )
    dialog.close()


def test_the_plain_presets_stay_left_on_a_wide_dialog(
    qgs_app, mock_iface, unsaved_checkboxes
):
    """Places, Transportation and Addresses are a QGridLayout, not a row with
    a stretch, so the test above steps over them. Without a stretch column of
    its own the grid shares the spare width between its cells: on a 900px
    dialog they sat about 200px apart, directly above the category lines that
    are packed left (#230).
    """
    dialog = overture_dialog(mock_iface)
    dialog.resize(900, dialog.height())
    dialog.show()
    qgs_app.processEvents()

    packed = dialog.overture_checkboxes["buildings"]
    for key in ("places", "transportation", "addresses"):
        checkbox = dialog.overture_checkboxes[key]
        # A stretched cell shows up as a checkbox far wider than its label
        # needs: Places went from 69px to 283px, which is what pushed the
        # three of them apart.
        assert checkbox.width() <= checkbox.sizeHint().width() + 20, (
            f"{key} is {checkbox.width()}px wide for a "
            f"{checkbox.sizeHint().width()}px label on a 900px dialog, "
            "so the grid is spreading its cells instead of staying left"
        )
    assert dialog.overture_checkboxes["places"].geometry().left() == (
        packed.geometry().left()
    ), "the plain presets do not start at the same column as the others"
    dialog.close()
