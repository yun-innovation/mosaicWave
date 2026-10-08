from __future__ import annotations

import base64
import os
import socket
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mosaicwave.auth.passwords import hash_password, verify_password
from mosaicwave.models import LocalCredential, User, new_id, utcnow

PROVIDER_LOCAL = "local"
PROVIDER_QNAP = "qnap"


class AuthUnavailable(Exception):
    """Provider cannot run on this host (e.g. QNAP login off QTS)."""


def _looks_like_qts() -> bool:
    return Path("/etc/config/uLinux.conf").is_file() or Path("/sbin/getcfg").is_file()


def user_count(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0
    )


def find_user(session: Session, provider: str, subject: str) -> User | None:
    return session.scalar(
        select(User).where(
            User.provider == provider,
            User.provider_subject == subject,
            User.deleted_at.is_(None),
        )
    )


def get_or_create_user(
    session: Session,
    *,
    provider: str,
    subject: str,
    display_name: str,
    role: str | None = None,
) -> User:
    existing = find_user(session, provider, subject)
    if existing is not None:
        return existing
    if role is None:
        role = "admin" if user_count(session) == 0 else "member"
    user = User(
        id=new_id(),
        provider=provider,
        provider_subject=subject,
        display_name=display_name or subject,
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def create_local_user(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str,
) -> User:
    if find_user(session, PROVIDER_LOCAL, username) is not None:
        raise ValueError("username already exists")
    if role not in ("admin", "member"):
        raise ValueError("role must be admin or member")
    user = get_or_create_user(
        session,
        provider=PROVIDER_LOCAL,
        subject=username,
        display_name=display_name or username,
        role=role,
    )
    session.add(LocalCredential(user_id=user.id, password_hash=hash_password(password)))
    session.flush()
    return user


def verify_local(session: Session, username: str, password: str) -> User | None:
    user = find_user(session, PROVIDER_LOCAL, username)
    if user is None:
        return None
    cred = session.get(LocalCredential, user.id)
    if cred is None or not verify_password(password, cred.password_hash):
        return None
    return user


def _getcfg(section: str, key: str, default: str = "") -> str:
    cmd = Path("/sbin/getcfg")
    if not cmd.is_file():
        return default
    try:
        proc = subprocess.run(
            [str(cmd), section, key, "-d", default],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return default
    text = (proc.stdout or "").strip()
    return text if text else default


_QNAP_CGI_PATHS = (
    Path("/home/httpd/cgi-bin/authLogin.cgi"),
    Path("/usr/local/apache/cgi-bin/authLogin.cgi"),
    Path("/usr/local/apache2/cgi-bin/authLogin.cgi"),
)


def _qnap_http_ports() -> list[tuple[str, int]]:
    """(scheme, port) pairs for QTS web. HTTPS first when Force SSL is on."""
    web = _getcfg("System", "Web Access Port", "8080")
    ssl_port = _getcfg("Stunnel", "Port", "443")
    try:
        web_n = int(web)
    except ValueError:
        web_n = 8080
    try:
        ssl_n = int(ssl_port)
    except ValueError:
        ssl_n = 443
    force = _getcfg("System", "Force SSL", "").upper() in ("1", "TRUE", "YES")
    pairs: list[tuple[str, int]] = []
    if force:
        pairs.append(("https", ssl_n))
        pairs.append(("http", web_n))
    else:
        pairs.append(("http", web_n))
        pairs.append(("https", ssl_n))
    for extra in (("http", 8080), ("http", 80), ("https", 443), ("https", 8443)):
        if extra not in pairs:
            pairs.append(extra)
    return pairs


def _tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _strip_cgi_body(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    idx = text.find("<?xml")
    if idx >= 0:
        return text[idx:]
    if "\r\n\r\n" in text:
        return text.split("\r\n\r\n", 1)[1]
    if "\n\n" in text:
        return text.split("\n\n", 1)[1]
    return text


def _local_ipv4s() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def _qnap_cgi_bases() -> list[str]:
    override = (os.environ.get("MOSAICWAVE_QNAP_CGI") or "").strip()
    if override:
        return [override.split("?", 1)[0]]
    hosts = ("127.0.0.1", "localhost", *_local_ipv4s())
    bases: list[str] = []
    seen: set[str] = set()
    for scheme, port in _qnap_http_ports():
        for host in hosts:
            probe = "127.0.0.1" if host == "localhost" else host
            if not _tcp_open(probe, port):
                continue
            url = f"{scheme}://{host}:{port}/cgi-bin/authLogin.cgi"
            if url not in seen:
                seen.add(url)
                bases.append(url)
    return bases


def _http_cgi_body(url: str, data: bytes | None, timeout: float) -> bytes | None:
    kwargs: dict = {"timeout": timeout}
    if url.startswith("https:"):
        kwargs["context"] = ssl._create_unverified_context()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, **kwargs) as resp:
            return resp.read() or None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return body or None
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError, ValueError):
        return None


def _qnap_cgi_http(query: dict[str, str], timeout: float) -> str | None:
    encoded = urllib.parse.urlencode(query)
    payload = encoded.encode("ascii")
    for base in _qnap_cgi_bases():
        for body in (
            _http_cgi_body(f"{base}?{encoded}", None, timeout),
            _http_cgi_body(base, payload, timeout),
        ):
            if body:
                return _strip_cgi_body(body)
    return None


def _qnap_cgi_direct(query: dict[str, str], timeout: float) -> str | None:
    encoded = urllib.parse.urlencode(query)
    env = os.environ.copy()
    env.update(
        {
            "REQUEST_METHOD": "GET",
            "QUERY_STRING": encoded,
            "SCRIPT_NAME": "/cgi-bin/authLogin.cgi",
            "GATEWAY_INTERFACE": "CGI/1.1",
            "REMOTE_ADDR": "127.0.0.1",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8080",
            "HTTP_HOST": "127.0.0.1",
        }
    )
    for cgi in _QNAP_CGI_PATHS:
        if not cgi.is_file():
            continue
        try:
            proc = subprocess.run(
                [str(cgi)],
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
                cwd=str(cgi.parent),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        raw = proc.stdout or b""
        if raw.strip():
            return _strip_cgi_body(raw)
    return None


def _qnap_cgi_get(query: dict[str, str], timeout: float = 2.0) -> str | None:
    xml_text = _qnap_cgi_http(query, timeout)
    if xml_text:
        return xml_text
    return _qnap_cgi_direct(query, timeout)


def _xml_text(root: ET.Element, *names: str) -> str | None:
    for name in names:
        node = root.find(name)
        if node is not None and node.text:
            return node.text.strip()
    return None


def _parse_qnap_auth(xml_text: str) -> str | None:
    text = xml_text.strip().lstrip("\ufeff")
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    passed = _xml_text(root, "authPassed", "auth_passed")
    if passed not in ("1", "true", "TRUE"):
        return None
    username = _xml_text(root, "username", "user", "authUser")
    return username or None


def _qnap_pwd_variants(password: str) -> list[str]:
    encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
    if encoded == password:
        return [encoded]
    return [encoded, password]


def verify_qnap_password(username: str, password: str) -> str | None:
    """Ask QTS to check username/password. Returns the QNAP username on success."""
    if not _looks_like_qts() and not os.environ.get("MOSAICWAVE_QNAP_CGI"):
        raise AuthUnavailable("QNAP login is only available on QTS")
    xml_text: str | None = None
    for pwd in _qnap_pwd_variants(password):
        xml_text = _qnap_cgi_get(
            {"user": username, "pwd": pwd, "service": "1", "serviceKey": "1"}
        )
        if not xml_text:
            raise AuthUnavailable("QNAP auth service is not reachable")
        subject = _parse_qnap_auth(xml_text)
        if subject:
            return subject
    return None


def verify_qnap_sid(sid: str) -> str | None:
    if not sid:
        return None
    if not _looks_like_qts() and not os.environ.get("MOSAICWAVE_QNAP_CGI"):
        raise AuthUnavailable("QNAP login is only available on QTS")
    xml_text = _qnap_cgi_get({"sid": sid})
    if not xml_text:
        raise AuthUnavailable("QNAP auth service is not reachable")
    return _parse_qnap_auth(xml_text)


def qts_sid_from_cookies(cookies: dict[str, str]) -> str | None:
    return cookies.get("NAS_SID") or cookies.get("NASID") or cookies.get("QTS_SID")


def touch_user(user: User) -> None:
    user.rev = int(user.rev) + 1
    user.updated_at = utcnow()
