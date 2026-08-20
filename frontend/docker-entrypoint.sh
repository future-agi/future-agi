#!/bin/sh
set -eu

CONFIG_PATH="${CONFIG_PATH:-/usr/share/nginx/html/config.js}"
VITE_HOST_API="${VITE_HOST_API:-}"
VITE_HELP_LINK="${VITE_HELP_LINK:-}"

# Escape " so the value can't break out of the JS string literal.
escaped_host_api=$(printf '%s' "$VITE_HOST_API" | sed 's/"/\\"/g')
escaped_help_link=$(printf '%s' "$VITE_HELP_LINK" | sed 's/"/\\"/g')

cat > "$CONFIG_PATH" <<EOF
window.__FUTURE_AGI_CONFIG__ = {
  VITE_HOST_API: "${escaped_host_api}",
  VITE_HELP_LINK: "${escaped_help_link}"
};
EOF

echo "[entrypoint] wrote ${CONFIG_PATH} (VITE_HOST_API=${VITE_HOST_API}, VITE_HELP_LINK=${VITE_HELP_LINK})"
exec "$@"
