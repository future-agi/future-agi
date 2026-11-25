#!/bin/sh
# HEALTHCHECK probe: whichever listener is on, HTTP first. The HTTPS probe does
# not verify the certificate: it is a loopback liveness check of this
# container's own listener, not an identity check (the certificate names the
# service, not 127.0.0.1, and its CA need not be in the image). busybox wget
# speaks HTTPS through the base image's ssl_client. -Y off ignores any
# HTTP(S)_PROXY in the environment.
set -eu
if [ "${FRONTEND_HTTP_ENABLED:-true}" = true ]; then
  exec wget -q -Y off -T 5 -O /dev/null "http://127.0.0.1:${FRONTEND_HTTP_PORT:-80}/"
fi
exec wget -q -Y off -T 5 --no-check-certificate -O /dev/null "https://127.0.0.1:${FRONTEND_TLS_PORT:-443}/"
