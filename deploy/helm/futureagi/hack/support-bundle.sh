#!/usr/bin/env bash
# Collect a Future AGI support bundle for one Helm release: what support needs
# to diagnose an install, as a .tar.gz you can review before sending it.
#
#   support-bundle.sh [-n NAMESPACE] [-r RELEASE] [-o DIR] [--tail LINES] [--no-setup-checks]
#
# Defaults: namespace futureagi, release futureagi, the current directory,
# the last 1000 log lines of each container.
#
# What it collects:
#   helm status, history and user-supplied values (keys matching PASSWORD,
#   SECRET, TOKEN, KEY, DSN, CREDENTIAL or HTTP(S)_PROXY redacted, with any
#   block under them, and PEM blocks and the user:password@ part of every URL
#   under any key);
#   kubectl get of the release's workloads, Services, PVCs, routes, network
#   policies, ExternalSecrets and the namespace's events; describe output of
#   pods that are not ready; current and previous logs of every Future AGI
#   container, the bootstrap job included; nodes, StorageClasses, the image
#   digests the pods run; and GET /api/setup-checks/ through a short
#   port-forward to the backend.
#
# It never reads Secret objects, never runs `helm get manifest/hooks` (which
# can hold inline secrets), and redacts the whole value of `NAME: value` /
# `NAME=value` pairs with a sensitive name, PEM blocks (private keys) under
# any name, and credentials in URLs (scheme://user:pass@host), in the status,
# describe output and logs. Needs kubectl and helm
# with access to the namespace; curl for the setup checks.
set -uo pipefail

namespace=futureagi
release=futureagi
dest=.
tail_lines=1000
setup_checks=true
while [ $# -gt 0 ]; do
  case $1 in
    -n | --namespace) namespace=$2; shift 2 ;;
    -r | --release) release=$2; shift 2 ;;
    -o | --output) dest=$2; shift 2 ;;
    --tail) tail_lines=$2; shift 2 ;;
    --no-setup-checks) setup_checks=false; shift ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

kubectl=${KUBECTL:-kubectl}
helm=${HELM:-helm}
for tool in "$kubectl" "$helm"; do
  command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 1; }
done

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="futureagi-support-$release-$stamp"
work=$(mktemp -d)
dir="$work/$name"
mkdir -p "$dir/logs" "$dir/describe"
# The port-forward (the only background process) is stopped on every exit:
# finished, failed, Ctrl-C (a background job ignores SIGINT) or killed. bash
# runs the EXIT trap on HUP and TERM as well, then dies of the signal, so a
# caller knows it was interrupted: trapping them to exit would hide it.
forward=""
stop_forward() {
  [ -n "$forward" ] || return 0
  kill "$forward" 2>/dev/null || true
  wait "$forward" 2>/dev/null || true
  forward=""
}
trap 'stop_forward; rm -rf "$work"' EXIT
# Ctrl-C: without a trap, bash carries on when the command it is waiting for
# exits normally (a curl that had just failed as the signal landed). Clean up,
# then die of SIGINT all the same.
trap 'trap - INT EXIT; stop_forward; rm -rf "$work"; kill -INT $$' INT
selector="app.kubernetes.io/instance=$release"
k() { "$kubectl" -n "$namespace" "$@"; }

# Sensitive names: whole YAML keys (and their nested blocks) in the values,
# NAME: value / NAME=value pairs elsewhere. A proxy URL can carry a login
# (httpsProxy, HTTPS_PROXY); NO_PROXY stays readable.
sensitive='PASSWORD|PASSWD|SECRET|TOKEN|KEY|DSN|CREDENTIAL|HTTPS?_?PROXY|ALL_?PROXY'

# user:password@ in any URL (a proxy, a DATABASE_URL in extraEnv), whatever
# the name it is under.
redact_urls() {
  sed -E 's#([A-Za-z][A-Za-z0-9+.-]*://)[^/[:space:]]*@#\1<redacted>@#g'
}

# A PEM block (a private key) goes from BEGIN to END whatever its name: over
# lines (describe output, logs, a YAML block) or on one (\n-escaped in JSON).
# A marker's label is optional: Helm folds a long values string at 80
# columns, and may split -----BEGIN or -----END from it.
redact_pem() {
  awk '
    in_pem {
      if ($0 ~ /-----END/) in_pem = 0
      next
    }
    {
      line = $0
      while (match(line, /-----BEGIN/)) {
        head = substr(line, 1, RSTART - 1)
        tail = substr(line, RSTART + RLENGTH)
        if (!match(tail, /-----END[A-Z0-9 ]*(-----)?/)) {
          line = head "<redacted>"
          in_pem = 1
          break
        }
        line = head "<redacted>" substr(tail, RSTART + RLENGTH)
      }
      print line
    }
  '
}

redact_yaml() {
  redact_pem | awk -v pat="$sensitive" '
    function indent_of(s) { match(s, /^ */); return RLENGTH }
    {
      line = $0
      ind = indent_of(line)
      if (skip >= 0) {
        # the rest of the redacted value: lines deeper than its key (a block,
        # or a long string Helm folds at 80 columns), and a list at its indent
        if (line ~ /^[[:space:]]*$/ || ind > skip || (ind == skip && line ~ /^ *- /)) next
        skip = -1
      }
      if (match(line, /^ *(- )?["'\'']?[A-Za-z0-9_.\/-]+["'\'']?:/)) {
        key = substr(line, RSTART, RLENGTH)
        gsub(/^ *(- )?["'\'']?|["'\'']?:$/, "", key)
        if (toupper(key) ~ pat) {
          print substr(line, 1, RSTART + RLENGTH - 1) " <redacted>"
          match(line, /^ *(- )?/)
          skip = RLENGTH
          next
        }
      }
      print line
    }
    BEGIN { skip = -1 }
  ' | redact_urls
}

# A pair with a sensitive name, in any case, loses its whole value. After an
# unquoted NAME: (kubectl describe's env var, whose quotes are part of the
# value) it runs to the end of the line. Otherwise a quoted value goes up to
# its closing quote; an unquoted one after a quoted name (a JSON number) up
# to the next , } ] or space, in a URL query up to the next &, and any other
# to the end of the line. A value that ends its line, an empty one included,
# takes the lines indented under its name with it: kubectl describe continues
# a multiline env value so (its first line may be empty), and Python's pprint
# a long string. kubectl's "<set to the key 'k' in secret 's'>" names the
# value's Secret and stays.
redact_text() {
  redact_pem | awk -v pat="$sensitive" '
    function indent_of(s) { match(s, /^[[:space:]]*/); return RLENGTH }
    # Length of the quoted string s starts with, closing quote included (a
    # backslash escapes the next character); 0 if it does not close.
    function quoted_length(s,    q, i, c) {
      q = substr(s, 1, 1)
      for (i = 2; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\") i++
        else if (c == q) return i
      }
      return 0
    }
    BEGIN {
      # NAME, its closing quote (\" in a JSON string inside JSON), : or =
      pair = "[A-Z0-9_]*(" pat ")[A-Z0-9_]*(\\\\?[\"'\''])?[[:space:]]*[:=][[:space:]]*"
      under = -1
    }
    under >= 0 {
      if ($0 ~ /^[[:space:]]*$/ || indent_of($0) > under) next
      under = -1
    }
    {
      out = ""
      rest = $0
      while (match(toupper(rest), pair)) {
        name_column = length($0) - length(rest) + RSTART - 1
        before = RSTART > 1 ? substr(rest, RSTART - 1, 1) : ""
        name = substr(rest, RSTART, RLENGTH)
        quoted_name = name ~ /["'\'']/
        out = out substr(rest, 1, RSTART + RLENGTH - 1)
        rest = substr(rest, RSTART + RLENGTH)
        if (rest ~ /^<set to the key /) break
        first = substr(rest, 1, 1)
        mask = "<redacted>"
        value_length = length(rest)
        if (!quoted_name && name ~ /:/) {
          # NAME: value: the rest of the line, quotes and all
        } else if (substr(rest, 1, 2) == "\\\"") {
          n = index(substr(rest, 3), "\\\"")
          if (n) { value_length = n + 3; mask = "\\\"" mask "\\\"" }
        } else if (first == "\"" || first == "'\''") {
          n = quoted_length(rest)
          if (n) { value_length = n; mask = first mask first }
        } else if (before == "?" || before == "&") {
          match(rest, /^[^&#"[:space:]]*/)
          value_length = RLENGTH
        } else if (quoted_name && first != "{" && first != "[") {
          match(rest, /^[^],}[:space:]]*/)
          value_length = RLENGTH
        }
        if (rest != "") out = out mask
        rest = substr(rest, value_length + 1)
        if (rest ~ /^[[:space:]]*$/) under = name_column
      }
      print out rest
    }
  ' | redact_urls
}

run() { # run FILE CMD... : output (and errors) to FILE, never failing the bundle
  local file=$1
  shift
  { echo "\$ $*"; "$@" 2>&1; } >"$dir/$file" || true
}

echo "Collecting release $release in namespace $namespace ..."

# Helm: status (its notes print URLs) and history, and the user-supplied
# values, redacted.
{ echo "\$ $helm -n $namespace status $release"; "$helm" -n "$namespace" status "$release" 2>&1 || true; } | redact_text >"$dir/helm-status.txt"
run helm-history.txt "$helm" -n "$namespace" history "$release" --max 20
{ "$helm" -n "$namespace" get values "$release" -o yaml 2>&1 || true; } | redact_yaml >"$dir/helm-values.redacted.yaml"

# Cluster and namespace state (never Secrets).
run version.txt "$kubectl" version
run nodes.txt "$kubectl" get nodes -o wide
run node-usage.txt "$kubectl" top nodes
run storageclasses.txt "$kubectl" get storageclass
run resources.txt k get deploy,statefulset,job,pod,svc,pvc,hpa,pdb,networkpolicy,ingress,configmap -l "$selector" -o wide
run routes.txt k get httproute,grpcroute,externalsecret -l "$selector" -o wide
run events.txt k get events --sort-by=.lastTimestamp
run pod-usage.txt k top pods -l "$selector"
run images.txt k get pods -l "$selector" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{range .status.containerStatuses[*]}{"  "}{.name}{"  "}{.image}{"  "}{.imageID}{"\n"}{end}{end}'

# Pods that are not ready: describe (env values with sensitive names redacted).
pods=$(k get pods -l "$selector" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{" "}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' 2>/dev/null || true)
while read -r pod phase ready; do
  [ -n "$pod" ] || continue
  if [ "$ready" != "True" ] && [ "$phase" != "Succeeded" ]; then
    { k describe pod "$pod" 2>&1 || true; } | redact_text >"$dir/describe/$pod.txt"
  fi
done <<<"$pods"

# Logs of every container, current and previous, the bootstrap job's included.
for pod in $(k get pods -l "$selector" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
  for container in $(k get pod "$pod" -o jsonpath='{.spec.initContainers[*].name} {.spec.containers[*].name}' 2>/dev/null); do
    { k logs "$pod" -c "$container" --tail "$tail_lines" --timestamps 2>&1 || true; } | redact_text >"$dir/logs/$pod.$container.log"
    if k logs "$pod" -c "$container" --previous --tail 1 >/dev/null 2>&1; then
      { k logs "$pod" -c "$container" --previous --tail "$tail_lines" --timestamps 2>&1 || true; } | redact_text >"$dir/logs/$pod.$container.previous.log"
    fi
  done
done
for job in $(k get jobs -l "$selector" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
  { k logs "job/$job" --all-containers --tail "$tail_lines" --timestamps 2>&1 || true; } | redact_text >"$dir/logs/job.$job.log"
done

# The app's own diagnosis: GET /api/setup-checks/ through a port-forward.
if $setup_checks && command -v curl >/dev/null; then
  svc=$(k get svc -l "$selector,app.kubernetes.io/component=backend" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  port=$(k get svc "$svc" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null || true)
  if [ -n "$svc" ] && [ -n "$port" ]; then
    local_port=$((20000 + RANDOM % 20000))
    # kubectl itself in the background, not the function k: $! must be the
    # process to stop, not a subshell kubectl would outlive.
    "$kubectl" -n "$namespace" port-forward "svc/$svc" "$local_port:$port" >"$work/port-forward.log" 2>&1 &
    forward=$!
    for _ in $(seq 1 20); do
      curl -fsS -o /dev/null "http://127.0.0.1:$local_port/health/" -H 'Host: localhost' 2>/dev/null && break
      sleep 0.5
    done
    { curl -sS --max-time 60 -H 'Host: localhost' "http://127.0.0.1:$local_port/api/setup-checks/" 2>&1 || true; } | redact_text >"$dir/setup-checks.json"
    stop_forward
  else
    echo "no backend Service found for release $release" >"$dir/setup-checks.json"
  fi
fi

{
  echo "Future AGI support bundle"
  echo "release:   $release"
  echo "namespace: $namespace"
  echo "collected: $stamp"
  echo "Secret objects are never read; values, status, describe output and logs are"
  echo "redacted by name ($sensitive), PEM blocks and URL credentials."
  echo "Review the files before sending them."
} >"$dir/README.txt"

mkdir -p "$dest"
tar -C "$work" -czf "$dest/$name.tar.gz" "$name"
echo "Wrote $dest/$name.tar.gz"
