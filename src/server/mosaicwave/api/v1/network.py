from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from mosaicwave import bind, maintenance
from mosaicwave.api.errors import ApiError, wrapped
from mosaicwave.auth.deps import require_admin
from mosaicwave.config import _normalize_platform, schedule_api_restart
from mosaicwave.models import User

router = APIRouter()
_log = logging.getLogger(__name__)

_SOURCES = ("startup", "saved", "default")


class BindFailureOut(BaseModel):
    wanted: list[str]
    failed: dict[str, str]
    bound: list[str]
    time: str | None = None


class NetworkOut(BaseModel):
    mode: Literal["local", "all", "list"]
    addresses: list[str]
    effective: list[str]
    source: Literal["startup", "saved", "default"]
    startup_locked: bool
    port: int
    effective_port: int
    port_source: Literal["startup", "saved", "default"]
    port_startup_locked: bool
    failure: BindFailureOut | None = None
    restarting: bool = False


class NetworkIn(BaseModel):
    """Send only what changes: ``mode`` (with ``addresses`` for ``list``) and/or ``port``."""

    mode: Literal["local", "all", "list"] | None = None
    addresses: list[str] = []
    port: int | None = None


def _boot(request: Request) -> Path:
    settings = request.app.state.settings
    return Path(settings.bootstrap_dir or settings.data_dir).expanduser().resolve()


def _address_locked() -> bool:
    return bind.explicit_choice() is not None or (bind.bound_info() or {}).get("source") == "startup"


def _port_locked() -> bool:
    return bind.explicit_port() is not None or (bind.bound_info() or {}).get("port_source") == "startup"


def _out(request: Request, *, restarting: bool = False) -> NetworkOut:
    boot = _boot(request)
    platform = _normalize_platform(request.app.state.settings.platform)
    saved = bind.read_saved(boot)
    if saved is None:
        mode = "all" if bind.platform_default(platform) == bind.ALL_HOSTS else "local"
        saved = (mode, [])
    info = bind.bound_info()
    if info and isinstance(info.get("hosts"), list):
        effective = [h for h in info["hosts"] if isinstance(h, str)]
        source = info.get("source") if info.get("source") in _SOURCES else "saved"
    else:
        try:
            choice = bind.resolve(boot, platform)
            effective, source = choice.hosts, choice.source
        except ValueError:
            effective, source = bind.platform_default(platform), "default"
    if info and isinstance(info.get("port"), int):
        effective_port = info["port"]
        port_source = info.get("port_source") if info.get("port_source") in _SOURCES else "saved"
    else:
        effective_port, port_source = bind.resolve_port(boot)
    failure = bind.read_failure(boot)
    return NetworkOut(
        mode=saved[0],  # type: ignore[arg-type]
        addresses=[a for a in saved[1] if a not in bind.LOCAL_HOSTS],
        effective=effective,
        source=source,  # type: ignore[arg-type]
        startup_locked=_address_locked(),
        port=bind.read_saved_port(boot) or bind.seed_port(),
        effective_port=effective_port,
        port_source=port_source,  # type: ignore[arg-type]
        port_startup_locked=_port_locked(),
        failure=(
            BindFailureOut(
                wanted=[str(h) for h in failure.get("wanted", [])],
                failed={str(k): str(v) for k, v in (failure.get("failed") or {}).items()},
                bound=[str(h) for h in failure.get("bound", [])],
                time=failure.get("time") if isinstance(failure.get("time"), str) else None,
            )
            if failure
            else None
        ),
        restarting=restarting,
    )


@router.get("/settings/network", response_model=NetworkOut)
def get_network(request: Request, _admin: User = Depends(require_admin)) -> NetworkOut:
    return _out(request)


@router.put("/settings/network", response_model=NetworkOut)
def put_network(
    request: Request,
    body: NetworkIn,
    _admin: User = Depends(require_admin),
) -> NetworkOut:
    """Save the bind address and/or port and restart. Needs maintenance mode; see ``doc/qpkg.md``."""
    if body.mode is None and body.port is None:
        raise ApiError(400, "bind_invalid", "nothing to change", {"message": "nothing to change"})
    if body.mode is not None and _address_locked():
        raise ApiError(
            409,
            "bind_startup_locked",
            "the bind address was given at startup (MOSAICWAVE_HOST / --host); remove it to use this setting",
        )
    if body.port is not None and _port_locked():
        raise ApiError(
            409,
            "port_startup_locked",
            "the port was given at startup (MOSAICWAVE_PORT / --port); remove it to use this setting",
        )
    if maintenance.read(request.app.state.settings) is None:
        raise ApiError(409, "maintenance_required", "Turn maintenance mode on first")
    boot = _boot(request)
    current = _out(request)
    hosts = current.effective
    if body.mode is not None:
        try:
            hosts = bind.hosts_for(body.mode, body.addresses)
        except ValueError as exc:
            raise wrapped(400, "bind_invalid", exc) from exc
        for host in hosts:
            try:
                bind.probe_bindable(host)
            except OSError as exc:
                raise ApiError(
                    400,
                    "bind_not_local",
                    f"{host} is not an address of this machine ({exc.strerror or exc})",
                    {"address": host},
                ) from exc
    if body.port is not None:
        if not bind.valid_port(body.port):
            raise ApiError(
                400,
                "port_invalid",
                f"the port must be {bind.MIN_PORT} to {bind.MAX_PORT}",
                {"min": bind.MIN_PORT, "max": bind.MAX_PORT},
            )
        if body.port != current.effective_port:  # our own listener holds the current one
            for host in hosts:
                try:
                    bind.probe_port_free(host, body.port)
                except OSError as exc:
                    raise ApiError(
                        400,
                        "port_in_use",
                        f"port {body.port} is not free on {host} ({exc.strerror or exc})",
                        {"port": body.port, "address": host},
                    ) from exc
    if body.mode is not None:
        bind.write_saved(boot, body.mode, bind.extra_addresses(body.addresses) if body.mode == "list" else [])
        _log.info("bind address saved: %s %s", body.mode, hosts)
    if body.port is not None:
        bind.write_saved_port(boot, body.port)
        _log.info("port saved: %d", body.port)
    restarting = schedule_api_restart(boot)
    return _out(request, restarting=restarting)
