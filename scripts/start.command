#!/bin/sh
# Finder double-click: same as Windows scripts\start.cmd
cd "$(dirname "$0")" || exit 1
/bin/sh ./start.sh
status=$?
printf '\nPress Enter to close...'
read dummy || true
exit "${status}"
