#!/bin/sh
# Run qbuild on dist/qpkg/mosaicWave. Requires QDK. See doc/qpkg.md.
# No args: every shipped arch. One or more args: only those (QDK names, or x64 / arm64 aliases).
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE="${ROOT}/dist/qpkg/mosaicWave"
# Product QPKGs. Older QDK names (arm-x19, x86, …) are valid if you pass them explicitly.
DEFAULT_ARCHS="x86_64 arm_64"

normalize_arch() {
  case "$1" in
    x86_64|amd64|x64) printf '%s\n' x86_64 ;;
    arm_64|arm64|aarch64) printf '%s\n' arm_64 ;;
    arm-x09|arm-x19|arm-x31|arm-x41|x86|x86_ce53xx) printf '%s\n' "$1" ;;
    *) return 1 ;;
  esac
}

if [ ! -f "${STAGE}/qpkg.cfg" ]; then
  echo "QPKG staging missing: ${STAGE}"
  echo "Run: sh scripts/publish-qpkg.sh   (or .\\scripts\\publish-qpkg.cmd on Windows)"
  exit 1
fi

if ! command -v qbuild >/dev/null 2>&1; then
  echo "qbuild not found. Install QDK in this environment (WSL .deb or NAS QPKG)."
  echo "See doc/qpkg.md"
  exit 1
fi

if [ "$#" -eq 0 ]; then
  # shellcheck disable=SC2086
  set -- ${DEFAULT_ARCHS}
fi

ARCHS=""
for raw in "$@"; do
  if ! arch=$(normalize_arch "${raw}"); then
    echo "Unknown QPKG arch: ${raw}"
    echo "Shipped by default: ${DEFAULT_ARCHS}"
    echo "Or pass a QDK name: arm-x09 arm-x19 arm-x31 arm-x41 arm_64 x86 x86_ce53xx x86_64"
    exit 1
  fi
  ARCHS="${ARCHS} ${arch}"
done

cd "${STAGE}"
# Belt-and-suspenders: CR in qpkg.cfg makes QPKG_NAME/VER include ^M (bad filename, no icons).
for f in qpkg.cfg package_routines shared/mosaicWave.sh shared/requirements.txt; do
  if [ -f "${STAGE}/${f}" ]; then
    tr -d '\r' < "${STAGE}/${f}" > "${STAGE}/${f}.lf" && mv "${STAGE}/${f}.lf" "${STAGE}/${f}"
  fi
done
chmod +x "${STAGE}/shared/mosaicWave.sh"

for ARCH in ${ARCHS}; do
  echo "qbuild --build-arch ${ARCH} in ${STAGE}"
  qbuild --build-arch "${ARCH}"
done
echo "Look for .qpkg under ${STAGE}/build/"
