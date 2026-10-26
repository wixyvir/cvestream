import json
import sys
from collections import namedtuple
from collections.abc import Iterator

import pytest
import structlog

from cvestream.log import configure_logging, redact_tokens


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _redact(event_dict: dict[str, object]) -> dict[str, object]:
    return dict(redact_tokens(None, "info", event_dict))


def test_redacts_token_key() -> None:
    assert _redact({"event": "x", "token": "abc"}) == {
        "event": "x",
        "token": "[redacted]",
    }


def test_redacts_keys_ending_in_token() -> None:
    result = _redact({"event": "x", "feed_token": "abc", "api_token": "def"})
    assert result["feed_token"] == "[redacted]"
    assert result["api_token"] == "[redacted]"


def test_does_not_redact_unrelated_keys() -> None:
    result = _redact({"event": "x", "tokens_seen": 3, "endpoint_id": "prod"})
    assert result == {"event": "x", "tokens_seen": 3, "endpoint_id": "prod"}


def test_redacts_feed_path_in_string_values() -> None:
    result = _redact(
        {
            "event": "http.request_served",
            "path": "/feeds/Zm9vYmFy-_123.xml",
            "line": "GET /feeds/secret.xml HTTP/1.1 and /feeds/other.xml",
        }
    )
    assert result["path"] == "/feeds/[redacted].xml"
    assert result["line"] == (
        "GET /feeds/[redacted].xml HTTP/1.1 and /feeds/[redacted].xml"
    )


def test_redacts_feed_path_whatever_the_extension() -> None:
    result = _redact(
        {"event": "x", "a": "/feeds/tok123", "b": "GET /feeds/tok123.rss?x=1 HTTP/1.1"}
    )
    assert result["a"] == "/feeds/[redacted]"
    assert result["b"] == "GET /feeds/[redacted].rss?x=1 HTTP/1.1"


def test_redaction_is_idempotent() -> None:
    once = _redact({"event": "x", "path": "/feeds/tok.xml"})
    assert _redact(once) == once


def test_redacts_token_keys_case_insensitively() -> None:
    result = _redact({"event": "x", "Token": "a", "FEED_TOKEN": "b"})
    assert result == {"event": "x", "Token": "[redacted]", "FEED_TOKEN": "[redacted]"}


def test_named_tuples_and_struct_sequences_do_not_crash() -> None:
    pair = namedtuple("pair", "left right")
    result = _redact(
        {"event": "x", "pair": pair("/feeds/t.xml", 1), "ver": sys.version_info}
    )
    assert result["pair"] == ("/feeds/[redacted].xml", 1)
    assert result["ver"] == tuple(sys.version_info)


def test_redacts_inside_sets() -> None:
    result = _redact({"event": "x", "paths": {"/feeds/t.xml"}})
    assert result["paths"] == {"/feeds/[redacted].xml"}


def test_redacts_inside_nested_structures() -> None:
    result = _redact({"event": "x", "ctx": {"token": "a", "urls": ["/feeds/t.xml"]}})
    assert result["ctx"] == {"token": "[redacted]", "urls": ["/feeds/[redacted].xml"]}


def test_json_output_is_one_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("info", "json")
    log = structlog.get_logger()
    log.info("app.started", count=2, token="secret")
    log.debug("app.hidden")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "app.started"
    assert payload["level"] == "info"
    assert payload["count"] == 2
    assert payload["timestamp"].endswith("Z")
    assert payload["token"] == "[redacted]"
    assert "secret" not in lines[0]


def test_json_output_redacts_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("info", "json")
    log = structlog.get_logger()
    try:
        raise RuntimeError("GET /feeds/topsecret.xml failed")
    except RuntimeError:
        log.exception("app.failed")
    out = capsys.readouterr().out
    assert "topsecret" not in out
    assert json.loads(out)["event"] == "app.failed"


def test_debug_level_is_honoured(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("debug", "json")
    structlog.get_logger().debug("app.verbose")
    assert "app.verbose" in capsys.readouterr().out


def test_console_format_is_human_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("info", "console")
    structlog.get_logger().info("app.started", token="secret")
    out = capsys.readouterr().out
    assert "app.started" in out
    assert "secret" not in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
