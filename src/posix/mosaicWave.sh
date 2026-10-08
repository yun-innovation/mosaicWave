#!/bin/sh
# mosaicWave standalone (Linux / macOS). POSIX sh. Must be LF (no CR).
# Same app as the Windows MSI: static web + uvicorn. Do not set MOSAICWAVE_DATA_DIR.

set -e
# Resolve $0 so a /usr/local/bin/mosaicWave symlink still finds /Applications/mosaicWave.app.
_script=$0
while [ -h "${_script}" ]; do
    _dir=$(CDPATH= cd -- "$(dirname "${_script}")" && pwd)
    _script=$(readlink "${_script}")
    case "${_script}" in
        /*) ;;
        *) _script="${_dir}/${_script}" ;;
    esac
done
ROOT=$(CDPATH= cd -- "$(dirname "${_script}")" && pwd)
DEFAULT_PORT=8090
NAME=mosaicWave

uname_s=$(uname -s 2>/dev/null || echo Linux)
if [ "${uname_s}" = Darwin ]; then
    # LaunchDaemon PATH is /usr/bin:/bin. Homebrew Python lives here.
    export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"
fi
case "${ROOT}" in
    /Applications/mosaicWave.app|/Applications/mosaicWave.app/*|/Applications/mosaicWave|/Applications/mosaicWave/*)
        export MOSAICWAVE_PROFILE="${MOSAICWAVE_PROFILE:-prod}"
        ;;
esac

bootstrap_dir() {
    if [ -n "${MOSAICWAVE_DATA_DIR}" ]; then
        echo "${MOSAICWAVE_DATA_DIR}"
        return
    fi
    profile=$(printf '%s' "${MOSAICWAVE_PROFILE}" | tr '[:upper:]' '[:lower:]')
    if [ "${uname_s}" = Darwin ]; then
        case "${profile}" in
            prod|production|msi)
                echo "/Library/Application Support/mosaicWave"
                return
                ;;
            dev|debug|development)
                echo "/Library/Application Support/mosaicWave-dev"
                return
                ;;
        esac
        case "${ROOT}" in
            /Applications/mosaicWave.app|/Applications/mosaicWave.app/*|/Applications/mosaicWave|/Applications/mosaicWave/*)
                echo "/Library/Application Support/mosaicWave"
                return
                ;;
        esac
        echo "/Library/Application Support/mosaicWave-dev"
        return
    fi
    case "${profile}" in
        dev|debug|development)
            echo "${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave-dev"
            return
            ;;
    esac
    echo "${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave"
}

BOOT=$(bootstrap_dir)
case "${BOOT}" in
    "/Library/Application Support/mosaicWave"|"/Library/Application Support/mosaicWave"/*|"/Library/Application Support/mosaicWave-dev"|"/Library/Application Support/mosaicWave-dev"/*)
        umask 002
        ;;
esac
RUNTIME="${BOOT}/runtime"
VENV="${RUNTIME}/venv"
PID_FILE="${BOOT}/mosaicWave.pid"
LOG_FILE="${BOOT}/mosaicWave.log"
PORT_FILE="${BOOT}/web.port"

mw_log() {
    mkdir -p "${BOOT}" 2>/dev/null || true
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "${LOG_FILE}"
    echo "$*"
}

web_port() {
    p=""
    if [ -n "${MOSAICWAVE_PORT}" ]; then
        p="${MOSAICWAVE_PORT}"
    elif [ -f "${PORT_FILE}" ]; then
        p=$(tr -d ' \t\r\n' < "${PORT_FILE}" 2>/dev/null || true)
    fi
    case "${p}" in
        ""|*[!0-9]*)
            echo "${DEFAULT_PORT}"
            ;;
        *)
            echo "${p}"
            ;;
    esac
}

python_ok() {
    [ -x "$1" ] || return 1
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null
}

find_host_python() {
    for cand in python3.12 python3.11 python3.10 python3; do
        p=$(command -v "${cand}" 2>/dev/null) || continue
        if python_ok "${p}"; then
            echo "${p}"
            return 0
        fi
    done
    for p in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        if python_ok "${p}"; then
            echo "${p}"
            return 0
        fi
    done
    return 1
}

ensure_venv() {
    HOST_PY=$(find_host_python) || {
        mw_log "No Python 3.10+ on PATH. Install python3 (Homebrew python@3.12, or the distro package)."
        return 1
    }
    if [ ! -x "${VENV}/bin/python" ]; then
        mw_log "Creating venv with ${HOST_PY} in ${VENV}"
        mkdir -p "${RUNTIME}"
        "${HOST_PY}" -m venv "${VENV}" || return 1
    fi
    VPY="${VENV}/bin/python"
    if ! "${VPY}" -c "import uvicorn" 2>/dev/null; then
        mw_log "Installing Python deps (pip)"
        "${VPY}" -m pip install --upgrade pip >> "${LOG_FILE}" 2>&1 || return 1
        "${VPY}" -m pip install --prefer-binary -r "${ROOT}/requirements.txt" >> "${LOG_FILE}" 2>&1 || {
            mw_log "pip install failed (need HTTPS to PyPI). See ${LOG_FILE}"
            return 1
        }
    fi
}

pid_alive() {
    pid=$1
    [ -n "${pid}" ] || return 1
    if kill -0 "${pid}" 2>/dev/null; then
        return 0
    fi
    # kill -0 fails for another user's process (LaunchDaemon as root).
    ps -p "${pid}" >/dev/null 2>&1
}

port_in_use() {
    p=$(web_port)
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"${p}" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi
    return 1
}

LAUNCHD_LABEL=com.mosaicwave.app
LAUNCHD_PLIST=/Library/LaunchDaemons/com.mosaicwave.app.plist

have_launchd() {
    [ "${uname_s}" = Darwin ] && [ -f "${LAUNCHD_PLIST}" ]
}

kill_port_holders() {
    p=$(web_port)
    command -v lsof >/dev/null 2>&1 || return 0
    pids=$(lsof -nP -iTCP:"${p}" -sTCP:LISTEN -t 2>/dev/null || true)
    [ -n "${pids}" ] || return 0
    echo "${pids}" | while IFS= read -r pid; do
        [ -n "${pid}" ] || continue
        kill "${pid}" 2>/dev/null || true
    done
    sleep 1
    pids=$(lsof -nP -iTCP:"${p}" -sTCP:LISTEN -t 2>/dev/null || true)
    echo "${pids}" | while IFS= read -r pid; do
        [ -n "${pid}" ] || continue
        kill -9 "${pid}" 2>/dev/null || true
    done
}

is_running() {
    if [ -f "${PID_FILE}" ]; then
        pid=$(cat "${PID_FILE}" 2>/dev/null || true)
        if pid_alive "${pid}"; then
            return 0
        fi
        rm -f "${PID_FILE}" 2>/dev/null || true
    fi
    if port_in_use; then
        return 0
    fi
    return 1
}

repair_boot_perms() {
    if [ "$(id -u)" -ne 0 ] || [ "${uname_s}" != Darwin ]; then
        return 0
    fi
    case "${BOOT}" in
        "/Library/Application Support/mosaicWave"|"/Library/Application Support/mosaicWave"/*|"/Library/Application Support/mosaicWave-dev"|"/Library/Application Support/mosaicWave-dev"/*)
            mkdir -p "${BOOT}/runtime"
            # setgid admin so new files stay group-writable (like ProgramData ACLs).
            chown root:admin "${BOOT}" "${BOOT}/runtime" 2>/dev/null || true
            chmod 2775 "${BOOT}" "${BOOT}/runtime" 2>/dev/null || true
            for f in web.port mosaicWave.pid mosaicWave.log; do
                if [ -e "${BOOT}/${f}" ]; then
                    chown root:admin "${BOOT}/${f}" 2>/dev/null || true
                    chmod 664 "${BOOT}/${f}" 2>/dev/null || true
                fi
            done
            ;;
    esac
}

boot_writable() {
    mkdir -p "${BOOT}" 2>/dev/null || true
    probe="${BOOT}/mw-write"
    touch "${probe}" 2>/dev/null || return 1
    rm -f "${probe}" 2>/dev/null || true
    return 0
}

need_boot_write() {
    repair_boot_perms
    if boot_writable; then
        return 0
    fi
    echo "${NAME}: cannot write ${BOOT}" >&2
    echo "That folder is machine-wide (like Windows %PROGRAMDATA%)." >&2
    echo "If the LaunchDaemon is already running, open http://127.0.0.1:$(web_port)/" >&2
    echo "Otherwise start as root:" >&2
    echo "  sudo \"$0\" start" >&2
    if [ -f /Library/LaunchDaemons/com.mosaicwave.app.plist ]; then
        echo "  or: sudo launchctl bootstrap system /Library/LaunchDaemons/com.mosaicwave.app.plist" >&2
    fi
    exit 1
}

export_app_env() {
    export MOSAICWAVE_PLATFORM=standalone
    export MOSAICWAVE_WEB_ROOT="${ROOT}/web"
    export PYTHONPATH="${ROOT}/server${PYTHONPATH:+:${PYTHONPATH}}"
    MOSAICWAVE_PORT=$(web_port)
    export MOSAICWAVE_PORT
    # Do not export MOSAICWAVE_DATA_DIR unless the user already set it.
}

cmd_run() {
    need_boot_write
    ensure_venv || exit 1
    export_app_env
    mkdir -p "${BOOT}"
    echo "${MOSAICWAVE_PORT}" > "${PORT_FILE}"
    if port_in_use; then
        mw_log "port ${MOSAICWAVE_PORT} already in use; not starting a second copy"
        exit 0
    fi
    if [ "${uname_s}" = Darwin ] && [ -f "${BOOT}/config.json" ] && grep -q data_dir_smb "${BOOT}/config.json" 2>/dev/null; then
        # LaunchDaemon has no login session; wait for DHCP so a guest smb:// remount can succeed.
        /usr/sbin/ipconfig waitall >/dev/null 2>&1 || true
    fi
    echo $$ > "${PID_FILE}"
    cd "${ROOT}/server"
    exec "${VENV}/bin/python" -m uvicorn mosaicwave.main:app --host 127.0.0.1 --port "${MOSAICWAVE_PORT}"
}

cmd_start() {
    if have_launchd; then
        if [ "$(id -u)" -ne 0 ]; then
            echo "LaunchDaemon is installed. Start with: sudo \"$0\" start" >&2
            exit 1
        fi
        need_boot_write
        ensure_venv || exit 1
        export_app_env
        echo "${MOSAICWAVE_PORT}" > "${PORT_FILE}"
        launchctl bootout "system/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
        kill_port_holders
        sleep 1
        if ! launchctl bootstrap system "${LAUNCHD_PLIST}" >/dev/null 2>&1; then
            launchctl load -w "${LAUNCHD_PLIST}" >/dev/null 2>&1 || true
        fi
        echo "${NAME} started (LaunchDaemon) on http://127.0.0.1:${MOSAICWAVE_PORT}/"
        return 0
    fi
    if is_running; then
        echo "${NAME} already running on http://127.0.0.1:$(web_port)/  data ${BOOT}"
        return 0
    fi
    need_boot_write
    ensure_venv || exit 1
    export_app_env
    mkdir -p "${BOOT}"
    echo "${MOSAICWAVE_PORT}" > "${PORT_FILE}"
    cd "${ROOT}/server"
    nohup "${VENV}/bin/python" -m uvicorn mosaicwave.main:app --host 127.0.0.1 --port "${MOSAICWAVE_PORT}" \
        >> "${LOG_FILE}" 2>&1 &
    echo $! > "${PID_FILE}"
    echo "${NAME} started on http://127.0.0.1:${MOSAICWAVE_PORT}/ (pid $(cat "${PID_FILE}"))"
}

cmd_stop() {
    if have_launchd && [ "$(id -u)" -ne 0 ]; then
        echo "LaunchDaemon is installed. Stop with: sudo \"$0\" stop" >&2
        exit 1
    fi
    if have_launchd; then
        launchctl bootout "system/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
    fi
    pid=""
    if [ -f "${PID_FILE}" ]; then
        pid=$(cat "${PID_FILE}" 2>/dev/null || true)
    fi
    if [ -n "${pid}" ]; then
        kill "${pid}" 2>/dev/null || true
        i=0
        while [ "${i}" -lt 20 ] && pid_alive "${pid}"; do
            i=$((i + 1))
            sleep 1
        done
        if pid_alive "${pid}"; then
            kill -9 "${pid}" 2>/dev/null || true
        fi
    fi
    kill_port_holders
    rm -f "${PID_FILE}" 2>/dev/null || true
    echo "${NAME} stopped."
}

cmd_status() {
    if is_running; then
        extra=""
        if [ -f "${PID_FILE}" ]; then
            extra=" pid $(cat "${PID_FILE}")"
        fi
        echo "${NAME} running${extra} port $(web_port) data ${BOOT}"
        return 0
    fi
    echo "${NAME} is not running. data ${BOOT}"
    return 1
}

cmd_port() {
    if [ -z "$1" ]; then
        echo "$(web_port)"
        return 0
    fi
    case "$1" in
        *[!0-9]*)
            echo "port must be an integer (1024-65535)"
            return 1
            ;;
    esac
    if [ "$1" -lt 1024 ] || [ "$1" -gt 65535 ]; then
        echo "port must be 1024-65535"
        return 1
    fi
    need_boot_write
    echo "$1" > "${PORT_FILE}"
    echo "port $1 (restart to apply)"
}

cmd_deps() {
    ensure_venv
}

cmd_wipe() {
    need_boot_write
    ensure_venv || exit 1
    export_app_env
    cd "${ROOT}/server"
    exec "${VENV}/bin/python" -m mosaicwave.wipe "$@"
}

usage() {
    echo "usage: $0 run|start|stop|restart|status|deps|port [NNNN]|wipe [--db] [--storage]"
}

case "${1:-}" in
    run) cmd_run ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status) cmd_status ;;
    deps) cmd_deps ;;
    port) shift; cmd_port "$1" ;;
    wipe) shift; cmd_wipe "$@" ;;
    *) usage; exit 1 ;;
esac
