"""A team's channel: the product list and rules vulnerabilities are matched against.

No token lives here: the token to endpoint map belongs to the config snapshot,
which keeps secrets out of domain objects, their `repr()` and the logs.
"""

from dataclasses import dataclass

from packaging.version import Version


@dataclass(frozen=True, slots=True)
class ProductEntry:
    name: str
    aliases: tuple[str, ...] = ()
    # Same field name as the config contract (`exclude[]`).
    exclude: tuple[str, ...] = ()
    # Already parsed by `version.parse_version` at the boundary.
    min_version: Version | None = None
    # False: structured affected-product data only, never the description.
    match_description: bool = True


@dataclass(frozen=True, slots=True)
class Rules:
    min_cvss: float | None = None
    kev_only: bool = False
    # Unscored means max_score() is None; known-exploited is exempt.
    require_score: bool = False


@dataclass(frozen=True, slots=True)
class Endpoint:
    # Slug `[a-z0-9-]{1,40}`, validated by config.
    id: str
    name: str
    products: tuple[ProductEntry, ...]
    rules: Rules = Rules()
    # Ignored by the matcher: the pipeline skips disabled endpoints.
    enabled: bool = True
