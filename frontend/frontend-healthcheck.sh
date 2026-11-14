#!/bin/sh
# BusyBox wget supports HTTPS (the image ships ssl_client), but verifies the
# certificate and cannot disable that verification for a loopback probe. For
# private CAs without a trust bundle, fall back to checking the TLS port.
set -eu
if [ "${FRONTEND_HTTP_ENABLED:-true}" = true ]; then
  exec wget -q -Y off -T 5 -O /dev/null "http://127.0.0.1:${FRONTEND_HTTP_PORT:-80}/"
fi
if [ "${FRONTEND_TLS_ENABLED:-false}" != true ]; then
  echo '[healthcheck] no listener enabled' >&2
  exit 1
fi
port=${FRONTEND_TLS_PORT:-443}
if wget -q -Y off -T 2 -O /dev/null "https://127.0.0.1:$port/" 2>/dev/null; then
  exit 0
fi
# TCP liveness is sufficient when loopback's private CA isn't trusted inside
# the image. Kubernetes HTTPS probes validate the response, without TLS trust.
exec nc -z -w 2 127.0.0.1 "$port"
