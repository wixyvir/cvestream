"""`cvestream endpoint`: create, list, show, edit and delete endpoints.

Every command goes through the active provider (`config_provider`), so the same
commands work on the YAML file and on Valkey. `list` and `show` only read.
"""

from typing import Annotated

import typer

from cvestream.cli.context import (
    is_interactive,
    open_store,
    state_of,
    user_error,
    warn_dropped_comments,
)
from cvestream.cli.output import feed_url, format_table, print_json, product_view
from cvestream.config.edit import (
    UNSET,
    create_endpoint,
    delete_endpoint,
    edit_endpoint,
    find_endpoint,
)
from cvestream.config.models import EndpointConfig

endpoint_app = typer.Typer(
    name="endpoint",
    no_args_is_help=True,
    help="Endpoints: create, list, show, edit and delete.",
)


def _summary(endpoint: EndpointConfig) -> dict[str, object]:
    return {
        "id": endpoint.id,
        "name": endpoint.name,
        "enabled": endpoint.enabled,
        "min_cvss": endpoint.min_cvss,
        "kev_only": endpoint.kev_only,
        "product_count": len(endpoint.products),
    }


@endpoint_app.command("create")
def create(
    ctx: typer.Context,
    endpoint_id: Annotated[
        str,
        typer.Argument(
            metavar="ID", help="Endpoint id: lowercase letters, digits, hyphens."
        ),
    ],
    name: Annotated[str, typer.Option("--name", help="Human-readable name.")],
    min_cvss: Annotated[
        float | None,
        typer.Option("--min-cvss", help="Minimum CVSS score, 0 to 10."),
    ] = None,
    kev_only: Annotated[
        bool,
        typer.Option("--kev-only", help="Only vulnerabilities known to be exploited."),
    ] = False,
    disabled: Annotated[
        bool, typer.Option("--disabled", help="Create the endpoint disabled.")
    ] = False,
) -> None:
    """Create an endpoint with a new random token; print its feed URL.

    Add products with `cvestream product add`.
    """
    token = state_of(ctx).new_token()
    with open_store() as session:
        session.store.update(
            lambda document: create_endpoint(
                document,
                id=endpoint_id,
                name=name,
                token=token,
                enabled=not disabled,
                min_cvss=min_cvss,
                kev_only=kev_only,
            )
        )
        warn_dropped_comments(session)
        typer.echo(f"created endpoint {endpoint_id}")
        typer.echo(f"feed: {feed_url(session.settings, token)}")


@endpoint_app.command("list")
def list_endpoints(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the endpoints, sorted by id. Tokens are not printed."""
    with open_store() as session:
        document = session.store.document()
    endpoints = sorted(document.endpoints, key=lambda e: e.id)
    if as_json:
        print_json([_summary(e) for e in endpoints])
        return
    rows = [
        [e.id, e.name, e.enabled, e.min_cvss, e.kev_only, len(e.products)]
        for e in endpoints
    ]
    typer.echo(
        format_table(
            ["ID", "NAME", "ENABLED", "MIN_CVSS", "KEV_ONLY", "PRODUCTS"], rows
        )
    )


@endpoint_app.command("show")
def show(
    endpoint_id: Annotated[str, typer.Argument(metavar="ID", help="Endpoint id.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show an endpoint: its settings, its products and its feed URL."""
    with open_store() as session:
        endpoint = find_endpoint(session.store.document(), endpoint_id)
        url = feed_url(session.settings, endpoint.token)
    products = [product_view(item) for item in endpoint.products]
    if as_json:
        summary = _summary(endpoint)
        del summary["product_count"]
        print_json({**summary, "products": products, "feed_url": url})
        return
    typer.echo(f"id:        {endpoint.id}")
    typer.echo(f"name:      {endpoint.name}")
    typer.echo(f"enabled:   {'true' if endpoint.enabled else 'false'}")
    typer.echo(f"min_cvss:  {'-' if endpoint.min_cvss is None else endpoint.min_cvss}")
    typer.echo(f"kev_only:  {'true' if endpoint.kev_only else 'false'}")
    typer.echo(f"products:  {len(products)}")
    for product in products:
        typer.echo(f"  - {_describe(product)}")
    typer.echo(f"feed:      {url}")


def _describe(product: dict[str, object]) -> str:
    text = str(product["name"])
    if product["min_version"] is not None:
        text += f" >= {product['min_version']}"
    extras = []
    if product["aliases"]:
        extras.append("aliases: " + ", ".join(map(str, product["aliases"])))  # ty: ignore[invalid-argument-type]
    if product["exclude"]:
        extras.append("exclude: " + ", ".join(map(str, product["exclude"])))  # ty: ignore[invalid-argument-type]
    if not product["match_description"]:
        extras.append("description matching off")
    return f"{text} ({'; '.join(extras)})" if extras else text


@endpoint_app.command("edit")
def edit(
    endpoint_id: Annotated[str, typer.Argument(metavar="ID", help="Endpoint id.")],
    name: Annotated[str | None, typer.Option("--name", help="New name.")] = None,
    min_cvss: Annotated[
        float | None, typer.Option("--min-cvss", help="New minimum CVSS, 0 to 10.")
    ] = None,
    no_min_cvss: Annotated[
        bool, typer.Option("--no-min-cvss", help="Remove the CVSS threshold.")
    ] = False,
    kev_only: Annotated[
        bool | None,
        typer.Option(
            "--kev-only/--no-kev-only",
            help="Only vulnerabilities known to be exploited, or not.",
        ),
    ] = None,
    enable: Annotated[
        bool | None,
        typer.Option("--enable/--disable", help="Serve the endpoint, or not."),
    ] = None,
) -> None:
    """Change an endpoint. The token changes only with `cvestream token rotate`."""
    if min_cvss is not None and no_min_cvss:
        user_error("--min-cvss and --no-min-cvss cannot be used together")
    threshold = None if no_min_cvss else UNSET if min_cvss is None else min_cvss
    with open_store() as session:
        session.store.update(
            lambda document: edit_endpoint(
                document,
                endpoint_id,
                name=UNSET if name is None else name,
                enabled=UNSET if enable is None else enable,
                min_cvss=threshold,
                kev_only=UNSET if kev_only is None else kev_only,
            )
        )
        warn_dropped_comments(session)
    typer.echo(f"updated endpoint {endpoint_id}")


@endpoint_app.command("delete")
def delete(
    endpoint_id: Annotated[str, typer.Argument(metavar="ID", help="Endpoint id.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Do not ask for confirmation.")
    ] = False,
) -> None:
    """Delete an endpoint with its products and its token."""
    if not yes:
        if not is_interactive():
            user_error("pass --yes to delete without a prompt")
        if not typer.confirm(f"Delete endpoint {endpoint_id}?", default=False):
            user_error("aborted")
    with open_store() as session:
        session.store.update(lambda document: delete_endpoint(document, endpoint_id))
        warn_dropped_comments(session)
    typer.echo(f"deleted endpoint {endpoint_id}")
