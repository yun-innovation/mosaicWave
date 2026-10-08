#!/bin/sh
# mosaicWave QPKG start/stop. BusyBox /bin/sh — no bashisms. Must be LF (no CR).
# Same Python app as standalone: FastAPI + static export. platform=qnap.

CONF=/etc/config/qpkg.conf
QPKG_NAME="mosaicWave"
PATH="/opt/python3/bin:/opt/QPython312/bin:/opt/QPython3/bin:/opt/bin:/opt/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"
export PATH

resolve_root() {
    r=$(/sbin/getcfg "${QPKG_NAME}" Install_Path -f "${CONF}" 2>/dev/null)
    if [ -n "${r}" ] && [ -d "${r}" ]; then
        echo "${r}"
        return 0
    fi
    src=$0
    if [ -L "${src}" ]; then
        src=$(readlink "${src}")
    fi
    CDPATH= cd -- "$(dirname "${src}")" && pwd
}

QPKG_ROOT=$(resolve_root)
PID_FILE="${QPKG_ROOT}/mosaicWave.pid"
LOG_FILE="${QPKG_ROOT}/mosaicWave.log"
DEFAULT_PORT=8090

# App Center Remove deletes $QPKG_ROOT. Photos/SQLite live beside .qpkg on the volume.
persistent_data_dir() {
    root=$1
    case "${root}" in
        */.qpkg/mosaicWave)
            echo "$(dirname "$(dirname "${root}")")/.mosaicWave"
            ;;
        *)
            echo "${root}/data"
            ;;
    esac
}

migrate_install_data() {
    dest=$1
    old="${QPKG_ROOT}/data"
    [ -d "${old}" ] || return 0
    [ "${old}" = "${dest}" ] && return 0
    if [ -f "${dest}/library.db" ]; then
        return 0
    fi
    if [ ! -f "${old}/library.db" ]; then
        return 0
    fi
    mw_log "Moving library to ${dest} (survives QPKG reinstall)"
    mkdir -p "${dest}"
    for x in "${old}"/* "${old}"/.[!.]*; do
        [ -e "${x}" ] || continue
        mv "${x}" "${dest}/" || return 1
    done
    rmdir "${old}" 2>/dev/null || true
}

mw_log() {
    mkdir -p "${QPKG_ROOT}" 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "${LOG_FILE}"
    echo "$*"
}

get_web_port() {
    /sbin/getcfg "${QPKG_NAME}" Web_Port -d "${DEFAULT_PORT}" -f "${CONF}"
}

set_web_port() {
    /sbin/setcfg "${QPKG_NAME}" Web_Port "$1" -f "${CONF}"
    echo "$1" > "${QPKG_ROOT}/web.port"
}

port_busy() {
    netstat -ltn 2>/dev/null | grep -E ":${1}[[:space:]]" >/dev/null
}

next_free_port() {
    p=$1
    i=0
    while [ "${i}" -lt 50 ]; do
        if ! port_busy "${p}"; then
            echo "${p}"
            return 0
        fi
        p=$((p + 1))
        i=$((i + 1))
    done
    return 1
}

# Keep qpkg.conf Web_Port in sync with a free TCP port (QTS desktop iframe uses this).
assign_listen_port() {
    want=$(get_web_port)
    case "${want}" in
        *[!0-9]*|"") want=${DEFAULT_PORT} ;;
    esac
    if port_busy "${want}"; then
        free=$(next_free_port "${want}") || {
            mw_log "no free TCP port near ${want}"
            return 1
        }
        if [ "${free}" != "${want}" ]; then
            mw_log "port ${want} in use; using ${free}"
            set_web_port "${free}"
            if [ -x /usr/local/sbin/notify ]; then
                /usr/local/sbin/notify send -A A039 -C C001 -M 46 -l info -t 3 "[{0}] {1} web port is {2} (previous port was busy)." "App Center" "${QPKG_NAME}" "${free}"
            fi
        fi
        want=${free}
    else
        set_web_port "${want}"
    fi
    WEB_PORT=${want}
    export WEB_PORT
}

cmd_wipe() {
    if ! ensure_venv; then
        exit 1
    fi
    DATA_DIR=$(persistent_data_dir "${QPKG_ROOT}")
    mkdir -p "${DATA_DIR}"
    export MOSAICWAVE_PLATFORM=qnap
    export MOSAICWAVE_DATA_DIR="${DATA_DIR}"
    export PYTHONPATH="${QPKG_ROOT}/server"
    cd "${QPKG_ROOT}/server" || exit 1
    "${QPKG_ROOT}/venv/bin/python" -m mosaicwave.wipe "$@"
}

cmd_port() {
    if [ -z "$1" ]; then
        echo "$(get_web_port)"
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
    set_web_port "$1"
    echo "Web_Port=$1. Restart mosaicWave (App Center Disable/Enable or $0 restart)."
}

python_ok() {
    [ -x "$1" ] || return 1
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null
}

find_host_python() {
    for pkg in QPython312 Python312 Python3; do
        ip=$(/sbin/getcfg "${pkg}" Install_Path -f "${CONF}" 2>/dev/null) || continue
        [ -n "${ip}" ] || continue
        for p in \
            "${ip}/opt/python3/bin/python3" \
            "${ip}/bin/python3.12" \
            "${ip}/bin/python3" \
            "${ip}/python3"
        do
            if python_ok "${p}"; then
                echo "${p}"
                return 0
            fi
        done
    done
    for p in \
        /opt/python3/bin/python3 \
        /opt/QPython312/bin/python3 \
        /opt/QPython3/bin/python3 \
        /opt/bin/python3 \
        /usr/local/bin/python3
    do
        if python_ok "${p}"; then
            echo "${p}"
            return 0
        fi
    done
    for cand in python3.12 python3; do
        p=$(command -v "${cand}" 2>/dev/null) || continue
        if python_ok "${p}"; then
            echo "${p}"
            return 0
        fi
    done
    for p in /share/*/.qpkg/Python3/opt/python3/bin/python3; do
        if python_ok "${p}"; then
            echo "${p}"
            return 0
        fi
    done
    return 1
}

pip_env() {
    mkdir -p "${QPKG_ROOT}/tmp" "${QPKG_ROOT}/.pip-cache"
    export TMPDIR="${QPKG_ROOT}/tmp"
    export TMP="${QPKG_ROOT}/tmp"
    export PIP_CACHE_DIR="${QPKG_ROOT}/.pip-cache"
}

ensure_venv() {
    HOST_PY=$(find_host_python) || {
        mw_log "No Python 3.10+ found. Install App Center QPKG Python 3 (3.10+) first."
        return 1
    }
    if [ ! -x "${QPKG_ROOT}/venv/bin/python" ]; then
        mw_log "Creating venv with ${HOST_PY}"
        "${HOST_PY}" -m venv "${QPKG_ROOT}/venv" || {
            mw_log "python -m venv failed"
            return 1
        }
    fi
    VPY="${QPKG_ROOT}/venv/bin/python"
    if ! "${VPY}" -c "import uvicorn" 2>/dev/null; then
        mw_log "Installing Python deps (pip; cache on data volume, not /tmp)"
        pip_env
        "${VPY}" -m ensurepip --upgrade >> "${LOG_FILE}" 2>&1 || true
        if ! "${VPY}" -m pip --version >> "${LOG_FILE}" 2>&1; then
            mw_log "venv has no pip. Reinstall Python 3 QPKG or run: ${VPY} -m ensurepip"
            return 1
        fi
        "${VPY}" -m pip install --upgrade pip >> "${LOG_FILE}" 2>&1 || return 1
        "${VPY}" -m pip install --prefer-binary -r "${QPKG_ROOT}/requirements.txt" >> "${LOG_FILE}" 2>&1 || {
            mw_log "pip install failed (need HTTPS to PyPI). See ${LOG_FILE}"
            return 1
        }
    fi
    "${VPY}" -c "import uvicorn" 2>/dev/null || {
        mw_log "uvicorn missing after pip"
        return 1
    }
    return 0
}

wait_listen() {
    i=0
    while [ "${i}" -lt 30 ]; do
        if "${QPKG_ROOT}/venv/bin/python" -c "import socket; s=socket.create_connection(('127.0.0.1', int('${WEB_PORT}')), 1); s.close()" 2>/dev/null; then
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    return 1
}

is_running() {
    if [ -f "${PID_FILE}" ]; then
        pid=$(cat "${PID_FILE}")
        if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
        rm -f "${PID_FILE}"
    fi
    return 1
}

start_qpkg() {
    ENABLED=$(/sbin/getcfg ${QPKG_NAME} Enable -u -d FALSE -f ${CONF})
    if [ "${ENABLED}" != "TRUE" ]; then
        echo "${QPKG_NAME} is disabled."
        exit 1
    fi
    if is_running; then
        echo "${QPKG_NAME} is already running."
        exit 0
    fi
    if ! ensure_venv; then
        if [ -x /usr/local/sbin/notify ]; then
            /usr/local/sbin/notify send -A A039 -C C001 -M 46 -l error -t 3 "[{0}] {1} failed to start. See mosaicWave.log (Python 3.10+ and pip)." "App Center" "${QPKG_NAME}"
        fi
        if [ -x /sbin/log_tool ]; then
            /sbin/log_tool -t 2 -a "${QPKG_NAME}: start failed. See mosaicWave.log"
        fi
        exit 1
    fi
    assign_listen_port || exit 1
    PY="${QPKG_ROOT}/venv/bin/python"
    DATA_DIR=$(persistent_data_dir "${QPKG_ROOT}")
    migrate_install_data "${DATA_DIR}"
    mkdir -p "${DATA_DIR}" "${QPKG_ROOT}/tmp"
    export TMPDIR="${QPKG_ROOT}/tmp"
    export TMP="${QPKG_ROOT}/tmp"
    export OMP_NUM_THREADS=1
    export OPENBLAS_NUM_THREADS=1
    export MOSAICWAVE_PLATFORM=qnap
    export MOSAICWAVE_DATA_DIR="${DATA_DIR}"
    export MOSAICWAVE_WEB_ROOT="${QPKG_ROOT}/web"
    export MOSAICWAVE_HOST=0.0.0.0
    export MOSAICWAVE_PORT="${WEB_PORT}"
    export PYTHONPATH="${QPKG_ROOT}/server"
    cd "${QPKG_ROOT}/server" || exit 1
    # QTS init PATH has no nohup. Ignore HUP so App Center can exit without killing uvicorn.
    (
        trap '' HUP
        exec "${PY}" -m uvicorn mosaicwave.main:app --host 0.0.0.0 --port "${WEB_PORT}"
    ) >> "${LOG_FILE}" 2>&1 &
    echo $! > "${PID_FILE}"
    if ! wait_listen; then
        mw_log "uvicorn did not listen on ${WEB_PORT} within 30s"
        stop_qpkg
        if [ -x /usr/local/sbin/notify ]; then
            /usr/local/sbin/notify send -A A039 -C C001 -M 46 -l error -t 3 "[{0}] {1} failed to listen on port {2}." "App Center" "${QPKG_NAME}" "${WEB_PORT}"
        fi
        exit 1
    fi
    mw_log "${QPKG_NAME} started on port ${WEB_PORT}."
    if [ -x /sbin/log_tool ]; then
        /sbin/log_tool -t 0 -a "${QPKG_NAME} started on port ${WEB_PORT}."
    fi
}

stop_qpkg() {
    if [ ! -f "${PID_FILE}" ]; then
        echo "${QPKG_NAME} is not running."
        return 0
    fi
    pid=$(cat "${PID_FILE}")
    if [ -n "${pid}" ]; then
        kill "${pid}" 2>/dev/null
        i=0
        while [ ${i} -lt 20 ]; do
            if ! kill -0 "${pid}" 2>/dev/null; then
                break
            fi
            i=$((i + 1))
            sleep 1
        done
        kill -9 "${pid}" 2>/dev/null
    fi
    rm -f "${PID_FILE}"
    echo "${QPKG_NAME} stopped."
    if [ -x /sbin/log_tool ]; then
        /sbin/log_tool -t 0 -a "${QPKG_NAME} stopped."
    fi
}

case "$1" in
start)
    start_qpkg
    ;;
stop)
    stop_qpkg
    ;;
restart)
    stop_qpkg
    start_qpkg
    ;;
deps)
    ensure_venv || exit 1
    assign_listen_port
    ;;
port)
    cmd_port "$2" || exit 1
    ;;
wipe)
    shift
    cmd_wipe "$@" || exit 1
    ;;
status)
    if is_running; then
        echo "${QPKG_NAME} is running."
        exit 0
    fi
    echo "${QPKG_NAME} is stopped."
    exit 1
    ;;
*)
    echo "Usage: $0 {start|stop|restart|status|deps|port [n]|wipe [--db] [--storage]}"
    exit 1
    ;;
esac
