import logging

from gpq_downloader import logger
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


def test_info_is_the_level_when_nothing_has_set_one():
    assert logger.current_level() == "Info"


def test_debug_is_silent_at_info(restore_level, logged):
    logger.set_level("Info")
    logger.debug("quiet")
    assert logged == []


def test_debug_speaks_at_debug(restore_level, logged):
    logger.set_level("Debug")
    logger.debug("loud")
    assert [m for m, _ in logged] == ["loud"]


def test_warning_turns_the_info_chatter_off_but_keeps_warnings(restore_level, logged):
    logger.set_level("Warning")
    logger.log("chatter")
    logger.log("careful", 1)
    logger.log("broken", 2)
    assert [m for m, _ in logged] == ["careful", "broken"]


def test_the_core_logger_moves_with_it(restore_level):
    logger.set_level("Debug")
    assert logger._core_logger.level == logging.DEBUG
    logger.set_level("Warning")
    assert logger._core_logger.level == logging.WARNING


def test_an_unknown_level_reads_as_info(restore_level):
    logger.set_level("Chatty")
    assert logger.current_level() == "Info"
