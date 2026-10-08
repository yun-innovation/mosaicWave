#!/bin/sh
# Assemble Linux/macOS tree under dist/posix/mosaicWave. Then pack-posix.sh.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
STAGE="${ROOT}/dist/posix/mosaicWave"
WEB="${ROOT}/src/web"
SERVER="${ROOT}/src/server"
POSIX="${ROOT}/src/posix"

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
mkdir -p "${STAGE}/server" "${STAGE}/web"
cp -R "${SERVER}/mosaicwave" "${STAGE}/server/"
find "${STAGE}/server" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
cp -R "${WEB}/out/." "${STAGE}/web/"
cp "${ROOT}/src/qpkg/shared/requirements.txt" "${STAGE}/"
for f in mosaicWave.sh wipe.sh install.sh uninstall.sh mosaicWave.service com.mosaicwave.app.plist macos-Info.plist macos-open.sh macos-open.c _port.sh port-who.sh port-kill.sh; do
  tr -d '\r' < "${POSIX}/${f}" > "${STAGE}/${f}"
done
cp "${POSIX}/mosaicWave.icns" "${STAGE}/"
cp "${POSIX}/mosaicWave-icon.png" "${STAGE}/"
test -f "${STAGE}/mosaicWave.icns"
test -f "${STAGE}/mosaicWave-icon.png"
chmod +x "${STAGE}/mosaicWave.sh" "${STAGE}/wipe.sh" "${STAGE}/install.sh" "${STAGE}/uninstall.sh" "${STAGE}/macos-open.sh" \
  "${STAGE}/port-who.sh" "${STAGE}/port-kill.sh"

echo "POSIX staging ready: ${STAGE}"
echo "Next: sh scripts/pack-posix.sh"
