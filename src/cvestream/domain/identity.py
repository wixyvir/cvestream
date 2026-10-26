"""Vulnerability identity: alias normalization and primary ID choice (FR-8).

A URL is never an identity. The primary ID is CVE > EUVD > advisory.
"""

import re
from collections.abc import Iterable
from enum import StrEnum

_CVE = re.compile(r"CVE-(\d{4})-(\d{4,})")
_EUVD = re.compile(r"EUVD-(\d{4})-(\d+)")


class AliasKind(StrEnum):
    CVE = "cve"
    EUVD = "euvd"
    ADVISORY = "advisory"


def normalize_alias(raw: str) -> str | None:
    """Upper-case identifier, or `None` when `raw` is empty or not an identifier."""
    alias = raw.strip().upper()
    if not alias or "/" in alias or any(c.isspace() for c in alias):
        return None
    return alias


def alias_kind(alias: str) -> AliasKind:
    """Kind of an already normalized alias."""
    if _CVE.fullmatch(alias):
        return AliasKind.CVE
    if _EUVD.fullmatch(alias):
        return AliasKind.EUVD
    return AliasKind.ADVISORY


def _sort_key(alias: str) -> tuple[int, int, str]:
    # Numeric (year, number) for CVE and EUVD so CVE-2026-9999 < CVE-2026-10000;
    # advisories sort lexicographically.
    found = _CVE.fullmatch(alias) or _EUVD.fullmatch(alias)
    if found:
        return int(found[1]), int(found[2]), alias
    return 0, 0, alias


_PRIORITY = (AliasKind.CVE, AliasKind.EUVD, AliasKind.ADVISORY)


def primary_id(aliases: Iterable[str]) -> str:
    """Pick the primary ID among `aliases` (CVE > EUVD > advisory, deterministic)."""
    usable = {a for raw in aliases if (a := normalize_alias(raw)) is not None}
    for kind in _PRIORITY:
        candidates = [a for a in usable if alias_kind(a) is kind]
        if candidates:
            return min(candidates, key=_sort_key)
    raise ValueError("no usable alias to pick a primary ID from")
