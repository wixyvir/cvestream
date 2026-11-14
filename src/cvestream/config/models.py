"""The v1 configuration contract.

These models are the single source of truth for `endpoints.yaml`, for the
published JSON Schema (`schemas/config-v1.json`) and, later, for the Valkey
provider and the CLI. Every field has a `description`: third-party tools read
it in the schema. Changing a model changes the contract: regenerate the schema
in the same commit (`uv run cvestream config schema`).

Validation happens here, at the boundary: the domain trusts what it receives.
"""

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules
from cvestream.domain.version import parse_version

SCHEMA_VERSION = 1

# `<name>` or `<name> >= <version>`. ECMA-compatible (no named groups) because it
# is both the JSON Schema `pattern` and what the parser compiles.
SHORTHAND_PATTERN = r"^\s*([^<>=\s](?:[^<>=]*[^<>=\s])?)(?:\s*>=\s*([^<>=\s]+))?\s*$"
_SHORTHAND = re.compile(SHORTHAND_PATTERN)
# The URL-safe base64 alphabet of `secrets.token_urlsafe`; no dot, as `log.py` assumes.
FEED_KEY_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"

_MAX_TERM = 200

Term = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=_MAX_TERM),
]
EndpointId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")]
FeedToken = Annotated[str, StringConstraints(pattern=FEED_KEY_PATTERN)]


def _split_shorthand(value: str) -> tuple[str, str | None]:
    match = _SHORTHAND.match(value)
    if match is None:
        msg = 'expected "<name>" or "<name> >= <version>"'
        raise ValueError(msg)
    return match.group(1), match.group(2)


def split_shorthand(value: str) -> tuple[str, str | None]:
    """`(name, version)` of a shorthand product; the version may be `None`.

    Raises `ValueError` when `value` is not a shorthand. Not validated further:
    the version is checked by the `Shorthand` type.
    """
    return _split_shorthand(value)


def _check_shorthand(value: str) -> str:
    _, version = _split_shorthand(value)
    if version is not None and parse_version(version) is None:
        msg = f"invalid version {version!r}"
        raise ValueError(msg)
    return value


def _check_version(value: str) -> str:
    if parse_version(value) is None:
        # `!r` keeps the message on one line when the value holds a newline.
        msg = f"invalid version {value!r}"
        raise ValueError(msg)
    return value


Shorthand = Annotated[
    str,
    StringConstraints(max_length=_MAX_TERM),
    AfterValidator(_check_shorthand),
    WithJsonSchema(
        {
            "type": "string",
            "maxLength": _MAX_TERM,
            "pattern": SHORTHAND_PATTERN,
            "description": (
                'Shorthand product: "<name>" or "<name> >= <minimum version>", '
                'for example "php >= 8.2".'
            ),
        }
    ),
]
MinVersion = Annotated[str, AfterValidator(_check_version)]

_CONFIG = ConfigDict(extra="forbid", strict=True)


class ProductEntryConfig(BaseModel):
    model_config = _CONFIG

    name: Term = Field(description="Product name, matched as a whole word.")
    aliases: list[Term] = Field(
        default=[], description="Other names the same product goes by."
    )
    exclude: list[Term] = Field(
        default=[],
        description=(
            "Terms that rule this entry out when they appear in the affected "
            "products or in the description."
        ),
    )
    min_version: MinVersion | None = Field(
        default=None,
        description=(
            "Minimum version of interest, as a quoted string (YAML reads 8.10 "
            "as the number 8.1)."
        ),
    )
    match_description: bool = Field(
        default=True,
        description=(
            "Search the description when a vulnerability lists no affected "
            "product; false matches on affected-product data only."
        ),
    )

    @field_validator("min_version", mode="before")
    @classmethod
    def _quoted(cls, value: object) -> object:
        if isinstance(value, int | float) and not isinstance(value, bool):
            msg = "must be a quoted string (YAML reads 8.10 as the number 8.1)"
            raise ValueError(msg)
        return value


def _product_kind(value: object) -> str:
    return "shorthand" if isinstance(value, str) else "entry"


ProductItem = Annotated[
    Annotated[Shorthand, Tag("shorthand")]
    | Annotated[ProductEntryConfig, Tag("entry")],
    Discriminator(_product_kind),
]


def _to_product(item: str | ProductEntryConfig) -> ProductEntry:
    if isinstance(item, str):
        name, version = _split_shorthand(item)
        return ProductEntry(
            name=name, min_version=parse_version(version) if version else None
        )
    return ProductEntry(
        name=item.name,
        aliases=tuple(item.aliases),
        exclude=tuple(item.exclude),
        min_version=parse_version(item.min_version) if item.min_version else None,
        match_description=item.match_description,
    )


class EndpointConfig(BaseModel):
    model_config = _CONFIG

    id: EndpointId = Field(
        description=(
            "Unique slug identifying the endpoint: lowercase letters, digits "
            "and hyphens, 1 to 40 characters."
        )
    )
    name: Term = Field(description="Human-readable endpoint name.")
    enabled: bool = Field(
        default=True, description="A disabled endpoint is kept but not served."
    )
    min_cvss: Annotated[float, Field(ge=0, le=10)] | None = Field(
        default=None,
        description="Minimum CVSS score, 0 to 10; omit for no threshold.",
    )
    kev_only: bool = Field(
        default=False,
        description="Only vulnerabilities known to be exploited (CISA KEV).",
    )
    token: FeedToken = Field(
        repr=False,
        description=(
            "Secret that gives access to the feed, unique across endpoints: "
            "32 to 128 URL-safe characters (A-Z a-z 0-9 _ -)."
        ),
    )
    products: list[ProductItem] = Field(
        default=[],
        description=(
            "Products to watch: a shorthand string or a full entry. "
            "An endpoint without products matches nothing."
        ),
    )

    def to_domain(self) -> Endpoint:
        """The domain object; the token stays out of it."""
        return Endpoint(
            id=self.id,
            name=self.name,
            products=tuple(_to_product(item) for item in self.products),
            rules=Rules(min_cvss=self.min_cvss, kev_only=self.kev_only),
            enabled=self.enabled,
        )


class ConfigDocument(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, title="cvestream configuration v1"
    )

    version: Literal[1] = Field(description="Contract version. Must be 1.")
    endpoints: list[EndpointConfig] = Field(
        default=[], description="The endpoints, each with its own feed."
    )

    @field_validator("version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> object:
        # `Literal[1]` accepts `True` (`True == 1`), even in strict mode.
        if isinstance(value, bool):
            msg = "must be the integer 1"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _unique(self) -> ConfigDocument:
        # Messages carry the path (a model-level error has no location) and
        # never the token value.
        ids: dict[str, int] = {}
        tokens: dict[str, int] = {}
        for index, endpoint in enumerate(self.endpoints):
            if endpoint.id in ids:
                msg = f"endpoints[{index}].id: duplicate endpoint id '{endpoint.id}'"
                raise ValueError(msg)
            ids[endpoint.id] = index
            if endpoint.token in tokens:
                first = self.endpoints[tokens[endpoint.token]].id
                msg = f"endpoints[{index}].token: same token as endpoint '{first}'"
                raise ValueError(msg)
            tokens[endpoint.token] = index
        return self
