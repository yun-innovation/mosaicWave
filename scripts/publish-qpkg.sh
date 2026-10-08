#!/bin/sh
# Assemble a QDK tree under dist/qpkg/mosaicWave. Then pack-qpkg.sh (qbuild). See doc/qpkg.md.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE="${ROOT}/dist/qpkg/mosaicWave"
WEB="${ROOT}/src/web"
SERVER="${ROOT}/src/server"
QPKG="${ROOT}/src/qpkg"

if [ ! -d "${WEB}/node_modules" ]; then
  echo "Installing npm packages..."
  if ! ( cd "${WEB}" && npm install --no-audit --no-fund ); then
    echo "npm install failed (often TLS intercept). Retrying with --strict-ssl false."
    ( cd "${WEB}" && npm install --no-audit --no-fund --strict-ssl false )
  fi
fi
echo "Building web export..."
if [ -e "${WEB}/.next" ] && ! rm -f "${WEB}/.next/types/routes.d.ts" 2>/dev/null; then
    echo "src/web/.next is not writable (sudo start.sh left root-owned files). Run: sudo rm -rf src/web/.next" >&2
    exit 1
fi
( cd "${WEB}" && npm run build )
test -f "${WEB}/out/index.html"

rm -rf "${STAGE}"
mkdir -p "${STAGE}/shared/server" "${STAGE}/shared/web"
# Strip CR: qbuild sources qpkg.cfg; a trailing CR becomes part of QPKG_NAME/VER.
for srcdst in \
  "${QPKG}/qpkg.cfg:${STAGE}/qpkg.cfg" \
  "${QPKG}/package_routines:${STAGE}/package_routines" \
  "${QPKG}/shared/mosaicWave.sh:${STAGE}/shared/mosaicWave.sh" \
  "${QPKG}/shared/wipe.sh:${STAGE}/shared/wipe.sh" \
  "${QPKG}/shared/requirements.txt:${STAGE}/shared/requirements.txt"
do
  src="${srcdst%%:*}"
  dst="${srcdst#*:}"
  tr -d '\r' < "${src}" > "${dst}"
done
chmod +x "${STAGE}/shared/mosaicWave.sh" "${STAGE}/shared/wipe.sh"
if [ -d "${QPKG}/icons" ]; then
  mkdir -p "${STAGE}/icons"
  cp -R "${QPKG}/icons/." "${STAGE}/icons/"
fi
cp -R "${SERVER}/mosaicwave" "${STAGE}/shared/server/"
find "${STAGE}/shared/server" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
cp -R "${WEB}/out/." "${STAGE}/shared/web/"

echo "QPKG staging ready: ${STAGE}"
echo "Next: sh scripts/pack-qpkg.sh  (qbuild)"
