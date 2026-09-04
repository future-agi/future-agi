#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
eval "$(sed -n '/^port_holder() {/,/^}/p' "$script_dir/install")"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

cat >"$tmpdir/netstat" <<'EOF'
#!/usr/bin/env bash
cat <<'OUTPUT'
Active Connections
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       44576
  TCP    0.0.0.0:3001           0.0.0.0:0              ESTABLISHED     44577
OUTPUT
EOF
chmod +x "$tmpdir/netstat"

PATH="$tmpdir:/usr/bin:/bin"
export PATH

result="$(port_holder 3000)"
test "$result" = "TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       44576"
test -z "$(port_holder 3001)"
