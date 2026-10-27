"""Free-text affected-version parsing (the only module that parses versions).

Sources give versions as free text (EUVD: `"8.1.0 <8.2.29"`, `"0 ≤2.21.1"`).
`parse_range` returns a `VersionRange` when the text is understood and `None`
otherwise; `None` means unusable, and callers let the item through labelled
"versions unknown" (recall over precision, FR-25).
"""

import re
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

_UNUSABLE = frozenset({"", "n/a", "na", "-", "unknown", "unspecified"})

# One version token: no whitespace, no comparison operator, no wildcard.
_V = r"[^\s<>=*]+"
_UPPER_OPEN = r"(?P<hi>" + _V + r"|\*)"
_SINGLE = re.compile(rf"(?P<v>{_V})")
# EUVD: "<from> <<fixed>" and "<from> ≤<last>" (after normalization: "<" and "<=")
_FROM_TO = re.compile(rf"(?P<lo>{_V})\s*(?P<op><=|<)\s*{_UPPER_OPEN}")
_BELOW = re.compile(rf"(?P<op><=|<)\s*(?P<hi>{_V})")
_ABOVE = re.compile(rf"(?P<op>>=|>)\s*(?P<lo>{_V})")
# The hyphen needs spaces on both sides: "1.0.0-rc.1" is a single version.
_HYPHEN = re.compile(rf"(?P<lo>{_V}) - (?P<hi>{_V})")


def parse_version(text: str) -> Version | None:
    """Parse one version, `None` when `packaging` rejects it."""
    try:
        return Version(text.strip())
    except InvalidVersion:
        return None


@dataclass(frozen=True, slots=True)
class VersionRange:
    """Affected versions; a `None` bound is unbounded."""

    lower: Version | None
    lower_inclusive: bool
    upper: Version | None
    upper_inclusive: bool

    def reaches(self, minimum: Version) -> bool:
        """True when the range contains some version at or above `minimum`."""
        if self.upper is None:
            return True
        return self.upper > minimum or (self.upper == minimum and self.upper_inclusive)


def _build(
    lower: str | None,
    upper: str | None,
    *,
    lower_inclusive: bool = True,
    upper_inclusive: bool = False,
) -> VersionRange | None:
    """Build a range from bound texts; `None` for unparseable or empty ranges."""
    low = parse_version(lower) if lower is not None else None
    high = parse_version(upper) if upper is not None else None
    if (lower is not None and low is None) or (upper is not None and high is None):
        return None
    if low is not None and high is not None:
        if low > high or (low == high and not (lower_inclusive and upper_inclusive)):
            return None
    # An unbounded side is reported as inclusive, so equal ranges compare equal.
    return VersionRange(
        low, lower_inclusive or low is None, high, upper_inclusive or high is None
    )


def parse_range(text: str | None) -> VersionRange | None:
    """Parse free-text affected versions into a range, `None` when unusable."""
    if text is None:
        return None
    normalized = " ".join(text.split()).replace("≤", "<=").replace("≥", ">=")
    if normalized.lower() in _UNUSABLE:
        return None
    if normalized == "*":
        return VersionRange(None, True, None, True)
    if found := _SINGLE.fullmatch(normalized):
        return _build(found["v"], found["v"], upper_inclusive=True)
    if found := _FROM_TO.fullmatch(normalized):
        upper = None if found["hi"] == "*" else found["hi"]
        return _build(found["lo"], upper, upper_inclusive=found["op"] == "<=")
    if found := _BELOW.fullmatch(normalized):
        return _build(None, found["hi"], upper_inclusive=found["op"] == "<=")
    if found := _ABOVE.fullmatch(normalized):
        return _build(found["lo"], None, lower_inclusive=found["op"] == ">=")
    if found := _HYPHEN.fullmatch(normalized):
        return _build(found["lo"], found["hi"], upper_inclusive=True)
    return None
