"""Positive allow-list for `domain`: stdlib, `packaging`, `cvestream.domain.*`.

import-linter can only deny a list of modules; this test is the real allow-list.
"""

import ast
import sys
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[3] / "src" / "cvestream" / "domain"
ALLOWED_THIRD_PARTY = {"packaging"}


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path}: relative import not allowed"
            assert node.module is not None
            modules.add(node.module)
    return modules


def _is_allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY:
        return True
    return module == "cvestream.domain" or module.startswith("cvestream.domain.")


def test_domain_imports_only_stdlib_packaging_or_domain() -> None:
    assert DOMAIN.is_dir()
    offenders = {
        str(path.relative_to(DOMAIN)): sorted(
            m for m in _imported_modules(path) if not _is_allowed(m)
        )
        for path in DOMAIN.rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}
