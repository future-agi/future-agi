#!/usr/bin/env bash
# scripts/release/sbom.sh [--binary FILE] [--out FILE]
# Writes a CycloneDX 1.5 JSON SBOM of the gateway, agentcc-gateway.cdx.json
# (or --out FILE), built by hand from the Go toolchain's own output, so no
# SBOM tool is installed:
#
#   go list -m -json all   every module in the build graph: one component each,
#                          with its version and package URL
#   go.sum                 each module's h1: hashes, as properties
#                          (golang:h1 for the module zip, golang:gomod-h1 for
#                          its go.mod); an h1: is a SHA-256 over a file list,
#                          not a file digest, so it is not put in `hashes`
#   go version -m BINARY   which modules are linked into the binary
#                          (golang:linked true|false), the toolchain, the build
#                          settings, and, as the SBOM's subject, the binary's
#                          sha256. Without --binary a temporary build is made
#                          with build-reproducible.sh's flags.
#
# metadata.timestamp is SOURCE_DATE_EPOCH (the caller's, else the last commit's
# time, else 315532800) and serialNumber is derived from the binary's sha256,
# so the same binary and go.sum give a byte-identical SBOM.
#
# Modules whose metadata the offline module cache lacks are still listed (path,
# version and go.sum hashes are all an SBOM needs); `go list` reports them on
# stderr. Fails if a linked module's h1: in the binary differs from go.sum.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GW=$(cd "$HERE/../.." && pwd)
BIN=""
OUT="agentcc-gateway.cdx.json"
while [ $# -gt 0 ]; do
  case "$1" in
    --binary) BIN=${2:?--binary needs a file}; shift 2 ;;
    --out) OUT=${2:?--out needs a file}; shift 2 ;;
    *) echo "usage: sbom.sh [--binary FILE] [--out FILE]" >&2; exit 2 ;;
  esac
done
# The Go commands below run in the module directory: make both paths absolute.
abspath() { case "$1" in /*) echo "$1" ;; *) echo "$PWD/$1" ;; esac; }
OUT=$(abspath "$OUT")
[ -z "$BIN" ] || BIN=$(abspath "$BIN")
[ -n "${GOPROXY+x}" ] || export GOPROXY=off
[ -n "${GOFLAGS+x}" ] || export GOFLAGS=-mod=readonly
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH=$(git -C "$GW" log -1 --format=%ct 2>/dev/null || true)
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/agentcc-sbom.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

if [ -z "$BIN" ]; then
  BIN="$WORK/agentcc-gateway"
  (cd "$GW" && CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w" -o "$BIN" ./cmd/agentcc)
fi
[ -f "$BIN" ] || { echo "sbom: no binary at $BIN" >&2; exit 2; }

(cd "$GW" && go version -m "$BIN") >"$WORK/version-m.txt"
# -e: list a module even when the offline cache lacks its .info or go.mod.
(cd "$GW" && go list -m -e -json all) >"$WORK/modules.json"
if command -v sha256sum >/dev/null; then sha256sum "$BIN"; else shasum -a 256 "$BIN"; fi |
  cut -d' ' -f1 >"$WORK/bin.sha256"

python3 -I - "$WORK/modules.json" "$GW/go.sum" "$WORK/version-m.txt" "$WORK/bin.sha256" "$OUT" \
  "$SOURCE_DATE_EPOCH" <<'PY'
import json
import sys
import uuid
from datetime import datetime, timezone

modules_path, gosum_path, versionm_path, binsha_path, out_path, epoch = sys.argv[1:7]

decoder, text, i, modules = json.JSONDecoder(), open(modules_path).read(), 0, []
while i < len(text):
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text):
        obj, i = decoder.raw_decode(text, i)
        modules.append(obj)

sums = {}
for line in open(gosum_path):
    parts = line.split()
    if len(parts) == 3:
        path, version, h1 = parts
        kind = "gomod-h1" if version.endswith("/go.mod") else "h1"
        sums.setdefault((path, version.removesuffix("/go.mod")), {})[kind] = h1

linked, settings, toolchain = {}, {}, ""
for n, line in enumerate(open(versionm_path)):
    parts = line.strip().split("\t")
    if n == 0:
        toolchain = line.split(":", 1)[1].strip()
    elif parts[0] == "dep":
        linked[(parts[1], parts[2])] = parts[3] if len(parts) > 3 else ""
    elif parts[0] == "=>" and len(parts) > 2:
        linked[(parts[1], parts[2])] = parts[3] if len(parts) > 3 else ""
    elif parts[0] == "build" and len(parts) > 1:
        key, _, value = parts[1].partition("=")
        settings[key] = value

main = next(m for m in modules if m.get("Main"))
errors, components = [], []
for m in modules:
    if m.get("Main"):
        continue
    path, version = m["Path"], m["Version"]
    real = m.get("Replace") or m
    rpath, rversion = real["Path"], real.get("Version", "")
    h = sums.get((rpath, rversion), {})
    is_linked = (rpath, rversion) in linked or (path, version) in linked
    in_binary = linked.get((rpath, rversion), linked.get((path, version)))
    if is_linked and in_binary and h.get("h1") and in_binary != h["h1"]:
        errors.append("%s@%s: binary has %s, go.sum has %s" % (rpath, rversion, in_binary, h["h1"]))
    props = [{"name": "golang:linked", "value": "true" if is_linked else "false"}]
    props += [{"name": "golang:" + k, "value": h[k]} for k in ("h1", "gomod-h1") if k in h]
    if m.get("Indirect"):
        props.append({"name": "golang:indirect", "value": "true"})
    if m.get("Replace"):
        props.append({"name": "golang:replaced-by", "value": "%s@%s" % (rpath, rversion)})
    components.append({
        "type": "library",
        "bom-ref": "pkg:golang/%s@%s" % (path, version),
        "name": path,
        "version": version,
        "purl": "pkg:golang/%s@%s" % (path, version),
        "scope": "required" if is_linked else "excluded",
        "properties": props,
    })
if errors:
    sys.exit("sbom: linked module hashes differ from go.sum:\n  " + "\n  ".join(errors))

stamp = datetime.fromtimestamp(int(epoch), timezone.utc)
binsha = open(binsha_path).read().strip()
subject = "pkg:golang/%s" % main["Path"]
bom = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "serialNumber": "urn:uuid:%s" % uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (subject, binsha)),
    "version": 1,
    "metadata": {
        "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": {"components": [{"type": "application", "name": "agentcc-gateway/scripts/release/sbom.sh"}]},
        "component": {
            "type": "application",
            "bom-ref": subject,
            "name": main["Path"],
            "purl": subject,
            "hashes": [{"alg": "SHA-256", "content": binsha}],
            "properties": [{"name": "golang:toolchain", "value": toolchain}]
            + [{"name": "golang:build:" + k, "value": v} for k, v in sorted(settings.items())],
        },
    },
    "components": sorted(components, key=lambda c: c["bom-ref"]),
    "dependencies": [{"ref": subject, "dependsOn": sorted(c["bom-ref"] for c in components if c["scope"] == "required")}],
}
with open(out_path, "w") as fh:
    json.dump(bom, fh, indent=2)
    fh.write("\n")
print("sbom: wrote %s: %d modules (%d linked into the binary), binary sha256 %s"
      % (out_path, len(components), sum(c["scope"] == "required" for c in components), binsha))
PY
