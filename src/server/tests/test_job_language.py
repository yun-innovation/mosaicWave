"""Job text, backup and export warnings, and Takeout warnings in the response language
(doc/i18n.md § API language, milestone 8 PR 2)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from mosaicwave.api.v1.library import _warning_outs
from mosaicwave.config import Settings
from mosaicwave.i18n import request_state, translate
from mosaicwave.jobtext import add_event, failure, jt, localize_warning, localized_warnings, set_current, set_error, warning_item
from mosaicwave.library.importjob import job_view
from mosaicwave.models import Job

from test_backup import _seed

HEADER = "X-MosaicWave-Language"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", takeout_dir=None, source_dir=None)


@contextmanager
def _answering_in(language: str | None) -> Iterator[None]:
    token = request_state.set({"language": language} if language else {})
    try:
        yield
    finally:
        request_state.reset(token)


def _until(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 20
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in {"done", "error", "cancelled"}:
            return body
        time.sleep(0.05)
    raise TimeoutError(body)


def _events(client: TestClient, job_id: str, **kwargs: object) -> list[str]:
    res = client.get(f"/api/v1/jobs/{job_id}/events", **kwargs)  # type: ignore[arg-type]
    assert res.status_code == 200, res.text
    return [e["message"] for e in res.json()]


def test_job_events_are_read_in_the_response_language(tmp_path: Path) -> None:
    client = _seed(_settings(tmp_path))
    job = client.post("/api/v1/me/backups", json={}).json()
    assert _until(client, job["id"])["status"] == "done"

    english = _events(client, job["id"])
    assert english[0] == "Library backup started"
    assert english[-1].startswith("Backup ") and "assets" in english[-1]

    zh = _events(client, job["id"], headers={HEADER: "zh-Hant"})
    assert len(zh) == len(english)
    assert zh[0] == translate("job_library_backup_started", {}, "zh-Hant", "")
    assert zh[0] != english[0]
    assert zh[-1] != english[-1]
    assert "{{" not in zh[-1]
    # The name and counts come from params, so they appear as they are.
    name = english[-1].split(":")[0].removeprefix("Backup ")
    assert name in zh[-1]

    # Reading in another language changes nothing that is stored.
    assert _events(client, job["id"]) == english
    assert _events(client, job["id"], params={"lang": "zh-Hans"})[0] == translate(
        "job_library_backup_started", {}, "zh-Hans", ""
    )


def test_job_state_rendered_when_read_and_english_kept_in_the_fields() -> None:
    row = Job(kind="takeout_import", status="error")
    set_error(row, jt("job_cancelled"))
    set_current(row, jt("job_current_importing"))
    # The stored fields stay English, so older readers and logs are unchanged.
    assert (row.error, row.current) == ("cancelled", "importing")

    with _answering_in(None):
        assert (job_view(row)["error"], job_view(row)["current"]) == ("cancelled", "importing")
    with _answering_in("zh-Hant"):
        view = job_view(row)
    assert view["error"] == translate("job_cancelled", {}, "zh-Hant", "")
    assert view["current"] == translate("job_current_importing", {}, "zh-Hant", "")
    assert view["error"] != "cancelled"


def test_a_plain_assignment_drops_the_code() -> None:
    row = Job(kind="takeout_import", status="running")
    set_current(row, jt("job_current_importing"))
    row.current = "Album/photo.jpg"  # a photo path is data, never translated
    assert row.current_code is None and row.current_params is None
    with _answering_in("zh-Hans"):
        assert job_view(row)["current"] == "Album/photo.jpg"


def test_failure_keeps_the_deep_message_as_a_param() -> None:
    deep = OSError(22, "Invalid argument")
    plain = failure(deep)
    assert (plain.code, plain.params) == ("job_failed", {"message": "[Errno 22] Invalid argument"})
    at = failure(deep, "Album/2:15 PM.jpg")
    assert at.code == "job_failed_at"
    assert at.params == {"current": "Album/2:15 PM.jpg", "message": "[Errno 22] Invalid argument"}
    # The OS text is shown as it is in every language; only the frame is translated.
    assert "[Errno 22] Invalid argument" in at.render("zh-Hant")
    assert "Album/2:15 PM.jpg" in at.render("zh-Hant")
    assert at.render("en") == "Album/2:15 PM.jpg: [Errno 22] Invalid argument"


def test_events_without_a_code_stay_as_written() -> None:
    class Session:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, item: object) -> None:
            self.added.append(item)

    session = Session()
    add_event(session, "job1", "info", "a plain line")
    add_event(session, "job1", "info", jt("job_moved"))
    plain, coded = session.added
    assert (plain.message, plain.code) == ("a plain line", None)  # type: ignore[attr-defined]
    assert (coded.message, coded.code) == ("Library moved to the new folder", "job_moved")  # type: ignore[attr-defined]


def test_backup_and_export_warning_sentences_are_recognised() -> None:
    sentences = {
        "job_warn_vanished_backup": "3 file(s) disappeared while the backup ran",
        "job_warn_unsupported_names": "2 file(s) with unsupported names were left out",
        "job_warn_outside_sources": "1 source(s) keep their files outside libraries/ and are not in this archive",
        "job_warn_photos_missing": "4 photo(s) were not found and are not in the export",
        "job_warn_removed_folder": "5 photo(s) belong to a removed folder and are not in the export",
        "job_warn_unreadable": "6 file(s) could not be read and are not in the export",
    }
    for code, text in sentences.items():
        item = warning_item(text)
        assert item is not None and item.code == code, text
        assert item.english == text
        zh = localize_warning(text, "zh-Hant")
        assert zh == item.render("zh-Hant") and zh != text
        assert str(item.params["count"]) in zh
    assert localize_warning(sentences["job_warn_unreadable"], "en") == sentences["job_warn_unreadable"]
    assert localize_warning("something else", "zh-Hant") == "something else"


def test_manifest_warnings_follow_the_response_language() -> None:
    items = ["3 file(s) disappeared while the backup ran", "an unknown note"]
    with _answering_in("zh-Hans"):
        out = localized_warnings(items)
    assert out[0] == translate("job_warn_vanished_backup", {"count": 3}, "zh-Hans", "")
    assert out[1] == "an unknown note"
    with _answering_in(None):
        assert localized_warnings(items) == items


def test_takeout_warning_message_follows_the_response_language() -> None:
    raw = [
        {
            "code": "reads_zips",
            "severity": "info",
            "message": "Import reads 2 zip archives in place.",
            "path": None,
            "params": {"count": 2},
        },
        {"code": "", "severity": "warning", "message": "no code, so it stays", "path": None, "params": {}},
    ]
    with _answering_in(None):
        english = _warning_outs(raw)
    assert [w.message for w in english] == [raw[0]["message"], "no code, so it stays"]
    with _answering_in("zh-Hant"):
        zh = _warning_outs(raw)
    assert zh[0].message == translate("reads_zips", {"count": 2}, "zh-Hant", "")
    assert zh[0].message != raw[0]["message"]
    assert zh[0].code == "reads_zips" and zh[0].params == {"count": 2}  # code and params never change
    assert zh[1].message == "no code, so it stays"
