# Provider spec contracts

The contract tests (`../contracttest`) replay real or authored responses
through each adapter. They only see the shapes someone recorded. This package
checks the adapters against the providers' **official machine-readable API
specs** instead. Those specs list every field, enum value, stream event and
error type of every response the gateway parses, so we can:

- **catch drift as soon as a provider publishes it.** A daily job diffs each
  spec against a committed, flattened subset, before any model call and before
  any response carries the change.
- **make coverage a number.** A test in every adapter package accounts for each
  spec path, enum value and event. Each one is mapped by a Go type, carried in a
  declared blob, forwarded, or allowlisted with a reason. Anything else fails.

Nothing here needs credentials or the network at test time.

## Pieces

```
internal/providers/contractspec/
  openapi.go discovery.go smithy.go   flatteners: OpenAPI 3.0/3.1/3.2, Google discovery, Smithy 2.0
  subset.go diff.go                   the subset format and the drift diff
  providers.go                        subset definitions: per provider, the spec's location and the roots we parse
  coverage.go enums.go                Check (TestSpecCoverage) and CheckDeclaredEnums (TestSpecEnums)
  subsets/<provider>/<root>.json      committed subsets (embedded)
  subsets/<provider>/source.json      provenance: URL, commit or revision, sha256, fetch date, licence
  subsets/unfetchable.json            providers with no fetchable spec, why, and their change signal
cmd/contract-specs/                   fetch, flatten, -update or -check
internal/providers/<p>/spec_test.go   TestSpecCoverage (+ TestSpecEnums)
internal/providers/<p>/testdata/spec_allowlist.json
.github/workflows/agentcc-provider-spec-drift.yml
```

## The subset format

A subset is one root of one provider's spec: a response, a stream's events,
or an error body. It is canonical JSON with one path per line and sorted keys,
so `git diff` reads as a list of changed paths. Paths use the
`contracttest.UnknownFields` syntax:

- `$.a.b` steps into an object.
- `$.a[]` steps into the elements of an array.
- `$.m.*` steps into the values of a map.
- `<event>:$...` is a stream event (`message_delta:$.delta.stop_reason`).
- `chunk:$...` is a stream of undifferentiated chunks.

```json
{
  "schema": 1,
  "provider": "anthropic",
  "root": "messages",
  "format": "openapi",
  "select": "POST /v1/messages -> 200 application/json",
  "description": "Messages response, decoded by ChatCompletion (translateResponse); ...",
  "paths": {
    "$.content[].signature": {"kinds":["string"],"variants":["type=thinking"]},
    "$.model": {"kinds":["string"],"enum":["claude-haiku-4-5", "..."],"open_enum":true},
    "$.stop_reason": {"kinds":["string"],"enum":["end_turn","max_tokens","model_context_window_exceeded","pause_turn","refusal","stop_sequence","tool_use"],"nullable":true},
    "$.usage.speed": {"kinds":["string"],"enum":["fast","standard"],"nullable":true}
  }
}
```

| field | meaning |
|---|---|
| `kinds` | object, array, map, string, integer, number, boolean, any; plus `string(int64)` (discovery), `blob` and `timestamp` (Smithy) |
| `enum` | values from `enum`, `const`, a discriminator's mapping, or a Smithy enum |
| `open_enum` | a union of listed values and a plain string, such as model IDs. Growth here is informational |
| `nullable` | null allowed (OpenAPI 3.0 `nullable`, a 3.1 `"null"` type, or a null union arm) |
| `opaque` | free-form in the spec (any, a free-form object, a Smithy document) |
| `variants` | the union arms that carry this path (`type=thinking`; Smithy `<union>=image`) |
| `same_as` | a schema another root already flattens (Responses stream events carry the whole Response). Not repeated |
| `pruned` | the subtree is deliberately not listed (a request parameter echoed back), with the reason |
| `recursive` | the walk stopped at a cycle |
| `events`, `exception_events` | a stream's event names. For Smithy, these include the exception events, and an operation's error shape names |

A Smithy union also gets a pseudo-path `<path>.<union>`. Its enum lists the
union's members, so a new member reads as a new enum value. The coverage test
does not count it as a JSON key.

How each format is walked:

- **OpenAPI:** local `$ref` only, with 3.1 sibling keywords merged. `allOf`
  members are merged. `oneOf`/`anyOf` are merged at the same path, because that
  is how a flat Go struct models a union. `additionalProperties` becomes `.*`.
  `writeOnly` fields are skipped.
- **Discovery:** `$ref` by schema ID, and `format: int64` is recorded as
  `string(int64)`. Discovery has no `oneOf`.
- **Smithy:** members bound to HTTP headers, status or query are skipped.
  `httpPayload` is the body, `jsonName` is honoured, and a `@streaming` union is
  split into one root per event.

Specs are untrusted input. They are parsed and never executed. Only local refs
are followed, and every walk and every decoded size is bounded.

## Sources and pins

| provider | spec (format) | roots |
|---|---|---|
| openai | `openai/openai-openapi` `openapi.json` (OpenAPI 3.1) | chat, chat_stream, errors, completions, embeddings, images, transcription, translation, responses, responses_stream, models |
| anthropic | `anthropics/anthropic-sdk-python` `scripts/mock-spec.json.gz` (OpenAPI 3.1, gunzipped) | messages, stream, errors, models |
| gemini | `generativelanguage.googleapis.com/$discovery/rest?version=v1beta` (discovery) | generate_content, embed_content, batch_embed_contents, predict, models |
| vertex | `aiplatform.googleapis.com/$discovery/rest?version=v1beta1` (discovery; the version the gemini adapter calls) | generate_content, embed_content, predict |
| bedrock | `aws/api-models-aws` `bedrock-runtime-2023-09-30.json` (Smithy 2.0) | converse, converse_stream, converse_errors, invoke_stream |
| cohere | `cohere-ai/cohere-developer-experience` `cohere-openapi.yaml` (OpenAPI 3.1) | chat, chat_stream, errors, embed, rerank, models |
| azure | `Azure/azure-rest-api-specs` `.../OpenAIInference/stable/2024-10-21/inference.json` (OpenAPI 3.0; the adapter's default api-version) | chat, chat_stream, errors, embeddings, images, transcription |
| groq | `groq/groq-python` `scripts/mock` (`EMBEDDED_SPEC`, base64+gzip, read as data and never run) | chat, chat_stream, embeddings, transcription, models |
| together | `togethercomputer/together-py` `scripts/mock` (`EMBEDDED_SPEC`) | chat, chat_stream, errors, embeddings, images |
| openrouter | `OpenRouterTeam/python-sdk` `.speakeasy/in.openapi.yaml` (OpenAPI 3.1) | chat, chat_stream, errors, embeddings, models |
| xai | `xai-org/xai-sdk-ts` `spec/openapi.json` (OpenAPI 3.1; the REST API, in an Apache-2.0 SDK repository) | chat, chat_stream, completions, embeddings, images, responses, models |

How sources are pinned:

- **GitHub sources:** `git ls-remote` resolves the latest commit, and the tool
  fetches `raw.githubusercontent.com/<repo>/<sha>/<path>`. `source.json`
  records that immutable URL.
- **Discovery documents:** there is no history to pin to. `source.json`
  records the `revision` and the sha256 of the fetched bytes.

Raw hashes are no use for drift. A discovery document's bytes change between
two fetches minutes apart at the same revision. OpenAI's `info.version` has
stayed `2.3.0` across hundreds of commits. Only the subset is diffed.

Bedrock InvokeModel bodies are a blob in the Smithy model. They hold the
model's own format, which is Anthropic Messages for Claude, so the bedrock spec
test binds them to the `anthropic/*` subsets.

The full specs are not vendored. Cohere's is `proprietary`, and they total
about 34 MB. Only the extracted field names and enum values are kept.

**Providers without a fetchable spec** are listed in `subsets/unfetchable.json`,
with the reason. Where the provider publishes a value that changes whenever its
spec does, that value is tracked as a change signal: a moved value fails
`-check`, but there is no content to diff.

| provider | why | change signal |
|---|---|---|
| perplexity, fireworks, cerebras | spec only at a Stainless bucket URL, which returns 403 to anonymous callers | `openapi_spec_hash` in the SDK's `.stats.yml` |
| mistral | Speakeasy registry (needs auth) and docs host | `sources.mistral-openapi.sourceBlobDigest` in `client-python/.speakeasy/workflow.lock` |
| deepseek, deepinfra | no official machine-readable spec | none |

## cmd/contract-specs

Run it from `agentcc-gateway/`:

```sh
go run ./cmd/contract-specs -fetch -check                                 # drift check, every provider (exit 1 on drift, 2 on a fetch error)
go run ./cmd/contract-specs -fetch -update -provider anthropic            # re-pin one provider at its latest commit
go run ./cmd/contract-specs -fetch -ref <sha> -update -provider openai    # re-pin at a given commit
go run ./cmd/contract-specs -spec ./openapi.json -provider openai -check  # offline, against a local (decoded) copy
go run ./cmd/contract-specs -fetch -check -report out/ -cache specs/      # what CI runs: per-provider issue bodies and the fetched specs
```

`-check` prints a review-friendly diff. `FAIL` lines are changes that can
carry content the committed contract does not know about:

- a new field, with the paths under it counted
- a new enum value
- a new stream or error event
- a kind change
- a field that became nullable
- a new root, or a root that can no longer be extracted (its operation was
  renamed or removed)

Removals, open-enum growth (model IDs), deprecations and narrowing are
reported as `info`.

```
anthropic/messages:
  FAIL  new enum value       $.content[].content.error_code  + content_too_large
  FAIL  new field            $.diagnostics                   object|null  (+3 paths below)
  FAIL  new field            $.usage.speed                   string|null enum=fast,standard
  info  open enum grew       $.model                         + claude-haiku-5-5, claude-opus-5-5, claude-sonnet-5-5
  => 3 failing change(s), 1 informational
```

That is anthropic-sdk-python pinned at `f9b0cf2` (2026-09-03) and checked
against `202db72` (2026-10-23). The same run against openai-openapi pinned at
`db3e531` (2026-07-16) reports 77 failing changes. Among them:

- chat: `service_tier: fast`, `obfuscation`, `delta.audio`, `metadata`, new
  usage details, and two newly nullable fields
- the newly modeled chat error responses
- new Responses stream events

## Spec coverage tests

A provider package's `spec_test.go` declares which Go types decode each root.
`contractspec.Check` then classifies every spec path:

| class | meaning | needs |
|---|---|---|
| mapped | a Go field decodes it (reflection, the way `encoding/json` does: tags, embedded structs, case-insensitive) | nothing |
| forwarded | an unknown key directly under a `Passthrough` type (`models.ChatCompletionResponse`'s `Extra`), with its whole subtree | the type listed in `Passthrough` |
| blob | under a `json.RawMessage`, `any` or custom unmarshaler while the spec has structure below it | an `opaque` allowlist entry saying what happens to the blob, or a `Binding` for the type it is decoded into later |
| on an unhandled variant or event | every union arm carrying it is an allowlisted unhandled enum value, or its stream event is allowlisted | covered by that entry |
| unmapped | anything else | an `unmapped` allowlist entry at its outermost path |

Mapped paths are also checked for kind: when the spec allows a kind the Go
type cannot decode, `json.Unmarshal` fails on the whole body. An example is
OpenRouter's integer `error.code` going into `models.ErrorDetail.Code string`,
which turns every OpenRouter 429 into a 502. A mismatch must be fixed or listed
under `kinds` with a reason.

Enum values are checked by calling the adapter's own code, so the check
follows the code: `lookupStopReason`, `translateResponse` with a block of each
type, the stream parser with each event or delta (its `ShapeReporter` reports
say what it skips), `reportFinishReason`, `toChat`, `convert`. Unhandled values
must be allowlisted. Stream events are checked the same way.

The check also fails on:

- a stale allowlist entry: a mapped path, a handled value, or anything no
  longer in the spec
- an entry whose reason is empty or starts with `TODO`
- a subset of the provider that is neither checked nor listed in `Elsewhere`

`TestSpecEnums` compares the value sets a contract test declares
(`contracttest.EnumSpec`: each adapter package's `ContractEnums`, and the
openai package's by dialect and endpoint) with the spec. Every wired package
has it: anthropic, openai (with groq, together, openrouter and xai), azure,
gemini (with vertex), bedrock and cohere. It resolves `*:` wildcard paths across
every event, and `same_as` nodes into the subset that flattens them. It
fails when the spec lists a value the declaration lacks, and logs declared
values that the spec omits (Anthropic's `ping` and `error` events).

The allowlist (`testdata/spec_allowlist.json`) has five sections, and each
entry names its subset:

- `unmapped`: outermost paths
- `opaque`: blobs
- `enums`: path and value
- `events`
- `kinds`

Allowlist reasons follow the contract tests' convention:

- `known gap <ID>: ...` for content the gateway loses. Reuse the ID from
  `meta.json` when it is the same loss.
- `no canonical field: ...` when OpenAI's shape has no slot for it.
- `no content: ...` when the protocol makes it empty.

### Coverage today

Every wired root, from `go test ./internal/providers/{anthropic,openai,azure,gemini,bedrock,cohere}/ -run TestSpec -v`
(`checked in` is the adapter package whose `spec_test.go` checks the root: the openai package checks the groq,
together, openrouter and xai presets' specs too, the gemini package checks vertex, and the bedrock package binds
InvokeModel bodies, which are Anthropic Messages, to the `anthropic/*` subsets). Every root accounts for all its
paths.

| checked in | root | paths | mapped | in declared blobs | allowlisted (entries) | on unhandled variants / events | accounted | enum values checked | stream events |
|---|---|--:|--:|--:|--:|--:|--:|---|---|
| `anthropic` | `anthropic/errors` | 9 | 5 | 0 | 4 (2) | 0 | 9/9 | 9 at 1 paths, 4 allowlisted | – |
| `anthropic` | `anthropic/messages` | 116 | 26 | 43 | 45 (27) | 2 | 116/116 | 19 at 2 paths, 9 allowlisted | – |
| `anthropic` | `anthropic/stream` | 255 | 38 | 0 | 194 (30) | 23 | 255/255 | 24 at 3 paths, 13 allowlisted | 6/6 handled, 0 allowlisted |
| `openai` | `groq/chat` | 198 | 71 (46 via Extra) | 28 | 99 (4) | 0 | 198/198 | 4 at 1 paths, 0 allowlisted | – |
| `openai` | `groq/chat_stream` | 199 | 72 (47 via Extra) | 4 | 123 (6) | 0 | 199/199 | 4 at 1 paths, 0 allowlisted | – |
| `openai` | `groq/embeddings` | 12 | 10 | 2 | 0 (0) | 0 | 12/12 | – | – |
| `openai` | `groq/models` | 8 | 8 | 0 | 0 (0) | 0 | 8/8 | – | – |
| `openai` | `groq/transcription` | 2 | 2 | 0 | 0 (0) | 0 | 2/2 | – | – |
| `openai` | `openai/chat` | 119 | 62 (37 via Extra) | 45 | 12 (4) | 0 | 119/119 | 5 at 1 paths, 0 allowlisted | – |
| `openai` | `openai/chat_stream` | 108 | 62 (37 via Extra) | 12 | 34 (4) | 0 | 108/108 | 5 at 1 paths, 0 allowlisted | – |
| `openai` | `openai/completions` | 37 | 15 | 22 | 0 (0) | 0 | 37/37 | – | – |
| `openai` | `openai/embeddings` | 12 | 10 | 2 | 0 (0) | 0 | 12/12 | – | – |
| `openai` | `openai/errors` | 12 | 6 | 0 | 6 (1) | 0 | 12/12 | – | – |
| `openai` | `openai/images` | 22 | 14 | 0 | 8 (6) | 0 | 22/22 | – | – |
| `openai` | `openai/models` | 9 | 8 | 0 | 1 (1) | 0 | 9/9 | – | – |
| `openai` | `openai/responses` | 427 | 30 | 0 | 118 (42) | 279 | 427/427 | 32 at 2 paths, 28 allowlisted | – |
| `openai` | `openai/responses_stream` | 424 | 31 | 0 | 24 (17) | 369 | 424/424 | – | 9/59 handled, 50 allowlisted |
| `openai` | `openai/transcription` | 43 | 2 | 0 | 41 (8) | 0 | 43/43 | – | – |
| `openai` | `openai/translation` | 17 | 2 | 0 | 15 (3) | 0 | 17/17 | – | – |
| `openai` | `openrouter/chat` | 175 | 91 (66 via Extra) | 58 | 26 (6) | 0 | 175/175 | 5 at 1 paths, 1 allowlisted | – |
| `openai` | `openrouter/chat_stream` | 154 | 98 (73 via Extra) | 10 | 46 (5) | 0 | 154/154 | 5 at 1 paths, 1 allowlisted | – |
| `openai` | `openrouter/embeddings` | 26 | 10 | 2 | 14 (5) | 0 | 26/26 | – | – |
| `openai` | `openrouter/errors` | 7 | 4 | 0 | 3 (3) | 0 | 7/7 | – | – |
| `openai` | `openrouter/models` | 95 | 5 | 0 | 90 (20) | 0 | 95/95 | – | – |
| `openai` | `together/chat` | 54 | 39 (15 via Extra) | 9 | 6 (5) | 0 | 54/54 | 5 at 1 paths, 1 allowlisted | – |
| `openai` | `together/chat_stream` | 35 | 29 (3 via Extra) | 0 | 6 (5) | 0 | 35/35 | 5 at 1 paths, 1 allowlisted | – |
| `openai` | `together/embeddings` | 9 | 7 | 2 | 0 (0) | 0 | 9/9 | – | – |
| `openai` | `together/errors` | 6 | 6 | 0 | 0 (0) | 0 | 6/6 | – | – |
| `openai` | `together/images` | 10 | 5 | 0 | 5 (5) | 0 | 10/10 | – | – |
| `openai` | `xai/chat` | 69 | 44 (18 via Extra) | 23 | 2 (2) | 0 | 69/69 | – | – |
| `openai` | `xai/chat_stream` | 70 | 45 (19 via Extra) | 10 | 15 (2) | 0 | 70/70 | – | – |
| `openai` | `xai/completions` | 37 | 27 (12 via Extra) | 10 | 0 (0) | 0 | 37/37 | – | – |
| `openai` | `xai/embeddings` | 12 | 10 | 2 | 0 (0) | 0 | 12/12 | – | – |
| `openai` | `xai/images` | 27 | 12 | 0 | 15 (6) | 0 | 27/27 | – | – |
| `openai` | `xai/models` | 29 | 8 | 0 | 21 (13) | 0 | 29/29 | – | – |
| `azure` | `azure/chat` | 117 | 49 (25 via Extra) | 27 | 41 (4) | 0 | 117/117 | 5 at 1 paths, 0 allowlisted | – |
| `azure` | `azure/chat_stream` | 50 | 21 | 0 | 29 (3) | 0 | 50/50 | 5 at 1 paths, 0 allowlisted | – |
| `azure` | `azure/embeddings` | 12 | 10 | 2 | 0 (0) | 0 | 12/12 | – | – |
| `azure` | `azure/errors` | 30 | 6 | 0 | 24 (1) | 0 | 30/30 | – | – |
| `azure` | `azure/images` | 39 | 7 | 0 | 32 (2) | 0 | 39/39 | – | – |
| `azure` | `azure/transcription` | 18 | 2 | 0 | 16 (4) | 0 | 18/18 | – | – |
| `gemini` | `gemini/batch_embed_contents` | 13 | 5 | 0 | 8 (2) | 0 | 13/13 | – | – |
| `gemini` | `gemini/embed_content` | 12 | 4 | 0 | 8 (2) | 0 | 12/12 | – | – |
| `gemini` | `gemini/generate_content` | 300 | 34 | 4 | 262 (45) | 0 | 300/300 | 23 at 1 paths, 18 allowlisted | – |
| `gemini` | `gemini/predict` | 3 | 3 | 0 | 0 (0) | 0 | 3/3 | – | – |
| `gemini` | `vertex/embed_content` | 29 | 4 | 0 | 25 (2) | 0 | 29/29 | – | – |
| `gemini` | `vertex/generate_content` | 225 | 34 | 4 | 187 (44) | 0 | 225/225 | 19 at 1 paths, 14 allowlisted | – |
| `gemini` | `vertex/predict` | 8 | 3 | 0 | 5 (5) | 0 | 8/8 | – | – |
| `bedrock` | `anthropic/messages` | 116 | 17 | 43 | 52 (15) | 4 | 116/116 | 19 at 2 paths, 13 allowlisted | – |
| `bedrock` | `anthropic/stream` | 255 | 34 | 0 | 154 (28) | 67 | 255/255 | 24 at 3 paths, 16 allowlisted | 6/6 handled, 0 allowlisted |
| `bedrock` | `bedrock/converse` | 677 | 29 | 0 | 521 (16) | 127 | 677/677 | 23 at 2 paths, 17 allowlisted | – |
| `bedrock` | `bedrock/converse_errors` | 20 | 10 | 0 | 0 (0) | 10 | 20/20 | – | 5/9 handled, 4 allowlisted |
| `bedrock` | `bedrock/converse_stream` | 591 | 20 | 0 | 523 (17) | 48 | 591/591 | 18 at 3 paths, 11 allowlisted | 11/11 handled, 0 allowlisted |
| `bedrock` | `bedrock/invoke_stream` | 16 | 0 | 0 | 14 (6) | 2 | 16/16 | – | 6/7 handled, 1 allowlisted |
| `cohere` | `cohere/chat` | 48 | 23 | 0 | 24 (6) | 1 | 48/48 | 8 at 2 paths, 2 allowlisted | – |
| `cohere` | `cohere/chat_stream` | 98 | 44 | 22 | 7 (2) | 25 | 98/98 | 6 at 1 paths, 6 allowlisted | 9/12 handled, 3 allowlisted |
| `cohere` | `cohere/embed` | 50 | 12 | 0 | 38 (17) | 0 | 50/50 | – | – |
| `cohere` | `cohere/errors` | 3 | 2 | 0 | 1 (1) | 0 | 3/3 | – | – |
| `cohere` | `cohere/rerank` | 30 | 10 | 0 | 20 (10) | 0 | 30/30 | – | – |

Committed subsets no adapter decodes are declared in `Elsewhere`, with the reason: `anthropic/models`,
`cohere/models` and `gemini/models` are read by contractwatch's listers (cmd/model-watch), because those adapters'
`ListModels` return nil without calling upstream (known gaps X8, K10, G26). Bedrock's model list
(`ListFoundationModels`, `ListInferenceProfiles`) is in the control-plane Smithy model, which is not vendored, so
it has no committed subset at all; the bedrock adapter's `ListModels` calls no upstream either. `xai/responses` is
relayed, not decoded: the gateway passes a preset's `/v1/responses` body through and reads only its model and usage
(the server's `TestContractOpenAIResponsesUsage`, over the preset's fixtures), and never sends a preset's chat over
the Responses API, so the openai package's `presetSpecRoots` lists it in `Elsewhere`. Its finish reasons are free
strings, so the adapter's finish-reason check is left out for `xai/chat` and `xai/chat_stream`
(`contractPreset.SpecFreeStrings`); the xai fixtures' value sets pin stop, length and end_turn.

### Adopting it in a provider package

1. Write `spec_test.go`. Copy `anthropic/spec_test.go` (a native adapter) or
   `openai/spec_test.go` (an OpenAI-format one; its `openAIRoots(provider)`
   serves the groq, together, openrouter and xai subsets too):

   ```go
   func TestSpecCoverage(t *testing.T) {
       contractspec.Check(t, contractspec.Coverage{
           Provider: "gemini",
           Roots: []contractspec.Root{{
               Subset:   "gemini/generate_content",
               Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiResponse]())},
               Enums: []contractspec.EnumCheck{{Path: "$.candidates[].finishReason", By: "lookupGeminiFinishReason",
                   Handled: func(v string) bool { _, ok := lookupGeminiFinishReason(v); return ok }}},
           }},
           Elsewhere: map[string]string{"gemini/models": "decoded by contractwatch's GeminiLister"},
       })
   }
   ```

   - Bind every root the adapter decodes.
   - Add a nested `Bind` for each raw payload the adapter decodes in a second
     step. Give several types when it decodes by trial.
   - Give `EachEvent` the stream envelope type.
   - List `Passthrough` types.
   - Add an `EnumCheck` for each finish or stop reason, block, delta or item
     type the adapter dispatches on, calling its real function.
   - Set `Events` for streams.

   For an OpenAI-compatible preset, add another `Check` in `openai/spec_test.go`
   with `Provider: "groq"`, `Roots: openAIRoots(t, "groq")` (it keeps the roots
   the preset's spec has), and `Allowlist: "testdata/spec_allowlist.groq.json"`.

2. Run it with `CONTRACTSPEC_DRAFT=1 go test ./internal/providers/<p>/ -run SpecCoverage`.
   This writes `testdata/spec_allowlist.json` with every finding, keeping the
   existing reasons and marking new ones `TODO`.

3. Replace every `TODO` with a reason. Reuse the gap IDs from the package's
   `meta.json`, and continue the provider's ID sequence for new losses (the
   prefixes are listed in `contracttest/README.md`; `KNOWN_GAPS.md` shows the
   IDs in use). Then run without `CONTRACTSPEC_DRAFT` until it passes.

4. If the contract test declares `contracttest.EnumSpec` sets, add
   `TestSpecEnums`. It is a few lines; see `anthropic/spec_test.go`.

5. Review the allowlist like code. Each `known gap` is a field the gateway
   drops today.

## When the drift job fails

`.github/workflows/agentcc-provider-spec-drift.yml` runs daily (04:41 UTC) and
on demand, with an optional provider filter. It needs no secrets. On drift:

- it writes the diff to the job summary;
- it uploads the reports and the fetched specs (14 days);
- its `notify` job opens or updates one issue per provider, titled
  `Provider spec drift: <provider>`. Issues are deduped by exact title, and a
  closed one is reopened. `notify` is the only job with `issues: write`, and it
  runs no repository code.

To clear it:

1. `go run ./cmd/contract-specs -fetch -update -provider <p>`, then review
   `git diff internal/providers/contractspec/subsets/<p>`.
2. `go test ./internal/providers/... -run SpecCoverage`. Every new path, value
   or event now fails there, in the adapter that parses it.
3. Map it in the parser (a struct field plus its translation, a case in the
   lookup function, an event handler), or allowlist it with a reason.
4. Commit the subset, its `source.json` and the parser or allowlist change
   together, then close the issue.

For an unfetchable provider whose change signal moved, read its changelog,
update its contract fixtures if a parsed response changed, and record the new
value with `-fetch -update -provider <p>`.

## Limits

- Specs describe what a provider promises, not what it sends. Fields a
  provider returns without documenting (OpenRouter's `reasoning_details`,
  Groq's `x_groq`) still need the recorded fixtures and the weekly live
  contract drift job.
- Several specs leave streaming unmodeled or modeled wrongly:
  - Anthropic omits `ping` and `error`, and attaches its stream union to no
    operation.
  - Gemini and Vertex give `streamGenerateContent` the response schema. Their
    discovery documents model neither the error envelope (a `google.rpc.Status`
    in its HTTP JSON mapping) nor the SSE framing of a stream (data-only
    `alt=sse` events, a mid-stream error chunk); the gemini contract fixtures'
    `error_*` and `stream_*` cases are the only check on those.
  - Groq's stream schema is orphaned.
  - xAI's chunk schema is orphaned too, and its spec models no error bodies
    (400 and 422 have no content) and no Responses stream events, so xai has
    no errors or responses_stream root. Its finish reasons, and most output
    item types, are plain strings named only in prose.
  - Azure puts chunks under `application/json`.
  - OpenAI's `[DONE]` and mid-stream errors exist only in prose.

  These subsets root at the schema directly, and the adapters' extra events are
  logged by `TestSpecEnums`.
- Discovery documents have no history. A pinned revision cannot be refetched,
  so the committed subset is the only copy of the old contract.
- Some enums are closed in a spec but grow often, such as Anthropic's
  `error.details.error_code`. A new value there fails the drift job even
  though the adapter ignores the field. That is intended: the job reports what
  changed, and the coverage test decides whether it matters.
