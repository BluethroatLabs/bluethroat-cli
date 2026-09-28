"""Process exit codes for ``bluethroat``."""

from __future__ import annotations


CLEAN = 0
SUSPICIOUS = 1
MALICIOUS = 2
INCOMPLETE = 3
INVALID = 4
RATE_LIMITED = 5
AUTH = 6
SERVICE = 7


class CliError(Exception):
    """A failure the command should print and exit with ``code``."""

    def __init__(self, message: str, code: int, *, body: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.body = body


def code_for(verdict: str, *, complete: bool) -> int:
    """Map a finished report to an exit code.

    An unfinished or partial report is never a successful clean scan.
    """
    label = (verdict or "").strip().lower()
    if label == "invalid":
        return INVALID
    if label == "error":
        return SERVICE
    if not complete or label == "partial":
        return INCOMPLETE
    return {
        "clean": CLEAN,
        "suspicious": SUSPICIOUS,
        "malicious": MALICIOUS,
    }.get(label, INCOMPLETE)
