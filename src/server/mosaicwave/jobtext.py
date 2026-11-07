"""Text a job or a backup writes for a person, kept as a code plus params (doc/i18n.md § API language).

``jt("job_import_done", assets=3, albums=1)`` is the whole text. The English sentence is written
into the same text fields as before (``job.error``, ``job.current``, a job event's ``message``, a
manifest's ``warnings``), so older clients and logs still read plain English; the code and params
go next to it, and the API renders them into the response language when a job is read. A text with
no code (a photo path in ``current``, a deep ``OSError`` message) is data and is never translated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from mosaicwave.i18n import DEFAULT_LOCALE, catalog, current_language, translate


@dataclass(frozen=True)
class JobText:
    code: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def english(self) -> str:
        return translate(self.code, self.params, DEFAULT_LOCALE, self.code)

    def render(self, locale: str) -> str:
        return translate(self.code, self.params, locale, self.english)

    def params_json(self) -> str | None:
        return json.dumps(self.params, sort_keys=True, default=str) if self.params else None

    def as_item(self) -> dict[str, Any]:
        return {"code": self.code, "params": self.params}


def jt(code: str, **params: Any) -> JobText:
    return JobText(code, params)


def failure(exc: BaseException, current: str | None = None) -> JobText:
    """A job's error from an exception.

    An exception that carries its own ``code`` and ``params`` (ExportError and the like may in
    time) keeps them; anything else is the category ``job_failed`` around the English message,
    which ``params.message`` shows as it is. ``current`` (the file being worked on) is put in
    front when the message does not already name it.
    """
    code = getattr(exc, "code", None)
    params = getattr(exc, "params", None)
    if isinstance(code, str) and code.startswith("job_") and isinstance(params, dict):
        return JobText(code, params)
    message = str(exc) or exc.__class__.__name__
    if current and current not in message:
        return JobText("job_failed_at", {"current": current, "message": message})
    return JobText("job_failed", {"message": message})


_SLOT = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@lru_cache(maxsize=1)
def _warning_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """One pattern per ``job_warn_*`` English template, to recognise a warning written as a sentence."""
    patterns = []
    for code, template in catalog(DEFAULT_LOCALE).items():
        if not code.startswith("job_warn_"):
            continue
        parts = _SLOT.split(template)  # literal, name, literal, name, ...
        regex = "".join(
            f"(?P<{part}>.+?)" if index % 2 else re.escape(part) for index, part in enumerate(parts)
        )
        patterns.append((code, re.compile(f"^{regex}$", re.DOTALL)))
    return tuple(patterns)


def warning_item(text: str) -> JobText | None:
    """The code + params behind a backup or export warning sentence, or None for any other text.

    Archive manifests keep warnings as English sentences (older archives and clients read them);
    the API turns the ones it knows back into a code so it can answer in the response language.
    """
    for code, pattern in _warning_patterns():
        match = pattern.match(text)
        if match:
            params = {k: int(v) if v.isdigit() else v for k, v in match.groupdict().items()}
            return JobText(code, params)
    return None


def localize_warning(text: str, locale: str) -> str:
    """``text`` in ``locale`` when it is a known warning sentence; otherwise as it is."""
    if locale == DEFAULT_LOCALE:
        return text
    item = warning_item(text)
    return item.render(locale) if item else text


def localized_warnings(items: Any) -> list[str]:
    """A manifest's warning sentences in the response language of the request being answered."""
    locale = current_language()
    return [localize_warning(str(item), locale) for item in items or []]


def set_error(row: Any, text: JobText) -> None:
    """``job.error`` as English text plus its code (the plain assignment goes first: it clears the code)."""
    row.error = text.english
    row.error_code = text.code
    row.error_params = text.params_json()


def set_current(row: Any, text: JobText) -> None:
    row.current = text.english
    row.current_code = text.code
    row.current_params = text.params_json()


def add_event(session: Any, job_id: str, level: str, text: JobText | str) -> None:
    """A job log line. A plain ``str`` (a backup warning already written, a deep error) has no code."""
    from mosaicwave.models import JobEvent, new_id

    if isinstance(text, JobText):
        event = JobEvent(
            id=new_id(), job_id=job_id, level=level, message=text.english, code=text.code, params=text.params_json()
        )
    else:
        event = JobEvent(id=new_id(), job_id=job_id, level=level, message=text)
    session.add(event)


def parse_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def render(code: str | None, raw_params: str | dict[str, Any] | None, locale: str, fallback: str | None) -> str | None:
    """``fallback`` (the stored English) unless there is a code with a text in ``locale``."""
    if not code:
        return fallback
    params = raw_params if isinstance(raw_params, dict) else parse_params(raw_params)
    return translate(code, params, locale, fallback if fallback is not None else code)
