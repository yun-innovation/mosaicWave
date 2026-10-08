#!/bin/sh
# Standalone wipe, same as "mosaicWave.sh wipe" (symmetry with Windows wipe.cmd).
#   ./wipe.sh
#   ./wipe.sh --db
#   ./wipe.sh --storage
set -e
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec "${HERE}/mosaicWave.sh" wipe "$@"
