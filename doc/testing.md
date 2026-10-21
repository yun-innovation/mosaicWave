# Testing

How to run the checks by hand (there is no CI) and which tests are skipped on which OS.

## Run

| What | Command | Folder |
| --- | --- | --- |
| Server tests | `python -m pytest -q` (Windows: `.\.venv\Scripts\python.exe -m pytest -q`) | `src/server` |
| Web build (runs `i18n-check` first) | `npm run build` | `src/web` |

First-time server setup: `python3 -m venv .venv`, then `.venv/bin/pip install -e ".[dev]"` (Windows: `.\.venv\Scripts\pip.exe`). `npm run build` rewrites `src/web/next-env.d.ts` to production paths; do not commit that change (`git restore src/web/next-env.d.ts`).

Show why tests were skipped: add `-rs`.

## Expected result

| OS | Result |
| --- | --- |
| Windows | 0 failed, 15 skipped (12 of them listed below) |
| macOS / Linux | 0 failed; far fewer skips (the Windows-only tests skip themselves) |

A failure on any OS is a real finding. Do not fix it by adding a skip.

## Skipped on Windows (the former "12 known failures")

Until 2026-10-21 these failed on Windows and were carried in everyone's head as "the known 12". They are now marked `skipif(os.name == "nt")` so a failure means something. All 12 are test-environment problems, not product bugs on Windows: the code under test is macOS mounting and keychain handling, which does not run on Windows.

| Test (file) | What it needs | Windows failure |
| --- | --- | --- |
| `test_relocates_macos_legacy_user_library` (`test_config.py`) | A macOS home path | `NotImplementedError: cannot instantiate 'PosixPath'` |
| `test_is_app_volume_name_is_case_insensitive` (`test_smbpath.py`) | `/Volumes/...` strings | `'\\Volumes\\mosaicwave-dev' == '/Volumes/mosaicwave-dev'` |
| `test_app_volume_root_bare_prefix_is_top_level_only` | same | `'' == '/Volumes/mosaicWave'` |
| `test_effective_mount_dest_strips_library_folder` | same | separator `\` instead of `/` |
| `test_effective_mount_dest_keeps_user_dest` | same | `ValueError: mount point must be an absolute path` (`smbpath.py` line 1312) |
| `test_normalize_mount_rejects_finder_public` | same | error message regex does not match |
| `test_darwin_keychain_password_reads_system_keychain` | `os.geteuid` | `AttributeError: module 'os' has no attribute 'geteuid'` |
| `test_darwin_keychain_password_root_skips_login_keychain` | same | same |
| `test_ensure_system_keychain_root_writes_system_only` | same | same |
| `test_prepare_mount_dest_volumes_permission_is_error` | same | same |
| `test_prepare_mount_dest_volumes_sudo_n` | same | same |
| `test_takeout_sanitizes_colon_filename` (`test_takeout.py`) | A file named `Photo from 2:15 PM.jpg` | Windows turns `:15 PM.jpg` into an NTFS data stream of a file called `Photo from 2`, so the importer finds 0 photos (`assert 0 == 1`) |

The skip reason is printed by `-rs` and points here. The shared marker for the ten `test_smbpath.py` tests is `POSIX_ONLY` at the top of that file.

## Reproduce the failures (Windows)

The skip markers can be switched off for one run:

```
cd src/server
.\.venv\Scripts\python.exe -m pytest -q -p no:skipping tests/test_smbpath.py tests/test_config.py tests/test_takeout.py
```

Expected: `12 failed, 103 passed` with exactly the tests in the table above (checked 2026-10-21 on Windows 11, Python 3.12).

## Check on macOS (for the macOS session)

These 12 are the point of running the suite on a Mac: they test code that only runs there, and they have not been run on macOS since the skips were added.

1. `cd src/server`, create the venv (see Run), then `python -m pytest -q -rs`.
2. Expect **0 failed**. The `test_takeout_sanitizes_colon_filename` and macOS-path tests should now run (not skip).
3. Run just the twelve: `python -m pytest -q tests/test_smbpath.py tests/test_config.py::test_relocates_macos_legacy_user_library tests/test_takeout.py::test_takeout_sanitizes_colon_filename`.
4. If one fails on macOS, report the test name and the full traceback. It means either a real macOS bug or a test that was only ever correct on another setup. Do not skip it to make the run green.
5. Tests use `tmp_path`, so they do not touch `/Library/Application Support/mosaicWave`. If a test still asks for sudo or a mount, say so in the report.
6. Also run `npm run build` in `src/web` and `git restore src/web/next-env.d.ts` afterwards.

Report back: macOS version, Python version, the pass / fail / skip counts, and any failures. The Windows-side session will record it in [progress-archive.md](progress-archive.md).
