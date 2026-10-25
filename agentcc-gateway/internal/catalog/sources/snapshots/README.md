# Bundled snapshots of OpenAI-compatible catalog sources

A managed provider with `catalog_source: openai_compatible` starts from
`<upstream_slug>.json` here when the file exists, the way an OpenRouter
provider starts from `internal/catalog/openrouter_snapshot.json`, and is
replaced by its first live `GET {base_url}/models`. Without a file the source
starts empty until that first fetch.

Write a file with `scripts/update-provider-catalogs.sh openai-compatible`,
which reads only the upstream's public model list and keeps, per model, the
fields `sources.ParseOpenAICompatible` reads (`id`, `context_length`,
`pricing`), with the time it was fetched:

```
{"fetched_at": "2026-10-16T12:00:00Z", "source_url": "https://.../v1/models", "data": [{"id": "...", "context_length": 131072, "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}}]}
```

No snapshot is bundled with the gateway, and no OpenAI-compatible source is
configured by default.
