#!/usr/bin/env bash
# scripts/update-provider-catalogs.sh
# Refreshes the catalog sources bundled with the gateway, other than
# OpenRouter's (scripts/update-openrouter-catalog.sh does that one):
#
#   openai-compatible <upstream_slug> <models_url | --from-file FILE> [--out FILE]
#       Reads an OpenAI-compatible public GET /models list and writes the
#       bundled snapshot internal/catalog/sources/snapshots/<slug>.json, kept
#       to the fields the generic parser reads (id, context_length, pricing),
#       with the time it was fetched. Prints how many models it lists and how
#       many the gateway prices (an OpenRouter-shaped price that bills a
#       call): the counts to quote for it.
#
#   price-card <card.json> [--models-from FILE] [--check]
#       Refreshes a price card's model details (context_length, max_output,
#       supported_parameters, modalities) from OpenRouter's public model
#       list, matched on each card model's canonical id. It never writes a
#       price or the card's as_of: card prices are list prices the owner
#       confirms. It never raises a card's context_length either: a card
#       may cap it below OpenRouter's listing (a longer prompt priced at a
#       tier the card cannot express). It lists every model with no price
#       that bills a call, every capped context, and every price that
#       differs from OpenRouter's listing, for the owner to check.
#       --check reports without writing.
#
# Run by hand. It makes outbound GETs to public model lists only: no key is
# sent, nothing is written upstream. --from-file / --models-from read a list
# saved earlier instead, with no network at all. Tests never run it.
set -euo pipefail

cd "$(dirname "$0")/.."

usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage

python3 -I - "$@" <<'PY'
import json, os, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
SNAPSHOT_DIR = "internal/catalog/sources/snapshots"
PRICE_KEYS = ("prompt", "completion", "input_cache_read", "input_cache_write",
              "request", "image", "web_search", "internal_reasoning")
CARD_PRICE_KEYS = {"prompt": "prompt", "completion": "completion",
                   "input_cache_read": "cache_read", "input_cache_write": "cache_write"}

def fail(msg):
    sys.exit("update-provider-catalogs: " + msg)

def get(url):
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": "agentcc-catalog-update"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            fail("GET %s: HTTP %d" % (url, e.code))
        except urllib.error.URLError:
            time.sleep(2 ** attempt)
    fail("GET %s: retries exhausted" % url)

def read_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def entries(doc):
    return doc if isinstance(doc, list) else (doc.get("data") or [])

def openrouter_pricing(p):
    return {k: p[k] for k in PRICE_KEYS if isinstance(p, dict) and isinstance(p.get(k), (str, int, float))}

def flag(args, name, has_value=True):
    if name not in args:
        return None
    i = args.index(name)
    if not has_value:
        del args[i]
        return True
    if i + 1 >= len(args):
        fail(name + " needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value

def openai_compatible(args):
    from_file, out = flag(args, "--from-file"), flag(args, "--out")
    if len(args) != (1 if from_file else 2):
        fail("openai-compatible <upstream_slug> <models_url | --from-file FILE> [--out FILE]")
    slug = args[0]
    source = from_file or args[1]
    doc = read_file(from_file) if from_file else get(args[1])
    keep, priced = [], 0
    for m in entries(doc):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        e = {"id": m["id"]}
        if isinstance(m.get("context_length"), int):
            e["context_length"] = m["context_length"]
        p = openrouter_pricing(m.get("pricing"))
        if p:
            e["pricing"] = p
            priced += sells(p)
        keep.append(e)
    if not keep:
        fail("%s lists no models" % source)
    keep.sort(key=lambda e: e["id"])
    out = out or os.path.join(SNAPSHOT_DIR, slug + ".json")
    snapshot = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_url": source, "data": keep}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=1)
        f.write("\n")
    print("%s: %d models, %d priced (an OpenRouter-shaped price that bills a call), %d unpriced (listed, never managed) -> %s"
          % (slug, len(keep), priced, len(keep) - priced, out))

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def bills(prices):
    """Whether prices bill a call alone: a prompt, completion or request
    price above zero (the gateway's catalog.Prices.BillsCall)."""
    return any(num((prices or {}).get(k)) > 0 for k in ("prompt", "completion", "request"))

def sells(pricing):
    """Whether the generic parser prices a listed model: no dynamic ("-1")
    line, and a price that bills a call."""
    return bills(pricing) and not any(num(v) < 0 for v in pricing.values())

def price_card(args):
    models_from, check = flag(args, "--models-from"), flag(args, "--check", has_value=False)
    if len(args) != 1:
        fail("price-card <card.json> [--models-from FILE] [--check]")
    path = args[0]
    card = read_file(path)
    listing = {m["id"].lower(): m for m in entries(read_file(models_from) if models_from else get(OPENROUTER_MODELS))
               if isinstance(m, dict) and m.get("id")}
    unpriced, differs, unmatched, capped, changed = [], [], [], [], 0
    for m in card.get("models", []):
        if not bills(m.get("prices")):
            unpriced.append(m["id"])
        canon = (m.get("canonical") or "").lower()
        ref = listing.get(canon)
        if ref is None:
            unmatched.append("%s (canonical %s)" % (m["id"], canon or "not set"))
            continue
        arch = ref.get("architecture") or {}
        details = {
            "context_length": ref.get("context_length"),
            "max_output": (ref.get("top_provider") or {}).get("max_completion_tokens"),
            "supported_parameters": ref.get("supported_parameters"),
            "modalities": {"input": arch.get("input_modalities"), "output": arch.get("output_modalities")},
        }
        for key, value in details.items():
            if key == "modalities" and not (value["input"] and value["output"]):
                continue
            if key == "context_length" and value and isinstance(m.get(key), int) and m[key] < value:
                capped.append("%s: card %d, OpenRouter lists %d" % (m["id"], m[key], value))
                continue
            if value and m.get(key) != value:
                m[key], changed = value, changed + 1
        for or_key, card_key in CARD_PRICE_KEYS.items():
            listed, ours = (ref.get("pricing") or {}).get(or_key), (m.get("prices") or {}).get(card_key)
            if listed is not None and ours is not None and float(listed) != float(ours):
                differs.append("%s %s: card %s, OpenRouter lists %s" % (m["id"], card_key, ours, listed))
    if not check and changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card, f, indent=2)
            f.write("\n")
    print("%s: %d model details %s; prices and as_of (%s) untouched"
          % (path, changed, "would change" if check else "refreshed", card.get("as_of")))
    for title, items in (("no price that bills a call (listed, never offered as managed)", unpriced),
                         ("context_length kept below OpenRouter's listing (the card caps it)", capped),
                         ("price differs from OpenRouter's listing; confirm with the vendor", differs),
                         ("not in OpenRouter's list; details not refreshed", unmatched)):
        if items:
            print("  " + title + ":")
            for item in items:
                print("    " + item)

args = sys.argv[1:]
mode, rest = args[0], args[1:]
if mode == "openai-compatible":
    openai_compatible(rest)
elif mode == "price-card":
    price_card(rest)
else:
    fail("unknown mode %r: openai-compatible or price-card" % mode)
PY
