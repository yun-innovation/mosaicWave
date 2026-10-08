#!/bin/sh
# Start Next.js dev server on http://127.0.0.1:3000 in this session.
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"

ensure_web
stop_existing_dev_servers "" web
echo "Web  http://127.0.0.1:3000"
cd "${WEB_DIR}"
exec npm run dev
