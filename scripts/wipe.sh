#!/bin/sh
# Wipe library.db and/or storage for this app's own data dir, then recreate an empty
# schema (same DDL as API startup; does not start uvicorn). No flags: wipes both.
# Default data dir: /Library/Application Support/mosaicWave-dev on macOS (MOSAICWAVE_PROFILE=dev)
# On Mac this re-execs with sudo (like start.sh) so an SMB-configured data dir can mount under /Volumes.
#   sh scripts/wipe.sh
#   sh scripts/wipe.sh --db
#   sh scripts/wipe.sh --storage
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/wipe.sh" "$@"

ensure_api
cd "${SERVER_DIR}"
exec "${VENV_PYTHON}" -m mosaicwave.wipe "$@"
