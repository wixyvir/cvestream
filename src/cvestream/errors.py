"""Base exception.

Every exception of ours inherits from `CvestreamError` and its name ends in `Error`.
"""


class CvestreamError(Exception):
    """Root of all cvestream exceptions."""
