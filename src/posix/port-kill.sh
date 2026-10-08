#!/bin/sh
# Stop whichever process is listening on mosaicWave's HTTP port.
# Default 8090; override with argument, MOSAICWAVE_PORT, or web.port.
set -e
_script=$0
while [ -h "${_script}" ]; do
    _dir=$(CDPATH= cd -- "$(dirname "${_script}")" && pwd)
    _script=$(readlink "${_script}")
    case "${_script}" in
        /*) ;;
        *) _script="${_dir}/${_script}" ;;
    esac
done
HERE=$(CDPATH= cd -- "$(dirname "${_script}")" && pwd)
# shellcheck disable=SC1091
. "${HERE}/_port.sh"

resolve_listen_port "${1-}"
print_port_recap "${PORT}" "${PORT_FROM}"
echo
warn_respawn
echo

if [ "$(uname -s 2>/dev/null)" = Darwin ] && [ -f /Library/LaunchDaemons/com.mosaicwave.app.plist ]; then
    if [ "$(id -u)" -eq 0 ]; then
        echo "Stopping LaunchDaemon com.mosaicwave.app"
        launchctl bootout system/com.mosaicwave.app >/dev/null 2>&1 || true
        launchctl unload -w /Library/LaunchDaemons/com.mosaicwave.app.plist >/dev/null 2>&1 || true
        sleep 1
    else
        echo "Not root: LaunchDaemon may restart uvicorn after kill. Re-run with sudo to boot it out."
    fi
fi

if ! list_listeners "${PORT}"; then
    exit 0
fi

pids=$(listener_pids "${PORT}")
if [ -z "${pids}" ]; then
    echo "No PID to kill on ${PORT}."
    exit 0
fi

echo "${pids}" | while IFS= read -r pid; do
    [ -n "${pid}" ] || continue
    [ "${pid}" -gt 1 ] || continue
    echo "SIGTERM PID ${pid}"
    kill "${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null || true
done
sleep 1
left=$(listener_pids "${PORT}")
echo "${left}" | while IFS= read -r pid; do
    [ -n "${pid}" ] || continue
    [ "${pid}" -gt 1 ] || continue
    echo "SIGKILL PID ${pid}"
    kill -9 "${pid}" 2>/dev/null || true
done
sleep 1
if list_listeners "${PORT}" >/dev/null 2>&1; then
    echo "Port ${PORT} is still in use." >&2
    list_listeners "${PORT}" || true
    exit 1
fi
echo "Port ${PORT} is free."
