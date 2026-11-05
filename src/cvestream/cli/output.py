"""Output of the operator commands: JSON, plain tables, feed URLs.

Plain text only (no Rich, no ANSI): the output is read by scripts as often as by
people. A token is printed only inside a feed URL (`endpoint create|show`,
`token rotate`) and by `config export`.
"""

import json
from collections.abc import Sequence
from typing import Any

import typer

from cvestream.config.models import ProductEntryConfig, split_shorthand
from cvestream.settings import Settings

_NONE = "-"


def print_json(value: object) -> None:
    """`value` as indented JSON on stdout; `None` stays `null`."""
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False))


def feed_url(settings: Settings, token: str) -> str:
    """`<base>/feeds/<token>.xml`, the base being `http.public.base_url`.

    Without that setting: `http://localhost:<http.public.port>`.
    """
    public = settings.http.public
    base = public.base_url or f"http://localhost:{public.port}"
    return f"{base}/feeds/{token}.xml"


def cell(value: object) -> str:
    """One table cell: booleans as `true`/`false`, `None` as `-`, lists joined."""
    if value is None or value == "" or value == []:
        return _NONE
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(str(part) for part in value)
    return str(value)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Left-aligned columns separated by two spaces; no trailing whitespace."""
    grid = [[str(header) for header in headers]] + [
        [cell(value) for value in row] for row in rows
    ]
    widths = [max(len(line[index]) for line in grid) for index in range(len(headers))]
    return "\n".join(
        "  ".join(
            text.ljust(width) for text, width in zip(line, widths, strict=True)
        ).rstrip()
        for line in grid
    )


def product_view(item: str | ProductEntryConfig) -> dict[str, Any]:
    """A product item in one shape, whatever its stored form.

    A shorthand string is expanded; `min_version` is `None` when unknown.
    """
    if isinstance(item, str):
        name, version = split_shorthand(item)
        return {
            "name": name,
            "aliases": [],
            "exclude": [],
            "min_version": version,
            "match_description": True,
        }
    return {
        "name": item.name,
        "aliases": list(item.aliases),
        "exclude": list(item.exclude),
        "min_version": item.min_version,
        "match_description": item.match_description,
    }
