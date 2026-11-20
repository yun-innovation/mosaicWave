"""Which addresses the API listens on, and how the server starts on them.

One rule for every instance (debug and packaged, every OS); see ``doc/qpkg.md`` § Bind address.
Highest first: an explicit startup choice (``MOSAICWAVE_HOST`` or ``--host``), the saved setting
(``web_bind`` in the bootstrap ``config.json``), the platform default. The launchers only run
``python -m mosaicwave.serve``; they never pick an address themselves.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("mosaicwave.bind")

FAILURE_FILE = "bind-failure.json"
BOUND_ENV = "MOSAICWAVE_BOUND"
RETRY_SECONDS = 30.0
MODES = ("local", "all", "list")
LOCAL_HOSTS = ["127.0.0.1"]
ALL_HOSTS = ["0.0.0.0"]

SOURCE_STARTUP = "startup"
SOURCE_SAVED = "saved"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class Choice:
    hosts: list[str]
    source: str


def parse_hosts(raw: str) -> list[str]:
    """``"127.0.0.1, localhost 192.0.2.10"`` to a de-duplicated list of IP addresses."""
    out: list[str] = []
    for part in re.split(r"[,\s;]+", raw or ""):
        part = part.strip().strip("[]")
        if not part:
            continue
        if part.lower() == "localhost":
            part = "127.0.0.1"
        try:
            text = str(ipaddress.ip_address(part))
        except ValueError:
            raise ValueError(f"not an IP address: {part}") from None
        if text not in out:
            out.append(text)
    if not out:
        raise ValueError("no address given")
    return out


def hosts_for(mode: str, addresses: list[str] | None = None) -> list[str]:
    if mode == "local":
        return list(LOCAL_HOSTS)
    if mode == "all":
        return list(ALL_HOSTS)
    if mode == "list":
        extras = extra_addresses(addresses)
        if any(ipaddress.ip_address(h).is_unspecified and ":" not in h for h in extras):
            return list(ALL_HOSTS)  # 0.0.0.0 already includes loopback (and cannot share the port with it)
        return list(LOCAL_HOSTS) + extras
    raise ValueError(f"unknown mode: {mode}")


def extra_addresses(addresses: list[str] | None) -> list[str]:
    """The typed addresses without loopback, which is always bound and not a choice. Needs at least one."""
    extras = [h for h in parse_hosts(",".join(addresses or [])) if h not in LOCAL_HOSTS]
    if not extras:
        raise ValueError("give at least one address besides 127.0.0.1 (it is always on)")
    return extras


def platform_default(platform: str) -> list[str]:
    return list(ALL_HOSTS) if platform == "qnap" else list(LOCAL_HOSTS)


def read_saved(bootstrap: Path) -> tuple[str, list[str]] | None:
    """The saved ``(mode, addresses)``; ``None`` if absent or unusable."""
    from mosaicwave.config import _read_config_dict

    data = _read_config_dict(bootstrap)
    raw = data.get("web_bind") if data else None
    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    addresses = raw.get("addresses")
    if not isinstance(addresses, list):
        addresses = []
    addresses = [a for a in addresses if isinstance(a, str)]
    try:
        hosts_for(str(mode), addresses)
    except ValueError:
        _log.warning("ignoring web_bind in config.json: %r", raw)
        return None
    return str(mode), addresses


def write_saved(bootstrap: Path, mode: str, addresses: list[str]) -> None:
    from mosaicwave.config import _read_config_dict, write_config_dict

    data = dict(_read_config_dict(bootstrap) or {})
    data["web_bind"] = {"mode": mode, "addresses": addresses if mode == "list" else []}
    write_config_dict(bootstrap, data)


def explicit_choice(explicit: str | None = None) -> str | None:
    raw = explicit if explicit is not None else os.environ.get("MOSAICWAVE_HOST")
    return raw.strip() if raw and raw.strip() else None


def resolve(bootstrap: Path, platform: str, explicit: str | None = None) -> Choice:
    """The addresses to bind and where the choice came from. Raises ``ValueError`` on a bad explicit one."""
    raw = explicit_choice(explicit)
    if raw is not None:
        return Choice(parse_hosts(raw), SOURCE_STARTUP)
    saved = read_saved(bootstrap)
    if saved is not None:
        return Choice(hosts_for(*saved), SOURCE_SAVED)
    return Choice(platform_default(platform), SOURCE_DEFAULT)


PORT_SEED_ENV = "MOSAICWAVE_PORT_DEFAULT"
PORT_FILE = "web.port"
DEFAULT_PORT = 8000
MIN_PORT = 1024
MAX_PORT = 65535


def valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and MIN_PORT <= value <= MAX_PORT


def _port_from_text(raw: str | None) -> int | None:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if 1 <= value <= MAX_PORT else None


def explicit_port(explicit: int | None = None) -> int | None:
    """The port given at startup (``--port`` or ``MOSAICWAVE_PORT``); ``None`` if none or unusable."""
    if explicit is not None:
        return explicit
    return _port_from_text(os.environ.get("MOSAICWAVE_PORT"))


def read_saved_port(bootstrap: Path) -> int | None:
    from mosaicwave.config import _read_config_dict

    data = _read_config_dict(bootstrap)
    value = data.get("web_port") if data else None
    return value if valid_port(value) else None


def write_saved_port(bootstrap: Path, port: int) -> None:
    from mosaicwave.config import _read_config_dict, write_config_dict

    data = dict(_read_config_dict(bootstrap) or {})
    data["web_port"] = port
    write_config_dict(bootstrap, data)


def seed_port() -> int:
    """What the launcher offers when no port is saved or given (``MOSAICWAVE_PORT_DEFAULT``), else 8000."""
    return _port_from_text(os.environ.get(PORT_SEED_ENV)) or DEFAULT_PORT


def resolve_port(bootstrap: Path, explicit: int | None = None) -> tuple[int, str]:
    """``(port, source)``: explicit startup port, saved port, launcher seed or default."""
    given = explicit_port(explicit)
    if given is not None:
        return given, SOURCE_STARTUP
    saved = read_saved_port(bootstrap)
    if saved is not None:
        return saved, SOURCE_SAVED
    return seed_port(), SOURCE_DEFAULT


def write_port_file(bootstrap: Path, port: int) -> None:
    """``web.port`` beside ``config.json``: the port the server last used, for the helper scripts."""
    try:
        bootstrap.mkdir(parents=True, exist_ok=True)
        (bootstrap / PORT_FILE).write_text(f"{port}\n", encoding="utf-8")
    except OSError:
        _log.warning("could not write %s", PORT_FILE, exc_info=True)


def validate_for_save(mode: str, addresses: list[str] | None) -> list[str]:
    """Hosts for a new saved setting. ``ValueError`` if invalid; ``OSError`` if one is not on this machine."""
    hosts = hosts_for(mode, addresses)
    for host in hosts:
        probe_bindable(host)
    return hosts


def _family(host: str) -> int:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def make_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(_family(host), socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does
        sock.bind((host, port))
        sock.set_inheritable(True)
    except OSError:
        sock.close()
        raise
    return sock


def probe_bindable(host: str) -> None:
    """Raise ``OSError`` unless ``host`` is an address of this machine (bind on a free port, then close)."""
    make_socket(host, 0).close()


def probe_port_free(host: str, port: int) -> None:
    """Raise ``OSError`` if ``host:port`` cannot be bound. No ``SO_REUSEADDR``: on Windows it would let a
    second listener bind a port that is already in use."""
    sock = socket.socket(_family(host), socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    finally:
        sock.close()


def bind_all(
    hosts: list[str],
    port: int,
    *,
    retry_seconds: float = RETRY_SECONDS,
    pause: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[socket.socket], dict[str, str]]:
    """Bind every host, retrying the failed ones until ``retry_seconds`` have passed.

    Returns the bound sockets (in ``hosts`` order) and ``{host: error}`` for those that never bound.
    """
    deadline = clock() + retry_seconds
    bound: dict[str, socket.socket] = {}
    errors: dict[str, str] = {}
    while True:
        for host in hosts:
            if host in bound:
                continue
            try:
                bound[host] = make_socket(host, port)
                errors.pop(host, None)
            except OSError as exc:
                errors[host] = exc.strerror or str(exc)
        if len(bound) == len(hosts) or clock() >= deadline:
            break
        sleep(pause)
    return [bound[h] for h in hosts if h in bound], errors


def failure_path(bootstrap: Path) -> Path:
    return bootstrap / FAILURE_FILE


def read_failure(bootstrap: Path) -> dict | None:
    try:
        data = json.loads(failure_path(bootstrap).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_failure(bootstrap: Path, choice: Choice, errors: dict[str, str], bound: list[str]) -> None:
    payload = {
        "source": choice.source,
        "wanted": choice.hosts,
        "failed": errors,
        "bound": bound,
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        bootstrap.mkdir(parents=True, exist_ok=True)
        failure_path(bootstrap).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        _log.warning("could not write %s", FAILURE_FILE, exc_info=True)


def bound_hosts() -> list[str]:
    """What ``serve`` bound (from the environment it sets); empty for a plain uvicorn start."""
    info = bound_info()
    hosts = info.get("hosts") if info else None
    return [h for h in hosts if isinstance(h, str)] if isinstance(hosts, list) else []


def bound_info() -> dict | None:
    raw = os.environ.get(BOUND_ENV)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _close(socks: list[socket.socket]) -> None:
    for sock in socks:
        sock.close()


class StartupBindError(Exception):
    """The server cannot start on the addresses it was told to use."""


def bind_for_start(
    bootstrap: Path,
    platform: str,
    port: int,
    explicit: str | None = None,
    **retry: object,
) -> tuple[list[socket.socket], Choice]:
    """Choose and bind. Falls back to loopback for a saved or default choice; never for an explicit one."""
    try:
        choice = resolve(bootstrap, platform, explicit)
    except ValueError as exc:
        raise StartupBindError(f"bad bind address from the startup choice: {exc}") from exc
    _log.info("bind %s (%s)", ", ".join(choice.hosts), _SOURCE_TEXT[choice.source])
    socks, errors = bind_all(choice.hosts, port, **retry)  # type: ignore[arg-type]
    if not errors:
        failure_path(bootstrap).unlink(missing_ok=True)
        return socks, choice
    detail = "; ".join(f"{h}:{port} ({e})" for h, e in errors.items())
    if choice.source == SOURCE_STARTUP:
        _close(socks)
        raise StartupBindError(f"cannot bind {detail}. The address was given at startup, so there is no fallback.")
    _log.error("cannot bind %s", detail)
    if not socks:
        fallback = [h for h in LOCAL_HOSTS if h not in errors]
        if not fallback:
            # Loopback itself was in the list and failed: the port is taken.
            raise StartupBindError(f"cannot bind {detail}")
        socks, fb_errors = bind_all(fallback, port, **retry)  # type: ignore[arg-type]
        if fb_errors:
            raise StartupBindError(f"cannot bind {detail}")
        _log.error("falling back to %s", ", ".join(fallback))
    _write_failure(bootstrap, choice, errors, [s.getsockname()[0] for s in socks])
    return socks, choice


_SOURCE_TEXT = {
    SOURCE_STARTUP: "startup choice",
    SOURCE_SAVED: "saved setting",
    SOURCE_DEFAULT: "platform default",
}


def record_bound(
    socks: list[socket.socket], choice: Choice, port: int | None = None, port_source: str | None = None
) -> None:
    os.environ[BOUND_ENV] = json.dumps(
        {
            "hosts": [s.getsockname()[0] for s in socks],
            "wanted": choice.hosts,
            "source": choice.source,
            "port": port if port is not None else (socks[0].getsockname()[1] if socks else None),
            "port_source": port_source or SOURCE_DEFAULT,
        }
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m mosaicwave.serve", description="Run the mosaicWave API.")
    parser.add_argument("--host", help="explicit bind address or comma-separated list (beats the saved setting)")
    parser.add_argument("--port", type=int, help="explicit port (beats the saved port; default: see qpkg.md)")
    parser.add_argument("--reload", action="store_true", help="debug: restart on code changes")
    parser.add_argument("--print-port", action="store_true", help="print the port that would be used and exit")
    parser.add_argument("--print-hosts", action="store_true", help="print the addresses that would be used, comma separated, and exit")
    parser.add_argument("--describe", action="store_true", help="print the address and port that would be used, and where they come from, then exit")
    parser.add_argument("--set-port", type=int, help="save this port in config.json (as the admin Server page does) and exit")
    args = parser.parse_args(argv)

    from mosaicwave.config import _normalize_platform, _platform_bootstrap_dir

    boot = _platform_bootstrap_dir()
    if args.set_port is not None:
        if not valid_port(args.set_port):
            print(f"mosaicWave: the port must be {MIN_PORT} to {MAX_PORT}", file=sys.stderr)
            return 2
        write_saved_port(boot, args.set_port)
        print(args.set_port)
        return 0
    port, port_source = resolve_port(boot, args.port)
    if args.print_port:
        print(port)
        return 0
    platform = _normalize_platform(os.environ.get("MOSAICWAVE_PLATFORM"))
    if args.print_hosts:
        try:
            print(",".join(resolve(boot, platform, args.host).hosts))
        except ValueError as exc:
            print(f"mosaicWave: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.describe:
        try:
            choice = resolve(boot, platform, args.host)
        except ValueError as exc:
            print(f"mosaicWave: {exc}", file=sys.stderr)
            return 2
        print(f"{', '.join(choice.hosts)} ({_SOURCE_TEXT[choice.source]}), port {port} ({_SOURCE_TEXT[port_source]})")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
    _log.info("port %d (%s)", port, _SOURCE_TEXT[port_source])
    try:
        socks, choice = bind_for_start(boot, platform, port, args.host)
    except StartupBindError as exc:
        print(f"mosaicWave: {exc}", file=sys.stderr)
        return 1
    record_bound(socks, choice, port, port_source)
    write_port_file(boot, port)
    import uvicorn

    if args.reload:
        # Same as uvicorn.run(reload=True), but with every socket we bound (uvicorn would bind just one).
        from uvicorn.supervisors import ChangeReload

        config = uvicorn.Config("mosaicwave.main:app", host=socks[0].getsockname()[0], port=port, reload=True)
        server = uvicorn.Server(config)
        ChangeReload(config, target=server.run, sockets=socks).run()
        return 0
    config = uvicorn.Config("mosaicwave.main:app", host=socks[0].getsockname()[0], port=port)
    uvicorn.Server(config).run(sockets=socks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
