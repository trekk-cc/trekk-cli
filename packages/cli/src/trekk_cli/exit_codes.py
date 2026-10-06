"""Documented exit codes of `trekk` (listed in `trekk harvest --help`)."""

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    USAGE = 2
    PARTIAL = 3
    INTERRUPTED = 4
