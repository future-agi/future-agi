#!/bin/bash
# After a take: run the exact copied Claude Code install command in a throwaway project and compare
# the lock it writes with the published support-agent lock. Usage (from e2e/): demo/verify-install.sh <take>
# The command targets the stack's public origin; DEMO_CA is the local CA that signs it (demo/public-origin.mjs).
set -euo pipefail
out="$HOME/demo-out/$1"; proj=$(mktemp -d)
export CURL_CA_BUNDLE="${DEMO_CA:-$HOME/demo-tls/ca.pem}"
export FI_API_KEY=$(node -e "console.log(require('$out/state.json').owner.apiKey)")
export FI_SECRET_KEY=$(node -e "console.log(require('$out/state.json').owner.secretKey)")
cmd=$(cat "$out/install-command.txt")
case "$cmd" in *FAKE*|*"$FI_API_KEY"*|*"$FI_SECRET_KEY"*|*localhost*|*127.0.0.1*) echo "command embeds a key or a loopback address"; exit 1;; esac
(cd "$proj" && bash -c "$cmd")
find "$proj" -type f | sed "s#$proj/##" | sort
node demo/compare-lock.mjs "$out/state.json" "$proj" | tee "$out/install-verification.json"
rm -rf "$proj"
