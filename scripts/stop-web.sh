#!/bin/sh
# Stop Next.js (port 3000 / src/web).
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/stop-web.sh"

echo "Stopping mosaicWave web..."
stop_mosaic_web
