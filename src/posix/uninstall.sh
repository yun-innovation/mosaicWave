#!/bin/sh
# Remove the installed app. Does not delete the library.
set -e
uname_s=$(uname -s 2>/dev/null || echo Linux)
if [ "${uname_s}" = Darwin ]; then
    PREFIX="${1:-/Applications/mosaicWave.app}"
else
    PREFIX="${1:-${HOME}/.local/lib/mosaicWave}"
fi
case "${PREFIX}" in
    ""|"/"|"/Applications"|"/Library"|"${HOME}"|"${HOME}/")
        echo "refusing PREFIX=${PREFIX}" >&2
        exit 1
        ;;
esac
SH="${PREFIX}/mosaicWave.sh"
if [ ! -x "${SH}" ] && [ "${uname_s}" = Darwin ] && [ -x /Applications/mosaicWave/mosaicWave.sh ]; then
    SH="/Applications/mosaicWave/mosaicWave.sh"
fi
if [ -x "${SH}" ]; then
    "${SH}" stop >/dev/null 2>&1 || true
fi
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now mosaicWave >/dev/null 2>&1 || true
    rm -f "${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user/mosaicWave.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
if [ -f /Library/LaunchDaemons/com.mosaicwave.app.plist ]; then
    launchctl bootout system/com.mosaicwave.app >/dev/null 2>&1 || true
    launchctl unload /Library/LaunchDaemons/com.mosaicwave.app.plist >/dev/null 2>&1 || true
    rm -f /Library/LaunchDaemons/com.mosaicwave.app.plist
fi
if [ -f "${HOME}/Library/LaunchAgents/com.mosaicwave.app.plist" ]; then
    launchctl unload "${HOME}/Library/LaunchAgents/com.mosaicwave.app.plist" >/dev/null 2>&1 || true
    rm -f "${HOME}/Library/LaunchAgents/com.mosaicwave.app.plist"
fi
rm -f /usr/local/bin/mosaicWave
rm -f "${HOME}/.local/bin/mosaicWave"
if [ -d "${PREFIX}" ]; then
    rm -rf "${PREFIX}"
fi
if [ "${uname_s}" = Darwin ]; then
    if [ "${PREFIX}" = "/Applications/mosaicWave.app" ]; then
        rm -rf /Applications/mosaicWave
    elif [ "${PREFIX}" = "/Applications/mosaicWave" ]; then
        rm -rf /Applications/mosaicWave.app
    fi
fi
echo "Removed ${PREFIX}. Library data was not deleted."
if [ "${uname_s}" = Darwin ]; then
    echo "Data remains in /Library/Application Support/mosaicWave and /Library/Application Support/mosaicWave-dev (and any older copy under ~/Library/Application Support)."
fi
