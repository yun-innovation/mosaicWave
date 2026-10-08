#!/bin/sh
# Start FastAPI (reload) on http://127.0.0.1:8000 in this session.
# On Mac this re-execs with sudo so Save folder can mount_smbfs under /Volumes.
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/start-api.sh"

API_PID=""
CLEANED=0

cleanup() {
    [ "${CLEANED}" = 1 ] && return
    CLEANED=1
    trap - INT TERM EXIT
    if [ -n "${API_PID}" ]; then
        pkill -P "${API_PID}" 2>/dev/null || true
        kill "${API_PID}" 2>/dev/null || true
    fi
    stop_mosaic_api quiet
}

ensure_api
stop_existing_dev_servers api ""
echo "API  http://127.0.0.1:8000/docs"

trap cleanup INT TERM EXIT
while :; do
    start_debug_api
    set +e
    wait "${API_PID}"
    status=$?
    set -e
    if consume_debug_restart_flag; then
        echo "Restarting API after Save folder..."
        stop_mosaic_api quiet
        wait_port_free 8000 || true
        continue
    fi
    cleanup
    exit "${status}"
done
