#!/bin/sh
# Entry point of the fake standalone image (see Dockerfile). sh stays PID 1
# with a TERM trap, so `docker stop` (a Compose recreate) takes no 10 s grace.
trap 'exit 0' TERM INT
mkdir -p /data /www/api/health /www/ui
echo "start $VERSION $MODE $FLAVOR $(date +%s)" >> /data/history
case "$MODE" in
  crash)
    echo "fake app $VERSION crashes on start" >&2
    exit 1
    ;;
  slow)
    # Looks like Django migrations to bin/update's readiness loop.
    i=1
    steps=$((SLOW_SECONDS / 2))
    while [ "$i" -le "$steps" ]; do
      echo "  Applying fake.$(printf '%04d' "$i")_migration... OK"
      sleep 2 &
      wait $!
      i=$((i + 1))
    done
    ;;
esac
echo "ui $VERSION" > /www/ui/index.html
echo "$VERSION $MODE $FLAVOR" > /www/api/version.txt
# unhealthy: the UI answers, the API's /health/ never does.
[ "$MODE" = unhealthy ] || echo ok > /www/api/health/index.html
echo "$VERSION" > /data/schema
httpd -p 3000 -h /www/ui
httpd -f -p 8000 -h /www/api &
api=$!
trap 'kill "$api" 2>/dev/null; exit 0' TERM INT
echo "fake app $VERSION ($MODE) serving"
wait "$api"
