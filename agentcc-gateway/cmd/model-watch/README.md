# model-watch

Notices when a provider ships a model the provider contract tests do not
cover, picks the covered model to clone its cases from, and lays out the
`contract-recorder` runs that bring it under contract.

The contract tests (`internal/providers/contracttest`) pin what each adapter
does with a model's responses, but only for models someone added by hand.
`model-watch` asks each provider which models it serves, through the
provider's own list-models API, and compares that with a committed registry,
`internal/providers/contractwatch/known_models.json`. The daily workflow
`.github/workflows/agentcc-provider-new-models.yml` runs it, records each new
model from its template, live-tests the recording, and opens one tracking issue
per new model.

Run it from `agentcc-gateway/`:

```sh
go run ./cmd/model-watch -offline                        # registry vs fixture directories; no network
go run ./cmd/model-watch -check                          # list, diff, plan; exit 1 if any model is NEW
go run ./cmd/model-watch -check -provider gemini,groq -plan-out plan.json -summary-out summary.md
go run ./cmd/model-watch -newest -plan-out newest.json   # newest covered model per family (no network)
go run ./cmd/model-watch -update-registry                # add NEW models as ignored, for local triage
go run ./cmd/model-watch -fmt                            # rewrite the registry in canonical form
```

## Modes

Pick exactly one.

| mode | network | what it does | exit |
|---|---|---|---|
| `-check` | yes | Lists every provider whose credentials are set, diffs the listing against the registry, checks coverage, and plans a template for each NEW model. Prints NEW, RETIRED, deprecations and stale coverage. | 0 clean; 1 NEW models, a failed listing, an ID with unexpected characters, or stale coverage; 2 bad flags or registry |
| `-offline` | no | Registry ↔ fixture directories only (the STALE check), plus untriaged placeholders. | 0 consistent; 1 stale or placeholders |
| `-newest` | no | For each provider with credentials, the newest covered model of every family, as a re-record plan. | 0 |
| `-update-registry` | yes | `-check`, then adds every NEW model to the registry as `ignored` with a `TODO(triage)` reason. For local triage: `go test` fails until each placeholder has a real reason or the model is recorded. | 0 |
| `-fmt` | no | Rewrites the registry in its canonical form (the coverage test requires it). | 0 |
| `-report` | no | Renders the recording half of the job summary and one issue title and body per NEW model, from `-plan`, `-results` (the workflow's `results.jsonl`) and the logs next to it. | 0 |

Providers without their credentials are skipped with a message naming the
variable, never failed.

## Flags

| flag | default | meaning |
|---|---|---|
| `-registry` | `internal/providers/contractwatch/known_models.json` | the registry |
| `-providers-dir` | `internal/providers` | where the fixtures are |
| `-provider` | all | comma-separated registry sections |
| `-plan-out` | | `-check`: the NEW-model plan; `-newest`: the re-record plan (JSON arrays) |
| `-summary-out` | | Markdown summary (`-check`: discovery; `-report`: recordings) |
| `-max-record` | 0 (no cap) | mark at most this many plan entries `"record": true`, round-robin across providers |
| `-max-cases` | 0 (all) | cases per model, in priority order (below) |
| `-rotate` | 0 | `-newest`: shift the `-max-record` window by this many windows; the workflow passes the day number so every family comes round |
| `-timeout` | 1m | per-provider listing timeout |
| `-plan`, `-results`, `-issues-out`, `-run-url`, `-artifact` | | `-report` inputs and outputs |

## Credentials

The same environment variables `contract-recorder` reads, so one set of
secrets serves both.

| registry section | variables | list API |
|---|---|---|
| anthropic | `ANTHROPIC_API_KEY` | `GET api.anthropic.com/v1/models` with `anthropic-version: 2023-06-01`, `limit=1000`, paginated by `after_id` |
| openai | `OPENAI_API_KEY` | `GET api.openai.com/v1/models` (not paginated) |
| gemini | `GEMINI_API_KEY` (AI Studio) | `GET generativelanguage.googleapis.com/v1beta/models`, `pageSize=1000`, paginated by `pageToken`; the key goes in `x-goog-api-key`, never the URL |
| cohere | `COHERE_API_KEY` | `GET api.cohere.com/v1/models`, `page_size=1000`, paginated by `page_token` |
| bedrock | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (`AWS_SESSION_TOKEN`); region `AWS_REGION`, else `AWS_DEFAULT_REGION`, else `us-east-1` | `ListFoundationModels` (`GET /foundation-models`) and `ListInferenceProfiles` (`GET /inference-profiles?type=SYSTEM_DEFINED`, paginated by `nextToken`) on `bedrock.<region>.amazonaws.com`, SigV4 service `bedrock`. Needs `bedrock:ListFoundationModels` and `bedrock:ListInferenceProfiles`. |
| groq | `GROQ_API_KEY` | `GET api.groq.com/openai/v1/models` |
| mistral | `MISTRAL_API_KEY` | `GET api.mistral.ai/v1/models` |
| cerebras | `CEREBRAS_API_KEY` | `GET api.cerebras.ai/v1/models` |
| together, fireworks, openrouter | `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `OPENROUTER_API_KEY` | none: each is an aggregator whose listing is its whole routing table (hundreds of third-party models in one envelope), so the registry keeps them at `list: none`. A lister exists (`GET /v1/models` at the preset's base URL; Together answers with a bare array), so switching one to `api` is a registry edit. The credentials gate `-newest`. |
| perplexity | `PERPLEXITY_API_KEY` | none: a static list in the registry (Perplexity's `GET /v1/models` lists its Agent API models, not Sonar's) |
| azure | `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` | none: Azure serves deployments a resource owner names, not models. The credentials only gate `-newest`. |
| vertex | `VERTEX_PROJECT`, `VERTEX_ACCESS_TOKEN` | none: Vertex AI needs a short-lived OAuth token a scheduled job cannot hold; new Gemini models show in the gemini section first. The credentials gate `-newest` in a local run. |

The OpenAI-compatible sections list at the base URL and path prefix of the
preset the gateway calls (`providers.KnownProviders`) with the recorder's
`<PRESET>_API_KEY`; `sections_test.go` pins both.

Keys go in headers. Error messages are redacted of every credential, the HTTP
client never follows redirects, and a model ID with characters outside
`[A-Za-z0-9._:/@+~-]` is refused (IDs flow into file names, shell arguments
and issue titles).

What each lister keeps from the API, beyond the ID: Anthropic `display_name`,
`created_at`, `lifecycle`, `line`, `max_input_tokens`, `max_tokens`,
`retires_at`, and the `capabilities` marked supported (with
`capabilities.thinking`); OpenAI `created`, `owned_by`, `shutdown_date`; Groq
`active`, `context_window`, `max_completion_tokens`; Mistral `capabilities`,
`deprecation`, `type`; OpenRouter, Together and Fireworks `context_length`
(OpenRouter `expiration_date`); Gemini `displayName`,
token limits, `supportedGenerationMethods`, `thinking`; Cohere `endpoints`,
`features`, `context_length`, `finetuned`, `is_deprecated`; Bedrock modalities,
`inferenceTypesSupported`, `responseStreamingSupported`, `modelLifecycle`,
and for a profile its foundation model's metadata.

## The registry

`internal/providers/contractwatch/known_models.json`. It lives next to the
code that reads it and the coverage test that checks it; one file covers every
provider (the OpenAI-compatible presets' fixtures live in the openai suite, Vertex AI's in the gemini suite), and
`contracttest.Discover` never sees it. Every model ID a provider lists gets
exactly one status:

| status | meaning | fields |
|---|---|---|
| `covered` | it has contract fixtures: its model directory exists | |
| `alias` | a dated snapshot or alias of a covered model | `alias_of`: a covered ID |
| `ignored` | deliberately not under contract | `reason` (mandatory) |

```json
{
  "schema_version": 1,
  "about": "...",
  "providers": {
    "groq": {
      "list": "api",
      "note": "...",
      "fixtures": {"provider": "openai", "prefix": "groq@"},
      "ignore_rules": [
        {"match": "whisper-*", "reason": "transcription, not chat"}
      ],
      "models": [
        {"id": "openai/gpt-oss-120b", "status": "covered"}
      ]
    }
  }
}
```

- `list` is `api`, `static` (with a `static` block: `checked` date, `source`
  URL, `models`) or `none`; `static` and `none` need a `note` saying why.
- `fixtures` says where covered models' fixtures live: covered `M` is
  `internal/providers/<provider>/testdata/contracts/<ModelDir(prefix + M)>`.
  Default: the section's own adapter, no prefix.
- `ignore_rules` ignore every listed ID matching a glob (`*` matches any run,
  including `/` and `:`; `?` one character), each with a reason. An explicit
  entry must not also match a rule, so an ID never has two statuses.
- The file is kept in canonical form (models sorted by ID, one per line);
  `-fmt` writes it, and the coverage test fails on anything else.

How a listed ID is classified, in order: its explicit entry; an ignore rule;
the provider's own metadata (`AutoIgnore`: none of the methods or endpoints
the adapter parses (Gemini `generateContent` or `embedContent`; Cohere
`chat`, `embed` or `rerank`; Mistral `completion_chat`, `ocr`,
`audio_transcription` or `audio_speech`, and no embedding model: one whose
flags name only `moderation`, `classification`, `completion_fim` or
`audio_transcription_realtime`, since an embedding model sets no flag), a Bedrock
model without text output, a fine-tune, `lifecycle` deprecated or `LEGACY`,
an announced `shutdown_date` (Mistral `deprecation`, OpenRouter
`expiration_date`), `active: false`, dedicated capacity only, or a Bedrock
model invocable only through a profile, whose profile is listed instead); for Bedrock, another geography of an entry (`us.` and
`global.` profiles of one model on one API take the entry's status); else
**NEW**. Several NEW geographies of one Bedrock model fold into one NEW entry
(`global.` first, then `us.`) that lists the rest under `variants`.

The diff also reports:

- **RETIRED**: a `covered` or `alias` entry upstream no longer lists. Its
  fixtures can no longer be re-recorded and the weekly drift job will fail on
  it; it is a warning here, not a failure.
- **DEPRECATING**: a covered or alias model upstream marks deprecated or
  gives a shutdown date.
- **STALE coverage** (also offline, in `go test ./internal/providers/contractwatch/`):
  a `covered` entry whose model directory is missing, or a model directory no
  covered entry claims.
- notes: ignored entries no longer listed (safe to prune), rules that matched
  nothing, a static list last checked more than 90 days ago.
- whether `internal/modeldb/litellm.json` prices each NEW model
  (`in_modeldb`); without an entry, requests to it are not costed.

## The plan

`-check -plan-out plan.json` writes a JSON array, one entry per NEW model:

```json
[
  {
    "provider": "gemini",
    "new_model": "gemini-4.1-argon",
    "template_model": "gemini-4-argon",
    "reason": "family gemini; thinking yes for both (per the API); same tier argon; nearest version 4 → 4.1",
    "record": true,
    "recorder": {"provider": "gemini", "from": "gemini-4-argon", "as": "gemini-4.1-argon",
                 "cases": ["text_basic", "tool_call", "stream_tool_call", "thinking"]},
    "in_modeldb": false,
    "metadata": {"id": "gemini-4.1-argon", "thinking": true, "context_window": 1048576, "capabilities": ["generateContent", "countTokens"]}
  }
]
```

Optional keys: `warnings` (a mismatch no template could avoid) and
`alias_of` (the new ID differs from the template only by snapshot date or
geography, so an `alias` entry may be all it needs). `recorder` holds exactly
the `contract-recorder` arguments: `-provider`, `-from`, `-as`, and `-case`
with the cases joined by commas. For a Groq model the suite is `openai` and
the keys carry the `groq@` prefix. Entries with no covered model to clone
have no `recorder` and `record: false`.

Within a provider, entries are ordered for recording: successors of a covered
family first, then newest first (the provider's created date, else the
version and date in the ID). `-max-record` marks entries round-robin across
providers, so one provider's backlog cannot use the whole budget.

`-max-cases` keeps the template's cases in this order: `text_basic`,
`stream_text`, `tool_call`, `tool_use`, `stream_tool_call`,
`stream_tool_use`, `thinking`, `reasoning`, `stream_thinking`,
`stream_reasoning`, then other plain cases by name, then cases whose
`case.json` names an error status or another endpoint. Cases marked
`"recordable": false` are never planned.

## Choosing the template

`SelectTemplate` ranks the section's covered models of the new model's
**kind**, the endpoint class it serves: `chat`, `completions`, `embeddings`,
`images`, `speech`, `transcription`, `rerank` or `ocr`. A new model's kind
is what its listing metadata says (a Gemini model with `embedContent` and no
`generateContent`, a Cohere model with the `rerank` endpoint and no `chat`),
else what its ID names (`embed`, `tts`, `transcribe`, `whisper`, `image`,
`dall`, `imagen`, `rerank`, `ocr`), else chat. A covered model's kinds are
what its cases' `case.json` endpoints exercise, so `gpt-3.5-turbo-instruct`
(completions cases) is no template for a chat model whatever its name. A
template's cases drive one endpoint class, so a model with no covered model
of its kind gets no template ("no covered openai speech model"): write its
fixtures by hand. Among those, each criterion outweighs everything after it:

1. **Bedrock: the same API.** The adapter sends inference profiles (`us.`,
   `eu.`, `ap.`, `global.`, ARNs) to Converse and every other ID to
   InvokeModel; their fixtures and allowlists are not interchangeable. A test
   reads `isInferenceProfile` in `internal/providers/bedrock/converse.go` and
   fails if the prefix lists disagree.
2. **The same vendor**: the Bedrock vendor segment, or a Groq ID's `vendor/`.
3. **The same family**: the ID's leading name with tier words removed
   (`gemini`, `gpt`, `claude`, `command-a`, `nova`, `gpt-oss`, `llama`).
4. **Thinking support, when the API states it for both** (Gemini `thinking`,
   Anthropic `capabilities.thinking`, a Cohere `reasoning` feature): a thinking
   model's cases are not cloned onto a model without thinking, or the other way
   round, while a compatible candidate exists. Otherwise the choice carries a
   warning.
5. **A shared tier word**: `flash`, `pro`, `lite`, `argon`, `sol`, `terra`,
   `luna`, `astra`, `haiku`, `sonnet`, `opus`, `fable`, `mythos`, `mini`,
   `nano`, `scout`... `flash-lite` and `flash` share `flash`; `argon` and
   `flash` share nothing.
6. **The same reasoning hint in the ID** (`command-a-reasoning-…` vs
   `command-a-…`, `…-non-reasoning`).
7. **The nearest version** (Anthropic's `4-5` reads as 4.5; Bedrock's `-v1:0`
   is dropped), else the nearest snapshot date (Cohere's `MM-YYYY`).
8. Then an exact tier match, the same parameter size, a template no newer than
   the new model, the newer template, the ID.

So, with today's fixtures: `gemini-4.1-argon` ← `gemini-4-argon`;
`gpt-5-mini` ← `gpt-5.5-2026-04-23` (not `gpt-4o-mini-transcribe-…`, the
only covered `mini`); `gpt-image-2.5` ← `gpt-image-2-2026-04-21`;
`gemini-3.9-flash-tts` ← `gemini-3.8-flash-tts`; `embed-english-v3.0` ←
`embed-v5.0-fast`;
`gpt-6.1-sol` ← `gpt-6-sol`; `claude-opus-5-6` ← `claude-opus-5-5`;
`gemini-3.9-flash-lite` ← `gemini-3.8-flash` (it shares `flash`, and 3.8 is
nearer than 3.1-flash-lite); `us.amazon.nova-2-pro-v1:0` ←
`us.amazon.nova-2-lite-v1:0`; `us.anthropic.claude-opus-5-5` ←
`global.anthropic.claude-sonnet-4-6` (Converse), not
`anthropic.claude-opus-5-5` (InvokeModel); `command-a-vision-07-2025` ←
`command-a-03-2025`, not the reasoning model. `selector_test.go` has the full
table.

## `-newest`

Covered models are grouped by family key (Bedrock API, vendor, family, tier
words, reasoning hint); the newest of each group (version, then date) is
re-recorded with `contract-recorder -model <id> -case ... -out`, and the
provider's contract test runs on it in live mode. With today's fixtures that is
43 families from 45 covered models (every section's credentials set). `-max-record` caps a run and `-rotate`
moves the window, so every family is re-recorded within
`ceil(families / cap)` days.

## Triage

Each NEW model needs a registry entry:

1. **Bring it under contract** (the tracking issue has the commands):
   `go run ./cmd/contract-recorder -provider P -from <template> -as <new>`;
   the recorder writes `"cloned_from": "<template>"` into the new
   `meta.json`, which makes the contract test diff each case's shape against
   the template's; write the goldens with `-update`; triage unknown fields
   and shapes into the adapter or the allowlist; add
   `{"id": "<new>", "status": "covered"}`.
2. **An alias**: `{"id": "<new>", "status": "alias", "alias_of": "<covered>"}`.
3. **Ignore it**: `{"id": "<new>", "status": "ignored", "reason": "..."}`, or an
   `ignore_rules` glob for a whole class.

`-update-registry` adds every NEW model as `ignored` with a `TODO(triage)`
reason, so a whole backlog can be triaged in one PR: `go test
./internal/providers/contractwatch/` fails until each placeholder is replaced.
Do this before enabling the daily workflow on a repository whose registry has
not seen the live APIs yet: the first run otherwise reports every uncovered
model (a simulation from the SDK model lists and `litellm.json` came to about
120 across providers) and opens issues for them at `max_new_issues` a day.

## The daily workflow

`.github/workflows/agentcc-provider-new-models.yml`, daily at 04:41 UTC and on
demand. Not on pull requests: it calls paid APIs and needs secrets.

1. `model-watch -check -plan-out plan.json -max-record $max_new_models -max-cases $max_cases`.
2. `model-watch -newest -max-record $max_newest_models -rotate <day number>`.
3. For each NEW entry marked `record`: `contract-recorder -provider P -from T
   -as N -case ... -exclude-endpoint assistants,vector_stores -out
   $RUNNER_TEMP/model-watch/recordings/new/<n>` (the recorder writes
   `cloned_from` into the recorded `meta.json`), then
   `AGENTCC_CONTRACT_FIXTURES_DIR=<that dir> go test ./internal/providers/P/ -run '^TestContract$'`.
   Each recording has its own directory, so a model's test sees only its
   cases. The same for each newest entry, with `-model`. A recorder exit 3
   (no case it sends matched) is reported as "nothing to record", not
   failed.
4. `model-watch -report` renders the recording table and drift lines into
   the job summary, after the discovery summary, and one issue body per NEW
   model. Recordings, plans, logs and issue bodies are uploaded as the
   artifact `model-watch-<run id>` on every run.
5. The job fails while a model is NEW, a listing fails, coverage is stale,
   or a recording failed or drifted.
6. A second job, the only one with `issues: write` (everything else is
   `contents: read`), opens one issue per NEW model titled
   `[model-watch] New <provider> model not under contract: <id>`, or rewrites
   the body of the existing one (found by exact title); a closed issue for a
   still-NEW model is reopened with a comment, since only a registry entry
   clears it. It runs no repository code, only on `future-agi/future-agi`,
   and only for the default branch, and opens at most `max_new_issues` new
   issues per run.

Inputs (defaults on the schedule): `provider` (all), `max_new_models` 5,
`max_newest_models` 8, `max_cases` 4, `max_new_issues` 10. A run makes at most
`(max_new_models + max_newest_models) × max_cases` recording calls, 52 by
default, plus one list call per provider (two for Bedrock, more if paginated).

Secrets are the drift workflow's: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GROQ_API_KEY`, `PERPLEXITY_API_KEY`, `MISTRAL_API_KEY`, `CEREBRAS_API_KEY`,
`TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `OPENROUTER_API_KEY`,
`GEMINI_API_KEY`, `COHERE_API_KEY`,
`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (with
`bedrock:ListFoundationModels` and `bedrock:ListInferenceProfiles`), and
`AZURE_OPENAI_KEY` + `AZURE_OPENAI_ENDPOINT` for re-recording the azure
fixtures; repository variable `AWS_REGION` (default `us-east-1`, which the
registry's Bedrock entries assume).

## Tests

`go test ./internal/providers/contractwatch/ ./cmd/model-watch/` needs no
network: every lister is tested against `httptest` servers (pagination, auth
headers, metadata, error redaction, stuck cursors), the SigV4 signer against
AWS's published test vectors, and `TestMain` in both packages replaces the
default transport with one that refuses any non-loopback host. The coverage
test (`TestRegistryMatchesFixtures`) runs in the gateway CI's `make test` and
fails whenever the registry and `internal/providers/*/testdata/contracts`
disagree.
