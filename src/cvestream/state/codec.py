"""`SourceRecord` to JSON and back, and the content hash.

The only place that serializes a record. Pure functions, no SQLAlchemy.
`None` stays `null`: unknown is never `0` or `""`.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    SourceRecord,
)
from cvestream.state.schema import StateError, format_utc, parse_utc

CODEC_VERSION = 1


def _dt(value: datetime | None) -> str | None:
    return None if value is None else format_utc(value)


def _from_dt(value: object) -> datetime | None:
    return None if value is None else parse_utc(_text(value))


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise StateError("stored record has a malformed field")
    return value


def _opt_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateError("stored record has a malformed field")
    return value


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise StateError("stored record has a malformed field")
    return value


def _sourced(item: Sourced[Any], value: object) -> dict[str, object]:
    return {
        "value": value,
        "source": item.source,
        "observed_at": format_utc(item.observed_at),
    }


def _score_to_json(score: CvssScore | None) -> dict[str, object] | None:
    if score is None:
        return None
    return {"score": score.score, "version": score.version, "vector": score.vector}


def _score_from_json(data: object) -> CvssScore | None:
    if data is None:
        return None
    raw = _mapping(data)
    score = raw["score"]
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise StateError("stored record has a malformed field")
    return CvssScore(
        score=float(score),
        version=_opt_text(raw["version"]),
        vector=_opt_text(raw["vector"]),
    )


def _affected_to_json(affected: AffectedProduct) -> dict[str, object]:
    return {
        "product": affected.product,
        "vendor": affected.vendor,
        "version_text": affected.version_text,
    }


def _affected_from_json(data: object) -> AffectedProduct:
    raw = _mapping(data)
    return AffectedProduct(
        product=_text(raw["product"]),
        vendor=_opt_text(raw["vendor"]),
        version_text=_opt_text(raw["version_text"]),
    )


def _kev_to_json(kev: KevEntry) -> dict[str, object]:
    return {
        "catalog": kev.catalog.value,
        "known_ransomware_use": kev.known_ransomware_use,
        "added_at": _dt(kev.added_at),
    }


def _kev_from_json(data: object) -> KevEntry:
    raw = _mapping(data)
    ransomware = raw["known_ransomware_use"]
    if ransomware is not None and not isinstance(ransomware, bool):
        raise StateError("stored record has a malformed field")
    try:
        catalog = KevCatalog(_text(raw["catalog"]))
    except ValueError:
        raise StateError("stored record names an unknown KEV catalog") from None
    return KevEntry(
        catalog=catalog,
        known_ransomware_use=ransomware,
        added_at=_from_dt(raw["added_at"]),
    )


def _sourced_from_json[T](data: object, build: Callable[[object], T]) -> Sourced[T]:
    raw = _mapping(data)
    return Sourced(
        value=build(raw["value"]),
        source=_text(raw["source"]),
        observed_at=_require(_from_dt(raw["observed_at"])),
    )


def _require(value: datetime | None) -> datetime:
    if value is None:
        raise StateError("stored record has a malformed field")
    return value


def record_to_json(record: SourceRecord) -> dict[str, object]:
    """JSON-ready mapping of `record`; `record_from_json` is its inverse."""
    return {
        "v": CODEC_VERSION,
        "source": record.source,
        "record_id": record.record_id,
        "observed_at": format_utc(record.observed_at),
        "aliases": list(record.aliases),
        "title": (
            None if record.title is None else _sourced(record.title, record.title.value)
        ),
        "description": (
            None
            if record.description is None
            else _sourced(record.description, record.description.value)
        ),
        "scores": [_sourced(s, _score_to_json(s.value)) for s in record.scores],
        "affected": [_sourced(a, _affected_to_json(a.value)) for a in record.affected],
        "kev": [_sourced(k, _kev_to_json(k.value)) for k in record.kev],
        "references": list(record.references),
        "url": record.url,
        "published_at": _dt(record.published_at),
        "updated_at": _dt(record.updated_at),
        "epss": (
            None if record.epss is None else _sourced(record.epss, record.epss.value)
        ),
    }


def _opt_sourced_text(data: object) -> Sourced[str] | None:
    return None if data is None else _sourced_from_json(data, _text)


def _epss_from_json(data: object) -> Sourced[float] | None:
    if data is None:
        return None

    def number(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise StateError("stored record has a malformed field")
        return float(value)

    return _sourced_from_json(data, number)


def record_from_json(data: Mapping[str, object]) -> SourceRecord:
    """Rebuild a `SourceRecord`; an unknown codec version raises `StateError`."""
    if data.get("v") != CODEC_VERSION:
        raise StateError("stored record has an unsupported codec version")
    try:
        return SourceRecord(
            source=_text(data["source"]),
            record_id=_text(data["record_id"]),
            observed_at=_require(_from_dt(data["observed_at"])),
            aliases=tuple(_text(a) for a in _sequence(data["aliases"])),
            title=_opt_sourced_text(data["title"]),
            description=_opt_sourced_text(data["description"]),
            scores=tuple(
                _sourced_from_json(s, _score_from_json)
                for s in _sequence(data["scores"])
            ),
            affected=tuple(
                _sourced_from_json(a, _affected_from_json)
                for a in _sequence(data["affected"])
            ),
            kev=tuple(
                _sourced_from_json(k, _kev_from_json) for k in _sequence(data["kev"])
            ),
            references=tuple(_text(r) for r in _sequence(data["references"])),
            url=_opt_text(data["url"]),
            published_at=_from_dt(data["published_at"]),
            updated_at=_from_dt(data["updated_at"]),
            epss=_epss_from_json(data["epss"]),
        )
    except KeyError:
        raise StateError("stored record is missing a field") from None


def _strip_observed_at(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _strip_observed_at(v) for k, v in value.items() if k != "observed_at"
        }
    if isinstance(value, list):
        return [_strip_observed_at(v) for v in value]
    return value


def content_hash(record: SourceRecord) -> str:
    """sha256 hex of the record without any `observed_at`.

    Adapters stamp `observed_at = now` on every fetch; including it would make
    every poll look like a change.
    """
    payload = _strip_observed_at(record_to_json(record))
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
