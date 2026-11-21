import json
from pathlib import Path

from cvestream.config.models import SHORTHAND_PATTERN
from cvestream.config.schema import SCHEMA_PATH, config_schema, render_schema

ROOT = Path(__file__).parents[3]


def test_committed_schema_is_fresh() -> None:
    committed = (ROOT / SCHEMA_PATH).read_text(encoding="utf-8")
    assert committed == render_schema(), "run: uv run cvestream config schema"


def test_render_is_deterministic() -> None:
    assert render_schema() == render_schema()
    assert render_schema().endswith("}\n")


def test_schema_dialect_and_title() -> None:
    schema = config_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "cvestream configuration v1"
    assert schema["additionalProperties"] is False


def test_render_round_trips() -> None:
    assert json.loads(render_schema()) == config_schema()


def test_shorthand_pattern_is_published() -> None:
    schema = config_schema()
    items = schema["$defs"]["EndpointConfig"]["properties"]["products"]["items"]
    patterns = [option.get("pattern") for option in items["oneOf"]]
    assert SHORTHAND_PATTERN in patterns


def test_every_property_has_a_description() -> None:
    schema = config_schema()
    for name in ("EndpointConfig", "ProductEntryConfig"):
        for prop, definition in schema["$defs"][name]["properties"].items():
            assert definition.get("description"), f"{name}.{prop}"
    for prop, definition in schema["properties"].items():
        assert definition.get("description"), prop


def test_schema_path_is_relative() -> None:
    assert SCHEMA_PATH == Path("schemas/config-v1.json")


def test_unique_product_names_are_described() -> None:
    schema = config_schema()
    description = schema["$defs"]["EndpointConfig"]["properties"]["products"][
        "description"
    ]
    assert "unique" in description


def test_exact_contradiction_is_published() -> None:
    schema = config_schema()
    clause = schema["$defs"]["ProductEntryConfig"]["not"]
    assert clause == {
        "required": ["exact", "match_description"],
        "properties": {
            "exact": {"const": True},
            "match_description": {"const": True},
        },
    }
    exact = schema["$defs"]["ProductEntryConfig"]["properties"]["exact"]
    assert exact["default"] is False
