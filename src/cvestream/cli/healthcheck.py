"""`cvestream healthcheck`: the probe of the image and of orchestrators.

Standard library and `typer` only: it must start fast, work in an image without
`curl` and never pull in the app, the HTTP stack or the providers. It asks the
private listener on 127.0.0.1 and ignores proxy environment variables.
"""

import http.client
import urllib.error
import urllib.request
from typing import Annotated, NoReturn

import typer

DEFAULT_PORT = 9090
TIMEOUT_SECONDS = 2.0


def _fail(path: str, reason: str) -> NoReturn:
    typer.echo(f"healthcheck {path}: {reason}", err=True)
    raise typer.Exit(code=1)


def healthcheck(
    ready: Annotated[
        bool,
        typer.Option("--ready", help="Probe /readyz instead of /healthz."),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=1,
            max=65535,
            help="Private listener port (http.private.port).",
        ),
    ] = DEFAULT_PORT,
) -> None:
    """Exit 0 when the daemon answers 200, 1 otherwise. Prints nothing on success."""
    path = "/readyz" if ready else "/healthz"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # The scheme and the host are fixed: nothing here comes from a user.
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with opener.open(url, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        _fail(path, f"HTTP {exc.code}")
    # `http.client.HTTPException`: something that is not HTTP answers on the port.
    except (
        urllib.error.URLError,
        OSError,
        TimeoutError,
        http.client.HTTPException,
    ) as exc:
        _fail(path, type(exc).__name__)
    if status != 200:
        _fail(path, f"HTTP {status}")
