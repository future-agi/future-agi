#!/usr/bin/env bash
# H16: the dryrun-int2 walk group (groups.json). Usage: dryrun.sh [plan|run|print]
exec "$(dirname "${BASH_SOURCE[0]}")/launch.sh" dryrun-int2 "$@"
