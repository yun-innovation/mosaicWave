"""Response language of the API (doc/i18n.md § API language)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client
from mosaicwave.config import Settings
from mosaicwave.i18n import LOCALES, catalog, normalize_locale, translate
from mosaicwave.main import create_app

HEADER = "X-MosaicWave-Language"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


def _fresh(tmp_path: Path) -> TestClient:
    """A server nobody has logged in to: ``POST /auth/login`` answers 400/401 with a code."""
    return TestClient(create_app(_settings(tmp_path)))


def _text(code: str, language: str, **params: object) -> str:
    text = translate(code, params, language, "")
    assert text, f"{code} has no {language} text"
    return text


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en", "en"),
        ("en-GB", "en"),
        ("EN_us", "en"),
        ("zh-Hant", "zh-Hant"),
        ("zh-TW", "zh-Hant"),
        ("zh-HK", "zh-Hant"),
        ("zh-Hant-TW", "zh-Hant"),
        ("zh-Hans", "zh-Hans"),
        ("zh-CN", "zh-Hans"),
        ("zh-SG", "zh-Hans"),
        ("zh", "zh-Hans"),
        ("fr", None),
        ("", None),
        (None, None),
    ],
)
def test_tags_map_to_supported_locales(tag: str | None, expected: str | None) -> None:
    assert normalize_locale(tag) == expected


def _codes(locale: str) -> set[str]:
    return {re.sub(r"_(one|other)$", "", key) for key in catalog(locale)}


def test_server_catalogs_cover_the_same_codes() -> None:
    english = _codes("en")
    assert english
    for locale in LOCALES:
        assert _codes(locale) == english, locale


def test_plural_and_placeholders() -> None:
    one = translate("reads_zips", {"count": 1}, "en", "")
    many = translate("reads_zips", {"count": 3}, "en", "")
    assert "1 zip" in one and "3 zip" in many
    assert one != many
    # Chinese has a single form; a missing code falls back to the given text.
    assert "3" in translate("reads_zips", {"count": 3}, "zh-Hant", "")
    assert translate("no_such_code", {}, "zh-Hant", "plain English") == "plain English"


def test_header_sets_the_response_language(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    english = client.post("/api/v1/auth/login", json={})
    assert english.json()["code"] == "credentials_required"
    assert english.headers["content-language"] == "en"
    assert HEADER in english.headers["vary"]

    zh = client.post("/api/v1/auth/login", json={}, headers={HEADER: "zh-Hant"})
    assert zh.status_code == english.status_code
    body = zh.json()
    assert body["code"] == "credentials_required"  # the code never changes
    assert body["detail"] == _text("credentials_required", "zh-Hant")
    assert body["detail"] != english.json()["detail"]
    assert zh.headers["content-language"] == "zh-Hant"
    assert HEADER in zh.headers["vary"]


def test_query_names_the_language_and_wide_tags_work(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    res = client.post("/api/v1/auth/login?lang=zh-CN", json={})
    assert res.json()["detail"] == _text("credentials_required", "zh-Hans")
    assert res.headers["content-language"] == "zh-Hans"
    res = client.post("/api/v1/auth/login", json={}, headers={HEADER: "zh-TW"})
    assert res.headers["content-language"] == "zh-Hant"


def test_accept_language_is_never_read(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    res = client.post("/api/v1/auth/login", json={}, headers={"Accept-Language": "zh-TW,zh;q=0.9"})
    assert res.headers["content-language"] == "en"
    assert res.json()["detail"] == "username and password required"


def test_unsupported_response_language_falls_back_without_error(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    res = client.post("/api/v1/auth/login", json={}, headers={HEADER: "fr"})
    assert res.status_code == 400
    assert res.json()["code"] == "credentials_required"
    assert res.headers["content-language"] == "en"
    res = client.post("/api/v1/auth/login?lang=xx", json={}, headers={HEADER: "klingon"})
    assert res.headers["content-language"] == "en"


def test_params_fill_the_translated_text(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    res = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "short", "display_name": ""},
        headers={HEADER: "zh-Hans"},
    )
    body = res.json()
    assert body["code"] == "password_too_short"
    assert body["params"] == {"min": 8}
    assert body["detail"] == _text("password_too_short", "zh-Hans", min=8)
    assert "8" in body["detail"] and "{{" not in body["detail"]


def test_saved_locale_is_the_fallback_and_the_request_wins(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    assert client.patch("/api/v1/me", json={"locale": "zh-Hans"}).status_code == 200

    # No language named: the saved one answers.
    saved = client.patch("/api/v1/me", json={"locale": "fr"})
    assert saved.status_code == 400
    assert saved.json()["detail"] == _text("invalid_locale", "zh-Hans", locale="fr")
    assert saved.headers["content-language"] == "zh-Hans"

    # A named language beats the saved one, for this request only.
    named = client.patch("/api/v1/me", json={"locale": "fr"}, headers={HEADER: "zh-Hant"})
    assert named.json()["detail"] == _text("invalid_locale", "zh-Hant", locale="fr")
    assert named.headers["content-language"] == "zh-Hant"
    assert client.get("/api/v1/me").json()["locale"] == "zh-Hans"

    # An unsupported name falls through to the saved language.
    odd = client.patch("/api/v1/me", json={"locale": "fr"}, headers={HEADER: "fr"})
    assert odd.headers["content-language"] == "zh-Hans"


def test_a_request_language_is_never_saved(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.get("/api/v1/me", headers={HEADER: "zh-Hant"})
    assert res.json()["locale"] is None
    assert client.get("/api/v1/auth/status?lang=zh-Hans").json()["me"]["locale"] is None
    assert client.get("/api/v1/me").json()["locale"] is None


def test_switch_with_a_different_response_language_answers_in_the_target(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.patch("/api/v1/me", json={"locale": "zh-TW"}, headers={HEADER: "en"})
    assert res.status_code == 200
    assert res.json()["locale"] == "zh-Hant"  # only the canonical code is saved and returned
    assert res.headers["content-language"] == "zh-Hant"
    assert client.get("/api/v1/me").json()["locale"] == "zh-Hant"

    # The next request, naming nothing, arrives in the new language.
    after = client.patch("/api/v1/me", json={"locale": "fr"})
    assert after.json()["detail"] == _text("invalid_locale", "zh-Hant", locale="fr")
    assert after.headers["content-language"] == "zh-Hant"


def test_clearing_the_locale_answers_in_the_request_language(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    client.patch("/api/v1/me", json={"locale": "zh-Hant"})
    res = client.patch("/api/v1/me", json={"locale": None}, headers={HEADER: "zh-Hans"})
    assert res.status_code == 200
    assert res.json()["locale"] is None
    assert res.headers["content-language"] == "zh-Hans"
    assert client.get("/api/v1/me").headers["content-language"] == "en"


def test_unsupported_target_is_refused_and_nothing_is_saved(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    client.patch("/api/v1/me", json={"locale": "zh-Hant"})
    res = client.patch("/api/v1/me", json={"locale": "fr-CA"}, headers={HEADER: "en"})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "invalid_locale"
    assert body["params"] == {"locale": "fr-CA"}
    assert res.headers["content-language"] == "en"  # the error is in the response language
    assert client.get("/api/v1/me").json()["locale"] == "zh-Hant"


def test_unsupported_response_language_does_not_block_a_switch(tmp_path: Path) -> None:
    client = authed_client(_settings(tmp_path))
    res = client.patch("/api/v1/me", json={"locale": "zh-Hans"}, headers={HEADER: "fr"})
    assert res.status_code == 200
    assert res.json()["locale"] == "zh-Hans"


def test_auth_status_lists_the_supported_locales(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    assert client.get("/api/v1/auth/status").json()["locales"] == ["en", "zh-Hant", "zh-Hans"]


def test_non_api_and_binary_answers_carry_no_content_language(tmp_path: Path) -> None:
    client = _fresh(tmp_path)
    res = client.get("/not-the-api")
    assert "content-language" not in res.headers
