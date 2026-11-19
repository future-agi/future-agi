#!/bin/sh
# Read-only context from the approved working directory; no network or log reads.
set -eu

printf 'Collected at (UTC): '
date -u '+%Y-%m-%dT%H:%M:%SZ'
printf 'System type: '
uname -s

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'Repository revision: '
    git rev-parse --verify HEAD
else
    printf 'Repository revision: unavailable (not in a Git worktree)\n'
fi

printf 'Review this output before sharing. No environment variables or logs were collected.\n'
