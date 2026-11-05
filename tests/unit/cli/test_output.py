import json

import pytest

from cvestream.cli.output import (
    cell,
    feed_url,
    format_table,
    print_json,
    product_view,
)
from cvestream.config.models import ProductEntryConfig
from cvestream.settings import load_settings

TOKEN = "t" * 43


def test_feed_url_defaults_to_localhost_and_the_public_port() -> None:
    settings = load_settings({})
    assert feed_url(settings, TOKEN) == f"http://localhost:8080/feeds/{TOKEN}.xml"
    settings = load_settings({"CVESTREAM_HTTP__PUBLIC__PORT": "18080"})
    assert feed_url(settings, TOKEN) == f"http://localhost:18080/feeds/{TOKEN}.xml"


def test_feed_url_uses_the_base_url() -> None:
    settings = load_settings(
        {"CVESTREAM_HTTP__PUBLIC__BASE_URL": "https://f.example.org/"}
    )
    assert feed_url(settings, TOKEN) == f"https://f.example.org/feeds/{TOKEN}.xml"
    settings = load_settings({"CVESTREAM_HTTP__PUBLIC__BASE_URL": "https://e.org/cve"})
    assert feed_url(settings, TOKEN) == f"https://e.org/cve/feeds/{TOKEN}.xml"


def test_print_json(capsys: pytest.CaptureFixture[str]) -> None:
    print_json([{"a": None, "b": "é", "c": 1.5}])
    out = capsys.readouterr().out
    assert json.loads(out) == [{"a": None, "b": "é", "c": 1.5}]
    assert "é" in out
    assert out.endswith("\n")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "-"),
        ("", "-"),
        ([], "-"),
        (True, "true"),
        (False, "false"),
        (7.5, "7.5"),
        (0, "0"),
        (["a", "b"], "a, b"),
    ],
)
def test_cell(value: object, text: str) -> None:
    assert cell(value) == text


def test_format_table_aligns_left_without_trailing_spaces() -> None:
    table = format_table(["ID", "NAME"], [["a", "Long name"], ["longer-id", None]])
    assert table.splitlines() == [
        "ID         NAME",
        "a          Long name",
        "longer-id  -",
    ]
    assert all(line == line.rstrip() for line in table.splitlines())


def test_format_table_without_rows_has_the_header() -> None:
    assert format_table(["ID", "NAME"], []) == "ID  NAME"


def test_product_view_of_a_shorthand() -> None:
    assert product_view("php >= 8.2") == {
        "name": "php",
        "aliases": [],
        "exclude": [],
        "min_version": "8.2",
        "match_description": True,
    }
    assert product_view("php")["min_version"] is None


def test_product_view_of_an_entry() -> None:
    entry = ProductEntryConfig(
        name="gitlab",
        aliases=["gl"],
        exclude=["ce"],
        min_version="16.0",
        match_description=False,
    )
    assert product_view(entry) == {
        "name": "gitlab",
        "aliases": ["gl"],
        "exclude": ["ce"],
        "min_version": "16.0",
        "match_description": False,
    }
