#!/bin/sh
# Finder double-click: same as Windows scripts\stop.cmd
cd "$(dirname "$0")" || exit 1
/bin/sh ./stop.sh
status=$?
printf '\nPress Enter to close...'
read dummy || true
exit "${status}"
