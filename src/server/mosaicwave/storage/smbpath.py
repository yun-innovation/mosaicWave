"""Map smb:// (and UNC) text to a host folder. Does not add an SMB FileStore backend."""

from __future__ import annotations

import errno
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


@dataclass(frozen=True)
class RemoteShare:
    host: str
    share: str
    rest: str = ""
    user: str = ""
    password: str = field(default="", repr=False)


def parse_remote_share(raw: str) -> RemoteShare | None:
    text = (raw or "").strip()
    if not text:
        return None
    if "://" in text:
        parsed = urlparse(text)
        if parsed.scheme.lower() not in ("smb", "cifs"):
            return None
        user = unquote(parsed.username or "").strip()
        password = unquote(parsed.password or "")
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        if netloc.startswith("["):
            host, _, _rest = netloc[1:].partition("]")
        else:
            host, _, port = netloc.partition(":")
            if port and not port.isdigit():
                host = netloc
        host = unquote(host).strip()
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        if not host or not parts:
            return None
        return RemoteShare(
            host=host, share=parts[0], rest="/".join(parts[1:]), user=user, password=password
        )
    slashes = text.replace("\\", "/")
    if not slashes.startswith("//"):
        if _looks_like_host_share(text):
            return parse_remote_share("smb://" + slashes.lstrip("/"))
        return None
    parts = [unquote(p) for p in slashes[2:].split("/") if p]
    if len(parts) < 2:
        return None
    if ":" in parts[0] and len(parts[0]) == 2:
        return None
    host = parts[0]
    user = ""
    password = ""
    if "@" in host:
        cred, _, host = host.rpartition("@")
        if ":" in cred:
            user, _, password = cred.partition(":")
        else:
            user = cred
    return RemoteShare(
        host=host, share=parts[1], rest="/".join(parts[2:]), user=user, password=password
    )


def _looks_like_host_share(text: str) -> bool:
    """True for Settings SMB body `fileserver/Public/folder` (scheme shown as a prefix)."""
    raw = (text or "").strip()
    if not raw or raw[0] in "/\\" or "://" in raw:
        return False
    if len(raw) >= 2 and raw[1] == ":":
        return False
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return False
    return parts[0] not in (".", "..")


def _join_rest(root: str, rest: str, *, windows: bool) -> str:
    rest = rest.replace("\\", "/").strip("/")
    if not rest:
        return root
    if windows:
        return root.rstrip("\\") + "\\" + rest.replace("/", "\\")
    return root.rstrip("/") + "/" + rest


def _strip_bonjour_host(host: str) -> str:
    h = host.strip()
    lower = h.lower()
    for suffix in ("._smb._tcp.local", "._afpovertcp._tcp.local"):
        if lower.endswith(suffix):
            return h[: -len(suffix)]
    return h


def _host_keys(host: str) -> set[str]:
    h = _strip_bonjour_host(host).strip().lower()
    if not h:
        return set()
    keys = {h}
    if h.endswith(".local"):
        keys.add(h[: -len(".local")])
    else:
        keys.add(h + ".local")
    return keys


def _hosts_equal(left: str, right: str) -> bool:
    if _host_keys(left) & _host_keys(right):
        return True
    try:
        left_ips = {info[4][0] for info in socket.getaddrinfo(left, None)}
        right_ips = {info[4][0] for info in socket.getaddrinfo(right, None)}
    except (OSError, socket.gaierror):
        return False
    return bool(left_ips & right_ips)


def _smb_url(share: RemoteShare) -> str:
    """Host URL only. Username belongs in nsmb.conf / Settings, not user@host."""
    path = f"smb://{share.host}/{share.share}"
    if share.rest:
        return f"{path}/{share.rest}"
    return path


def _darwin_volume_prefix() -> str:
    """Packaged: mosaicWave. Debug: mosaicWave-dev. Same split as the data folder."""
    profile = (os.environ.get("MOSAICWAVE_PROFILE") or "").strip().lower()
    if profile in ("prod", "production", "msi"):
        return "mosaicWave"
    if profile in ("dev", "debug", "development"):
        return "mosaicWave-dev"
    try:
        from mosaicwave.config import _installed_macos_payload

        if _installed_macos_payload():
            return "mosaicWave"
    except Exception:
        pass
    return "mosaicWave-dev"


def _darwin_mount_dir_name(share: RemoteShare) -> str:
    raw = f"{_darwin_volume_prefix()}-{share.host}-{share.share}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)[:80]


def locate_share_root_text(share: RemoteShare) -> str | None:
    if os.name == "nt":
        return f"\\\\{share.host}\\{share.share}"
    if sys.platform == "darwin":
        for m_host, m_share, point in _darwin_mounts():
            if m_share.lower() != share.share.lower():
                continue
            if _hosts_equal(m_host, share.host):
                return point
        return None
    return f"//{share.host}/{share.share}"


def _darwin_unique_dest(share: RemoteShare) -> str:
    return f"/Volumes/{_darwin_mount_dir_name(share)}"


def _darwin_unique_dest_candidates(share: RemoteShare, *, limit: int = 5) -> list[str]:
    """Primary unique dest, then numbered fallbacks if the primary is mounted by
    something else and unreadable here (never unmount an existing mount to free it)."""
    base = _darwin_mount_dir_name(share)
    out = [f"/Volumes/{base}"]
    for n in range(2, max(limit, 1) + 1):
        out.append(f"/Volumes/{base}-{n}")
    return out


def locate_accessible_share_root(share: RemoteShare, *, mount_point: str = "") -> str | None:
    """Folder this process can list. On macOS that is the requested dest or the unique volume."""
    if sys.platform == "darwin":
        want = os.path.normpath(mount_point) if (mount_point or "").strip() else ""
        if want and (_is_smbfs_mount(want) or os.path.ismount(want)) and _service_can_access(want):
            return want
        dest = _darwin_unique_dest(share)
        if (_is_smbfs_mount(dest) or os.path.ismount(dest)) and _service_can_access(dest):
            if not want or os.path.normpath(dest) == want:
                return dest
        for m_host, m_share, point in _darwin_mounts():
            if want:
                if os.path.normpath(point) != want:
                    continue
            elif not _is_app_volume_name(point):
                continue
            if m_share.lower() != share.share.lower():
                continue
            if _hosts_equal(m_host, share.host) and _service_can_access(point):
                return point
        return None
    root = locate_share_root_text(share)
    if root is None:
        return None
    joined = _join_rest(root, share.rest, windows=False)
    if _service_can_access(root) and (not share.rest or _service_can_access(joined)):
        return root
    return None


def native_share_path_text(share: RemoteShare) -> str:
    windows = os.name == "nt"
    root = locate_share_root_text(share)
    if root is None:
        if sys.platform == "darwin":
            return _smb_url(share)
        if windows:
            root = f"\\\\{share.host}\\{share.share}"
        else:
            root = f"//{share.host}/{share.share}"
    return _join_rest(root, share.rest, windows=windows)


def native_share_path(share: RemoteShare) -> Path:
    return Path(native_share_path_text(share))


def _parse_mount_stdout(stdout: str) -> list[tuple[str, str, str]]:
    """(host, share, mountpoint) from `mount` smbfs/cifs lines."""
    found: list[tuple[str, str, str]] = []
    for line in stdout.splitlines():
        if " on " not in line:
            continue
        if "smbfs" not in line.lower() and "cifs" not in line.lower() and "//" not in line:
            continue
        device, _, rest = line.partition(" on ")
        mountpoint, _, _opts = rest.partition(" (")
        device = device.strip().replace("\\", "/")
        mountpoint = mountpoint.strip()
        if not device.startswith("//") or not mountpoint.startswith("/"):
            continue
        body = device[2:]
        if "@" in body:
            body = body.split("@", 1)[1]
        parts = [p for p in body.split("/") if p]
        if len(parts) < 2:
            continue
        found.append((_strip_bonjour_host(parts[0]), parts[1], mountpoint))
    return found


def _run_mount_stdout() -> str:
    try:
        proc = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _darwin_mounts() -> list[tuple[str, str, str]]:
    """smbfs mounts visible to this process. LaunchDaemon has no login session."""
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for host, share, point in _parse_mount_stdout(_run_mount_stdout()):
        key = (host.lower(), share.lower(), os.path.normpath(point))
        if key in seen:
            continue
        seen.add(key)
        found.append((host, share, point))
    return found


def _is_smbfs_mount(path: str) -> bool:
    want = os.path.normpath(path)
    return any(os.path.normpath(point) == want for _h, _s, point in _darwin_mounts())


def _service_can_access(path: str) -> bool:
    """True when this process (LaunchDaemon, no login session) can list the folder."""
    try:
        os.listdir(path)
        return True
    except OSError:
        return False


def _is_mosaicwave_volume_name(name: str) -> bool:
    """mosaicWave / mosaicWave-dev (bare prefix), or any mosaicWave-* / mosaicWave-dev-* suffix.

    Case-insensitive: /Volumes is a case-insensitive filesystem, and macOS can hand
    back a mount under a different case than what we requested.
    """
    low = name.lower()
    return low in ("mosaicwave", "mosaicwave-dev") or low.startswith("mosaicwave-")


def _is_app_volume_name(path: str) -> bool:
    return _is_mosaicwave_volume_name(os.path.basename(os.path.normpath(path)))


def _app_volume_root(path: str) -> str:
    """Unique mosaicWave[-dev][-*] dest that contains path, or empty.

    Only the top-level /Volumes/{name} segment may be a bare "mosaicWave" /
    "mosaicWave-dev" — a nested folder happening to be named that (e.g. the
    library folder "mosaicWave" under a mount point) is not itself a volume.
    """
    posix = os.path.normpath(os.fspath(path).replace("\\", "/"))
    parts = [p for p in posix.split("/") if p]
    if len(parts) >= 2 and parts[0] == "Volumes" and _is_mosaicwave_volume_name(parts[1]):
        return "/Volumes/" + parts[1]
    cur = posix
    while cur and cur != os.path.dirname(cur):
        if os.path.basename(cur).lower().startswith("mosaicwave-"):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def _finder_share_point(share: RemoteShare) -> str:
    """Finder / user mount of this share, if any. Not usable by the LaunchDaemon."""
    if sys.platform != "darwin":
        return ""
    for m_host, m_share, point in _darwin_mounts():
        if _is_app_volume_name(point):
            continue
        if m_share.lower() == share.share.lower() and _hosts_equal(m_host, share.host):
            return point
    return ""


_MOUNT_DIR_JUNK = frozenset({".ds_store", ".localized", "thumbs.db", "desktop.ini"})
_MOSAICWAVE_LEFTOVER_NAMES = frozenset(
    {
        "mosaicwave",
        "mosaicwave-dev",
        "libraries",
        "blobs",
        "tmp",
        "config.json",
        "library.db",
        "session.key",
        "nsmb.conf",
        "smb.auth",
    }
)
_last_stashed_volume: str | None = None
_last_mount_error: str = ""
_last_mount_dest: str = ""
_last_mount_notice: str = ""


def _host_aliases(host: str) -> list[str]:
    h = host.strip()
    if not h:
        return []
    base = _strip_bonjour_host(h)
    short = base[: -len(".local")] if base.lower().endswith(".local") else base
    names = [h, short, f"{short}.local", f"{short}._smb._tcp.local"]
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _lookup_ips(host: str) -> list[str]:
    """IPs so a second smbfs session can exist beside Finder's hostname mount."""
    found: list[str] = []
    seen: set[str] = set()
    try:
        infos = socket.getaddrinfo(host, 445)
    except (OSError, socket.gaierror):
        return found
    for info in infos:
        ip = info[4][0]
        key = f"[{ip}]" if ":" in ip else ip
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        found.append(key)
    return found


def _mount_hosts(share: RemoteShare) -> list[str]:
    names = list(_host_aliases(share.host))
    seen = {n.lower() for n in names}
    for name in list(names):
        for ip in _lookup_ips(name):
            if ip.lower() in seen:
                continue
            seen.add(ip.lower())
            names.append(ip)
    return names


def _smb_escape(part: str) -> str:
    """Percent-encode a mount_smbfs user, password, or share (`@` → `%40`, `:` → `%3A`)."""
    return quote(part, safe="")


def _spec_auth_prefix(share: RemoteShare) -> str:
    if not share.user or not share.password:
        return ""
    return f"{_smb_escape(share.user)}:{_smb_escape(share.password)}@"


def _share_spec(host: str, share_name: str, *, auth: str = "") -> str:
    return f"//{auth}{host}/{_smb_escape(share_name)}"


def _simplified_specs(share: RemoteShare) -> list[str]:
    """//HOST/share — nsmb.conf [HOST] username=, no user@ in the spec."""
    return [_share_spec(host, share.share) for host in _host_aliases(share.host)]


def _credential_specs(share: RemoteShare) -> list[str]:
    """//user:pass@HOST/share with percent-encoded user and password."""
    auth = _spec_auth_prefix(share)
    if not auth:
        return []
    return [_share_spec(host, share.share, auth=auth) for host in _host_aliases(share.host)]


def _mount_specs(share: RemoteShare) -> list[str]:
    """Guest / unnamed login. Named user mounts go through _run_mount_smbfs stages."""
    specs: list[str] = []
    if share.user:
        for spec in _simplified_specs(share) + _credential_specs(share):
            if spec not in specs:
                specs.append(spec)
        return specs
    for host in _mount_hosts(share):
        specs.extend(
            (
                _share_spec(host, share.share),
                _share_spec(host, share.share, auth="guest@"),
                _share_spec(host, share.share, auth="GUEST:@"),
            )
        )
    return specs


def _redact_spec(spec: str) -> str:
    if "@" not in spec or not spec.startswith("//"):
        return spec
    cred, sep, rest = spec[2:].rpartition("@")
    if not sep or ":" not in cred:
        return spec
    user, _, _pw = cred.partition(":")
    return f"//{user}:***@{rest}"


def _redact_text(text: str, *, password: str = "", spec: str = "") -> str:
    out = text
    if spec:
        out = out.replace(spec, _redact_spec(spec))
    if password:
        out = out.replace(password, "***")
        encoded = _smb_escape(password)
        if encoded != password:
            out = out.replace(encoded, "***")
    # Catch //user:pass@ leftover when the caller forgot spec= (percent-encoded passwords too).
    out = re.sub(r"(//[^/@:\s]+):[^/@\s]+@", r"\1:***@", out)
    return out


def _dest_is_mount(dest: str) -> bool:
    return os.path.ismount(dest) or _is_smbfs_mount(dest)


def _wait_dest_ready(dest: str, *, tries: int = 100, delay: float = 0.3) -> bool:
    """mount_smbfs exit 0 can land before the dest is actually usable.

    Usually this is just os.path.ismount / listdir beating the mount by a
    moment, but a slow NAS can take many seconds to finish the handshake —
    poll for ~30s before giving up rather than a couple of seconds.
    """
    for i in range(max(tries, 1)):
        if _dest_is_mount(dest) and _service_can_access(dest):
            return True
        try:
            os.chmod(dest, 0o777)
        except OSError:
            pass
        if i + 1 < tries:
            time.sleep(delay)
    return _dest_is_mount(dest) and _service_can_access(dest)


def _looks_like_auth_error(text: str) -> bool:
    lower = (text or "").lower()
    return "authentication" in lower or "logon unsuccessful" in lower or "status_logon" in lower


def _may_sudo_mkdir_volumes(dest: str) -> bool:
    """Only /Volumes/mosaicWave… (debug save folder). Never Finder Public."""
    posix = os.path.normpath(dest)
    if not posix.startswith("/Volumes/"):
        return False
    parts = [p for p in posix.split("/") if p]
    if len(parts) != 2:
        return False
    return parts[1].startswith("mosaicWave")


def _volumes_mkdir_hint(dest: str, cause: BaseException) -> str:
    posix = os.path.normpath(dest)
    return (
        f"could not create {dest}: {cause}. /Volumes is root-only. "
        "The packaged LaunchDaemon can mkdir there. Debug cannot. "
        "Browse an empty folder you can write, or in Terminal: "
        f"sudo mkdir -p '{posix}' && sudo chmod 0777 '{posix}' — then Retry Save folder."
    )


def _sudo_mkdir_mosaicwave_volume(dest: str, *, cause: BaseException) -> str:
    hint = _volumes_mkdir_hint(dest, cause)
    posix = os.path.normpath(dest)
    if sys.platform != "darwin" or not _may_sudo_mkdir_volumes(posix):
        return hint
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return hint
    for cmd in (
        ["sudo", "-n", "/bin/mkdir", "-p", posix],
        ["sudo", "-n", "/bin/chmod", "0777", posix],
    ):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=8, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"{hint} ({exc})"
        if proc.returncode != 0:
            return hint
    if os.path.isdir(posix):
        return ""
    return hint


def _is_mount_junk(name: str) -> bool:
    return name.startswith("._") or name.lower() in _MOUNT_DIR_JUNK


def _is_mosaicwave_leftover_name(name: str) -> bool:
    low = name.lower()
    if low in _MOSAICWAVE_LEFTOVER_NAMES:
        return True
    if low.startswith("library.db"):
        return True
    if low.startswith("leftover-volumes-"):
        return True
    return False


def _has_mosaicwave_items(path: str) -> bool:
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(_is_mosaicwave_leftover_name(name) for name in names)


def _unlink_mount_junk(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        pass
    try:
        os.chmod(path, 0o666)
        os.unlink(path)
        return True
    except OSError:
        return False


def _mount_dest_ready(dest: str) -> bool:
    """Empty (or junk-only) dir, or already a mount. Do not rmdir /Volumes dests."""
    if _dest_is_mount(dest) or _is_smbfs_mount(dest):
        return True
    if not os.path.isdir(dest):
        return False
    try:
        names = os.listdir(dest)
    except OSError:
        return False
    for name in names:
        if _is_mount_junk(name):
            if not _unlink_mount_junk(os.path.join(dest, name)):
                return False
            continue
        return False
    return True


def _leftover_dest_error(dest: str) -> str:
    if os.path.isdir(dest) and _has_mosaicwave_items(dest):
        return (
            f"could not use mount point {dest} "
            "(already has mosaicWave files). Use an empty folder."
        )
    return (
        f"could not use mount point {dest} "
        "(not empty, and could not move leftovers). Use an empty folder."
    )


def _prepare_mount_dest(dest: str) -> str:
    """Ensure dest exists and is empty. umount of a /Volumes smbfs share deletes the directory."""
    if _dest_is_mount(dest):
        return ""
    if os.path.exists(dest) and not os.path.isdir(dest):
        return f"could not use mount point {dest}"
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    if root and dest.startswith("/Volumes/mosaicWave"):
        if os.path.isdir(dest) and not clear_stale_mount_dir(dest):
            if stash_local_dir(dest) is None:
                return _leftover_dest_error(dest)
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as exc:
            return f"could not create {dest}: {exc}"
        return ""
    if _mount_dest_ready(dest):
        return ""
    if os.path.isdir(dest) and not clear_stale_mount_dir(dest):
        if stash_local_dir(dest) is None:
            return _leftover_dest_error(dest)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM):
            return _sudo_mkdir_mosaicwave_volume(dest, cause=exc)
        return f"could not create {dest}: {exc}"
    return ""


def _mount_cmd_error(proc: subprocess.CompletedProcess[str], spec: str, share: RemoteShare) -> str:
    raw = (proc.stderr or proc.stdout or "").strip()
    if not raw:
        raw = f"mount_smbfs -N -s {spec} exit {proc.returncode}"
    return _redact_text(raw, password=share.password, spec=spec)


def _nsmb_conf_files() -> list[Path]:
    files: list[Path] = []
    if sys.platform == "darwin":
        euid = os.geteuid() if hasattr(os, "geteuid") else 1
        if euid == 0:
            files.append(Path("/etc/nsmb.conf"))
            files.append(Path("/var/root/Library/Preferences/nsmb.conf"))
        else:
            try:
                files.append(Path.home() / "Library/Preferences/nsmb.conf")
            except (RuntimeError, OSError):
                pass
    try:
        from mosaicwave.config import _platform_bootstrap_dir

        files.append(_platform_bootstrap_dir() / "nsmb.conf")
    except Exception:
        files.append(Path("/Library/Application Support/mosaicWave/nsmb.conf"))
    return files


def _parse_nsmb(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if not current or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        sections[current][key.strip()] = val.strip()
    return sections


def _dump_nsmb(sections: dict[str, dict[str, str]]) -> str:
    lines = [
        "# mosaicWave: username only. Password is in the System keychain for mount_smbfs.",
        "",
    ]
    for name, kv in sections.items():
        lines.append(f"[{name}]")
        for key, val in kv.items():
            lines.append(f"{key}={val}")
        lines.append("")
    return "\n".join(lines)


def _nsmb_section_names(share: RemoteShare) -> list[str]:
    return [_strip_bonjour_host(share.host)]


def _nsmb_values(share: RemoteShare) -> dict[str, str]:
    if not share.user:
        return {}
    return {"username": share.user}


def _merge_nsmb_share(text: str, share: RemoteShare, *, replace_username: bool = False) -> str:
    sections = _parse_nsmb(text)
    values = _nsmb_values(share)
    if not values:
        return text
    for name in _nsmb_section_names(share):
        slot = sections.setdefault(name, {})
        for key, val in values.items():
            if replace_username and key == "username":
                slot.pop("user", None)
                slot[key] = val
                continue
            if not slot.get(key) and not (key == "username" and slot.get("user")):
                slot[key] = val
    return _dump_nsmb(sections)


def _write_nsmb_file(path: Path, share: RemoteShare, *, replace_username: bool = False) -> None:
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        existing = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _merge_nsmb_share(existing, share, replace_username=replace_username), encoding="utf-8"
    )
    if path.as_posix() == "/etc/nsmb.conf":
        return
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def _write_nsmb_conf(share: RemoteShare, *, replace_username: bool = False) -> None:
    if not share.user:
        return
    for path in _nsmb_conf_files():
        try:
            _write_nsmb_file(path, share, replace_username=replace_username)
        except OSError:
            continue


def _nsmb_username(kv: dict[str, str]) -> str:
    return (kv.get("username") or kv.get("user") or "").strip()


def nsmb_username_for_share(share: RemoteShare) -> str:
    """username= under [HOST] in nsmb.conf. Do not look up [user@HOST]."""
    names = {n.lower() for n in _nsmb_section_names(share)}
    for path in _nsmb_conf_files():
        try:
            sections = _parse_nsmb(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for name, kv in sections.items():
            if name.lower() not in names:
                continue
            user = _nsmb_username(kv)
            if user:
                return user
    return ""


def nsmb_profile_exists(share: RemoteShare) -> bool:
    if not share.user:
        return False
    found = nsmb_username_for_share(share)
    return bool(found) and found.lower() == share.user.lower()


def _with_nsmb_user(share: RemoteShare) -> RemoteShare:
    if share.user:
        return share
    user = nsmb_username_for_share(share)
    if not user:
        return share
    return RemoteShare(share.host, share.share, share.rest, user, share.password)


_SYSTEM_KEYCHAIN = "/Library/Keychains/System.keychain"
_SMB_PROTOCOL = "smb "


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _keychain_path_for_process() -> str:
    """LaunchDaemon/root: System keychain only. A login-keychain search prompts at the console."""
    if _running_as_root():
        return _SYSTEM_KEYCHAIN
    return ""


def _keychain_add_smb(share: RemoteShare, keychain: str = _SYSTEM_KEYCHAIN) -> bool:
    if sys.platform != "darwin" or not share.user or not share.password:
        return False
    cmd = [
        "/usr/bin/security",
        "add-internet-password",
        "-a",
        share.user,
        "-s",
        _strip_bonjour_host(share.host),
        "-w",
        share.password,
        "-D",
        "network password",
        "-r",
        _SMB_PROTOCOL,
        "-T",
        "/sbin/mount_smbfs",
        "-T",
        "/usr/sbin/mount_smbfs",
        "-T",
        "/usr/bin/security",
        "-A",
        "-U",
    ]
    if keychain:
        cmd.append(keychain)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def ensure_system_keychain(share: RemoteShare) -> bool:
    """Create or update the SMB password (System keychain as root; login keychain if not root)."""
    if not share.user or not share.password:
        return False
    return _keychain_add_smb(share, _keychain_path_for_process())


def ensure_nsmb_profile(share: RemoteShare) -> bool:
    """Add [HOST] username= to nsmb.conf if that setting is not already there."""
    if nsmb_profile_exists(share):
        return False
    if not share.user:
        return False
    _write_nsmb_conf(share)
    return True


def save_smb_login(share: RemoteShare) -> None:
    """After a successful //user:pass@ mount: [HOST] username= and System keychain."""
    if not share.user:
        return
    _write_nsmb_conf(share, replace_username=True)
    if share.password:
        ensure_system_keychain(share)
        server = _strip_bonjour_host(share.host)
        _keychain_pw_cache[(share.user.lower(), server.lower())] = share.password


def prepare_smb_url(raw: str, *, user: str = "", password: str = "") -> str:
    """Return smb://host/share with no user@. Credentials are saved only after a live mount."""
    del password
    share = parse_remote_share(raw)
    if share is None:
        return raw
    bound = RemoteShare(
        share.host,
        share.share,
        share.rest,
        (user or "").strip() or share.user,
        "",
    )
    return _smb_url(bound)


def _password_from_nsmb(share: RemoteShare) -> str:
    if not share.user:
        return ""
    names = {n.lower() for n in _nsmb_section_names(share)}
    for path in _nsmb_conf_files():
        try:
            sections = _parse_nsmb(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for name, kv in sections.items():
            if name.lower() not in names:
                continue
            user = kv.get("user", "")
            password = kv.get("password", "")
            if password and (not user or user.lower() == share.user.lower()):
                return password
    return ""


def _legacy_smb_auth_paths() -> list[Path]:
    paths = [
        Path("/Library/Application Support/mosaicWave/smb.auth"),
        Path("/Library/Application Support/mosaicWave-dev/smb.auth"),
        Path(os.path.expanduser("~/Library/Application Support/mosaicWave-dev/smb.auth")),
        Path(os.path.expanduser("~/Library/Application Support/mosaicWave/smb.auth")),
    ]
    try:
        from mosaicwave.config import _platform_bootstrap_dir

        boot = _platform_bootstrap_dir() / "smb.auth"
        if boot not in paths:
            paths.insert(0, boot)
    except Exception:
        pass
    return paths


def _read_legacy_smb_auth(share: RemoteShare) -> str:
    if not share.user:
        return ""
    for path in _legacy_smb_auth_paths():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        host = user = password = ""
        for line in raw.splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().lower()
            val = val.strip()
            if key == "host":
                host = val
            elif key == "user":
                user = val
            elif key == "password":
                password = val
        if user.lower() != share.user.lower() or not password:
            continue
        if host and not _hosts_equal(host, share.host):
            continue
        return password
    return ""


_keychain_pw_cache: dict[tuple[str, str], str] = {}


def _darwin_keychain_password(share: RemoteShare) -> str:
    """Read the process keychain once. Same -s as add (stripped host). Cached for remount retries."""
    if sys.platform != "darwin" or not share.user:
        return ""
    server = _strip_bonjour_host(share.host)
    cache_key = (share.user.lower(), server.lower())
    if cache_key in _keychain_pw_cache:
        return _keychain_pw_cache[cache_key]
    cmd = [
        "/usr/bin/security",
        "find-internet-password",
        "-a",
        share.user,
        "-s",
        server,
        "-r",
        _SMB_PROTOCOL,
        "-w",
    ]
    keychain = _keychain_path_for_process()
    if keychain:
        cmd.append(keychain)
    pw = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
        if proc.returncode == 0:
            pw = (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pw = ""
    _keychain_pw_cache[cache_key] = pw
    return pw


def _with_password(share: RemoteShare) -> RemoteShare:
    if share.password:
        return share
    pw = _password_from_nsmb(share) or _read_legacy_smb_auth(share) or _darwin_keychain_password(
        share
    )
    if not pw:
        return share
    return RemoteShare(share.host, share.share, share.rest, share.user, pw)


def _stash_parent() -> str:
    for candidate in (
        "/Library/Application Support/mosaicWave",
        "/Library/Application Support/mosaicWave-dev",
        os.path.expanduser("~/Library/Application Support/mosaicWave-dev"),
        os.path.expanduser("~/Library/Application Support/mosaicWave"),
        "/var/tmp",
    ):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, "mw-stash-probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.unlink(probe)
            return candidate
        except OSError:
            continue
    return "/var/tmp"


def stash_local_dir(path: str) -> str | None:
    """Move a leftover mosaicWave-* dest (or a child under it). Never touches Finder /Volumes/Public."""
    global _last_stashed_volume
    path = _app_volume_root(path) or path
    if not _is_app_volume_name(path):
        return None
    if os.path.ismount(path) or _is_smbfs_mount(path):
        return None
    if not os.path.isdir(path):
        return None
    if clear_stale_mount_dir(path):
        _last_stashed_volume = ""
        return ""
    if _has_mosaicwave_items(path):
        return None
    parent = _stash_parent()
    base = os.path.basename(path.rstrip("/")) or "volume"
    dest = os.path.join(parent, f"leftover-Volumes-{base}")
    if os.path.exists(dest):
        dest = f"{dest}-{int(time.time())}"
    try:
        os.rename(path, dest)
    except OSError:
        try:
            shutil.move(path, dest)
        except OSError:
            return None
    _last_stashed_volume = dest
    return dest


def smb_url_for_host_path(raw: str | os.PathLike[str]) -> str | None:
    """smb:// URL for a typed path, or for a folder that sits on a Darwin smbfs mount."""
    text = os.fspath(raw).strip()
    if not text:
        return None
    share = parse_remote_share(text)
    if share is not None:
        return _smb_url(share)
    if sys.platform != "darwin":
        return None
    path_n = os.path.normpath(text.replace("\\", "/")).replace("\\", "/")
    for host, share_name, point in _darwin_mounts():
        point_n = os.path.normpath(point).replace("\\", "/")
        if path_n == point_n or path_n.startswith(point_n + "/"):
            rest = "" if path_n == point_n else path_n[len(point_n) :].lstrip("/")
            return _smb_url(RemoteShare(host, share_name, rest))
    return None


def refuse_unmounted_volumes_path(path: Path) -> None:
    """Do not mkdir a local leftover under /Volumes when the NAS is not mounted."""
    if sys.platform != "darwin":
        return
    posix = Path(os.fspath(path)).as_posix()
    if not posix.startswith("/Volumes/"):
        return
    parts = [p for p in posix.split("/") if p]
    if len(parts) < 2:
        return
    vol = "/Volumes/" + parts[1]
    if _is_smbfs_mount(vol) or os.path.ismount(vol):
        return
    raise ValueError(
        f"{vol} is not a mounted volume. Refusing to create a local folder under /Volumes. "
        "Use smb://server/share, or the app folder."
    )


def clear_stale_mount_dir(dest: str) -> bool:
    """Remove an empty leftover /Volumes dir so mount_smbfs can use that name."""
    if os.path.ismount(dest) or _is_smbfs_mount(dest):
        return False
    if not os.path.isdir(dest):
        return not os.path.exists(dest)
    try:
        names = os.listdir(dest)
    except OSError:
        return False
    for name in names:
        if _is_mount_junk(name):
            if not _unlink_mount_junk(os.path.join(dest, name)):
                return False
            continue
        return False
    try:
        os.rmdir(dest)
    except OSError:
        return False
    return True


def _unmount_dest(dest: str) -> None:
    if not (os.path.ismount(dest) or _is_smbfs_mount(dest)):
        return
    try:
        subprocess.run(
            ["/sbin/umount", dest], capture_output=True, text=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not (os.path.ismount(dest) or _is_smbfs_mount(dest)):
        return
    try:
        subprocess.run(
            ["/usr/sbin/diskutil", "unmount", "force", dest],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


_SMBFS_MOUNT_OPTS = "nostreams,noquarantine,nobrowse"


def _mount_smbfs_cmd(spec: str, dest: str) -> list[str]:
    """-f/-d so root can write; nostreams/noquarantine avoid EPERM on xattrs."""
    return [
        "/sbin/mount_smbfs",
        "-N",
        "-s",
        "-f",
        "0777",
        "-d",
        "0777",
        "-o",
        _SMBFS_MOUNT_OPTS,
        spec,
        dest,
    ]


def _recover_unusable_mount(dest: str) -> bool:
    """Leftover local dir only. Do not unmount a dest another process may be using."""
    if not dest.startswith("/Volumes/mosaicWave"):
        return False
    if _dest_is_mount(dest):
        return False
    stash_local_dir(dest)
    return _prepare_mount_dest(dest) == ""


def _try_mount_specs(specs: list[str], dest: str, share: RemoteShare) -> bool:
    """Run mount_smbfs for each spec. Success is exit 0 plus a listable unique volume."""
    global _last_mount_error
    errors: list[str] = []
    seen_err: set[str] = set()
    env = os.environ.copy()
    for spec in specs:
        retried = False
        while True:
            problem = _prepare_mount_dest(dest)
            if problem:
                if problem not in seen_err:
                    seen_err.add(problem)
                    errors.append(problem)
                break
            cmd = _mount_smbfs_cmd(spec, dest)
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(_redact_text(str(exc), password=share.password, spec=spec))
                _unmount_dest(dest)
                break
            if proc.returncode == 0:
                if _wait_dest_ready(dest):
                    _last_mount_error = ""
                    return True
                # This Save folder just ran mount_smbfs. Tear down that half-dead
                # session and retry. Do not touch a dest that was already mounted.
                if _dest_is_mount(dest):
                    _unmount_dest(dest)
                if not retried and _recover_unusable_mount(dest):
                    retried = True
                    continue
                err = _mount_cmd_error(proc, spec, share)
                if "exit 0" in err or proc.returncode == 0:
                    err = f"mount_smbfs exit 0 but {dest} was not a usable mount"
                if err not in seen_err:
                    seen_err.add(err)
                    errors.append(err)
                break
            _unmount_dest(dest)
            err = _mount_cmd_error(proc, spec, share)
            if err not in seen_err:
                seen_err.add(err)
                errors.append(err)
            break
    try:
        if not _dest_is_mount(dest) and dest.startswith("/Volumes/mosaicWave"):
            os.rmdir(dest)
    except OSError:
        pass
    detail = "; ".join(errors)
    _last_mount_error = _redact_text(detail[:800], password=share.password)
    return False


def _run_mount_smbfs(share: RemoteShare, dest: str) -> bool:
    """1) //user:pass@HOST/share  2) save nsmb+keychain  3) remount //HOST/share."""
    global _last_mount_error, _last_mount_dest
    _last_mount_error = ""
    _last_mount_dest = dest
    problem = _prepare_mount_dest(dest)
    if problem:
        _last_mount_error = problem
        return False
    provided_password = bool(share.password)
    share = _with_nsmb_user(share)
    if _dest_is_mount(dest) and _service_can_access(dest):
        if provided_password and share.user:
            save_smb_login(share)
        return True
    share = _with_password(share)
    if _dest_is_mount(dest):
        _last_mount_error = (
            f"{dest} is already mounted but this process cannot list it. "
            "Pick another mount point, or leave the field blank."
        )
        return False
    creds = _credential_specs(share)
    simple = _simplified_specs(share)
    if share.user and provided_password:
        if not creds:
            _last_mount_error = (
                "Enter the NAS password in Settings. The LaunchDaemon cannot use a login keychain."
            )
            return False
        if not _try_mount_specs(creds, dest, share):
            return False
        save_smb_login(share)
        _unmount_dest(dest)
        if _try_mount_specs(simple, dest, share):
            return True
        return _try_mount_specs(creds, dest, share)
    if share.user and not share.password:
        _last_mount_error = (
            "Enter the NAS password in Settings. The LaunchDaemon cannot use a login keychain."
        )
        return False
    if share.user:
        if _try_mount_specs(simple, dest, share):
            return True
        if creds:
            return _try_mount_specs(creds, dest, share)
        return False
    return _try_mount_specs(_mount_specs(share), dest, share)


def _effective_mount_dest(share: RemoteShare, mount_point: str = "") -> str:
    """User mount point as typed. Only strip a library folder under that dest."""
    unique = os.path.normpath(_darwin_unique_dest(share))
    if not (mount_point or "").strip():
        return unique
    dest_n = os.path.normpath(_normalize_mount_point(mount_point))
    if dest_n == unique or dest_n.startswith(unique + "/"):
        return unique
    vol = _app_volume_root(dest_n)
    if vol and dest_n.startswith(vol + "/"):
        return vol
    return dest_n


def _normalize_mount_point(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("mount point must be an absolute path")
    posix = os.path.normpath(path.as_posix())
    if posix in ("/", "/Volumes"):
        raise ValueError("mount point must be an empty folder, not / or /Volumes")
    if sys.platform == "darwin":
        err = _forbidden_finder_dest(posix)
        if err:
            raise ValueError(err)
    return posix


def _forbidden_finder_dest(dest: str) -> str:
    """Reject Finder /Volumes/Public and overlaying an existing volume."""
    posix = os.path.normpath(dest)
    if not posix.startswith("/Volumes/") or posix == "/Volumes":
        return ""
    vol = "/Volumes/" + [p for p in posix.split("/") if p][1]
    if _is_app_volume_name(vol) or _is_app_volume_name(posix):
        return ""
    base = os.path.basename(vol).lower()
    if base == "public" or base.startswith("public-"):
        return (
            f"{vol} is a Finder volume. Browse an empty folder for the mosaicWave mount, "
            "or leave mount point blank for /Volumes/mosaicWave-…"
        )
    if os.path.ismount(posix) or _is_smbfs_mount(posix):
        return (
            f"{posix} is already a mounted volume. Browse an empty folder for the mosaicWave "
            "mount, or leave mount point blank for /Volumes/mosaicWave-…"
        )
    return ""


def try_mount_share(
    share: RemoteShare, *, wait_gui: float = 8.0, mount_point: str = ""
) -> Path | None:
    """Mount to mount_point, or /Volumes/mosaicWave[-dev]-{host}-{share}. Finder Public unused.

    When using the default (computed) dest and it is mounted by something else and
    unreadable here, try numbered fallbacks (-2, -3, …) instead of unmounting it — an
    existing mount might be in active use by another mosaicWave instance (e.g. debug).
    An explicitly typed mount point never falls back; it fails the same way as before.
    """
    del wait_gui
    if sys.platform != "darwin":
        return None
    global _last_mount_dest, _last_mount_notice
    _last_mount_notice = ""
    primary = _effective_mount_dest(share, mount_point)
    unique = os.path.normpath(_darwin_unique_dest(share))
    candidates = _darwin_unique_dest_candidates(share) if primary == unique else [primary]
    for dest in candidates:
        existing = locate_accessible_share_root(share, mount_point=dest)
        if existing is not None:
            _last_mount_dest = existing
            provided_password = bool(share.password)
            if provided_password and share.user:
                save_smb_login(share)
            if dest != candidates[0]:
                _last_mount_notice = (
                    f"{candidates[0]} was already in use by another mosaicWave instance; "
                    f"mounted at {existing} instead."
                )
            return Path(_join_rest(existing, share.rest, windows=False))
        collision = _dest_is_mount(dest) and not _service_can_access(dest)
        _last_mount_dest = ""
        if _run_mount_smbfs(share, dest):
            used = _last_mount_dest or dest
            _wait_dest_ready(used)
            if _dest_is_mount(used) and _service_can_access(used):
                if dest != candidates[0]:
                    _last_mount_notice = (
                        f"{candidates[0]} was already in use by another mosaicWave instance; "
                        f"mounted at {used} instead."
                    )
                return Path(_join_rest(used, share.rest, windows=False))
        if not collision or len(candidates) == 1:
            return None
    return None


def last_mount_notice() -> str:
    """Non-error FYI from the last try_mount_share() call (e.g. used a fallback dest)."""
    return _last_mount_notice


def share_mount_dest() -> str:
    """Share-root dest of the last successful or attempted Darwin mount."""
    return _last_mount_dest


def coerce_host_folder(
    raw: str,
    *,
    mount: bool = True,
    wait_gui: float = 20.0,
    user: str = "",
    password: str = "",
    mount_point: str = "",
) -> Path:
    """Turn a typed Settings/Browse path (including smb://) into a filesystem Path."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("path is required")
    share = parse_remote_share(text)
    if share is not None:
        share = RemoteShare(
            share.host,
            share.share,
            share.rest,
            (user or "").strip() or share.user,
            password or share.password,
        )
        share = _with_nsmb_user(share)
    if share is None:
        if os.name != "nt" and text.startswith("\\\\"):
            text = text.replace("\\", "/")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError("data_dir must be an absolute path")
        if sys.platform == "darwin":
            posix = path.as_posix()
            if posix.startswith("/Volumes/") and posix != "/Volumes":
                vol = "/Volumes/" + [p for p in posix.split("/") if p][1]
                if not _is_app_volume_name(vol):
                    url = smb_url_for_host_path(path)
                    if url:
                        return coerce_host_folder(
                            url,
                            mount=mount,
                            wait_gui=wait_gui,
                            user=user,
                            password=password,
                            mount_point=mount_point,
                        )
                    raise ValueError(
                        f"{path} is a Finder volume. Paste smb://server/share "
                        "(mosaicWave mounts a unique mosaicWave-server-share volume), "
                        "or use the app folder."
                    )
                if not _service_can_access(os.fspath(path)) and not _service_can_access(vol):
                    raise ValueError(
                        f"{path} is not mounted. Paste smb://server/share, "
                        "or use the app folder."
                    )
        return path
    if mount:
        mounted = try_mount_share(share, wait_gui=wait_gui, mount_point=mount_point)
        if mounted is not None:
            return mounted
    if sys.platform != "darwin":
        root = locate_share_root_text(share)
        if root and _service_can_access(root):
            return Path(_join_rest(root, share.rest, windows=os.name == "nt"))
        raise ValueError(f"{_smb_url(share)} is not available to mosaicWave.")
    dest = _effective_mount_dest(share, mount_point)
    extra = f" Could not mount {_smb_url(share)} at {dest}."
    if _last_mount_error:
        extra += f" {_last_mount_error}"
    if share.user and _looks_like_auth_error(_last_mount_error):
        extra += (
            f" Authentication as {share.user} failed. Enter the NAS username and password "
            "in Settings. Or keep the library in the app folder."
        )
    elif share.user:
        extra += (
            " The unique mosaicWave volume did not stay mounted. Retry Save folder, "
            "or keep the library in the app folder."
        )
    else:
        extra += (
            " The LaunchDaemon has no login password. Enter the NAS username and password "
            "in Settings, enable guest, or keep the library in the app folder."
        )
    raise ValueError(_redact_text(f"{_smb_url(share)} is not available.{extra}", password=share.password))
