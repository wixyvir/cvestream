"""Pure edits of a configuration document, for the operator CLI.

Each function takes a `ConfigDocument` and returns a new one: no I/O, no
randomness (a token is an argument), the input is never mutated. A new value is
validated by the v1 contract models, so the CLI cannot write anything the daemon
would reject; the provider validates the whole result once more before writing.

Messages quote ids and product names, never a token.
"""

from collections.abc import Mapping
from typing import Any, Final

from cvestream.config.models import (
    ConfigDocument,
    EndpointConfig,
    ProductEntryConfig,
    split_shorthand,
)
from cvestream.config.provider import (
    ConfigEditError,
    ConfigValidationError,
    validate_document,
)

type ProductInput = str | Mapping[str, Any]


class _Unset:
    """Type of `UNSET`: "the option was not given" (`None` is a real value)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()

_SOURCE: Final = "endpoint"
_DOCUMENT_PREFIX: Final = "endpoints[0]"


def product_name(item: str | ProductEntryConfig) -> str:
    """The name of a stored product item, whatever its form."""
    if isinstance(item, str):
        return split_shorthand(item)[0]
    return item.name


def _same_name(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _validated(raw: Mapping[str, Any]) -> EndpointConfig:
    """The endpoint `raw` describes; field errors name the field, not an index."""
    try:
        document = validate_document({"version": 1, "endpoints": [raw]}, source=_SOURCE)
    except ConfigValidationError as exc:
        errors = [
            error.removeprefix(_DOCUMENT_PREFIX).removeprefix(".")
            for error in exc.errors
        ]
        raise ConfigValidationError(_SOURCE, errors) from None
    return document.endpoints[0]


def _raw(endpoint: EndpointConfig) -> dict[str, Any]:
    return endpoint.model_dump(mode="python")


def find_endpoint(document: ConfigDocument, endpoint_id: str) -> EndpointConfig:
    """The endpoint `endpoint_id`; `ConfigEditError` when there is none."""
    for endpoint in document.endpoints:
        if endpoint.id == endpoint_id:
            return endpoint
    msg = f"unknown endpoint '{endpoint_id}'"
    raise ConfigEditError(msg)


def _with(document: ConfigDocument, endpoints: list[EndpointConfig]) -> ConfigDocument:
    return document.model_copy(update={"endpoints": endpoints})


def _replaced(document: ConfigDocument, endpoint: EndpointConfig) -> ConfigDocument:
    return _with(
        document, [endpoint if e.id == endpoint.id else e for e in document.endpoints]
    )


def create_endpoint(
    document: ConfigDocument,
    *,
    id: str,
    name: str,
    token: str,
    enabled: bool = True,
    min_cvss: float | None = None,
    kev_only: bool = False,
) -> ConfigDocument:
    """`document` plus a new endpoint (appended, without products)."""
    endpoint = _validated(
        {
            "id": id,
            "name": name,
            "enabled": enabled,
            "min_cvss": min_cvss,
            "kev_only": kev_only,
            "token": token,
        }
    )
    if any(e.id == endpoint.id for e in document.endpoints):
        msg = f"endpoint '{endpoint.id}' already exists"
        raise ConfigEditError(msg)
    return _with(document, [*document.endpoints, endpoint])


def edit_endpoint(
    document: ConfigDocument,
    endpoint_id: str,
    *,
    name: str | _Unset = UNSET,
    enabled: bool | _Unset = UNSET,
    min_cvss: float | _Unset | None = UNSET,
    kev_only: bool | _Unset = UNSET,
) -> ConfigDocument:
    """Change the given fields of an endpoint; `min_cvss=None` clears the threshold."""
    endpoint = find_endpoint(document, endpoint_id)
    changes = {
        key: value
        for key, value in (
            ("name", name),
            ("enabled", enabled),
            ("min_cvss", min_cvss),
            ("kev_only", kev_only),
        )
        if not isinstance(value, _Unset)
    }
    if not changes:
        msg = "nothing to change"
        raise ConfigEditError(msg)
    return _replaced(document, _validated({**_raw(endpoint), **changes}))


def delete_endpoint(document: ConfigDocument, endpoint_id: str) -> ConfigDocument:
    """`document` without the endpoint, its products and its token."""
    find_endpoint(document, endpoint_id)
    return _with(document, [e for e in document.endpoints if e.id != endpoint_id])


def add_product(
    document: ConfigDocument, endpoint_id: str, item: ProductInput
) -> ConfigDocument:
    """Append a product item (a shorthand string or an entry) to an endpoint.

    A product name the endpoint already has (case-insensitive) is refused.
    """
    endpoint = find_endpoint(document, endpoint_id)
    new = _validated(
        {**_raw(endpoint), "products": [*_raw(endpoint)["products"], item]}
    )
    added = product_name(new.products[-1])
    for existing in endpoint.products:
        if _same_name(product_name(existing), added):
            msg = f"endpoint '{endpoint_id}' already has product '{added}'"
            raise ConfigEditError(msg)
    return _replaced(document, new)


def remove_product(
    document: ConfigDocument, endpoint_id: str, name: str
) -> tuple[ConfigDocument, int]:
    """Remove every product item named `name`; return the document and the count.

    Names are compared case-insensitively, stripped; aliases are not considered.
    """
    endpoint = find_endpoint(document, endpoint_id)
    kept = [p for p in endpoint.products if not _same_name(product_name(p), name)]
    removed = len(endpoint.products) - len(kept)
    if removed == 0:
        msg = f"endpoint '{endpoint_id}' has no product '{name.strip()}'"
        raise ConfigEditError(msg)
    updated = endpoint.model_copy(update={"products": kept})
    return _replaced(document, updated), removed


def set_token(document: ConfigDocument, endpoint_id: str, token: str) -> ConfigDocument:
    """Give an endpoint a new token."""
    endpoint = find_endpoint(document, endpoint_id)
    return _replaced(document, _validated({**_raw(endpoint), "token": token}))
