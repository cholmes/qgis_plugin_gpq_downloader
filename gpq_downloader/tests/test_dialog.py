import pytest
from unittest.mock import MagicMock, patch
from qgis.PyQt.QtWidgets import QDialog
from qgis.PyQt.QtCore import Qt

from gpq_downloader.dialog import DataSourceDialog

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