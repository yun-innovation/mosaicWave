#!/bin/sh
# Install mosaicWave standalone (Linux / macOS). Run from the unpacked tree.
# Linux default: ~/.local/lib/mosaicWave  (no sudo)
# macOS default: /Applications/mosaicWave.app (sudo). Data: /Library/Application Support/mosaicWave
#   sh install.sh
#   sudo sh install.sh
#   sh install.sh --prefix /opt/mosaicWave
#   sh install.sh --systemd        (Linux user unit)
#   sudo sh install.sh --launchd   (macOS LaunchDaemon; default on Darwin)
#   sudo sh install.sh --no-launchd
set -e
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
uname_s=$(uname -s 2>/dev/null || echo Linux)
PORT=""
DO_SYSTEMD=0
DO_LAUNCHD=0
if [ "${uname_s}" = Darwin ]; then
    PREFIX="/Applications/mosaicWave.app"
    DO_LAUNCHD=1
else
    PREFIX="${HOME}/.local/lib/mosaicWave"
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix)
            PREFIX=$2
            shift 2
            ;;
        --port)
            PORT=$2
            shift 2
            ;;
        --systemd)
            DO_SYSTEMD=1
            shift
            ;;
        --launchd)
            DO_LAUNCHD=1
            shift
            ;;
        --no-launchd)
            DO_LAUNCHD=0
            shift
            ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 1
            ;;
    esac
done

needs_root() {
    case "${PREFIX}" in
        /Applications|/Applications/*|/Library|/Library/*|/usr/*|/opt/*)
            return 0
            ;;
    esac
    return 1
}

if needs_root && [ "$(id -u)" -ne 0 ]; then
    echo "This install path requires root: ${PREFIX}" >&2
    echo "Re-run: sudo sh install.sh" >&2
    exit 1
fi

write_macos_launcher() {
    dest="${PREFIX}/Contents/MacOS/mosaicWave"
    mkdir -p "${PREFIX}/Contents/MacOS"
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/mwlaunch.XXXXXX")
    src="${HERE}/macos-open.c"
    machine=$(uname -m 2>/dev/null || echo arm64)
    launcher_ok() {
        [ -f "${dest}" ] || return 1
        if [ "${machine}" = arm64 ]; then
            file "${dest}" 2>/dev/null | grep -q arm64
            return $?
        fi
        return 0
    }
    # Do not run /usr/bin/cc unless CLT is installed (the stub can ask for Rosetta).
    if xcode-select -p >/dev/null 2>&1 && [ -f "${src}" ] && command -v cc >/dev/null 2>&1; then
        if [ "${machine}" = arm64 ] && cc -Os -arch arm64 -o "${dest}" "${src}" 2>/dev/null && launcher_ok; then
            chmod +x "${dest}"
            rm -rf "${tmp}"
            return 0
        fi
        if cc -Os -arch arm64 -arch x86_64 -o "${dest}" "${src}" 2>/dev/null && launcher_ok; then
            chmod +x "${dest}"
            rm -rf "${tmp}"
            return 0
        fi
        if cc -Os -o "${dest}" "${src}" 2>/dev/null && launcher_ok; then
            chmod +x "${dest}"
            rm -rf "${tmp}"
            return 0
        fi
        rm -f "${dest}"
    fi
    if command -v osacompile >/dev/null 2>&1; then
        if osacompile -o "${tmp}/OpenUI.app" -e 'open location "http://127.0.0.1:8090/"' >/dev/null 2>&1 \
            && [ -f "${tmp}/OpenUI.app/Contents/MacOS/applet" ]; then
            cp "${tmp}/OpenUI.app/Contents/MacOS/applet" "${dest}"
            chmod +x "${dest}"
            if launcher_ok; then
                rm -rf "${tmp}"
                return 0
            fi
            rm -f "${dest}"
        fi
    fi
    if [ -f "${HERE}/macos-open.sh" ]; then
        tr -d '\r' < "${HERE}/macos-open.sh" > "${dest}"
        chmod +x "${dest}"
    fi
    rm -rf "${tmp}"
}

# Finder shows a generic folder unless Contents/Info.plist + a native .icns exist.
# Pillow .icns is a fallback; iconutil on the Mac is what Finder trusts.
write_macos_bundle() {
    [ "${uname_s}" = Darwin ] || return 0
    mkdir -p "${PREFIX}/Contents/MacOS" "${PREFIX}/Contents/Resources"
    printf 'APPL????' > "${PREFIX}/Contents/PkgInfo"
    if [ -f "${HERE}/macos-Info.plist" ]; then
        tr -d '\r' < "${HERE}/macos-Info.plist" > "${PREFIX}/Contents/Info.plist"
    fi
    write_macos_launcher
    icns_out="${PREFIX}/Contents/Resources/mosaicWave.icns"
    png="${HERE}/mosaicWave-icon.png"
    made=0
    if [ -f "${png}" ] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        setdir=$(mktemp -d "${TMPDIR:-/tmp}/mwicon.XXXXXX")
        iconset="${setdir}/icon.iconset"
        mkdir -p "${iconset}"
        if sips -z 16 16 "${png}" --out "${iconset}/icon_16x16.png" >/dev/null 2>&1 \
            && sips -z 32 32 "${png}" --out "${iconset}/icon_16x16@2x.png" >/dev/null 2>&1 \
            && sips -z 32 32 "${png}" --out "${iconset}/icon_32x32.png" >/dev/null 2>&1 \
            && sips -z 64 64 "${png}" --out "${iconset}/icon_32x32@2x.png" >/dev/null 2>&1 \
            && sips -z 128 128 "${png}" --out "${iconset}/icon_128x128.png" >/dev/null 2>&1 \
            && sips -z 256 256 "${png}" --out "${iconset}/icon_128x128@2x.png" >/dev/null 2>&1 \
            && sips -z 256 256 "${png}" --out "${iconset}/icon_256x256.png" >/dev/null 2>&1 \
            && sips -z 512 512 "${png}" --out "${iconset}/icon_256x256@2x.png" >/dev/null 2>&1 \
            && sips -z 512 512 "${png}" --out "${iconset}/icon_512x512.png" >/dev/null 2>&1 \
            && sips -z 1024 1024 "${png}" --out "${iconset}/icon_512x512@2x.png" >/dev/null 2>&1 \
            && iconutil -c icns "${iconset}" -o "${icns_out}" >/dev/null 2>&1; then
            made=1
        fi
        rm -rf "${setdir}"
    fi
    if [ "${made}" -eq 0 ] && [ -f "${HERE}/mosaicWave.icns" ]; then
        cp "${HERE}/mosaicWave.icns" "${icns_out}"
        made=1
    fi
    if [ -f "${png}" ]; then
        cp "${png}" "${PREFIX}/Contents/Resources/mosaicWave-icon.png"
        icon_src="${png}"
    elif [ "${made}" -eq 1 ]; then
        icon_src="${icns_out}"
    else
        icon_src=""
    fi
    if [ -n "${icon_src}" ]; then
        seticon() {
            osascript - "$1" "$2" <<'APPLESCRIPT' >/dev/null 2>&1 || true
use framework "AppKit"
on run argv
  set iconPath to item 1 of argv
  set targetPath to item 2 of argv
  set img to current application's NSImage's alloc()'s initWithContentsOfFile:iconPath
  if img is missing value then return
  current application's NSWorkspace's sharedWorkspace()'s setIcon:img forFile:targetPath options:0
end run
APPLESCRIPT
        }
        seticon "${icon_src}" "${PREFIX}"
        if [ -n "${SUDO_USER}" ] && [ "${SUDO_USER}" != root ]; then
            sudo -u "${SUDO_USER}" osascript - "${icon_src}" "${PREFIX}" <<'APPLESCRIPT' >/dev/null 2>&1 || true
use framework "AppKit"
on run argv
  set iconPath to item 1 of argv
  set targetPath to item 2 of argv
  set img to current application's NSImage's alloc()'s initWithContentsOfFile:iconPath
  if img is missing value then return
  current application's NSWorkspace's sharedWorkspace()'s setIcon:img forFile:targetPath options:0
end run
APPLESCRIPT
        fi
    fi
    xattr -dr com.apple.quarantine "${PREFIX}" 2>/dev/null || true
    lsreg="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if [ -x "${lsreg}" ]; then
        "${lsreg}" -f "${PREFIX}" >/dev/null 2>&1 || true
    fi
    touch "${PREFIX}" "${PREFIX}/Contents/Info.plist" 2>/dev/null || true
}

for need in mosaicWave.sh requirements.txt server/mosaicwave web/index.html; do
    if [ ! -e "${HERE}/${need}" ]; then
        echo "missing ${need} (run publish-posix first, or unpack the tarball)" >&2
        exit 1
    fi
done

mkdir -p "${PREFIX}"
# Copy payload; do not follow into a nested dest if PREFIX is inside HERE.
if [ "${HERE}" != "${PREFIX}" ]; then
    rm -rf "${PREFIX}/server" "${PREFIX}/web"
    mkdir -p "${PREFIX}/server" "${PREFIX}/web"
    cp -R "${HERE}/server/mosaicwave" "${PREFIX}/server/"
    cp -R "${HERE}/web/." "${PREFIX}/web/"
    cp "${HERE}/requirements.txt" "${PREFIX}/"
    tr -d '\r' < "${HERE}/mosaicWave.sh" > "${PREFIX}/mosaicWave.sh"
    chmod +x "${PREFIX}/mosaicWave.sh"
    if [ -f "${HERE}/uninstall.sh" ]; then
        tr -d '\r' < "${HERE}/uninstall.sh" > "${PREFIX}/uninstall.sh"
        chmod +x "${PREFIX}/uninstall.sh"
    fi
    for extra_sh in _port.sh port-who.sh port-kill.sh wipe.sh; do
        if [ -f "${HERE}/${extra_sh}" ]; then
            tr -d '\r' < "${HERE}/${extra_sh}" > "${PREFIX}/${extra_sh}"
        fi
    done
    chmod +x "${PREFIX}/port-who.sh" "${PREFIX}/port-kill.sh" "${PREFIX}/wipe.sh" 2>/dev/null || true
    for extra in mosaicWave.service com.mosaicwave.app.plist; do
        if [ -f "${HERE}/${extra}" ]; then
            tr -d '\r' < "${HERE}/${extra}" > "${PREFIX}/${extra}"
        fi
    done
fi
write_macos_bundle
if [ ! -f "${PREFIX}/web/index.html" ]; then
    echo "missing ${PREFIX}/web/index.html (need a publish-posix tarball with the Next.js export)" >&2
    exit 1
fi
if [ "${PREFIX}" = "/Applications/mosaicWave.app" ] && [ -d /Applications/mosaicWave ]; then
    echo "Removing previous /Applications/mosaicWave"
    rm -rf /Applications/mosaicWave
fi

if [ "${uname_s}" = Darwin ] && needs_root; then
    export MOSAICWAVE_PROFILE="${MOSAICWAVE_PROFILE:-prod}"
    DATA="/Library/Application Support/mosaicWave"
    BIN="/usr/local/bin"
elif [ "${uname_s}" = Darwin ]; then
    DATA="${HOME}/Library/Application Support/mosaicWave"
    BIN="${HOME}/.local/bin"
else
    DATA="${XDG_DATA_HOME:-${HOME}/.local/share}/mosaicWave"
    BIN="${HOME}/.local/bin"
fi
mkdir -p "${DATA}" "${BIN}"
if [ "${uname_s}" = Darwin ] && needs_root; then
    # Like MSI Authenticated Users on %PROGRAMDATA%\mosaicWave\runtime.
    mkdir -p "${DATA}/runtime"
    chown root:admin "${DATA}" "${DATA}/runtime" 2>/dev/null || chown root:wheel "${DATA}" "${DATA}/runtime"
    chmod 2775 "${DATA}" "${DATA}/runtime"
fi

# LaunchDaemon runs as root (home is /var/root). Copy the installing user's
# previous library into the machine-wide folder once, like MSI ProgramData.
if [ "${uname_s}" = Darwin ] && needs_root && [ -n "${SUDO_USER}" ] && [ "${SUDO_USER}" != root ]; then
    if [ ! -f "${DATA}/library.db" ]; then
        user_home=$(eval echo "~${SUDO_USER}")
        legacy="${user_home}/Library/Application Support/mosaicWave"
        if [ -f "${legacy}/library.db" ]; then
            echo "Migrating library from ${legacy}"
            for name in library.db libraries blobs; do
                if [ -e "${legacy}/${name}" ]; then
                    cp -R "${legacy}/${name}" "${DATA}/"
                fi
            done
        fi
    fi
fi

if [ -n "${PORT}" ]; then
    "${PREFIX}/mosaicWave.sh" port "${PORT}"
fi

ln -sf "${PREFIX}/mosaicWave.sh" "${BIN}/mosaicWave"

if [ "${DO_SYSTEMD}" -eq 1 ]; then
    unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
    mkdir -p "${unit_dir}"
    sed "s|@PREFIX@|${PREFIX}|g" "${HERE}/mosaicWave.service" > "${unit_dir}/mosaicWave.service"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload
        echo "systemd user unit installed. Start: systemctl --user enable --now mosaicWave"
    else
        echo "wrote ${unit_dir}/mosaicWave.service (systemctl not found)"
    fi
fi

if [ "${DO_LAUNCHD}" -eq 1 ]; then
    if [ "${uname_s}" != Darwin ]; then
        echo "--launchd is macOS only" >&2
        exit 1
    fi
    plist_src="${HERE}/com.mosaicwave.app.plist"
    if [ "$(id -u)" -eq 0 ] && needs_root; then
        plist="/Library/LaunchDaemons/com.mosaicwave.app.plist"
        sed -e "s|@PREFIX@|${PREFIX}|g" -e "s|@DATA@|${DATA}|g" -e "s|@PROFILE@|prod|g" "${plist_src}" > "${plist}"
        chmod 644 "${plist}"
        launchctl bootout system/com.mosaicwave.app >/dev/null 2>&1 || true
        if ! launchctl bootstrap system "${plist}" >/dev/null 2>&1; then
            launchctl load -w "${plist}" >/dev/null 2>&1 || true
        fi
        echo "LaunchDaemon installed (system). Open http://127.0.0.1:8090/"
    else
        agents="${HOME}/Library/LaunchAgents"
        mkdir -p "${agents}"
        user_data="/Library/Application Support/mosaicWave-dev"
        mkdir -p "${user_data}" 2>/dev/null || true
        sed -e "s|@PREFIX@|${PREFIX}|g" -e "s|@DATA@|${user_data}|g" -e "s|@PROFILE@|debug|g" "${plist_src}" > "${agents}/com.mosaicwave.app.plist"
        echo "LaunchAgent written. Load: launchctl load ${agents}/com.mosaicwave.app.plist"
    fi
fi

echo "Installed to ${PREFIX}"
echo "Data:    ${DATA}  (uninstall does not delete this)"
if [ "${uname_s}" = Darwin ] && [ "${DO_LAUNCHD}" -eq 1 ] && needs_root; then
    echo "Service: LaunchDaemon (runs as root). Open http://127.0.0.1:8090/"
    echo "Manual:  sudo ${PREFIX}/mosaicWave.sh start"
else
    echo "Start:   ${PREFIX}/mosaicWave.sh start"
    echo "Or:      mosaicWave start   (if ${BIN} is on PATH)"
    echo "Open:    http://127.0.0.1:8090/   (Create admin)"
fi
