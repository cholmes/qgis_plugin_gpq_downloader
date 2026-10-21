import pytest
from unittest.mock import MagicMock, patch
from qgis.core import QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsVectorLayer
from qgis.gui import QgsMapCanvas

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


@pytest.mark.parametrize("module", ["dialog", "plugin"])
def test_no_bare_int_message_levels_in_source(module):
    """Every pushMessage/pushWidget passes a real message level. A bare int here
    raised TypeError in QGIS 4 but worked in QGIS 3, so only Qt6 catches it."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / f"{module}.py"
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

    src = pathlib.Path(__file__).resolve().parent.parent / "dialog.py"
    offenders = [
        f"dialog.py:{i}"
        for i, line in enumerate(src.read_text().splitlines(), 1)
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

    presets_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "presets.json"
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

    presets_path = pathlib.Path(__file__).resolve().parent.parent / "data" / "presets.json"
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
