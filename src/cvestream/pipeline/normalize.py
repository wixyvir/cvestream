"""`SourceRecord` to `NormalizedRecord`: aliases and primary ID (FR-8)."""

from cvestream.domain.identity import normalize_alias, primary_id
from cvestream.domain.model import NormalizedRecord, SourceRecord


def normalize(record: SourceRecord) -> NormalizedRecord | None:
    """Normalize the record's IDs; `None` when none of them is usable.

    The record's own ID counts as an alias. A URL is never an alias. The record
    itself is kept unchanged.
    """
    aliases = frozenset(
        alias
        for raw in (record.record_id, *record.aliases)
        if (alias := normalize_alias(raw)) is not None
    )
    if not aliases:
        return None
    return NormalizedRecord(
        record=record, aliases=aliases, primary_id=primary_id(aliases)
    )
