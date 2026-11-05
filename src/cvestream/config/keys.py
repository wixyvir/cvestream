"""The Valkey key layout of the v1 configuration contract.

Every key the provider reads or writes is built here, once. The layout is a
public contract (`docs/config-contract.md`): third-party tools such as BoM
connectors write the same keys, so a change here is a breaking change and
belongs in a new namespace (`cvestream:v2:`).

No I/O and no `valkey` import. Builders assume a valid endpoint slug
(`^[a-z0-9-]{1,40}$`): callers validate ids before building keys, and a slug has
no `:` or glob character, so no id can make a key leave its own namespace.
"""

from typing import Final

from cvestream.config.models import SCHEMA_VERSION

PREFIX: Final = "cvestream:v1:"

# Contract version, a JSON integer.
SCHEMA: Final = f"{PREFIX}schema"
# Integer every writer INCRs inside its transaction.
REVISION: Final = f"{PREFIX}revision"
# SET of endpoint ids.
ENDPOINTS: Final = f"{PREFIX}endpoints"

# What `SCHEMA` holds.
SCHEMA_VALUE: Final = SCHEMA_VERSION


def endpoint(endpoint_id: str) -> str:
    """JSON object: name, enabled, min_cvss, kev_only."""
    return f"{PREFIX}endpoint:{endpoint_id}"


def products(endpoint_id: str) -> str:
    """JSON array of product items."""
    return f"{PREFIX}endpoint:{endpoint_id}:products"


def token(endpoint_id: str) -> str:
    """The feed token, raw text. Own namespace: no connector pattern covers it."""
    return f"{PREFIX}token:{endpoint_id}"
