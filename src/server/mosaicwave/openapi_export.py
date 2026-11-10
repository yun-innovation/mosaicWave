"""The published OpenAPI schema: ``openapi/openapi.json`` at the repository root (doc/mobile.md, doc/architecture.md).

The mobile apps live in another repository and pin a version of this file, so it is committed and a test
(``tests/test_openapi_file.py``) fails when the API changed and the file did not.

    python -m mosaicwave.openapi_export            # rewrite openapi/openapi.json
    python -m mosaicwave.openapi_export --check    # exit 1 if the committed file is stale
    python -m mosaicwave.openapi_export some.json  # write somewhere else
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from mosaicwave.config import Settings
from mosaicwave.main import create_app
from mosaicwave.runtime import dispose_runtime

REPO_ROOT = Path(__file__).resolve().parents[3]
PUBLISHED = REPO_ROOT / "openapi" / "openapi.json"


def build_schema() -> dict:
    """The schema of a freshly created app. Nothing is read from a real data folder."""
    with tempfile.TemporaryDirectory(prefix="mosaicwave-openapi-") as tmp:
        settings = Settings(data_dir=Path(tmp) / "data", takeout_dir=None, source_dir=None)
        app = create_app(settings)
        try:
            return app.openapi()
        finally:
            dispose_runtime(app)


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def published_schema(path: Path = PUBLISHED) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    check = "--check" in argv
    paths = [a for a in argv if not a.startswith("--")]
    target = Path(paths[0]) if paths else PUBLISHED
    schema = build_schema()
    if check:
        if published_schema(target) == schema:
            print(f"{target} is up to date")
            return 0
        print(f"{target} is stale: run `python -m mosaicwave.openapi_export` in src/server and commit it", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(schema))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
