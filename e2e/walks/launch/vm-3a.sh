#!/usr/bin/env bash
# H16: the vm-3a walk group (groups.json). Usage: vm-3a.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" vm-3a "$@"
