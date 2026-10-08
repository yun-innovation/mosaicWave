#!/bin/sh
# Repo wrapper. Packaged install uses the copies next to mosaicWave.sh.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
exec /bin/sh "${ROOT}/src/posix/port-kill.sh" "$@"
