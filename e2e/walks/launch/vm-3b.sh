#!/usr/bin/env bash
# H16: the vm-3b walk group (groups.json). Usage: vm-3b.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" vm-3b "$@"
