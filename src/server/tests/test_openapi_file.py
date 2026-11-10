"""openapi/openapi.json is the contract the mobile repository pins (doc/mobile.md). It must match the code."""

from __future__ import annotations

from mosaicwave.openapi_export import PUBLISHED, build_schema, published_schema, render


def test_published_schema_matches_the_code() -> None:
    published = published_schema()
    assert published is not None, f"{PUBLISHED} is missing: run `python -m mosaicwave.openapi_export` in src/server"
    assert published == build_schema(), (
        "The API changed but openapi/openapi.json did not: run `python -m mosaicwave.openapi_export` "
        "in src/server and commit the result (doc/mobile.md)"
    )


def test_schema_is_stable_and_describes_the_core_paths() -> None:
    first = build_schema()
    assert render(first) == render(build_schema())  # same text twice, so a diff only shows real changes
    for path in ("/api/v1/auth/login", "/api/v1/assets", "/api/v1/assets/months", "/api/v1/albums", "/api/v1/time-zones"):
        assert path in first["paths"], path
    assert first["info"]["title"] == "mosaicWave"
