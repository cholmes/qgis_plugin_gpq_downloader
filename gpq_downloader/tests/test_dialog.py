import pytest
from unittest.mock import MagicMock, patch
from qgis.PyQt.QtWidgets import QDialog
from qgis.PyQt.QtCore import Qt

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
    assert not dialog.other_radio.isChecked()
    
    # Test switching radio buttons
    dialog.sourcecoop_radio.setChecked(True)
    assert not dialog.overture_radio.isChecked()
    assert dialog.sourcecoop_radio.isChecked()
    assert not dialog.other_radio.isChecked()

@patch('gpq_downloader.dialog.QgsSettings')
def test_dialog_settings_saved(mock_settings, qgs_app, mock_iface):
    """Test that settings are saved"""
    dialog = DataSourceDialog(None, mock_iface)
    dialog.save_checkbox_states()
    mock_settings.assert_called() 