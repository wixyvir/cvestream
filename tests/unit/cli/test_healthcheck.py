import ast
import http.client
import subprocess
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from typer.testing import CliRunner

import cvestream.cli.healthcheck as healthcheck_module
from cvestream.cli.main import app

runner = CliRunner()


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _Opener:
    def __init__(self, outcome: int | BaseException) -> None:
        self.outcome = outcome
        self.urls: list[str] = []
        self.timeouts: list[float] = []

    def open(self, url: str, timeout: float) -> _Response:
        self.urls.append(url)
        self.timeouts.append(timeout)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return _Response(self.outcome)


def _invoke(
    monkeypatch: pytest.MonkeyPatch, outcome: int | BaseException, *args: str
) -> tuple[int, str, str, _Opener, list[tuple[object, ...]]]:
    opener = _Opener(outcome)
    built: list[tuple[object, ...]] = []

    def build_opener(*handlers: object) -> _Opener:
        built.append(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    result = runner.invoke(app, ["healthcheck", *args])
    return result.exit_code, result.stdout, result.stderr, opener, built


def test_a_200_is_silent_and_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, err, opener, _ = _invoke(monkeypatch, 200)
    assert code == 0
    assert out == ""
    assert err == ""
    assert opener.urls == ["http://127.0.0.1:9090/healthz"]
    assert opener.timeouts == [2.0]


def test_ready_probes_readyz(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _, _, opener, _ = _invoke(monkeypatch, 200, "--ready")
    assert code == 0
    assert opener.urls == ["http://127.0.0.1:9090/readyz"]


def test_the_port_is_an_option(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, opener, _ = _invoke(monkeypatch, 200, "--port", "19090")
    assert opener.urls == ["http://127.0.0.1:19090/healthz"]


@pytest.mark.parametrize("port", ["0", "65536", "abc"])
def test_a_bad_port_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    code, *_ = _invoke(monkeypatch, 200, "--port", port)
    assert code == 2


def test_a_503_exits_1_with_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        "http://127.0.0.1:9090/readyz", 503, "Service Unavailable", Message(), None
    )
    code, out, err, _, _ = _invoke(monkeypatch, error, "--ready")
    assert code == 1
    assert out == ""
    assert err.splitlines() == ["healthcheck /readyz: HTTP 503"]


def test_a_non_200_success_status_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _, err, _, _ = _invoke(monkeypatch, 204)
    assert code == 1
    assert "HTTP 204" in err


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(ConnectionRefusedError("refused")),
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
        # Something that is not HTTP answers on the port.
        http.client.BadStatusLine("SSH-2.0-OpenSSH"),
    ],
)
def test_a_connection_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    code, out, err, _, _ = _invoke(monkeypatch, error)
    assert code == 1
    assert out == ""
    assert len(err.splitlines()) == 1


def test_proxy_variables_are_not_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    _, _, _, _, built = _invoke(monkeypatch, 200)
    (handlers,) = built
    (handler,) = handlers
    assert isinstance(handler, urllib.request.ProxyHandler)
    assert vars(handler)["proxies"] == {}


def test_the_module_imports_only_the_standard_library_and_typer() -> None:
    tree = ast.parse(Path(healthcheck_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"typer"}


def test_importing_the_cli_does_not_import_the_http_stack() -> None:
    program = (
        "import cvestream.cli.main, sys; "
        "print(sorted(m for m in ('cvestream.app', 'starlette', 'uvicorn', 'httpx') "
        "if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
