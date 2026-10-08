#!/bin/sh
# Stop FastAPI (port 8000 / mosaicwave uvicorn).
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/stop-api.sh"

echo "Stopping mosaicWave API..."
stop_mosaic_api
