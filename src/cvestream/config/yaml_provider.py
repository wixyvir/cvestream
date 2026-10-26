"""`endpoints.yaml` provider.

The file is read once per call: its bytes give the revision (SHA-256) and are
parsed, so the revision always describes the content that was validated.

Messages never quote the source: a PyYAML error message embeds the offending
line, which may be a `token:` line. They carry the position and the problem only.
"""

import hashlib
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

from cvestream.config.provider import (
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
    build_snapshot,
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that rejects a repeated mapping key.

    PyYAML otherwise keeps the last value silently, so a duplicated `min_cvss`
    would win without a trace.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> Any:
        seen: set[object] = set()
        for key_node, _ in node.value:
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


class YamlConfigProvider:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> bytes:
        try:
            return self._path.read_bytes()
        except OSError as exc:
            msg = f"{self._path}: cannot read: {type(exc).__name__}"
            raise ConfigUnavailableError(msg) from None

    def revision(self) -> str:
        return hashlib.sha256(self._read()).hexdigest()

    def load(self) -> ConfigSnapshot:
        content = self._read()
        source = str(self._path)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ConfigValidationError(source, ["file is not valid UTF-8"]) from None
        try:
            data = _parse(text)
        except yaml.YAMLError as exc:
            raise ConfigValidationError(source, [_yaml_problem(exc)]) from None
        if not isinstance(data, dict):
            msg = "top level must be a mapping"
            raise ConfigValidationError(source, [msg])
        return build_snapshot(
            data, revision=hashlib.sha256(content).hexdigest(), source=source
        )
