# Shared by port-who.sh / port-kill.sh. POSIX sh. Source only.

print_port_recap() {
    port=$1
    from=$2
    cat <<EOF
Listen port: ${port}  (${from})

This is not always 8090. mosaicWave picks the first that applies:
  1. Argument to this script          (sh port-who.sh 8100)
  2. MOSAICWAVE_PORT
  3. web.port in the data folder      (start / LaunchDaemon / systemd writes it)
  4. Windows installer PORT           (HKLM\\SOFTWARE\\mosaicWave)
  5. QPKG Web_Port                    (mosaicWave.sh port)
  6. Packaged default                 8090
Local debug scripts/start.cmd and scripts/start.sh use 8000, not 8090.
EOF
}

_read_port_file() {
    f=$1
    [ -f "${f}" ] || return 1
    p=$(tr -d ' \t\r\n' < "${f}" 2>/dev/null || true)
    case "${p}" in
        "" | *[!0-9]*) return 1 ;;
        *)
            echo "${p}"
            return 0
            ;;
    esac
}

resolve_listen_port() {
    PORT=""
    PORT_FROM=""
    if [ -n "$1" ]; then
        case "$1" in
            *[!0-9]*)
                echo "not a port number: $1" >&2
                return 1
                ;;
            *)
                PORT=$1
                PORT_FROM="script argument"
                return 0
                ;;
        esac
    fi
    if [ -n "${MOSAICWAVE_PORT}" ]; then
        case "${MOSAICWAVE_PORT}" in
            *[!0-9]*) ;;
            *)
                PORT=${MOSAICWAVE_PORT}
                PORT_FROM="MOSAICWAVE_PORT"
                return 0
                ;;
        esac
    fi
    for f in \
        "/Library/Application Support/mosaicWave/web.port" \
        "/Library/Application Support/mosaicWave-dev/web.port" \
        "${HOME}/Library/Application Support/mosaicWave-dev/web.port" \
        "${HOME}/Library/Application Support/mosaicWave/web.port" \
        "${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave-dev/web.port" \
        "${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave/web.port"
    do
        p=$(_read_port_file "${f}") || continue
        PORT=${p}
        PORT_FROM="web.port (${f})"
        return 0
    done
    if [ -f /etc/config/qpkg.conf ] && [ -x /sbin/getcfg ]; then
        p=$(/sbin/getcfg mosaicWave Web_Port -d "" -f /etc/config/qpkg.conf 2>/dev/null || true)
        case "${p}" in
            "" | *[!0-9]*) ;;
            *)
                PORT=${p}
                PORT_FROM="QPKG Web_Port"
                return 0
                ;;
        esac
    fi
    PORT=8090
    PORT_FROM="packaged default"
    return 0
}

list_listeners() {
    p=$1
    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"${p}" -sTCP:LISTEN 2>/dev/null; then
            return 0
        fi
        echo "No process is listening on ${p}."
        return 1
    fi
    if command -v ss >/dev/null 2>&1; then
        out=$(ss -lptn "sport = :${p}" 2>/dev/null || true)
        echo "${out}"
        case "${out}" in
            *pid=*) return 0 ;;
        esac
        echo "No process is listening on ${p}."
        return 1
    fi
    echo "Need lsof or ss to list listeners on ${p}." >&2
    return 1
}

listener_pids() {
    p=$1
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"${p}" -sTCP:LISTEN -t 2>/dev/null || true
        return 0
    fi
}

warn_respawn() {
    if [ "$(uname -s 2>/dev/null)" = Darwin ] && [ -f /Library/LaunchDaemons/com.mosaicwave.app.plist ]; then
        echo "LaunchDaemon com.mosaicwave.app is installed; killing the PID may respawn it."
        echo "Prefer: sudo /Applications/mosaicWave.app/mosaicWave.sh stop"
    fi
    if [ -f "${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user/mosaicWave.service" ]; then
        echo "A user systemd unit may restart the process. Prefer: mosaicWave.sh stop"
    fi
}
