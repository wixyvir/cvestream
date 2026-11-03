"""Free-text affected-version parsing (the only module that parses versions).

Sources give versions as free text. `parse_range` returns a `VersionRange` when
the text is one of these formats, and `None` otherwise:

- a single version (`8.2.29`);
- `X <Y` and `X ≤Y`, where `Y` may be `*` (EUVD: `8.1.0 <8.2.29`, `0 ≤2.21.1`);
- `<Y`, `<=Y`, `>=X` and `>X`;
- `X - Y`;
- `*`;
- a branch `B.*`, which covers the branch (`8.2.*` is `[8.2, 8.3)`), and
  `B.* <Y`, the PHP CNA format (`8.2.* <8.2.35` is `[8.2, 8.2.35)`: the upper
  bound is taken as written, without intersecting it with the branch);
- a compound range `>=X <Y`, with `>`, `<=` and `≥`/`≤` variants;
- a placeholder lower bound, `* <Y` or `unspecified <Y`, which is unbounded below.

Everything else is unusable: `8.3.x`, a pre-release branch (`8.2rc1.*`),
comma-separated ranges, `<= *`, an inverted or empty range. `None` means
unusable, and callers let the item through labelled "versions unknown" (recall
over precision, FR-25).
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
# A branch: a final release then ".*" (8.2.*, 8.*, v8.2.*). Not 8.2rc1.*, not 8.3.x.
_BRANCH = r"(?P<branch>[vV]?\d+(?:\.\d+)*)\.\*"
_BRANCH_ONLY = re.compile(_BRANCH)
# PHP CNA: "<branch>.* <fixed>" (and "<=", or an open "*" upper bound)
_BRANCH_TO = re.compile(rf"{_BRANCH}\s*(?P<op><=|<)\s*{_UPPER_OPEN}")
# ">=8.0 <8.3": two bounds separated by whitespace
_COMPOUND = re.compile(rf"(?P<lop>>=|>)\s*(?P<lo>{_V})\s+(?P<hop><=|<)\s*(?P<hi>{_V})")
# CVE 5 placeholder lower bound: "* <8.2.29", "unspecified <8.2.29"
_PLACEHOLDER_BELOW = re.compile(
    rf"(?:\*|unspecified)\s*(?P<op><=|<)\s*(?P<hi>{_V})", re.IGNORECASE
)


def parse_version(text: str) -> Version | None:
    """Parse one version, `None` when `packaging` rejects it."""
    try:
        return Version(text.strip())
    except InvalidVersion, ValueError:
        # ValueError: a digit run over Python's int-string limit (4300 digits).
        return None


def _branch_end(branch: Version) -> Version | None:
    """First version after the branch: 8.2 → 8.3, 8 → 9, 8.2.1 → 8.2.2."""
    *head, last = branch.release
    try:
        return parse_version(".".join(str(part) for part in (*head, last + 1)))
    except ValueError:  # last + 1 is over the int-string limit (4300 digits)
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
    # Before _FROM_TO, which would read "unspecified" as a (unparseable) lower bound.
    if found := _PLACEHOLDER_BELOW.fullmatch(normalized):
        return _build(None, found["hi"], upper_inclusive=found["op"] == "<=")
    if found := _FROM_TO.fullmatch(normalized):
        upper = None if found["hi"] == "*" else found["hi"]
        return _build(found["lo"], upper, upper_inclusive=found["op"] == "<=")
    if found := _BELOW.fullmatch(normalized):
        return _build(None, found["hi"], upper_inclusive=found["op"] == "<=")
    if found := _ABOVE.fullmatch(normalized):
        return _build(found["lo"], None, lower_inclusive=found["op"] == ">=")
    if found := _HYPHEN.fullmatch(normalized):
        return _build(found["lo"], found["hi"], upper_inclusive=True)
    if found := _BRANCH_TO.fullmatch(normalized):
        upper = None if found["hi"] == "*" else found["hi"]
        return _build(found["branch"], upper, upper_inclusive=found["op"] == "<=")
    if found := _BRANCH_ONLY.fullmatch(normalized):
        start = parse_version(found["branch"])
        if start is None:  # not a version (digit run over the int-string limit)
            return None
        end = _branch_end(start)
        if end is None:
            return None
        return _build(found["branch"], str(end))
    if found := _COMPOUND.fullmatch(normalized):
        return _build(
            found["lo"],
            found["hi"],
            lower_inclusive=found["lop"] == ">=",
            upper_inclusive=found["hop"] == "<=",
        )
    return None
