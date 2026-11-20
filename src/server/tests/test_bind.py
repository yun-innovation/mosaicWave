"""Bind address: one rule for every instance (doc/qpkg.md § Bind address)."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from authutil import authed_client, setup_admin
from mosaicwave import bind
from mosaicwave.config import Settings, write_data_dir_config

NOT_THIS_MACHINE = "203.0.113.5"  # TEST-NET-3: never assigned to a local adapter


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_HOST", raising=False)
    monkeypatch.delenv(bind.BOUND_ENV, raising=False)


def test_parse_hosts() -> None:
    assert bind.parse_hosts("127.0.0.1") == ["127.0.0.1"]
    assert bind.parse_hosts("localhost, 0.0.0.0") == ["127.0.0.1", "0.0.0.0"]
    assert bind.parse_hosts("192.0.2.10 192.0.2.10;[::1]") == ["192.0.2.10", "::1"]
    for bad in ("", "  ", "fileserver", "192.0.2", "1.2.3.4:80"):
        with pytest.raises(ValueError):
            bind.parse_hosts(bad)


def test_hosts_for_modes() -> None:
    assert bind.hosts_for("local") == ["127.0.0.1"]
    assert bind.hosts_for("all") == ["0.0.0.0"]
    # Loopback is always bound; a list only adds to it.
    assert bind.hosts_for("list", ["192.0.2.10"]) == ["127.0.0.1", "192.0.2.10"]
    assert bind.hosts_for("list", ["127.0.0.1", "localhost", "192.0.2.10"]) == ["127.0.0.1", "192.0.2.10"]
    assert bind.hosts_for("list", ["192.0.2.10", "0.0.0.0"]) == ["0.0.0.0"]  # includes loopback already
    assert bind.extra_addresses(["127.0.0.1", "192.0.2.10"]) == ["192.0.2.10"]
    for nothing in ([], ["127.0.0.1"], ["localhost"]):
        with pytest.raises(ValueError):
            bind.hosts_for("list", nothing)
    with pytest.raises(ValueError):
        bind.hosts_for("everything")


def test_precedence_startup_then_saved_then_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = tmp_path / "boot"
    got = bind.resolve(boot, "standalone")
    assert (got.hosts, got.source) == (["127.0.0.1"], "default")
    got = bind.resolve(boot, "qnap")
    assert (got.hosts, got.source) == (["0.0.0.0"], "default")

    bind.write_saved(boot, "list", ["192.0.2.10"])
    got = bind.resolve(boot, "qnap")
    assert (got.hosts, got.source) == (["127.0.0.1", "192.0.2.10"], "saved")

    explicit = bind.resolve(boot, "qnap", explicit="0.0.0.0")
    assert (explicit.hosts, explicit.source) == (["0.0.0.0"], "startup")
    monkeypatch.setenv("MOSAICWAVE_HOST", "localhost")
    got = bind.resolve(boot, "qnap")
    assert (got.hosts, got.source) == (["127.0.0.1"], "startup")
    with pytest.raises(ValueError):
        bind.resolve(boot, "qnap", explicit="not-an-ip")


def test_unusable_saved_setting_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"web_bind": {"mode": "list", "addresses": ["nope"]}}))
    got = bind.resolve(tmp_path, "standalone")
    assert (got.hosts, got.source) == (["127.0.0.1"], "default")


def test_bind_all_reports_what_failed() -> None:
    socks, errors = bind.bind_all(["127.0.0.1", NOT_THIS_MACHINE], 0, retry_seconds=0)
    try:
        assert [s.getsockname()[0] for s in socks] == ["127.0.0.1"]
        assert list(errors) == [NOT_THIS_MACHINE]
    finally:
        for s in socks:
            s.close()


def test_bind_all_retries_until_it_works() -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    holder.listen()
    calls = {"n": 0}

    def sleep(_seconds: float) -> None:
        calls["n"] += 1
        holder.close()  # the address frees up while we wait

    socks, errors = bind.bind_all(["127.0.0.1"], port, retry_seconds=60, pause=0, sleep=sleep)
    try:
        assert errors == {}
        assert calls["n"] >= 1
        assert [s.getsockname()[1] for s in socks] == [port]
    finally:
        for s in socks:
            s.close()


def _close(socks: list[socket.socket]) -> None:
    for s in socks:
        s.close()


def test_saved_address_that_fails_falls_back_to_loopback(tmp_path: Path) -> None:
    bind.write_saved(tmp_path, "list", [NOT_THIS_MACHINE])
    socks, choice = bind.bind_for_start(tmp_path, "standalone", 0, retry_seconds=0)
    try:
        assert choice.source == "saved"
        assert [s.getsockname()[0] for s in socks] == ["127.0.0.1"]
        failure = bind.read_failure(tmp_path)
        assert failure is not None
        assert failure["wanted"] == ["127.0.0.1", NOT_THIS_MACHINE]
        assert NOT_THIS_MACHINE in failure["failed"]
        assert failure["bound"] == ["127.0.0.1"]
    finally:
        _close(socks)

    # A later start that binds everything clears the report.
    bind.write_saved(tmp_path, "local", [])
    socks, _ = bind.bind_for_start(tmp_path, "standalone", 0, retry_seconds=0)
    _close(socks)
    assert bind.read_failure(tmp_path) is None


def test_partly_working_list_keeps_the_working_addresses(tmp_path: Path) -> None:
    bind.write_saved(tmp_path, "list", ["127.0.0.1", NOT_THIS_MACHINE])
    socks, _ = bind.bind_for_start(tmp_path, "standalone", 0, retry_seconds=0)
    try:
        assert [s.getsockname()[0] for s in socks] == ["127.0.0.1"]
        assert bind.read_failure(tmp_path)["bound"] == ["127.0.0.1"]
    finally:
        _close(socks)


def test_explicit_startup_address_that_fails_exits(tmp_path: Path) -> None:
    bind.write_saved(tmp_path, "local", [])
    with pytest.raises(bind.StartupBindError, match="no fallback"):
        bind.bind_for_start(tmp_path, "standalone", 0, explicit=NOT_THIS_MACHINE, retry_seconds=0)
    assert bind.read_failure(tmp_path) is None
    with pytest.raises(bind.StartupBindError, match="startup choice"):
        bind.bind_for_start(tmp_path, "standalone", 0, explicit="nope", retry_seconds=0)


def test_busy_port_with_no_fallback_possible_exits(tmp_path: Path) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        with pytest.raises(bind.StartupBindError):
            bind.bind_for_start(tmp_path, "standalone", holder.getsockname()[1], retry_seconds=0)
    finally:
        holder.close()


def test_record_bound_feeds_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "dev")
    bind.write_saved(tmp_path, "local", [])
    socks, choice = bind.bind_for_start(tmp_path, "standalone", 0, retry_seconds=0)
    try:
        bind.record_bound(socks, choice)
        assert bind.bound_hosts() == ["127.0.0.1"]
        assert Settings.from_env().bind_host == "127.0.0.1"
    finally:
        _close(socks)


def test_move_library_keeps_the_saved_bind(tmp_path: Path) -> None:
    bind.write_saved(tmp_path, "list", ["192.0.2.10"])
    write_data_dir_config(tmp_path, tmp_path / "elsewhere")
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["data_dir"] == str((tmp_path / "elsewhere").resolve())
    assert saved["web_bind"] == {"mode": "list", "addresses": ["192.0.2.10"]}


# --- API -----------------------------------------------------------------------------------------


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        takeout_dir=None,
        source_dir=None,
        bootstrap_dir=tmp_path / "boot",
    )
    return authed_client(settings)


def _maintenance_on(client: TestClient) -> None:
    res = client.put("/api/v1/maintenance", json={"enabled": True, "reason": "network"})
    assert res.status_code == 200, res.text


def test_network_admin_only(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    got = admin.get("/api/v1/settings/network")
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["mode"], body["effective"], body["source"]) == ("local", ["127.0.0.1"], "default")
    assert body["startup_locked"] is False and body["failure"] is None

    created = admin.put(
        "/api/v1/users",
        json={"username": "pat", "password": "memberpass", "display_name": "Pat", "role": "member"},
    )
    assert created.status_code == 200, created.text
    member = TestClient(admin.app)
    assert member.post("/api/v1/auth/login", json={"username": "pat", "password": "memberpass"}).status_code == 200
    assert member.get("/api/v1/settings/network").status_code == 403
    assert member.put("/api/v1/settings/network", json={"mode": "all"}).status_code == 403


def test_network_save_needs_maintenance(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    res = admin.put("/api/v1/settings/network", json={"mode": "all"})
    assert res.status_code == 409
    assert res.json()["code"] == "maintenance_required"
    assert bind.read_saved(tmp_path / "boot") is None


def test_network_save_validates(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    _maintenance_on(admin)
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": []})
    assert (res.status_code, res.json()["code"]) == (400, "bind_invalid")
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": ["127.0.0.1"]})
    assert (res.status_code, res.json()["code"]) == (400, "bind_invalid")  # loopback is not a choice
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": ["fileserver"]})
    assert (res.status_code, res.json()["code"]) == (400, "bind_invalid")
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": [NOT_THIS_MACHINE]})
    assert (res.status_code, res.json()["code"]) == (400, "bind_not_local")
    assert res.json()["params"]["address"] == NOT_THIS_MACHINE
    assert bind.read_saved(tmp_path / "boot") is None


def test_network_save_and_read_back(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    _maintenance_on(admin)
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": ["127.0.0.1", "0.0.0.0"]})
    assert res.status_code == 200, res.text
    assert res.json()["restarting"] is False  # skipped under pytest
    assert bind.read_saved(tmp_path / "boot") == ("list", ["0.0.0.0"])  # loopback is never stored
    body = admin.get("/api/v1/settings/network").json()
    assert (body["mode"], body["addresses"], body["effective"], body["source"]) == (
        "list",
        ["0.0.0.0"],
        ["0.0.0.0"],
        "saved",
    )
    res = admin.put("/api/v1/settings/network", json={"mode": "all"})
    assert res.status_code == 200, res.text
    assert bind.read_saved(tmp_path / "boot") == ("all", [])
    assert admin.get("/api/v1/settings/network").json()["effective"] == ["0.0.0.0"]


def test_network_locked_by_explicit_startup_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _client(tmp_path)
    _maintenance_on(admin)
    monkeypatch.setenv("MOSAICWAVE_HOST", "127.0.0.1")
    body = admin.get("/api/v1/settings/network").json()
    assert body["startup_locked"] is True and body["source"] == "startup"
    res = admin.put("/api/v1/settings/network", json={"mode": "all"})
    assert (res.status_code, res.json()["code"]) == (409, "bind_startup_locked")


def test_network_reports_the_last_bind_failure(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    boot = tmp_path / "boot"
    bind.write_saved(boot, "list", [NOT_THIS_MACHINE])
    socks, choice = bind.bind_for_start(boot, "standalone", 0, retry_seconds=0)
    try:
        bind.record_bound(socks, choice)
        body = admin.get("/api/v1/settings/network").json()
    finally:
        _close(socks)
    assert body["effective"] == ["127.0.0.1"]
    assert body["failure"]["wanted"] == ["127.0.0.1", NOT_THIS_MACHINE]
    assert NOT_THIS_MACHINE in body["failure"]["failed"]


def test_network_list_always_keeps_loopback(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    _maintenance_on(admin)
    other = _other_local_address()
    if other is None:
        pytest.skip("no non-loopback address on this machine")
    res = admin.put("/api/v1/settings/network", json={"mode": "list", "addresses": [other]})
    assert res.status_code == 200, res.text
    body = admin.get("/api/v1/settings/network").json()
    assert body["addresses"] == [other]  # only the extra is stored and shown
    assert body["effective"] == ["127.0.0.1", other]  # loopback is always bound
    assert bind.read_saved(tmp_path / "boot") == ("list", [other])


def _other_local_address() -> str | None:
    """An address of this machine other than loopback, if it has one."""
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127."):
                bind.probe_bindable(addr)
                return addr
    except OSError:
        pass
    return None

# --- port ------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOSAICWAVE_PORT", raising=False)
    monkeypatch.delenv(bind.PORT_SEED_ENV, raising=False)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_precedence_startup_then_saved_then_seed_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bind.resolve_port(tmp_path) == (8000, "default")
    monkeypatch.setenv(bind.PORT_SEED_ENV, "8090")
    assert bind.resolve_port(tmp_path) == (8090, "default")
    bind.write_saved_port(tmp_path, 9100)
    assert bind.resolve_port(tmp_path) == (9100, "saved")
    monkeypatch.setenv("MOSAICWAVE_PORT", "9200")
    assert bind.resolve_port(tmp_path) == (9200, "startup")
    assert bind.resolve_port(tmp_path, explicit=9300) == (9300, "startup")
    monkeypatch.setenv("MOSAICWAVE_PORT", "not-a-port")  # unusable: ignored, as before
    assert bind.resolve_port(tmp_path) == (9100, "saved")


def test_unusable_saved_port_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"web_port": 80}))
    assert bind.read_saved_port(tmp_path) is None
    (tmp_path / "config.json").write_text(json.dumps({"web_port": "9000"}))
    assert bind.read_saved_port(tmp_path) is None


def test_saving_the_bind_keeps_the_port_and_back(tmp_path: Path) -> None:
    bind.write_saved_port(tmp_path, 9100)
    bind.write_saved(tmp_path, "all", [])
    assert bind.read_saved_port(tmp_path) == 9100
    bind.write_saved_port(tmp_path, 9101)
    assert bind.read_saved(tmp_path) == ("all", [])
    write_data_dir_config(tmp_path, tmp_path / "elsewhere")
    assert bind.read_saved_port(tmp_path) == 9101
    assert bind.read_saved(tmp_path) == ("all", [])


def test_serve_print_and_set_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("mosaicwave.config._platform_bootstrap_dir", lambda: tmp_path)
    monkeypatch.setenv(bind.PORT_SEED_ENV, "8090")
    assert bind.main(["--print-port"]) == 0
    assert capsys.readouterr().out.strip() == "8090"
    assert bind.main(["--set-port", "9100"]) == 0
    assert capsys.readouterr().out.strip() == "9100"
    assert bind.read_saved_port(tmp_path) == 9100
    assert bind.main(["--print-port"]) == 0
    assert capsys.readouterr().out.strip() == "9100"
    assert bind.main(["--print-port", "--port", "9300"]) == 0
    assert capsys.readouterr().out.strip() == "9300"
    assert bind.main(["--set-port", "80"]) == 2
    assert bind.read_saved_port(tmp_path) == 9100


def test_port_file_is_written(tmp_path: Path) -> None:
    bind.write_port_file(tmp_path / "boot", 9100)
    assert (tmp_path / "boot" / "web.port").read_text().strip() == "9100"


def test_network_reports_the_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bind.PORT_SEED_ENV, "8090")
    admin = _client(tmp_path)
    body = admin.get("/api/v1/settings/network").json()
    assert (body["port"], body["effective_port"], body["port_source"], body["port_startup_locked"]) == (
        8090,
        8090,
        "default",
        False,
    )


def test_network_save_port(tmp_path: Path) -> None:
    admin = _client(tmp_path)
    boot = tmp_path / "boot"
    res = admin.put("/api/v1/settings/network", json={"port": _free_port()})
    assert (res.status_code, res.json()["code"]) == (409, "maintenance_required")
    _maintenance_on(admin)
    for bad in (0, 80, 1023, 65536, -5):
        res = admin.put("/api/v1/settings/network", json={"port": bad})
        assert (res.status_code, res.json()["code"]) == (400, "port_invalid"), bad
    res = admin.put("/api/v1/settings/network", json={})
    assert (res.status_code, res.json()["code"]) == (400, "bind_invalid")

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        busy = holder.getsockname()[1]
        res = admin.put("/api/v1/settings/network", json={"port": busy})
        assert (res.status_code, res.json()["code"]) == (400, "port_in_use")
        assert res.json()["params"]["port"] == busy
    finally:
        holder.close()
    assert bind.read_saved_port(boot) is None

    port = _free_port()
    res = admin.put("/api/v1/settings/network", json={"port": port})
    assert res.status_code == 200, res.text
    assert res.json()["port"] == port
    assert bind.read_saved_port(boot) == port
    assert bind.read_saved(boot) is None  # the address was not touched

    # Changing only the address keeps the port.
    res = admin.put("/api/v1/settings/network", json={"mode": "all"})
    assert res.status_code == 200, res.text
    assert bind.read_saved_port(boot) == port
    assert bind.read_saved(boot) == ("all", [])


def test_network_port_locked_by_explicit_startup_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _client(tmp_path)
    _maintenance_on(admin)
    monkeypatch.setenv("MOSAICWAVE_PORT", str(_free_port()))
    body = admin.get("/api/v1/settings/network").json()
    assert body["port_startup_locked"] is True and body["port_source"] == "startup"
    assert body["startup_locked"] is False  # the address is still the page's to change
    res = admin.put("/api/v1/settings/network", json={"port": _free_port()})
    assert (res.status_code, res.json()["code"]) == (409, "port_startup_locked")
    res = admin.put("/api/v1/settings/network", json={"mode": "all"})
    assert res.status_code == 200, res.text


def test_record_bound_carries_the_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOSAICWAVE_DATA_DIR", str(tmp_path / "data"))
    bind.write_saved(tmp_path, "local", [])
    socks, choice = bind.bind_for_start(tmp_path, "standalone", 0, retry_seconds=0)
    try:
        bind.record_bound(socks, choice, 9100, "saved")
        info = bind.bound_info()
        assert info["port"] == 9100 and info["port_source"] == "saved"
        assert Settings.from_env().bind_port == 9100
    finally:
        _close(socks)
