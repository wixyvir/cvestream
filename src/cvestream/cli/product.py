"""`cvestream product`: add, remove and list the products of an endpoint."""

from typing import Annotated, Any

import typer

from cvestream.cli.context import open_store, user_error, warn_dropped_comments
from cvestream.cli.output import format_table, print_json, product_view
from cvestream.config.edit import add_product, find_endpoint, remove_product
from cvestream.config.models import ConfigDocument, split_shorthand

product_app = typer.Typer(
    name="product",
    no_args_is_help=True,
    help="Products of an endpoint: add, remove and list.",
)


def _item(
    product: str,
    aliases: list[str],
    exclude: list[str],
    min_version: str | None,
    match_description: bool | None,
) -> str | dict[str, Any]:
    """The item to store: the shorthand as given when no option refines it."""
    try:
        name, version = split_shorthand(product)
    except ValueError:
        user_error('product: expected "<name>" or "<name> >= <version>"')
    if not (
        aliases or exclude or min_version is not None or match_description is not None
    ):
        return product
    if version is not None and min_version is not None:
        user_error("give the version once: in the product or with --min-version")
    entry: dict[str, Any] = {"name": name}
    if aliases:
        entry["aliases"] = aliases
    if exclude:
        entry["exclude"] = exclude
    if version is not None or min_version is not None:
        entry["min_version"] = version if version is not None else min_version
    if match_description is not None:
        entry["match_description"] = match_description
    return entry


@product_app.command("add")
def add(
    endpoint_id: Annotated[
        str, typer.Argument(metavar="ENDPOINT", help="Endpoint id.")
    ],
    product: Annotated[
        str,
        typer.Argument(
            metavar="PRODUCT",
            help='Product name, or the shorthand "name >= version".',
        ),
    ],
    alias: Annotated[
        list[str] | None,
        typer.Option("--alias", help="Another name for the product (repeatable)."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Term that rules the product out (repeatable)."),
    ] = None,
    min_version: Annotated[
        str | None,
        typer.Option("--min-version", help="Minimum version of interest."),
    ] = None,
    match_description: Annotated[
        bool | None,
        typer.Option(
            "--match-description/--no-match-description",
            help="Search the description when no affected product is listed.",
        ),
    ] = None,
) -> None:
    """Add a product to an endpoint.

    Without options the product is stored as given (a shorthand string); with
    any option it is stored as a full entry.
    """
    item = _item(product, alias or [], exclude or [], min_version, match_description)
    with open_store() as session:
        session.store.update(lambda document: add_product(document, endpoint_id, item))
        warn_dropped_comments(session)
    typer.echo(f"added product to endpoint {endpoint_id}")


@product_app.command("remove")
def remove(
    endpoint_id: Annotated[
        str, typer.Argument(metavar="ENDPOINT", help="Endpoint id.")
    ],
    name: Annotated[str, typer.Argument(metavar="NAME", help="Product name.")],
) -> None:
    """Remove every product named NAME (case-insensitive) from an endpoint."""
    removed = 0

    def change(document: ConfigDocument) -> ConfigDocument:
        nonlocal removed
        document, removed = remove_product(document, endpoint_id, name)
        return document

    with open_store() as session:
        session.store.update(change)
        warn_dropped_comments(session)
    noun = "product" if removed == 1 else "products"
    typer.echo(f"removed {removed} {noun} from endpoint {endpoint_id}")


@product_app.command("list")
def list_products(
    endpoint_id: Annotated[
        str, typer.Argument(metavar="ENDPOINT", help="Endpoint id.")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the products of an endpoint."""
    with open_store() as session:
        endpoint = find_endpoint(session.store.document(), endpoint_id)
    views = [product_view(item) for item in endpoint.products]
    if as_json:
        print_json(views)
        return
    rows = [
        [
            view["name"],
            view["min_version"],
            view["aliases"],
            view["exclude"],
            view["match_description"],
        ]
        for view in views
    ]
    typer.echo(
        format_table(
            ["NAME", "MIN_VERSION", "ALIASES", "EXCLUDE", "MATCH_DESCRIPTION"], rows
        )
    )
