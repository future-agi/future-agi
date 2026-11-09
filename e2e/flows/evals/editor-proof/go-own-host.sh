#!/bin/sh
set -eu
names="$(hostname) $(hostname -i)"
for name in $names; do
  NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1},$name"
  printf '{"kind":"own-host","hostname":"%s","port":0}\n' "$name"
done
export NO_PROXY
no_proxy="$NO_PROXY"
export no_proxy
exec "$@"
