"""`endpoints.yaml` provider.

The file is read once per call: its bytes give the revision (SHA-256) and are
parsed, so the revision always describes the content that was validated.

Messages never quote the source: a PyYAML error message embeds the offending
line, which may be a `token:` line. They carry the position and the problem only.
"""

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import yaml
from yaml.constructor import ConstructorError
from yaml.tokens import ScalarToken

from cvestream.config.models import ConfigDocument
from cvestream.config.provider import (
    ConfigConflictError,
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
    build_snapshot,
    validate_document,
)

_MERGE_TAG = "tag:yaml.org,2002:merge"
# A write that loses its race (the file changed between read and rename) starts
# over, at most this many times.
WRITE_ATTEMPTS: Final = 5
HEADER: Final = (
    "# Written by cvestream; comments are not kept (docs/configuration.md#cli).\n"
)
# A `#` at the start or after whitespace: a comment, unless a scalar holds it.
_COMMENT = re.compile(r"(?<!\S)#")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that rejects a repeated mapping key.

    PyYAML otherwise keeps the last value silently, so a duplicated `min_cvss`
    would win without a trace. Merge keys (`<<: *anchor`) keep working: a key
    written next to them overrides the merged one, as YAML intends.
    """

    def construct_object(self, node: yaml.Node, deep: bool = False) -> Any:
        try:
            return super().construct_object(node, deep=deep)
        except ValueError:
            # `2026-13-45`, `!!int abc`: PyYAML raises a bare `ValueError` whose
            # message quotes the value (maybe a token). Keep the position only.
            kind = node.tag.rsplit(":", 1)[-1]
            raise ConstructorError(
                None, None, f"invalid {kind} value", node.start_mark
            ) from None

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> Any:
        seen: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:
                continue  # `<<` has no constructor; the base class flattens it
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                continue  # unhashable key: the base class reports it
            if duplicate:
                raise ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


def _parse(text: str) -> object:
    loader = _UniqueKeyLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _yaml_problem(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        problem = exc.problem or type(exc).__name__
        return f"{mark.line + 1}:{mark.column + 1}: {problem}"
    return type(exc).__name__


def dump_document(document: ConfigDocument) -> str:
    """The YAML text of `document`, header line included.

    Only what differs from the defaults is written; `version`, `endpoints` and
    the required fields always are. Endpoint order is the document's. PyYAML
    quotes every string that would otherwise read back as another type
    (`"8.10"`, `"123"`, `"yes"`).
    """
    body = {
        "version": document.version,
        "endpoints": [
            endpoint.model_dump(mode="json", exclude_defaults=True)
            for endpoint in document.endpoints
        ],
    }
    text = yaml.safe_dump(
        body, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return HEADER + text


def _has_comments(content: bytes | None) -> bool:
    """Whether `content` holds a comment other than the cvestream header.

    A `#` inside a scalar (`name: 'Team #1'`, a block scalar) is not a comment:
    the YAML scanner gives the scalars' spans. Text the scanner rejects counts
    as commented when it holds a candidate `#` (the notice is only a notice).
    """
    if content is None:
        return False
    text = content.decode("utf-8", errors="replace")
    start = len(HEADER) if text.startswith(HEADER) else 0
    hashes = [m.start() for m in _COMMENT.finditer(text) if m.start() >= start]
    if not hashes:
        return False
    try:
        scalars = [
            (token.start_mark.index, token.end_mark.index)
            for token in yaml.scan(text, Loader=yaml.SafeLoader)
            if isinstance(token, ScalarToken)
        ]
    except yaml.YAMLError:
        return True
    return any(
        not any(begin <= index < end for begin, end in scalars) for index in hashes
    )


class YamlConfigProvider:
    def __init__(self, path: Path) -> None:
        self._path = path
        # Set by `update()` and `replace()`: the previous file had comments the
        # rewrite dropped, so the CLI can say so once.
        self.dropped_comments = False

    def _read(self) -> bytes:
        try:
            return self._path.read_bytes()
        except OSError as exc:
            msg = f"{self._path}: cannot read: {type(exc).__name__}"
            raise ConfigUnavailableError(msg) from None

    def _read_or_none(self) -> bytes | None:
        """The file bytes; `None` for a missing file (a store to seed)."""
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            msg = f"{self._path}: cannot read: {type(exc).__name__}"
            raise ConfigUnavailableError(msg) from None

    def revision(self) -> str:
        return hashlib.sha256(self._read()).hexdigest()

    def _data(self, content: bytes) -> dict[Any, Any]:
        source = str(self._path)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ConfigValidationError(source, ["file is not valid UTF-8"]) from None
        try:
            data = _parse(text)
        except yaml.YAMLError as exc:
            raise ConfigValidationError(source, [_yaml_problem(exc)]) from None
        except RecursionError:
            msg = "nesting is too deep"
            raise ConfigValidationError(source, [msg]) from None
        if not isinstance(data, dict):
            msg = "top level must be a mapping"
            raise ConfigValidationError(source, [msg])
        return data

    def load(self) -> ConfigSnapshot:
        content = self._read()
        return build_snapshot(
            self._data(content),
            revision=hashlib.sha256(content).hexdigest(),
            source=str(self._path),
        )

    def document(self) -> ConfigDocument:
        """The validated current content."""
        return validate_document(self._data(self._read()), source=str(self._path))

    # -- writing --

    def update(self, change: Callable[[ConfigDocument], ConfigDocument]) -> str:
        """Apply `change` to the file's document; return the new revision.

        A missing file starts from an empty document. An invalid current file
        refuses the edit (`replace()` repairs it). `change` is pure and may run
        once per attempt. If the file changes between the read and the rename
        (a hand edit, another CLI), the cycle starts over.
        """
        source = str(self._path)
        for _ in range(WRITE_ATTEMPTS):
            content = self._read_or_none()
            current = (
                ConfigDocument(version=1, endpoints=[])
                if content is None
                else validate_document(self._data(content), source=source)
            )
            new = validate_document(
                change(current).model_dump(mode="json"), source=source
            )
            revision = self._write(dump_document(new), expected=content)
            if revision is not None:
                self.dropped_comments = _has_comments(content)
                return revision
        msg = f"{source}: write conflict, gave up after {WRITE_ATTEMPTS} attempts"
        raise ConfigConflictError(msg)

    def replace(self, document: ConfigDocument) -> str:
        """Write `document` as the whole file, whatever the file holds now."""
        source = str(self._path)
        new = validate_document(document.model_dump(mode="json"), source=source)
        try:
            previous = self._path.read_bytes()
        except OSError:
            previous = None
        revision = self._write(dump_document(new), expected=None, check=False)
        if revision is None:  # pragma: no cover - `check=False` always writes
            msg = f"{source}: write conflict"
            raise ConfigConflictError(msg)
        self.dropped_comments = _has_comments(previous)
        return revision

    def _unchanged(self, expected: bytes | None) -> bool:
        """Whether the file still holds `expected` (`None`: still missing)."""
        try:
            return self._path.read_bytes() == expected
        except FileNotFoundError:
            return expected is None
        except OSError:
            return False

    def _write(
        self, text: str, *, expected: bytes | None, check: bool = True
    ) -> str | None:
        """Write `text` atomically; `None` when the file changed meanwhile.

        A temporary file in the target's own directory, `fsync`, then
        `os.replace`: the daemon sees the old or the new file, never a mix.
        """
        data = text.encode("utf-8")
        target = self._path.resolve()  # a symlink is written through, not replaced
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = 0o600
        except OSError as exc:
            raise self._cannot_write(exc) from None
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fchmod(handle.fileno(), mode)
                os.fsync(handle.fileno())
            if check and not self._unchanged(expected):
                return None
            os.replace(temporary, target)
            temporary = None
            self._sync_directory(target.parent)
        except OSError as exc:
            raise self._cannot_write(exc) from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return hashlib.sha256(data).hexdigest()

    def _cannot_write(self, exc: OSError) -> ConfigUnavailableError:
        msg = f"{self._path}: cannot write: {type(exc).__name__}"
        return ConfigUnavailableError(msg)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
