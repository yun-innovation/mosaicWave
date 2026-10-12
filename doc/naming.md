# Naming

Short product and path names for docs, packages, and agents. API and SQLite names live in [architecture.md](architecture.md) and [schema.md](schema.md) — do not restate them here. Personal / lab identifiers: [`.cursor/rules/no-personal-info.mdc`](../.cursor/rules/no-personal-info.mdc).

## Product string

| Context | Form |
| --- | --- |
| Display / docs / package display name | `mosaicWave` (camelW) |
| Python import package | `mosaicwave` (lowercase) |
| GitHub repo | `yun-innovation/mosaicWave` |
| QPKG `QPKG_NAME` / MSI product name | `mosaicWave` |
| MSI Manufacturer / `QPKG_AUTHOR` | company name only (Yun Innovation Technology Limited) |

Do not invent `MosaicWave`, `mosaic-wave`, or `Mosaic Wave` as identifiers.

## Data directories

| Host | Packaged | Debug |
| --- | --- | --- |
| Windows | `%PROGRAMDATA%\mosaicWave` | `%PROGRAMDATA%\mosaicWave-dev` |
| QNAP | `{volume}/.mosaicWave` | (same app; use packaged) |
| Linux | `~/.local/share/mosaicWave` | `~/.local/share/mosaicWave-dev` |
| macOS | `/Library/Application Support/mosaicWave` | `/Library/Application Support/mosaicWave-dev` |

Env override: `MOSAICWAVE_DATA_DIR` wins over Settings / `config.json`.

## macOS SMB mount examples (fictional)

- Share URL: `smb://fileserver/Public/mosaicWave`
- Unique volume: `/Volumes/mosaicWave-fileserver-Public`
- Debug unique volume: `/Volumes/mosaicWave-dev-fileserver-Public`

## Env and config keys

- Env: `MOSAICWAVE_DATA_DIR`, `MOSAICWAVE_PROFILE` (`dev` / `prod`), `MOSAICWAVE_PORT`, `MOSAICWAVE_WEB_ROOT`
- `config.json` (examples): `data_dir_smb`, `data_dir_mount`, `web.port` — see Settings / [smb-paths.md](smb-paths.md)

## Scripts and packages

- Dispatch: `scripts\publish.cmd` / `pack.cmd` with `msi` \| `qpkg` \| `posix`
- Library wipe: `wipe` (`wipe.cmd`, `wipe.sh`, `mosaicWave.sh wipe`) — not `init-db` / `init-data`
- Releases: `vMAJOR.MINOR.PATCH`; build installers from a clean checkout of the tag

## Git branches

`feature/<topic>`, `fix/<topic>`, `docs/<topic>`. Dependabot keeps its own names. Never commit on `main` ([process.md](process.md)).
