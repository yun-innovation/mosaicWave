#!/bin/sh
# Stop FastAPI + Next.js started by the mosaic start scripts.
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/stop.sh"

echo "Stopping mosaicWave API and web..."
stop_mosaic_api
stop_mosaic_web
