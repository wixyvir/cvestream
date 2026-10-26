"""Positive allow-list for `domain`: stdlib, `packaging`, `cvestream.domain.*`.

import-linter can only deny a list of modules; this test is the real allow-list.
"""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
DOMAIN = SRC / "cvestream" / "domain"
ALLOWED_THIRD_PARTY = {"packaging"}


def _package(path: Path) -> list[str]:
    """Package containing the module at `path`, as name parts."""
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    return parts[:-1]  # `pkg/__init__.py` -> pkg, `pkg/mod.py` -> pkg


def _imported_modules(source: str, package: list[str]) -> set[str]:
    """Absolute names of the modules imported by `source` (relative ones resolved)."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                assert node.module is not None
                modules.add(node.module)
                continue
            base = package[: len(package) - node.level + 1]
            if node.module is not None:
                modules.add(".".join([*base, node.module]))
            else:
                modules.update(".".join([*base, a.name]) for a in node.names)
    return modules


def _is_allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY:
        return True
    return module == "cvestream.domain" or module.startswith("cvestream.domain.")


def test_relative_imports_are_resolved_against_the_package() -> None:
    source = (
        "from . import model\nfrom .version import parse\nfrom .. import settings\n"
    )
    modules = _imported_modules(source, ["cvestream", "domain"])
    assert modules == {
        "cvestream.domain.model",
        "cvestream.domain.version",
        "cvestream.settings",
    }
    assert [m for m in sorted(modules) if not _is_allowed(m)] == ["cvestream.settings"]


def test_package_of_init_and_plain_module() -> None:
    assert _package(DOMAIN / "__init__.py") == ["cvestream", "domain"]
    assert _package(DOMAIN / "version.py") == ["cvestream", "domain"]


def test_domain_imports_only_stdlib_packaging_or_domain() -> None:
    assert DOMAIN.is_dir()
    offenders = {
        str(path.relative_to(DOMAIN)): sorted(
            m
            for m in _imported_modules(path.read_text(encoding="utf-8"), _package(path))
            if not _is_allowed(m)
        )
        for path in DOMAIN.rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}
