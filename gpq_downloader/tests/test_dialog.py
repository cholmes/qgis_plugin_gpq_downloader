import pytest
from unittest.mock import MagicMock, patch
from qgis.PyQt.QtWidgets import QDialog
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsMapLayerType

from gpq_downloader.dialog import DataSourceDialog

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

@patch('gpq_downloader.dialog.QgsSettings')
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


@patch('gpq_downloader.dialog.QgsSettings')
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


def test_finish_feature_selection_clears_layer_selection(qgs_app, mock_iface):
    """Feature-selection AOI capture should not leave layer features selected."""
    dialog = DataSourceDialog(None, mock_iface)

    active_layer = mock_iface.activeLayer.return_value
    active_layer.type.return_value = QgsMapLayerType.VectorLayer

    dialog.finish_feature_selection()

    active_layer.selectionChanged.disconnect.assert_called_with(dialog.on_selection_changed)
    active_layer.removeSelection.assert_called_once()
    mock_iface.actionPan.return_value.trigger.assert_called_once()


def test_reject_clears_layer_selection(qgs_app, mock_iface):
    """Canceling the dialog should clear any transient feature selection."""
    dialog = DataSourceDialog(None, mock_iface)

    active_layer = mock_iface.activeLayer.return_value
    active_layer.type.return_value = QgsMapLayerType.VectorLayer

    dialog.reject()

    active_layer.selectionChanged.disconnect.assert_called_with(dialog.on_selection_changed)
    active_layer.removeSelection.assert_called_once()
    mock_iface.actionPan.return_value.trigger.assert_called_once()


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
