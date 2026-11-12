#!/usr/bin/env bash
# scripts/update-openrouter-catalog.sh
# Refreshes the two OpenRouter snapshots bundled with the gateway, trimmed to
# the fields internal/catalog reads:
#
#   internal/catalog/openrouter_snapshot.json  GET /api/v1/models
#   internal/catalog/endpoints_snapshot.json   GET /api/v1/models/{id}/endpoints
#                                              for every model, and
#                                              GET /api/v1/endpoints/zdr
#
# The gateway re-fetches the model list at runtime (managed.catalog_refresh)
# and the endpoints of its 50 newest fixed-price models (catalog.endpoints_refresh);
# the snapshots cover boot and outages. Both carry the time they were fetched,
# so a gateway serving them reports their real age.
#
# Run by hand. It makes outbound GETs to OpenRouter's public catalog API only:
# no key is sent, nothing is written upstream. Tests never run it.
#
# Environment:
#   OPENROUTER_API_BASE  default https://openrouter.ai/api/v1
#   RPS                  endpoint requests per second (default 5)
#   RAW_DIR              keep every raw response here (optional)
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -I - "${OPENROUTER_API_BASE:-https://openrouter.ai/api/v1}" "${RPS:-5}" "${RAW_DIR:-}" <<'PY'
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

base, rps, raw_dir = sys.argv[1].rstrip("/"), float(sys.argv[2]), sys.argv[3]
MODELS_DEST = "internal/catalog/openrouter_snapshot.json"
ENDPOINTS_DEST = "internal/catalog/endpoints_snapshot.json"
PRICE_KEYS = ("prompt", "completion", "input_cache_read", "input_cache_write",
              "request", "image", "web_search", "internal_reasoning")
ENDPOINT_KEYS = ("name", "provider_name", "tag", "quantization", "context_length",
                 "max_completion_tokens", "max_prompt_tokens", "supported_parameters",
                 "status", "uptime_last_30m", "latency_last_30m", "throughput_last_30m",
                 "supports_implicit_caching")

def get(path, raw_name):
    url = base + path
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": "agentcc-catalog-update"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError("GET %s: retries exhausted" % url)
    if raw_dir:
        path_out = os.path.join(raw_dir, raw_name)
        os.makedirs(os.path.dirname(path_out), exist_ok=True)
        with open(path_out, "wb") as f:
            f.write(body)
    return json.loads(body)

def prices(p):
    return {k: p[k] for k in PRICE_KEYS if isinstance((p or {}).get(k), str)}

fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print("Downloading the OpenRouter model list...")
models = get("/models", "models.json")["data"]
keep = []
for m in models:
    arch = m.get("architecture") or {}
    entry = {
        "id": m["id"],
        "canonical_slug": m.get("canonical_slug"),
        "name": m.get("name"),
        "created": m.get("created"),
        "description": (m.get("description") or "")[:280],
        "context_length": m.get("context_length"),
        "architecture": {k: arch.get(k) for k in
                         ("modality", "input_modalities", "output_modalities", "tokenizer", "instruct_type")},
        "pricing": prices(m.get("pricing")),
        "top_provider": {"max_completion_tokens": (m.get("top_provider") or {}).get("max_completion_tokens")},
        "supported_parameters": m.get("supported_parameters"),
        "expiration_date": m.get("expiration_date"),
    }
    if m.get("benchmarks") is not None:
        entry["benchmarks"] = m["benchmarks"]
    keep.append(entry)
with open(MODELS_DEST, "w", encoding="utf-8") as f:
    f.write(json.dumps({"fetched_at": fetched_at, "data": keep}, separators=(",", ":")) + "\n")
print("Saved %s (%d models)" % (MODELS_DEST, len(keep)))

print("Downloading the zero-data-retention endpoint list...")
zdr = [{"model_id": e.get("model_id"), "tag": e.get("tag"), "name": e.get("name")}
       for e in get("/endpoints/zdr", "zdr.json").get("data") or []]

print("Downloading endpoints for %d models at %.1f requests/s..." % (len(models), rps))
per_model, failed, interval, last = [], [], 1.0 / rps, 0.0
for m in models:
    wait = last + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    last = time.monotonic()
    quoted = urllib.parse.quote(m["id"], safe="/:")
    try:
        data = get("/models/%s/endpoints" % quoted, "endpoints/%s.json" % m["id"].replace(":", "_"))["data"]
    except Exception as e:  # one model's failure must not lose the rest
        failed.append("%s (%s)" % (m["id"], e))
        continue
    eps = []
    for ep in data.get("endpoints") or []:
        trimmed = {k: ep.get(k) for k in ENDPOINT_KEYS if k in ep}
        trimmed["pricing"] = prices(ep.get("pricing"))
        eps.append(trimmed)
    per_model.append({"id": m["id"], "endpoints": eps})
with open(ENDPOINTS_DEST, "w", encoding="utf-8") as f:
    f.write(json.dumps({"fetched_at": fetched_at, "zdr": zdr, "data": per_model}, separators=(",", ":")) + "\n")
total = sum(len(m["endpoints"]) for m in per_model)
print("Saved %s (%d endpoints for %d models; %d ZDR endpoints)" % (ENDPOINTS_DEST, total, len(per_model), len(zdr)))
if failed:
    print("Endpoints not fetched for %d models:" % len(failed))
    for line in failed:
        print("  " + line)
PY
