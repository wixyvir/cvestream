"""`cvestream token`: rotate the feed token of an endpoint."""

from typing import Annotated

import typer

from cvestream.cli.context import open_store, state_of, warn_dropped_comments
from cvestream.cli.output import feed_url
from cvestream.config.edit import set_token

token_app = typer.Typer(
    name="token",
    no_args_is_help=True,
    help="Feed tokens: rotate the token of an endpoint.",
)


@token_app.command("rotate")
def rotate(
    ctx: typer.Context,
    endpoint_id: Annotated[
        str, typer.Argument(metavar="ENDPOINT", help="Endpoint id.")
    ],
) -> None:
    """Give an endpoint a new random token and print its new feed URL.

    The old URL stops working once the daemon reloads the configuration.
    """
    # Generated once, outside the change: the change may run once per attempt.
    token = state_of(ctx).new_token()
    with open_store() as session:
        session.store.update(lambda document: set_token(document, endpoint_id, token))
        warn_dropped_comments(session)
        typer.echo(f"feed: {feed_url(session.settings, token)}")
    typer.echo(
        "the old URL stops working once the daemon reloads (next cycle); "
        "give the new URL to the subscribers",
        err=True,
    )
