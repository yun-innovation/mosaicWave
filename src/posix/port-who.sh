#!/bin/sh
# Show whichever process is listening on mosaicWave's HTTP port.
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
list_listeners "${PORT}" || true
warn_respawn
