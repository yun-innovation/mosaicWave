#!/bin/sh
# Start FastAPI + Next.js in this terminal (debug ports 8000 / 3000).
# On Mac this re-execs with sudo so Save folder can mount_smbfs under /Volumes.
set -e
SCRIPTS_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
. "${SCRIPTS_DIR}/_dev.sh"
require_darwin_root "${SCRIPTS_DIR}/start.sh"

API_PID=""
WEB_PID=""
CLEANED=0

cleanup() {
    [ "${CLEANED}" = 1 ] && return
    CLEANED=1
    trap - INT TERM EXIT
    if [ -n "${API_PID}" ]; then
        pkill -P "${API_PID}" 2>/dev/null || true
        kill "${API_PID}" 2>/dev/null || true
    fi
    if [ -n "${WEB_PID}" ]; then
        pkill -P "${WEB_PID}" 2>/dev/null || true
        kill "${WEB_PID}" 2>/dev/null || true
    fi
    stop_mosaic_api quiet
    stop_mosaic_web quiet
}

ensure_api
ensure_web
stop_existing_dev_servers api web

echo "API  port 8000 (address from the admin Server page, default 127.0.0.1; see the 'bind' line below)"
echo "Web  http://${DEV_HOST}:3000"
if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
    echo "Mac debug API is root (SMB /Volumes). Ctrl+C stops both."
    if [ -f /Library/LaunchDaemons/com.mosaicwave.app.plist ]; then
        echo "Packaged app may still be at http://127.0.0.1:8090 (prod). Use this window's :3000 for debug."
    fi
else
    echo "Ctrl+C stops both."
fi
echo ""

trap cleanup INT TERM EXIT
start_debug_api
(
    cd "${WEB_DIR}" || exit 1
    exec_as_invoker npm run dev -- --hostname "${DEV_HOST}"
) &
WEB_PID=$!
set +e
while :; do
    if ! kill -0 "${WEB_PID}" 2>/dev/null; then
        break
    fi
    if ! kill -0 "${API_PID}" 2>/dev/null; then
        if consume_debug_restart_flag; then
            echo "Restarting API after Save folder..."
            stop_mosaic_api quiet
            wait_port_free 8000 || true
            start_debug_api
            # The API address may have changed (admin Network page): move the web dev server with it.
            OLD_HOST="${DEV_HOST}"
            resolve_dev_host
            if [ "${DEV_HOST}" != "${OLD_HOST}" ]; then
                echo "Web  moving to http://${DEV_HOST}:3000"
                pkill -P "${WEB_PID}" 2>/dev/null || true
                kill "${WEB_PID}" 2>/dev/null || true
                stop_mosaic_web quiet
                wait_port_free 3000 || true
                (
                    cd "${WEB_DIR}" || exit 1
                    exec_as_invoker npm run dev -- --hostname "${DEV_HOST}"
                ) &
                WEB_PID=$!
            fi
            continue
        fi
        break
    fi
    sleep 1
done
cleanup
exit 0
