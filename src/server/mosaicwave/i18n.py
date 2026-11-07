"""Response language for the API (doc/i18n.md § API language).

A request names the language it wants answered in only with the ``X-MosaicWave-Language``
header or ``?lang=``; ``Accept-Language`` is never read, so a browser's automatic header can
change nothing. A supported value wins, then ``user.locale``, then English. A request language
is never saved; only ``PATCH /me`` saves ``user.locale``.

The error catalog is ``locales/errors.<locale>.json``, generated from the web's
``src/web/app/locales/<locale>/errors.json`` by ``npm run sync-locales`` (``npm run i18n-check``
fails when the copy is stale), so the web stays the single place the texts are written.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.requests import Request

LOCALES = ("en", "zh-Hant", "zh-Hans")
DEFAULT_LOCALE = "en"
LANGUAGE_HEADER = "X-MosaicWave-Language"
LANGUAGE_QUERY = "lang"

_LOCALES_DIR = Path(__file__).parent / "locales"
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*(?:,[^}]*)?\}\}")
_TRADITIONAL = {"hant", "tw", "hk", "mo"}
_SIMPLIFIED = {"hans", "cn", "sg"}


def normalize_locale(tag: str | None) -> str | None:
    """A BCP 47 tag (``zh-TW``, ``en-GB``, ``zh_cn``) → a supported locale code, or None.

    The same table as the web: Chinese with a Traditional script or region (Hant, TW, HK, MO)
    is ``zh-Hant``; Simplified or no hint (``zh``, Hans, CN, SG) is ``zh-Hans``; any English is
    ``en``. Anything else is unsupported (None); it never errors by itself.
    """
    if not tag:
        return None
    parts = [p for p in re.split(r"[-_]", tag.strip().lower()) if p]
    if not parts:
        return None
    if parts[0] == "en":
        return "en"
    if parts[0] != "zh":
        return None
    if any(p in _TRADITIONAL for p in parts[1:]):
        return "zh-Hant"
    return "zh-Hans"


def requested_language(request: Request) -> str | None:
    """The supported language this request names (header, then ``?lang=``), or None."""
    for raw in (request.headers.get(LANGUAGE_HEADER), request.query_params.get(LANGUAGE_QUERY)):
        locale = normalize_locale(raw)
        if locale:
            return locale
    return None


def _language_of(state: Any) -> str:
    get = state.get if isinstance(state, dict) else (lambda key: getattr(state, key, None))
    for key in ("language", "user_locale"):
        value = get(key)
        if value in LOCALES:
            return str(value)
    return DEFAULT_LOCALE


def response_language(request: Request) -> str:
    """Request language → the caller's saved ``user.locale`` → English."""
    return _language_of(request.state)


# The state dict of the request being answered, so code far from the endpoint (a job's text
# rendered when it is read) can ask for the response language without a ``request`` argument.
# Endpoints run in a thread that copies this context; the dict itself is shared, so a language
# set later (the caller's saved locale, found by the auth dependency) is seen too.
request_state: ContextVar[dict[str, Any] | None] = ContextVar("mosaicwave_request_state", default=None)


def current_language() -> str:
    """The response language of the request being answered; English outside a request."""
    state = request_state.get()
    return DEFAULT_LOCALE if state is None else _language_of(state)


@lru_cache(maxsize=None)
def catalog(locale: str) -> dict[str, str]:
    """``errors:{code}`` → text for ``locale``; empty when the file is missing."""
    try:
        data = json.loads((_LOCALES_DIR / f"errors.{locale}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _fill(text: str, params: dict[str, Any]) -> str:
    def value(match: re.Match[str]) -> str:
        found = params.get(match.group(1))
        return match.group(0) if found is None else str(found)

    return _PLACEHOLDER.sub(value, text)


def _template(locale: str, code: str, params: dict[str, Any]) -> str | None:
    entries = catalog(locale)
    count = params.get("count")
    if isinstance(count, int | float):
        form = "one" if count == 1 else "other"
        for suffix in (form, "other"):
            if f"{code}_{suffix}" in entries:
                return entries[f"{code}_{suffix}"]
    return entries.get(code)


def translate(code: str, params: dict[str, Any] | None, locale: str, fallback: str) -> str:
    """The text for ``code`` in ``locale``, then English, then ``fallback`` (never raises)."""
    params = params or {}
    for name in (locale, DEFAULT_LOCALE):
        template = _template(name, code, params)
        if template is not None:
            return _fill(template, params)
    return fallback


def warning_text(code: str, params: dict[str, Any] | None, locale: str, fallback: str) -> str:
    """A Takeout warning's ``message`` in ``locale`` (English keeps ``fallback``, the sentence it was written with).

    ``no_takeout_zips`` gets a second sentence, as in the web, when the scan found nested zips
    or other zip names.
    """
    if locale == DEFAULT_LOCALE or not code:
        return fallback
    params = params or {}
    text = translate(code, params, locale, "")
    if not text:
        return fallback
    if code != "no_takeout_zips":
        return text
    hint = "no_takeout_zips_hint_nested" if params.get("nested") else (
        "no_takeout_zips_hint_names" if params.get("other_names") else None
    )
    extra = translate(hint, params, locale, "") if hint else ""
    return f"{text} {extra}" if extra else text


def detail_for(request: Request, code: str, params: dict[str, Any] | None, detail: str) -> str:
    """``detail`` in the response language. English keeps the server's own sentence."""
    locale = response_language(request)
    if locale == DEFAULT_LOCALE:
        return detail
    return translate(code, params, locale, detail)
