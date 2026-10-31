"""JSON Schema export of the v1 contract (`schemas/config-v1.json`).

The file is committed and CI fails when it is stale: regenerate it with
`uv run cvestream config schema` in the same commit as any `models.py` change.
"""

import json
from pathlib import Path
from typing import Any

from cvestream.config.models import ConfigDocument

SCHEMA_PATH = Path("schemas/config-v1.json")
_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def config_schema() -> dict[str, Any]:
    return {"$schema": _DIALECT, **ConfigDocument.model_json_schema()}


def render_schema() -> str:
    """Deterministic text: field order follows the models, `\\n` at the end."""
    return json.dumps(config_schema(), indent=2, ensure_ascii=False) + "\n"
