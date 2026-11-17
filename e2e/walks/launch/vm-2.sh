#!/usr/bin/env bash
# H16: the vm-2 walk group (groups.json). Usage: vm-2.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" vm-2 "$@"
