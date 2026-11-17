#!/usr/bin/env bash
# H16: the vm-5 walk group (groups.json). Usage: vm-5.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" vm-5 "$@"
