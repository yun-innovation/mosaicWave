#!/bin/sh
# Tar dist/posix/mosaicWave → dist/posix/mosaicWave_0.1.0_posix.tar.gz
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE="${ROOT}/dist/posix/mosaicWave"
OUT="${ROOT}/dist/posix"
test -f "${STAGE}/mosaicWave.sh" || {
  echo "POSIX staging missing. Run: sh scripts/publish-posix.sh" >&2
  exit 1
}
TAR="${OUT}/mosaicWave_0.1.0_posix.tar.gz"
rm -f "${TAR}"
( cd "${OUT}" && tar -czf mosaicWave_0.1.0_posix.tar.gz mosaicWave )
echo "Wrote ${TAR}"
echo "On the target: tar xf mosaicWave_0.1.0_posix.tar.gz && cd mosaicWave && sh install.sh"
