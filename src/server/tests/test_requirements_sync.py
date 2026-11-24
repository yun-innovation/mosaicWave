"""The packaged requirements.txt (QPKG, MSI, posix) and pyproject.toml must list the same dependencies.

The packages install from ``src/qpkg/shared/requirements.txt``; developers install from
``pyproject.toml``. A dependency added to only one of them reaches one kind of install and not the other.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _packaged() -> set[str]:
    lines = (ROOT / "src" / "qpkg" / "shared" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip().replace(" ", "") for line in lines if line.strip() and not line.startswith("#")}


def _project() -> set[str]:
    data = tomllib.loads((ROOT / "src" / "server" / "pyproject.toml").read_text(encoding="utf-8"))
    return {dep.replace(" ", "") for dep in data["project"]["dependencies"]}


def test_packaged_requirements_match_pyproject() -> None:
    assert _packaged() == _project()
