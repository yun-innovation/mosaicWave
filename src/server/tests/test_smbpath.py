from __future__ import annotations

import os
from pathlib import Path

import pytest

from mosaicwave.config import normalize_host_path_text
from mosaicwave.storage.smbpath import (
    RemoteShare,
    native_share_path_text,
    parse_remote_share,
)


def test_parse_smb_and_unc() -> None:
    assert parse_remote_share("smb://fileserver/Public/mosaicWave") == RemoteShare(
        "fileserver", "Public", "mosaicWave"
    )
    assert parse_remote_share("SMB://fileserver/Photos") == RemoteShare("fileserver", "Photos", "")
    assert parse_remote_share("cifs://nas/share/a/b") == RemoteShare("nas", "share", "a/b")
    assert parse_remote_share("smb://user@fileserver/Public") == RemoteShare(
        "fileserver", "Public", "", "user"
    )
    assert parse_remote_share("smb://nasuser:secret@fileserver/Public/mosaicWave") == RemoteShare(
        "fileserver", "Public", "mosaicWave", "nasuser", "secret"
    )
    assert parse_remote_share(r"\\fileserver\Public\mosaicWave") == RemoteShare(
        "fileserver", "Public", "mosaicWave"
    )
    assert parse_remote_share("//fileserver/Public") == RemoteShare("fileserver", "Public", "")
    assert parse_remote_share("fileserver/Public/mosaicWave") == RemoteShare(
        "fileserver", "Public", "mosaicWave"
    )
    assert parse_remote_share("/Volumes/Public") is None
    assert parse_remote_share(r"D:\photos") is None
    assert parse_remote_share("smb://fileserver") is None


def test_native_share_path_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "nt")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "win32")
    got = native_share_path_text(RemoteShare("fileserver", "Public", "mosaicWave"))
    assert got == r"\\fileserver\Public\mosaicWave"


def test_native_share_path_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "linux")
    got = native_share_path_text(RemoteShare("fileserver", "Public", "mosaicWave"))
    assert got == "//fileserver/Public/mosaicWave"


def test_native_share_path_darwin_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    got = native_share_path_text(RemoteShare("fileserver", "Public", "mosaicWave"))
    assert got == "smb://fileserver/Public/mosaicWave"


def test_darwin_ignores_local_volumes_dir_and_other_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("OTHER-NAS", "Public", "/Volumes/Public")],
    )
    from mosaicwave.storage.smbpath import locate_share_root_text

    assert locate_share_root_text(RemoteShare("fileserver", "Public", "")) is None


def test_coerce_smb_rejects_unmounted_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", lambda _s, **_k: None)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.isdir", lambda _p: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import coerce_host_folder

    with pytest.raises(ValueError, match="mosaicWave-dev-fileserver-Public"):
        coerce_host_folder("smb://fileserver/Public/mosaicWave", mount=True)


def test_native_share_path_darwin_uses_mount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vol = tmp_path / "Public"
    vol.mkdir()
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("fileserver", "Public", str(vol))],
    )
    got = native_share_path_text(RemoteShare("fileserver", "Public", "mosaicWave"))
    assert got == str(vol / "mosaicWave") or got.replace("\\", "/") == (vol / "mosaicWave").as_posix()


def test_normalize_smb_url_posix_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "linux")
    assert (
        normalize_host_path_text("smb://fileserver/Public/mosaicWave")
        == "//fileserver/Public/mosaicWave"
    )


@pytest.mark.skipif(os.name == "nt", reason="posix UNC slash conversion")
def test_normalize_unc_stays_slash_unc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.config.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "linux")
    assert (
        normalize_host_path_text(r"\\fileserver\Public\mosaicWave")
        == "//fileserver/Public/mosaicWave"
    )


def test_smb_url_for_host_path_from_mount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vol = tmp_path / "Public"
    folder = vol / "mosaicWave"
    folder.mkdir(parents=True)
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("fileserver", "Public", str(vol))],
    )
    from mosaicwave.storage.smbpath import smb_url_for_host_path

    assert smb_url_for_host_path(str(folder)) == "smb://fileserver/Public/mosaicWave"
    assert smb_url_for_host_path("smb://fileserver/Public/mosaicWave") == (
        "smb://fileserver/Public/mosaicWave"
    )


def test_clear_stale_mount_dir_junk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "mosaicWave-fileserver-Public"
    dest.mkdir()
    (dest / ".DS_Store").write_bytes(b"x")
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import clear_stale_mount_dir

    assert clear_stale_mount_dir(str(dest)) is True
    assert not dest.exists()


def test_clear_stale_mount_dir_junk_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "mosaicWave-fileserver-Public"
    dest.mkdir()
    (dest / ".DS_store").write_bytes(b"x")
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import clear_stale_mount_dir

    assert clear_stale_mount_dir(str(dest)) is True
    assert not dest.exists()


def test_clear_stale_mount_dir_keeps_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "vol"
    dest.mkdir()
    (dest / "library.db").write_bytes(b"x")
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import clear_stale_mount_dir

    assert clear_stale_mount_dir(str(dest)) is False
    assert (dest / "library.db").is_file()


def test_refuse_unmounted_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.ismount", lambda _p: False)
    from mosaicwave.storage.smbpath import refuse_unmounted_volumes_path

    with pytest.raises(ValueError, match="not a mounted volume"):
        refuse_unmounted_volumes_path(Path("/Volumes/Public/mosaicWave"))


def test_host_aliases_adds_local() -> None:
    from mosaicwave.storage.smbpath import _host_aliases, _mount_specs

    assert _host_aliases("fileserver") == [
        "fileserver",
        "fileserver.local",
        "fileserver._smb._tcp.local",
    ]
    assert _host_aliases("fileserver.local") == [
        "fileserver.local",
        "fileserver",
        "fileserver._smb._tcp.local",
    ]
    assert "fileserver._smb._tcp.local" in _host_aliases("fileserver._smb._tcp.local")
    assert "//fileserver._smb._tcp.local/Public" in _mount_specs(
        RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    )
    specs = _mount_specs(RemoteShare("fileserver", "Public", ""))
    assert "//fileserver/Public" in specs
    assert "//fileserver.local/Public" in specs
    assert "//guest@fileserver/Public" in specs
    named = _mount_specs(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser"))
    assert named[0] == "//fileserver/Public"
    assert "//nasuser@fileserver/Public" not in named
    from mosaicwave.storage.smbpath import _credential_specs, _simplified_specs

    secret_share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert _credential_specs(secret_share)[0] == "//nasuser:secret@fileserver/Public"
    assert _simplified_specs(secret_share)[0] == "//fileserver/Public"
    assert "//nasuser@fileserver/Public" not in _credential_specs(secret_share)
    secret = _mount_specs(secret_share)
    assert secret[0] == "//fileserver/Public"
    assert "//nasuser:secret@fileserver/Public" in secret
    assert "//nasuser@fileserver/Public" not in secret
    from mosaicwave.storage.smbpath import _redact_spec, _redact_text, _smb_escape, _smb_url

    assert _smb_escape("p@ss:w/ord") == "p%40ss%3Aw%2Ford"
    at = _mount_specs(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "p@ss:w/ord"))
    assert at[0] == "//fileserver/Public"
    encoded = "//nasuser:p%40ss%3Aw%2Ford@fileserver/Public"
    assert encoded in at
    assert "p@ss" not in encoded
    assert _redact_spec(encoded) == "//nasuser:***@fileserver/Public"
    assert _redact_spec("//nasuser:p@ss@fileserver/Public") == "//nasuser:***@fileserver/Public"
    hidden = _redact_text("auth p@ss:w/ord p%40ss%3Aw%2Ford", password="p@ss:w/ord", spec=encoded)
    assert "p@ss" not in hidden
    assert "%40" not in hidden
    assert _redact_spec("//nasuser:secret@fileserver/Public") == "//nasuser:***@fileserver/Public"

    assert (
        _smb_url(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret"))
        == "smb://fileserver/Public/mosaicWave"
    )


def test_nsmb_username_section_no_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "nsmb.conf"
    monkeypatch.setattr("mosaicwave.storage.smbpath._nsmb_conf_files", lambda: [conf])
    from mosaicwave.storage.smbpath import RemoteShare, _write_nsmb_conf, nsmb_profile_exists

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    _write_nsmb_conf(share)
    text = conf.read_text(encoding="utf-8")
    assert "[fileserver]" in text
    assert "username=nasuser" in text
    assert "password=" not in text
    assert nsmb_profile_exists(share) is True
    assert "[mosaicWave-fileserver-nasuser]" not in text
    from mosaicwave.storage.smbpath import nsmb_username_for_share

    host_only = RemoteShare("fileserver", "Public", "mosaicWave")
    assert nsmb_username_for_share(host_only) == "nasuser"


def test_ensure_nsmb_profile_adds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "nsmb.conf"
    monkeypatch.setattr("mosaicwave.storage.smbpath._nsmb_conf_files", lambda: [conf])
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: False)
    from mosaicwave.storage.smbpath import (
        RemoteShare,
        ensure_nsmb_profile,
        nsmb_profile_exists,
        prepare_smb_url,
    )

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert ensure_nsmb_profile(share) is True
    first = conf.read_text(encoding="utf-8")
    assert nsmb_profile_exists(share) is True
    assert ensure_nsmb_profile(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "other")) is False
    assert conf.read_text(encoding="utf-8") == first
    assert "username=nasuser" in first
    assert "password=" not in first
    assert (
        prepare_smb_url("smb://fileserver/Public/mosaicWave", user="nasuser", password="secret")
        == "smb://fileserver/Public/mosaicWave"
    )
    assert (
        prepare_smb_url("smb://nasuser@fileserver/Public/mosaicWave", user="nasuser")
        == "smb://fileserver/Public/mosaicWave"
    )


def test_stash_local_dir_moves_non_library_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = tmp_path / "mosaicWave-fileserver-Public"
    leftover.mkdir()
    (leftover / "readme.txt").write_text("keep", encoding="utf-8")
    stash_to = tmp_path / "stash"
    stash_to.mkdir()
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._stash_parent", lambda: str(stash_to))
    from mosaicwave.storage.smbpath import stash_local_dir

    moved = stash_local_dir(str(leftover))
    assert moved is not None
    assert not leftover.exists()
    assert (Path(moved) / "readme.txt").read_text(encoding="utf-8") == "keep"


def test_stash_local_dir_keeps_mosaicwave_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = tmp_path / "mosaicWave-fileserver-Public"
    lib = leftover / "mosaicWave"
    lib.mkdir(parents=True)
    (leftover / ".DS_Store").write_bytes(b"x")
    (lib / "note.txt").write_text("keep", encoding="utf-8")
    stash_to = tmp_path / "stash"
    stash_to.mkdir()
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._stash_parent", lambda: str(stash_to))
    from mosaicwave.storage.smbpath import stash_local_dir

    assert stash_local_dir(str(leftover)) is None
    assert stash_local_dir(str(lib)) is None
    assert (lib / "note.txt").read_text(encoding="utf-8") == "keep"
    assert not (leftover / ".DS_Store").exists()
    assert list(stash_to.iterdir()) == []


def test_is_app_volume_name_accepts_bare_prefix() -> None:
    """A mount point typed as exactly "mosaicWave" (no dash suffix) is still an app volume."""
    from mosaicwave.storage.smbpath import _is_app_volume_name

    assert _is_app_volume_name("/Volumes/mosaicWave") is True
    assert _is_app_volume_name("/Volumes/mosaicWave-dev") is True
    assert _is_app_volume_name("/Volumes/mosaicWave-fileserver-Public") is True
    assert _is_app_volume_name("/Volumes/Public") is False
    assert _is_app_volume_name("/Volumes/mosaicWaveX") is False


def test_is_app_volume_name_is_case_insensitive() -> None:
    """/Volumes is case-insensitive; macOS can hand back a mount in a different case
    than what we requested (e.g. lowercased by the OS/NAS)."""
    from mosaicwave.storage.smbpath import _app_volume_root, _is_app_volume_name

    assert _is_app_volume_name("/Volumes/mosaicwave-dev") is True
    assert _is_app_volume_name("/Volumes/MosaicWave") is True
    assert _app_volume_root("/Volumes/mosaicwave-dev") == "/Volumes/mosaicwave-dev"


def test_forbidden_finder_dest_allows_our_own_lowercased_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a dev mount that landed as /Volumes/mosaicwave-dev (lowercase) must
    not be rejected as "already a mounted volume" during config startup."""
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.ismount", lambda _p: True)
    from mosaicwave.storage.smbpath import _forbidden_finder_dest

    assert _forbidden_finder_dest("/Volumes/mosaicwave-dev") == ""


def test_app_volume_root_bare_prefix_is_top_level_only() -> None:
    """The bare "mosaicWave" dest itself is a volume root, but a nested folder that
    happens to be named "mosaicWave" under a *different* volume is not."""
    from mosaicwave.storage.smbpath import _app_volume_root

    assert _app_volume_root("/Volumes/mosaicWave") == "/Volumes/mosaicWave"
    assert _app_volume_root("/Volumes/mosaicWave/mosaicWave") == "/Volumes/mosaicWave"
    assert (
        _app_volume_root("/Volumes/mosaicWave-fileserver-Public/mosaicWave")
        == "/Volumes/mosaicWave-fileserver-Public"
    )


def test_effective_mount_dest_strips_library_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_volume_prefix", lambda: "mosaicWave")
    from mosaicwave.storage.smbpath import RemoteShare, _effective_mount_dest

    share = RemoteShare("fileserver", "Public", "mosaicWave")
    unique = "/Volumes/mosaicWave-fileserver-Public"
    assert _effective_mount_dest(share, "") == unique
    assert _effective_mount_dest(share, unique) == unique
    assert _effective_mount_dest(share, f"{unique}/mosaicWave") == unique


def test_effective_mount_dest_keeps_user_dest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "dev")
    from mosaicwave.storage.smbpath import RemoteShare, _effective_mount_dest

    share = RemoteShare("fileserver", "Public", "mosaicWave")
    other = "/Volumes/mosaicWave-fileserver-Public"
    assert _effective_mount_dest(share, other) == other
    assert _effective_mount_dest(share, f"{other}/mosaicWave") == other


def test_stash_ignores_finder_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import stash_local_dir

    assert stash_local_dir("/Volumes/Public") is None


def test_parse_mount_strips_bonjour_host() -> None:
    from mosaicwave.storage.smbpath import _parse_mount_stdout

    line = (
        "//nasuser@fileserver._smb._tcp.local/Public on /Volumes/Public "
        "(smbfs, nodev, nosuid, mounted by nasuser)"
    )
    assert _parse_mount_stdout(line) == [("fileserver", "Public", "/Volumes/Public")]


def test_try_mount_uses_unique_name_not_finder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("fileserver", "Public", "/Volumes/Public-1")],
    )
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", lambda _s, _d: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._wait_dest_ready", lambda _p, **_k: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: True)
    from mosaicwave.storage.smbpath import try_mount_share

    got = try_mount_share(RemoteShare("fileserver", "Public", "mosaicWave"), wait_gui=0)
    posix = str(got).replace("\\", "/")
    assert posix.endswith("/Volumes/mosaicWave-dev-fileserver-Public/mosaicWave")
    assert "Public-1" not in posix


def test_try_mount_uses_explicit_mount_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "mw-mnt"
    dest.mkdir()
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._wait_dest_ready", lambda _p, **_k: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: True)
    seen: list[str] = []

    def run(_share: object, point: str) -> bool:
        seen.append(point)
        return True

    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", run)
    from mosaicwave.storage.smbpath import try_mount_share

    got = try_mount_share(
        RemoteShare("fileserver", "Public", "mosaicWave"),
        wait_gui=0,
        mount_point=str(dest),
    )
    assert seen == [str(dest)]
    posix = str(got).replace("\\", "/")
    assert posix.endswith("/mw-mnt/mosaicWave")


def test_normalize_mount_rejects_finder_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import _normalize_mount_point

    with pytest.raises(ValueError, match="Finder"):
        _normalize_mount_point("/Volumes/Public")
    with pytest.raises(ValueError, match="Finder"):
        _normalize_mount_point("/Volumes/Public-1")
    with pytest.raises(ValueError, match="empty folder"):
        _normalize_mount_point("/Volumes")


def test_coerce_passes_explicit_mount_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "mw-mnt"
    dest.mkdir()
    seen: dict[str, str] = {}

    def try_mount(_share: object, **kwargs: object) -> Path:
        seen["mount_point"] = str(kwargs.get("mount_point") or "")
        return dest / "mosaicWave"

    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", try_mount)
    from mosaicwave.storage.smbpath import coerce_host_folder

    got = coerce_host_folder(
        "smb://fileserver/Public/mosaicWave",
        mount=True,
        user="nasuser",
        password="secret",
        mount_point=str(dest),
    )
    assert seen["mount_point"] == str(dest)
    assert got == dest / "mosaicWave"


def test_try_mount_skips_unreadable_finder_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("fileserver", "Public", "/Volumes/Public")],
    )
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._service_can_access",
        lambda p: "mosaicWave-" in str(p).replace("\\", "/"),
    )
    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", lambda _s, _d: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._wait_dest_ready", lambda _p, **_k: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: True)
    from mosaicwave.storage.smbpath import try_mount_share

    got = try_mount_share(RemoteShare("fileserver", "Public", "mosaicWave"), wait_gui=0)
    assert got is not None
    assert "mosaicWave-" in str(got).replace("\\", "/")


def test_coerce_explains_failed_unique_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._darwin_mounts",
        lambda: [("fileserver", "Public", "/Volumes/Public-1")],
    )
    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", lambda _s, **_k: None)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: False)
    from mosaicwave.storage.smbpath import coerce_host_folder

    with pytest.raises(ValueError, match="mosaicWave-dev-fileserver-Public"):
        coerce_host_folder("smb://fileserver/Public/mosaicWave", mount=True)


def test_coerce_named_user_asks_for_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", lambda _s, **_k: None)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_nsmb_profile", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: False)
    import mosaicwave.storage.smbpath as sp
    from mosaicwave.storage.smbpath import coerce_host_folder

    sp._last_mount_error = ""
    with pytest.raises(ValueError, match="did not stay mounted"):
        coerce_host_folder("smb://nasuser@fileserver/Public/mosaicWave", mount=True)


def test_coerce_reports_auth_only_when_server_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", lambda _s, **_k: None)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_nsmb_profile", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: False)
    import mosaicwave.storage.smbpath as sp
    from mosaicwave.storage.smbpath import coerce_host_folder

    sp._last_mount_error = "mount_smbfs: server rejected the connection: Authentication error"
    with pytest.raises(ValueError, match="Authentication as nasuser failed"):
        coerce_host_folder(
            "smb://nasuser@fileserver/Public/mosaicWave",
            mount=True,
            user="nasuser",
            password="secret",
        )


def test_coerce_keeps_settings_password_on_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.name", "posix")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_nsmb_profile", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: True)
    seen: dict[str, str] = {}

    def fake_mount(share: RemoteShare, **_k: object):
        seen["user"] = share.user
        seen["password"] = share.password
        return Path("/Volumes/mosaicWave-fileserver-Public/mosaicWave")

    monkeypatch.setattr("mosaicwave.storage.smbpath.try_mount_share", fake_mount)
    from mosaicwave.storage.smbpath import coerce_host_folder

    got = coerce_host_folder(
        "smb://fileserver/Public/mosaicWave",
        mount=True,
        user="nasuser",
        password="secret",
    )
    assert seen["user"] == "nasuser"
    assert seen["password"] == "secret"
    assert "mosaicWave" in str(got).replace("\\", "/")


def test_darwin_keychain_password_reads_system_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.geteuid", lambda: 0)
    from mosaicwave.storage import smbpath as sp

    sp._keychain_pw_cache.clear()
    seen: list[list[str]] = []

    def fake_run(cmd, **_k: object):
        seen.append(list(cmd))

        class Proc:
            returncode = 0
            stdout = "from-system\n"
            stderr = ""

        return Proc()

    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", fake_run)
    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser")
    got = sp._darwin_keychain_password(share)
    assert got == "from-system"
    assert seen == [
        [
            "/usr/bin/security",
            "find-internet-password",
            "-a",
            "nasuser",
            "-s",
            "fileserver",
            "-r",
            "smb ",
            "-w",
            sp._SYSTEM_KEYCHAIN,
        ]
    ]
    assert sp._darwin_keychain_password(share) == "from-system"
    assert len(seen) == 1


def test_darwin_keychain_password_root_skips_login_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.geteuid", lambda: 0)
    from mosaicwave.storage import smbpath as sp

    sp._keychain_pw_cache.clear()
    seen: list[list[str]] = []

    def fake_run(cmd, **_k: object):
        seen.append(list(cmd))

        class Proc:
            returncode = 1
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", fake_run)
    share = RemoteShare("fileserver._smb._tcp.local", "Public", "mosaicWave", "nasuser")
    assert sp._darwin_keychain_password(share) == ""
    assert len(seen) == 1
    assert seen[0][-1] == sp._SYSTEM_KEYCHAIN
    assert seen[0][seen[0].index("-s") + 1] == "fileserver"
    assert sp._darwin_keychain_password(share) == ""
    assert len(seen) == 1


def test_ensure_system_keychain_root_writes_system_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.geteuid", lambda: 0)
    from mosaicwave.storage import smbpath as sp

    seen: list[list[str]] = []

    def fake_run(cmd, **_k: object):
        seen.append(list(cmd))

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", fake_run)
    assert sp.ensure_system_keychain(
        RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    )
    assert len(seen) == 1
    assert seen[0][-1] == sp._SYSTEM_KEYCHAIN


def test_run_mount_requires_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_nsmb_profile", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._with_password", lambda s: s)
    from mosaicwave.storage import smbpath as sp

    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser")
    assert sp._run_mount_smbfs(share, dest) is False
    assert "password" in sp._last_mount_error.lower()
    assert "Finder" not in sp._last_mount_error
    assert "/Volumes/Public" not in sp._last_mount_error


def test_run_mount_keeps_listable_unique_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_nsmb_profile", lambda _s: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath.save_smb_login", lambda _s: None)
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda p: p == dest)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda p: p == dest)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not remount a listable unique volume")

    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", boom)
    monkeypatch.setattr("mosaicwave.storage.smbpath._unmount_dest", boom)
    from mosaicwave.storage.smbpath import _run_mount_smbfs

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert _run_mount_smbfs(share, dest) is True


def test_run_mount_saves_login_only_after_credentialed_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    order: list[str] = []
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: False)

    def fake_try(specs: list[str], _dest: str, _share: RemoteShare) -> bool:
        order.append(specs[0] if specs else "")
        return True

    def fake_save(_share: RemoteShare) -> None:
        order.append("save")

    def fake_unmount(_dest: str) -> None:
        order.append("umount")

    monkeypatch.setattr("mosaicwave.storage.smbpath._try_mount_specs", fake_try)
    monkeypatch.setattr("mosaicwave.storage.smbpath.save_smb_login", fake_save)
    monkeypatch.setattr("mosaicwave.storage.smbpath._unmount_dest", fake_unmount)
    from mosaicwave.storage.smbpath import _run_mount_smbfs

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert _run_mount_smbfs(share, dest) is True
    assert order[0] == "//nasuser:secret@fileserver/Public"
    assert order[1] == "save"
    assert order[2] == "umount"
    assert order[3] == "//fileserver/Public"
    assert "//nasuser@fileserver/Public" not in order


def test_run_mount_does_not_save_when_credentialed_connect_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    saved: list[str] = []
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._try_mount_specs", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.save_smb_login", lambda _s: saved.append("save")
    )
    from mosaicwave.storage.smbpath import _run_mount_smbfs

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert _run_mount_smbfs(share, dest) is False
    assert saved == []


def test_run_mount_already_mounted_skips_keychain_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    saved: list[str] = []
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda p: p == dest)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda p: p == dest)

    def boom_password(_share: object) -> object:
        raise AssertionError("must not read System keychain when the volume is already mounted")

    monkeypatch.setattr("mosaicwave.storage.smbpath._with_password", boom_password)
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.save_smb_login", lambda _s: saved.append("save")
    )
    from mosaicwave.storage.smbpath import _run_mount_smbfs

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser")
    assert _run_mount_smbfs(share, dest) is True
    assert saved == []


def test_run_mount_does_not_unmount_shared_unlistable_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = str(tmp_path / "mosaicWave-fileserver-Public")
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda p: p == dest)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: False)

    def boom_unmount(_dest: str) -> None:
        raise AssertionError("must not unmount a dest another process may be using")

    monkeypatch.setattr("mosaicwave.storage.smbpath._unmount_dest", boom_unmount)
    from mosaicwave.storage.smbpath import _run_mount_smbfs

    share = RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret")
    assert _run_mount_smbfs(share, dest) is False


def test_try_mount_existing_does_not_save_without_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "mosaicWave-dev-fileserver-Public"
    dest.mkdir()
    saved: list[str] = []
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.locate_accessible_share_root",
        lambda _s, **_k: str(dest),
    )
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath.save_smb_login", lambda _s: saved.append("save")
    )

    def boom_mount(*_a: object, **_k: object) -> bool:
        raise AssertionError("must not remount")

    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", boom_mount)
    from mosaicwave.storage.smbpath import try_mount_share

    got = try_mount_share(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser"), wait_gui=0)
    assert got == dest / "mosaicWave"
    assert saved == []


def test_save_smb_login_overwrites_nsmb_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "nsmb.conf"
    conf.write_text("[fileserver]\nusername=olduser\n", encoding="utf-8")
    monkeypatch.setattr("mosaicwave.storage.smbpath._nsmb_conf_files", lambda: [conf])
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", lambda _s: True)
    from mosaicwave.storage.smbpath import RemoteShare, save_smb_login

    save_smb_login(RemoteShare("fileserver", "Public", "mosaicWave", "nasuser", "secret"))
    text = conf.read_text(encoding="utf-8")
    assert "username=nasuser" in text
    assert "username=olduser" not in text
    assert "password=" not in text


def test_prepare_smb_url_does_not_write_nsmb(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_nsmb(*_a: object, **_k: object) -> None:
        raise AssertionError("nsmb too early")

    def boom_keychain(*_a: object, **_k: object) -> None:
        raise AssertionError("keychain too early")

    monkeypatch.setattr("mosaicwave.storage.smbpath._write_nsmb_conf", boom_nsmb)
    monkeypatch.setattr("mosaicwave.storage.smbpath.ensure_system_keychain", boom_keychain)
    from mosaicwave.storage.smbpath import prepare_smb_url

    assert (
        prepare_smb_url("smb://nasuser@fileserver/Public/mosaicWave", user="nasuser", password="secret")
        == "smb://fileserver/Public/mosaicWave"
    )


def test_redact_hides_encoded_password_in_mount_fallback() -> None:
    from mosaicwave.storage.smbpath import _redact_text, _smb_escape

    pw = "p@ss:w/ord"
    spec = f"//nasuser:{_smb_escape(pw)}@fileserver/Public"
    raw = f"mount_smbfs -N -s {spec} exit 0"
    out = _redact_text(raw, password=pw, spec=spec)
    assert pw not in out
    assert _smb_escape(pw) not in out
    assert "***" in out
    out2 = _redact_text(raw, password=pw)
    assert _smb_escape(pw) not in out2
    assert "***" in out2


def test_wait_dest_ready_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    n = {"i": 0}

    def fake_smbfs(_p: str) -> bool:
        n["i"] += 1
        return n["i"] >= 3

    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.ismount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", fake_smbfs)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: True)
    monkeypatch.setattr("mosaicwave.storage.smbpath.time.sleep", lambda _d: None)
    from mosaicwave.storage.smbpath import _wait_dest_ready

    assert _wait_dest_ready("/Volumes/x", tries=5, delay=0.01) is True


def test_wait_dest_ready_default_tolerates_a_slow_nas() -> None:
    """A slow mount_smbfs handshake needs more than a couple of seconds."""
    import inspect

    from mosaicwave.storage.smbpath import _wait_dest_ready

    tries = inspect.signature(_wait_dest_ready).parameters["tries"].default
    delay = inspect.signature(_wait_dest_ready).parameters["delay"].default
    assert tries * delay >= 20


def test_mount_smbfs_cmd_is_writable() -> None:
    from mosaicwave.storage.smbpath import _mount_smbfs_cmd

    cmd = _mount_smbfs_cmd("//nasuser:***@fileserver/Public", "/Volumes/mosaicWave-fileserver-Public")
    assert cmd[:4] == ["/sbin/mount_smbfs", "-N", "-s", "-f"]
    assert "0777" in cmd
    assert "nostreams,noquarantine,nobrowse" in cmd


def test_prepare_mount_dest_recreates_dir(tmp_path: Path) -> None:
    dest = tmp_path / "mosaicWave-fileserver-Public"
    from mosaicwave.storage.smbpath import _prepare_mount_dest

    assert _prepare_mount_dest(str(dest)) == ""
    assert dest.is_dir()
    dest.rmdir()
    assert _prepare_mount_dest(str(dest)) == ""
    assert dest.is_dir()


def test_prepare_mount_dest_keeps_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "mosaicWave-dev"
    dest.mkdir()
    (dest / ".DS_Store").write_text("", encoding="utf-8")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    from mosaicwave.storage.smbpath import _prepare_mount_dest

    assert _prepare_mount_dest(str(dest)) == ""
    assert dest.is_dir()
    assert not (dest / ".DS_Store").exists()


def test_prepare_mount_dest_keeps_mosaicwave_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "mosaicWave-fileserver-Public"
    (dest / "mosaicWave").mkdir(parents=True)
    (dest / ".DS_store").write_bytes(b"x")
    stash_to = tmp_path / "stash"
    stash_to.mkdir()
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._stash_parent", lambda: str(stash_to))
    from mosaicwave.storage.smbpath import _prepare_mount_dest

    err = _prepare_mount_dest(str(dest))
    assert "already has mosaicWave files" in err
    assert (dest / "mosaicWave").is_dir()
    assert not (dest / ".DS_store").exists()
    assert list(stash_to.iterdir()) == []


def test_prepare_mount_dest_volumes_permission_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = "/Volumes/mosaicWave-fileserver-Public"
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.geteuid", lambda: 501)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.exists", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.isdir", lambda _p: False)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError(13, "Permission denied")

    def sudo_fail(*_a: object, **_k: object) -> object:
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "sudo: a password is required"})()

    monkeypatch.setattr("mosaicwave.storage.smbpath.os.makedirs", boom)
    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", sudo_fail)
    from mosaicwave.storage.smbpath import _prepare_mount_dest

    err = _prepare_mount_dest(dest)
    assert "could not create" in err
    assert "sudo mkdir -p" in err
    assert dest in err


def test_prepare_mount_dest_volumes_sudo_n(monkeypatch: pytest.MonkeyPatch) -> None:
    dest = "/Volumes/mosaicWave-dev"
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setattr("mosaicwave.storage.smbpath._dest_is_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda _p: False)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.geteuid", lambda: 501)
    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.exists", lambda _p: False)
    made = {"ok": False}
    seen: list[list[str]] = []

    def isdir(_p: str) -> bool:
        return made["ok"]

    def boom(*_a: object, **_k: object) -> None:
        raise OSError(13, "Permission denied")

    def sudo_ok(cmd: list[str], **_k: object) -> object:
        seen.append(list(cmd))
        if "/bin/mkdir" in cmd:
            made["ok"] = True
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("mosaicwave.storage.smbpath.os.path.isdir", isdir)

    monkeypatch.setattr("mosaicwave.storage.smbpath.os.makedirs", boom)
    monkeypatch.setattr("mosaicwave.storage.smbpath.subprocess.run", sudo_ok)
    from mosaicwave.storage.smbpath import _prepare_mount_dest

    assert _prepare_mount_dest(dest) == ""
    assert seen[0][:4] == ["sudo", "-n", "/bin/mkdir", "-p"]
    assert seen[0][-1] == dest
    assert seen[1][:4] == ["sudo", "-n", "/bin/chmod", "0777"]


def test_darwin_volume_prefix_dev_and_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from mosaicwave.storage.smbpath import RemoteShare, _darwin_unique_dest

    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "dev")
    assert _darwin_unique_dest(RemoteShare("fileserver", "Public", "mosaicWave")) == (
        "/Volumes/mosaicWave-dev-fileserver-Public"
    )
    monkeypatch.setenv("MOSAICWAVE_PROFILE", "prod")
    assert _darwin_unique_dest(RemoteShare("fileserver", "Public", "mosaicWave")) == (
        "/Volumes/mosaicWave-fileserver-Public"
    )


def test_darwin_unique_dest_candidates_are_numbered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    from mosaicwave.storage.smbpath import (
        RemoteShare,
        _darwin_unique_dest,
        _darwin_unique_dest_candidates,
    )

    share = RemoteShare("fileserver", "Public", "mosaicWave")
    primary = _darwin_unique_dest(share)
    assert _darwin_unique_dest_candidates(share, limit=3) == [
        primary,
        f"{primary}-2",
        f"{primary}-3",
    ]


def test_try_mount_falls_back_to_numbered_dest_when_primary_collides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary dest is mounted by something else and unreadable here. Never unmount it —
    try the next numbered dest instead, and leave an FYI notice behind."""
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    from mosaicwave.storage.smbpath import (
        RemoteShare,
        _darwin_unique_dest,
        last_mount_notice,
        try_mount_share,
    )

    share = RemoteShare("fileserver", "Public", "mosaicWave")
    primary = _darwin_unique_dest(share)
    fallback = f"{primary}-2"
    mounted: set[str] = {primary}
    unreadable: set[str] = {primary}

    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda p: p in mounted)
    monkeypatch.setattr(
        "mosaicwave.storage.smbpath._service_can_access",
        lambda p: p in mounted and p not in unreadable,
    )
    attempted: list[str] = []

    def fake_run_mount(_share: object, dest: str) -> bool:
        attempted.append(dest)
        if dest in mounted:
            return False  # already occupied, matches real _run_mount_smbfs giving up
        mounted.add(dest)
        return True

    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", fake_run_mount)
    monkeypatch.setattr("mosaicwave.storage.smbpath._wait_dest_ready", lambda _p, **_k: True)

    got = try_mount_share(share, wait_gui=0)
    assert got is not None
    assert str(got).replace("\\", "/") == f"{fallback}/mosaicWave"
    assert attempted == [primary, fallback]
    notice = last_mount_notice()
    assert primary in notice
    assert fallback in notice


def test_try_mount_explicit_point_does_not_fall_back_on_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicitly typed mount point never silently redirects to another folder."""
    monkeypatch.setattr("mosaicwave.storage.smbpath.sys.platform", "darwin")
    dest = str(tmp_path / "mw-mnt")
    monkeypatch.setattr("mosaicwave.storage.smbpath._darwin_mounts", lambda: [])
    monkeypatch.setattr("mosaicwave.storage.smbpath._is_smbfs_mount", lambda p: p == dest)
    monkeypatch.setattr("mosaicwave.storage.smbpath._service_can_access", lambda _p: False)
    attempted: list[str] = []

    def fake_run_mount(_share: object, point: str) -> bool:
        attempted.append(point)
        return False

    monkeypatch.setattr("mosaicwave.storage.smbpath._run_mount_smbfs", fake_run_mount)
    from mosaicwave.storage.smbpath import try_mount_share

    got = try_mount_share(
        RemoteShare("fileserver", "Public", "mosaicWave"), wait_gui=0, mount_point=dest
    )
    assert got is None
    assert attempted == [dest]


def test_parse_mount_accepts_bootstrap_mnt() -> None:
    from mosaicwave.storage.smbpath import _parse_mount_stdout

    line = (
        "//nasuser@fileserver/Public on "
        "/Users/me/Library/Application Support/mosaicWave-dev-mnt/mosaicWave-fileserver-Public "
        "(smbfs, nodev, nosuid)"
    )
    assert _parse_mount_stdout(line) == [
        (
            "fileserver",
            "Public",
            "/Users/me/Library/Application Support/mosaicWave-dev-mnt/mosaicWave-fileserver-Public",
        )
    ]
