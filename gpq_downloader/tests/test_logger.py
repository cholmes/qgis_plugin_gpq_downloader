import pytest
from gpq_downloader.logger import log

def test_logger_basic():
    """Test basic logger functionality"""
    log("Test message")
    log("Test message", 1)
    log("Test message", 2)

def test_logger_levels():
    """Test different logger levels"""
    log("Info message", 0)
    log("Warning message", 1)
    log("Error message", 2) 