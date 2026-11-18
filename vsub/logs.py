"""SAS log scanning and exit-code mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_WARNINGS = 1
EXIT_ERRORS = 2

# Matches "ERROR:", "ERROR 22-322:", "WARNING:", "WARNING 1-322:" at line start
_ERROR = re.compile(r"^ERROR(?: \d+-\d+)?:")
_WARNING = re.compile(r"^WARNING(?: \d+-\d+)?:")


@dataclass
class LogSummary:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors:
            return EXIT_ERRORS
        if self.warnings:
            return EXIT_WARNINGS
        return EXIT_OK

    def merge(self, other: "LogSummary") -> "LogSummary":
        return LogSummary(self.errors + other.errors, self.warnings + other.warnings)


def scan_log(text: str) -> LogSummary:
    summary = LogSummary()
    for raw in text.splitlines():
        line = raw.lstrip("\x0c").rstrip()
        if _ERROR.match(line):
            summary.errors.append(line)
        elif _WARNING.match(line):
            summary.warnings.append(line)
    return summary
