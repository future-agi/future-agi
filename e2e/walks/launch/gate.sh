#!/usr/bin/env bash
# H16: the gate walk group (groups.json). Usage: gate.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" gate "$@"
