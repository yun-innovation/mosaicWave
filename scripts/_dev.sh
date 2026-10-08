# Shared by start.sh / stop.sh / start-api.sh / start-web.sh. POSIX sh. Source only.
# Requires SCRIPTS_DIR (absolute) set by the caller.

REPO_ROOT=$(CDPATH= cd -- "${SCRIPTS_DIR}/.." && pwd)
SERVER_DIR="${REPO_ROOT}/src/server"
WEB_DIR="${REPO_ROOT}/src/web"
VENV_DIR="${SERVER_DIR}/.venv"
UVICORN="${VENV_DIR}/bin/uvicorn"
VENV_PYTHON="${VENV_DIR}/bin/python"

# Mac debug Save folder mounts /Volumes/… (same as LaunchDaemon). That needs root.
# Re-exec with sudo; keep the caller PATH/HOME so python and npm still resolve.
require_darwin_root() {
    [ "$(uname -s 2>/dev/null)" = Darwin ] || return 0
    [ "$(id -u)" -eq 0 ] && return 0
    script=$1
    shift
    echo "Mac debug must be root so SMB can mount under /Volumes. Re-running with sudo..."
    exec sudo PATH="${PATH}" HOME="${HOME}" MOSAICWAVE_PROFILE="${MOSAICWAVE_PROFILE:-dev}" \
        /bin/sh "${script}" "$@"
}

run_as_invoker() {
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER}" ] && [ "${SUDO_USER}" != root ]; then
        sudo -u "${SUDO_USER}" env PATH="${PATH}" HOME="${HOME}" "$@"
    else
        "$@"
    fi
}

exec_as_invoker() {
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER}" ] && [ "${SUDO_USER}" != root ]; then
        exec sudo -u "${SUDO_USER}" env PATH="${PATH}" HOME="${HOME}" "$@"
    fi
    exec "$@"
}

find_python() {
    for c in python3.14 python3.13 python3.12 python3; do
        command -v "${c}" >/dev/null 2>&1 || continue
        if "${c}" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
            echo "${c}"
            return 0
        fi
    done
    echo "Python 3.12+ not found. Install Python 3.12 and retry." >&2
    return 1
}

# This is the fixed app/bootstrap folder (config.json, session.key, library.db). The
# library (libraries/+blobs/) can be elsewhere when config.json has data_dir_smb set.
app_folder_smb_note() {
    cfg="$1/config.json"
    if [ -f "${cfg}" ] && grep -Eq '"data_dir_smb"[[:space:]]*:[[:space:]]*"[^"]+"' "${cfg}" 2>/dev/null; then
        echo " — library is on an SMB share (config.json data_dir_smb), not here"
    fi
}

ensure_api() {
    export MOSAICWAVE_PROFILE=dev
    if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
        data="/Library/Application Support/mosaicWave-dev"
        if [ "$(id -u)" -eq 0 ]; then
            mkdir -p "${data}"
            chown root:admin "${data}" 2>/dev/null || chown root:wheel "${data}"
            chmod 2775 "${data}"
        elif ! mkdir -p "${data}" 2>/dev/null || [ ! -w "${data}" ]; then
            echo "Creating ${data} (machine-wide debug library, like %PROGRAMDATA%\\mosaicWave-dev)..."
            sudo mkdir -p "${data}" || return 1
            sudo chown root:admin "${data}" 2>/dev/null || sudo chown root:wheel "${data}"
            sudo chmod 2775 "${data}"
        fi
        echo "App folder ${data}$(app_folder_smb_note "${data}")  (MOSAICWAVE_PROFILE=dev; API is root so /Volumes SMB mounts work)"
    else
        data="${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave-dev"
        echo "App folder ${data}$(app_folder_smb_note "${data}")  (MOSAICWAVE_PROFILE=dev)"
    fi
    if [ -x "${UVICORN}" ]; then
        return 0
    fi
    echo "Creating venv and installing mosaicwave..."
    py=$(find_python) || return 1
    run_as_invoker "${py}" -m venv "${VENV_DIR}"
    (
        cd "${SERVER_DIR}" || exit 1
        run_as_invoker "${VENV_DIR}/bin/pip" install -e ".[dev]"
    )
}

ensure_web() {
    if [ -d "${WEB_DIR}/node_modules" ]; then
        return 0
    fi
    echo "Installing npm packages..."
    if ! ( cd "${WEB_DIR}" && run_as_invoker npm install --no-audit --no-fund ); then
        echo "npm install failed (often TLS intercept). Retrying with --strict-ssl false."
        ( cd "${WEB_DIR}" && run_as_invoker npm install --no-audit --no-fund --strict-ssl false ) || {
            echo "npm install failed" >&2
            return 1
        }
    fi
}

listen_pids() {
    port=$1
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null || true
        return 0
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -lptn "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p'
    fi
}

matching_pids() {
    pattern=$1
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "${pattern}" 2>/dev/null || true
    fi
}

uniq_pids() {
    printf '%s\n' "$@" | grep -E '^[0-9]+$' | grep -v '^0$' | sort -u
}

stop_pids() {
    label=$1
    quiet=$2
    shift 2
    ids=$(uniq_pids "$@")
    if [ -z "${ids}" ]; then
        if [ "${quiet}" != "quiet" ]; then
            echo "No ${label} process found."
        fi
        return 0
    fi
    echo "${ids}" | while IFS= read -r pid; do
        [ -n "${pid}" ] || continue
        echo "Stopping ${label} PID ${pid}"
        pkill -P "${pid}" 2>/dev/null || true
        kill "${pid}" 2>/dev/null || true
    done
}

wait_port_free() {
    port=$1
    n=0
    while [ "${n}" -lt 25 ]; do
        leftover=$(listen_pids "${port}")
        if [ -z "${leftover}" ]; then
            return 0
        fi
        sleep 0.2
        n=$((n + 1))
    done
    leftover=$(listen_pids "${port}")
    if [ -n "${leftover}" ]; then
        echo "Port ${port} is still in use after stopping the previous process." >&2
        return 1
    fi
}

stop_mosaic_api() {
    quiet=${1:-}
    ids="$(listen_pids 8000)
$(matching_pids "mosaicwave[.]main:app")
$(matching_pids "${VENV_DIR}/bin/(python|uvicorn)")"
    stop_pids API "${quiet}" ${ids}
}

stop_mosaic_web() {
    quiet=${1:-}
    ids="$(listen_pids 3000)
$(matching_pids "${WEB_DIR}.*next")"
    stop_pids web "${quiet}" ${ids}
}

stop_existing_dev_servers() {
    want_api=$1
    want_web=$2
    if [ "${want_api}" = "api" ]; then
        busy=$(listen_pids 8000)
        stop_mosaic_api quiet
        if [ -n "${busy}" ]; then
            wait_port_free 8000 || return 1
        fi
    fi
    if [ "${want_web}" = "web" ]; then
        busy=$(listen_pids 3000)
        stop_mosaic_web quiet
        if [ -n "${busy}" ]; then
            wait_port_free 3000 || return 1
        fi
    fi
}

dev_data_dir() {
    if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
        printf '%s\n' "/Library/Application Support/mosaicWave-dev"
    else
        printf '%s\n' "${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave-dev"
    fi
}

start_debug_api() {
    (
        cd "${SERVER_DIR}" || exit 1
        exec "${UVICORN}" mosaicwave.main:app --reload --host 127.0.0.1 --port 8000
    ) &
    API_PID=$!
}

consume_debug_restart_flag() {
    flag="$(dev_data_dir)/restart.flag"
    [ -f "${flag}" ] || return 1
    rm -f "${flag}"
    return 0
}
