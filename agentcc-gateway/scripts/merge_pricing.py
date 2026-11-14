"""Merges the LiteLLM pricing file and models.dev into the bundled catalogue.

Called by update-litellm-pricing.sh. LiteLLM is the primary source. models.dev
only fills the models LiteLLM does not list.
"""
import json
import sys

MODELS_DEV_SOURCE = "models.dev"

# models.dev provider ID -> (catalogue key prefix, litellm_provider, mode).
# Add a row only for a provider the gateway supports.
PROVIDERS = {
    "perplexity-agent": ("perplexity/", "perplexity", "responses"),
    "google": ("gemini/", "gemini", "chat"),
    "google-vertex": ("vertex_ai/", "vertex_ai", "chat"),
}

# models.dev cost field -> catalogue field. Its prices are USD per M tokens.
# A model with any other cost field (a context tier, cache writes) is skipped:
# the catalogue would price it at the base rate and under-charge.
COSTS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
}


def per_token(usd_per_million):
    return usd_per_million / 1_000_000


def convert(models_dev):
    """Returns ({catalogue key: entry}, skipped count) for the mapped providers."""
    entries, skipped = {}, 0
    for provider_id, (prefix, provider, mode) in PROVIDERS.items():
        for model_id, model in models_dev.get(provider_id, {}).get("models", {}).items():
            cost = model.get("cost", {})
            if not cost.get("input") or set(cost) - set(COSTS):
                skipped += 1
                continue
            entry = {"litellm_provider": provider, "mode": mode}
            if model.get("type") == "embedding":
                entry["mode"] = "embedding"
            for field, key in COSTS.items():
                if cost.get(field):
                    entry[key] = per_token(cost[field])
            limit = model.get("limit", {})
            if limit.get("context"):
                entry["max_input_tokens"] = limit["context"]
            if limit.get("output") and entry["mode"] != "embedding":
                entry["max_output_tokens"] = limit["output"]
            # Only a true flag is stated. A flag that says no makes the gateway refuse the request.
            if model.get("tool_call"):
                entry["supports_function_calling"] = True
            if model.get("reasoning"):
                entry["supports_reasoning"] = True
            if "image" in model.get("modalities", {}).get("input", []):
                entry["supports_vision"] = True
            entry["source"] = MODELS_DEV_SOURCE
            entries[prefix + model_id] = entry
    return entries, skipped


def is_models_dev(entry):
    return isinstance(entry, dict) and entry.get("source") == MODELS_DEV_SOURCE


def merge(upstream, current, models_dev):
    """Returns (merged catalogue, counts per source).

    Upstream wins on every key it has. A current entry upstream dropped is kept,
    except one that came from models.dev: that is derived again each run.
    """
    merged = dict(upstream)
    own = {k: v for k, v in current.items() if not is_models_dev(v)}

    kept = [k for k in own if k not in merged]
    for k in kept:
        merged[k] = own[k]

    # Upstream also drops capability flags from entries it keeps. The gateway
    # refuses tools, vision or json_schema for a model whose flag says no, so a
    # flag the current file states as true is carried forward.
    carried = 0
    for k, entry in merged.items():
        old = own.get(k)
        if not isinstance(old, dict) or not isinstance(entry, dict):
            continue
        for flag, value in old.items():
            if flag.startswith("supports_") and value is True and flag not in entry:
                entry[flag] = True
                carried += 1

    added, skipped = convert(models_dev)
    filled = {k: v for k, v in added.items() if k not in merged}
    merged.update(filled)
    counts = {
        "upstream": len(upstream),
        "kept": len(kept),
        "models.dev": len(filled),
        "models.dev skipped": skipped,
        "flags carried": carried,
    }
    return merged, counts


def main(upstream_path, models_dev_path, dest):
    with open(upstream_path) as f:
        upstream = json.load(f)
    with open(models_dev_path) as f:
        models_dev = json.load(f)
    with open(dest) as f:
        current = json.load(f)
    merged, counts = merge(upstream, current, models_dev)
    with open(dest, "w") as f:
        json.dump(merged, f, indent=4, ensure_ascii=False)
        f.write("\n")
    print(
        f"{len(merged)} entries: {counts['upstream']} from litellm, "
        f"{counts['models.dev']} from models.dev ({counts['models.dev skipped']} skipped), "
        f"{counts['kept']} kept that upstream dropped, "
        f"{counts['flags carried']} capability flags carried forward"
    )


if __name__ == "__main__":
    main(*sys.argv[1:4])
