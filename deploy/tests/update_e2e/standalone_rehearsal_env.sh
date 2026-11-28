#!/usr/bin/env bash
# Environment helpers for the manual standalone rehearsal. Sourcing starts nothing.

env_set() { # file, key, value: replace the last KEY= line, or append
  python3 - "$1" "$2" "$3" <<'PY'
import os, re, sys
path, key, value = sys.argv[1:4]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
hit = None
for i, line in enumerate(lines):
    if not line.lstrip().startswith("#") and re.match(r"^\s*(?:export\s+)?%s\s*=" % re.escape(key), line):
        hit = i
if hit is None:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key}={value}\n")
else:
    lines[hit] = f"{key}={value}\n"
mode = os.stat(path).st_mode & 0o777
with open(path, "w", encoding="utf-8") as handle:
    handle.write("".join(lines))
os.chmod(path, mode)
PY
}

pin_standalone_telemetry() {
  # Compose gives exported host settings precedence over the generated .env.
  unset FUTURE_AGI_TELEMETRY_DISABLED FUTURE_AGI_TELEMETRY_URL
  env_set "$1" FUTURE_AGI_TELEMETRY_DISABLED true &&
    env_set "$1" FUTURE_AGI_TELEMETRY_URL http://127.0.0.1:9
}

check_standalone_telemetry() ( # checkout, version about to boot, compose command...
  set -o pipefail
  cd "$1" || exit 1
  local version=$2
  shift 2
  # The model contains install secrets. Only the exit status leaves this pipe.
  FUTURE_AGI_VERSION="$version" "$@" config --format json 2>/dev/null | python3 -Ic 'import json, sys
app = json.load(sys.stdin)["services"]["app"]["environment"]
sys.exit(not (app.get("FUTURE_AGI_TELEMETRY_DISABLED") == "true" and
              app.get("FUTURE_AGI_TELEMETRY_URL") == "http://127.0.0.1:9"))' 2>/dev/null
)
