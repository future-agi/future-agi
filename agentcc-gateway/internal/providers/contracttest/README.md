# Provider contract tests

A provider adapter turns an upstream response (Anthropic Messages, Gemini
`generateContent`, Bedrock, ...) into the gateway's canonical OpenAI shape.
When a provider changes that response — a new content block type, a new stop
reason, a new usage counter — a hand-written Go struct ignores it and the
content disappears with no error. Contract tests replay real (or faithfully
authored) upstream bodies through the adapter's real code path and fail when
that happens.

Fixtures are keyed by **exact upstream model ID**, because providers change
shapes per model release, not per API version.

## What is covered

- **Coverage**: [`internal/providers/CONTRACT_COVERAGE.md`](../CONTRACT_COVERAGE.md)
  is the grid of every provider key (each native adapter, each
  OpenAI-compatible preset as `<preset>@`, Vertex AI as `vertex@`, each
  speech-relay vendor) by every endpoint its adapter implements, with the
  number of cases in each cell, the exemptions with their reasons, and the
  totals (models, cases, error cases, recordable and synthetic-only cases).
  `internal/providers/contractcoverage` generates it and fails while an
  implemented cell has neither a case nor a reasoned exemption, so its
  numbers are the coverage. As of this writing: 207 implemented cells over
  21 provider keys, 89 with contract cases and 118 exempt with a sourced
  reason (99 not served, 12 unverifiable, 6 answered without an upstream
  call, 1 relayed unparsed), none uncovered; 73 models and 383 cases, 111 of
  them error cases.
- **Losses**: [`internal/providers/KNOWN_GAPS.md`](../KNOWN_GAPS.md) lists
  every `known gap <ID>` the suites, the spec checks and the preset-health
  table (`TestPresetHealth`) allowlist, one line per ID, with where each is
  pinned: 187 IDs as of this writing. A gap is allowlisted, not fixed.
- **Drift between pull requests**: three scheduled workflows. None runs on
  pull requests; every secret is optional, and a provider without its secret
  is skipped.

| workflow | when | catches | secrets |
|---|---|---|---|
| [`agentcc-provider-spec-drift.yml`](../../../../.github/workflows/agentcc-provider-spec-drift.yml) | daily | a provider's official machine-readable spec changing (a new field, enum value, stream event); see `contractspec/README.md` | none |
| [`agentcc-provider-new-models.yml`](../../../../.github/workflows/agentcc-provider-new-models.yml) | daily | a model a provider lists that no fixture covers, recorded from a template; and the newest covered model of each family, re-recorded; see `cmd/model-watch/README.md` | the drift job's, used to list (`bedrock:ListFoundationModels` and `bedrock:ListInferenceProfiles` for AWS) and to record |
| [`agentcc-provider-contract-drift.yml`](../../../../.github/workflows/agentcc-provider-contract-drift.yml) | weekly | a live response that no longer matches the committed contract, for every recordable case; see "Scheduled drift check" | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `PERPLEXITY_API_KEY`, `MISTRAL_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `COHERE_API_KEY`, `AZURE_OPENAI_KEY` + `AZURE_OPENAI_ENDPOINT`, `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (variable `AWS_REGION`) |

Vertex AI (`vertex@` fixtures) is recorded only by hand: it needs an access
token that lasts an hour, which a repository secret cannot hold.

## Layout

Each provider package holds its own fixtures, so its tests can reach
unexported types:

```
internal/providers/<provider>/
  contract_test.go                       TestContract (see anthropic/ for the template)
  testdata/contracts/
    opaque.golden.json                   regions the adapter's structs do not watch (strict mode), written by -update
    <model-dir>/                         contracttest.ModelDir(<model-id>)
      meta.json                          provenance + allowlists, one per model
      <case>/
        case.json                        optional: endpoint, status, headers, content type, recordability
        request.json                     the request the gateway receives (canonical models.ChatCompletionRequest for chat)
        <upload>                         transcription / translation, optional: the audio request.json's "file" names
        upstream_patch.json              optional, recorder only: a JSON merge patch on the outbound body
        response.json | response.sse | response.eventstream | response.bin | response.txt
                                         raw upstream body, exactly one of these
        golden.json                      canonical output (or error), written by -update
        upstream_request.golden.json     what the gateway sent upstream, written by -update
```

- `<model-id>` is the model the gateway sends upstream (the dated snapshot when
  there is one, e.g. `claude-sonnet-4-5-20250929`).
- The directory is `contracttest.ModelDir(<model-id>)`: the ID with every byte
  outside RFC 3986's unreserved set (`A-Z a-z 0-9 - . _ ~`) percent-encoded,
  uppercase hex. Most IDs are their own directory name; Bedrock's
  `us.amazon.nova-2-lite-v1:0` lives in `us.amazon.nova-2-lite-v1%3A0`
  (Windows cannot check out a `:`, and Go module zips reject one), and a
  Vertex-style `claude-x@date` gets `%40`. `Discover` decodes the name and
  rejects any other spelling (a literal `:`, lowercase `%3a`), so
  `Case.Model` and `meta.json`'s `model` always hold the real ID. Never build
  these paths with `url.PathEscape`, which leaves `:` alone. Tools and flags
  (the recorder's `-model`, `-from`, `-as`) take real IDs.
- `meta.json`'s `model` must be the ID the directory name decodes to; the
  Anthropic test also checks the outbound request's `model` matches.
- `response.json` is a whole JSON body; `response.sse` a `text/event-stream`
  body; `response.eventstream` a binary AWS event-stream body (Bedrock);
  `response.bin` opaque bytes such as speech audio; `response.txt` a body
  that is not JSON, such as an HTML 502 page from a proxy in front of the
  provider. The file chooses the replay `Content-Type` (unless `case.json`
  sets one), and for a 2xx case whether it is a stream: `request.json`'s
  `stream`, when it has one, must agree. An error case's body is the error
  the upstream answered with, so `Case.IsStream` follows `request.json`.
- Recorded bodies are stored byte for byte. Synthetic JSON bodies may be
  pretty-printed for review; JSON parsing does not care. SSE bodies keep the
  provider's framing (`event:` / `data:` lines, blank-line separators).
- Case names are free-form; use `stream_` as a prefix for streaming cases.

### meta.json

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-5-20250929",
  "source": "synthetic",
  "recorded_at": null,
  "notes": "where the bodies came from and anything a reader should know",
  "allowed_unknown_fields": [
    {"path": "$.usage.service_tier", "reason": "known gap: ..."}
  ],
  "allowed_unknown_shapes": [
    {"kind": "unmapped_finish_reason", "value": "refusal", "reason": "known gap: ..."}
  ]
}
```

| field | rule |
|---|---|
| `provider` | the adapter name passed to `Discover` |
| `model` | the directory name |
| `source` | `"synthetic"` (hand-authored from provider docs) or `"recorded"` (captured from the real API) |
| `recorded_at` | `null` for synthetic, `"YYYY-MM-DD"` for recorded |
| `allowed_unknown_fields` | JSON paths the adapter's structs do not represent, each with a reason |
| `allowed_unknown_shapes` | unknown-shape reports the adapter makes on these fixtures, each with a reason; `kind` must be a `common.ShapeKind` |
| `cloned_from` | optional: the model ID this model's cases were cloned from (the recorder's `-from`); turns on the cross-model shape diff (see "Shape diffs") |
| `allowed_type_mismatches` | optional: paths whose JSON type the adapter's struct does not accept, each with a reason (strict mode) |
| `optional_fields` | optional: paths allowed to appear, disappear or change type between two captures of a case, each with a reason; an entry covers everything below it (see "Shape diffs") |

Unknown keys in `meta.json` are an error, so a typo cannot silently disable an
allowlist. The three optional fields are omitted when empty, so a meta.json
that does not use them is unchanged.

### case.json

Optional, one per case, strictly validated like `meta.json`. Without it a case
is a 200 `chat` case whose content type follows its response file.

```json
{
  "endpoint": "chat",
  "status": 429,
  "headers": {"retry-after": "3", "anthropic-ratelimit-requests-remaining": "0"},
  "content_type": "application/json",
  "recordable": false,
  "notes": "why the body looks like this, and why it cannot be recorded",
  "sanity_gaps": [{"failure": "carries no retry hint", "reason": "known gap X3: ..."}]
}
```

| field | default | rule |
|---|---|---|
| `endpoint` | `chat` | the provider method the case drives; one of `contracttest.Endpoints` (below) |
| `status` | 200 | the status replayed: 2xx, or 400-599 for an error case (`Case.IsError()`) |
| `headers` | none | extra response headers replayed, e.g. `retry-after`, rate-limit headers, `request-id`; not `Content-Type` (use `content_type`) or framing headers |
| `content_type` | by response file | overrides the response file's: `application/json`, `text/event-stream`, `application/vnd.amazon.eventstream`, `application/octet-stream` (`.bin`), `text/plain; charset=utf-8` (`.txt`) |
| `recordable` | `true` | `false` for a case no real request can induce (429, 5xx, most mid-stream errors): it stays synthetic, the recorder must skip it, and live mode ignores any recording of it. Needs `notes` |
| `notes` | | free text |
| `sanity_gaps` | none | failures the case's sanity check is known to report because of an adapter gap, each a substring of the failure plus a reason; applied by `AssertSaneError` and `ExpectGaps`, and stale when they no longer occur |

The fields are on `Case` as `Endpoint`, `Status`, `Headers`, `ContentType`,
`Recordable`, `Notes` and `SanityGaps`. `ReadCaseSpec(dir)` reads one for
tools (the recorder). In live mode a recording without its own `case.json`
uses the repo's same-named case's, as it does `request.json`; the
recorder's `-out` writes one wherever the repo's would be wrong (see "Live
mode").

Endpoints, one per capability interface in `internal/providers/provider.go`
that has an implementation (constants `contracttest.Endpoint*`):

| endpoint | drives | request.json |
|---|---|---|
| `chat` | `ChatCompletion` / `StreamChatCompletion` | `models.ChatCompletionRequest` (`Case.Request`) |
| `chat_via_responses` | `ChatCompletionViaResponses` / `StreamChatCompletionViaResponses` | `models.ChatCompletionRequest` (`Case.Request`) |
| `completions` | `TextCompletion` (`/v1/completions`) | `models.CompletionRequest` |
| `embeddings`, `images`, `speech`, `transcription`, `translation`, `rerank`, `ocr` | `CreateEmbedding`, `CreateImage`, `CreateSpeech`, `CreateTranscription`, `CreateTranslation`, `Rerank`, `OCR` | the canonical request type |
| `responses`, `assistants`, `vector_stores` | the OpenAI passthroughs | the raw request body |
| `anthropic_messages`, `anthropic_count_tokens` | `CreateAnthropicMessage` / `StreamAnthropicMessage`, `CountAnthropicTokens` | the raw Messages body |
| `genai_generate`, `genai_count_tokens`, `genai_embed` | `GenerateContent` / `StreamGenerateContent`, `GenAICountTokens`, `EmbedContent` | the raw GenAI body |
| `list_models` | `ListModels` | `{}` |

For every endpoint but the two chat ones `Case.Request` is nil and the test
decodes `Case.RawRequest` into its own type. Search, video, realtime and the
speech relay have no endpoint: nothing implements the first three, and the
relay is a WebSocket byte pipe (its contract is the speech package's
sessions, `speech/testdata/sessions/`). Add an endpoint when one is wired;
`contractcoverage`'s `TestCapabilityTable` fails until the grid knows it.

### Request inputs: uploads and passthrough requests

Two kinds of request are more than a JSON body. The helpers are in
`contracttest/inputs.go`.

- **Uploads** (`transcription`, `translation`): the request is a multipart
  form. `request.json` holds the canonical request's JSON fields, and
  optionally `"file": "<name>"`, the upload's file name, whose extension is
  the audio format (upstreams read the format from it): `"audio.mp3"`.

  ```json
  {"model": "whisper-1", "file": "audio.mp3", "response_format": "verbose_json"}
  ```

  The replay server never listens to the audio, but the suites upload
  exactly what the recorder sends (`c.InputFile()`), so the upstream golden
  pins the upload's name and size. Recording needs real audio: the recorder
  uploads the file of that name beside `request.json`, and fails the case
  while there is none (`-dry-run` says so). Keep it short, a few seconds of
  speech, and licence-free: the openai and azure cases upload `audio.mp3`
  (English) and `german.m4a` (German), speech generated with espeak-ng and
  ffmpeg, each case's `case.json` notes saying how. Without `"file"` the upload is
  `contracttest.DefaultUpload()`, half a second of silence as `speech.wav`:
  fine for the shape of a plain answer, but a model may answer silence with
  no segments or words at all, so a `verbose_json`, timestamp or
  translation case should name real speech. `c.InputFile()` returns the
  upload (`Name`, `Data`) the way the recorder sends it: the named file
  beside the `request.json` in use (the repo's, for a live recording that
  has none), or the default; an error wrapping `fs.ErrNotExist` when the
  named file is absent.

- **Passthrough requests** (`assistants`, `vector_stores`): the gateway
  forwards a method, a path and a query as well as a body, so `request.json`
  is a `contracttest.PassthroughRequest` (`DecodePassthrough` reads it
  strictly), for example `{"method": "GET", "path": "/v1/vector_stores",
  "query": {"limit": ["2"]}}` or `{"method": "POST", "path":
  "/v1/vector_stores", "body": {"name": "docs"}}`. `method` is `GET`, `POST` or `DELETE`; `body` is absent for a `GET` or
  `DELETE`; an Assistants run streams when its body says `"stream": true`.
  A path with an object ID (`/v1/vector_stores/vs_abc/files`) only works
  against the account that owns the object: mark such a case `"recordable":
  false`. Calls that create objects can be recorded by hand; the drift job
  does not record these two endpoints.

- **Streams**: a case streams when `request.json` says `"stream": true`. A
  native GenAI body has no `stream` key (streaming is another URL), so a
  `genai_generate` case streams when its response file is `response.sse`,
  or, for the recorder, before it has one, when its name starts with
  `stream`.

- **Client headers**: the native passthroughs (`anthropic_*`, `genai_*`)
  get none beyond what the adapter sets itself (`anthropic-version`), in the
  suites and in the recorder alike.

## Running

```sh
go test ./internal/providers/<provider>/ -run Contract           # check
go test ./internal/providers/<provider>/ -run Contract -update   # rewrite goldens
go test ./internal/providers/<provider>/ -run 'Contract/<model-id>/<case>'
```

`-update` is defined by this package, so pass it only to packages whose tests
import it: `go test ./internal/providers/... -update` fails with "flag
provided but not defined" in the others.

Review golden diffs like code: a golden change is a change in what clients
receive.

## What a contract test checks

For every case, `TestContract` builds the real provider against a replay
server and calls the capability method its `case.json` endpoint names
(`ChatCompletion` or `StreamChatCompletion` for chat; see the endpoint table
under "case.json"), then asserts:

1. **Canonical output golden** (`golden.json`): the response, or for a stream
   `{"chunks", "error", "assembled"}` where `assembled` is what an OpenAI
   client would end up with, or for an error case the canonical error
   (`ErrorGolden`).
2. **Outbound request golden** (`upstream_request.golden.json`): method, path,
   query (sorted; `key` / `api_key` / `access_token` values `REDACTED`),
   headers (credentials `REDACTED`, signing dates `VOLATILE`, transport headers
   dropped) and decoded body.
3. **No unknown fields**: every key in the upstream body maps to a field of the
   struct the adapter decodes it with (`UnknownFields`), unless allowlisted.
4. **No unknown shapes**: the adapter reported nothing it skipped
   (`common.ShapeReporter`), unless allowlisted.
5. **Known event types** (streams): every event type is in the provider's
   documented set (`AssertKnownEventTypes`).
6. **Sanity** (`AssertSane`): canonical `finish_reason`; `tool_calls` only with
   tool calls; tool calls have an id, a name and JSON arguments; usage present,
   `prompt_tokens > 0`, totals add up; and if the upstream body carried text or
   a tool call, the canonical output does too.

7. **Strict mode** (`Strict.Check`, which subsumes check 3): type mismatches,
   unexplained decode errors, enum values, opaque regions, and shape diffs.
   See "Strict mode".
8. **Error sanity** (`AssertSaneError`, error cases): see "Error cases".
9. **Mutation self-test** (`Strict.AssertMutationsDetected`): every part of
   the body the checks claim to watch is mutated, and every mutant must fail
   checks 3-8.

Checks 3 and 4 are what catch silent drops: a new block type leaves the golden
unchanged if the adapter skips it, so the golden alone cannot.

## Unknown fields and the allowlist

`UnknownFields(raw, reflect.TypeFor[T]())` walks the raw JSON against `T`'s
json tags the way `encoding/json` would and returns the keys a decode would
discard. Paths look like `$.content[].citations` — `[]` for any array element,
`.*` for any map value, no indices — and only the outermost unknown key is
listed. The walk treats `any`, `json.RawMessage`, and types implementing
`json.Unmarshaler` / `encoding.TextUnmarshaler` as accepting everything; when
the adapter decodes such a raw field later into its own struct, audit that
payload too with `UnknownFieldsAt(raw, typ, root)` (the Anthropic test does
this for citations and for every stream payload).

Stream paths are prefixed with the event type: `message_delta:$.delta.stop_details`.

Allowlist semantics:

- An entry matches one exact path.
- Every entry needs a reason. Use `"known gap <ID>: ..."` for content the
  gateway loses and should not, and say why it matters; say why otherwise
  (e.g. "no canonical field").
- A gap ID names one loss across every model and case it affects, so it can
  be cited in tickets. Reuse the ID when the same loss shows up again; add a
  new one with the provider's prefix otherwise: Anthropic `T` `C` `S` `U` `X`
  `R`, Gemini `G` (`GR` request side), Vertex AI `V`, OpenAI `O`, Azure `A`,
  Bedrock `B`, Cohere `K`, Groq `GQ`, Perplexity `P`, Mistral `MI`,
  OpenRouter `OR`, Together `TG`, Cerebras `CB`, Fireworks `FW`, xAI `XA`,
  Replicate `RP`, DeepInfra `DI`, Hugging Face `HF`, Anyscale `AN`, Deepgram
  `DG`, Cartesia `CA`. IDs are unique across providers. A preset whose base
  URL or path is wrong files its gap in `TestPresetHealth`
  (`internal/providers/presets_health_test.go`). Write a definition as
  `known gap <ID>: <reason>` (a mention without the colon is a reference,
  which must resolve to a definition); `KNOWN_GAPS.md` is generated from
  them, and `contractcoverage`'s `TestKnownGaps` fails on a malformed or
  undefined ID, and on an ID whose definitions word the loss differently
  until `contractcoverage/gap_summaries.json` has its reviewed summary (two
  losses under one ID get renumbered instead).
- The allowlist cannot rot. When every case of a model has been audited in one
  run, an entry that no case produced fails the test. A `-run` filter that
  selects only some cases skips this check, and so does live mode.
- Fields that carry nothing by protocol for every model (e.g. Anthropic's
  `message_start` `stop_reason`, always null) belong in the provider's test
  code, not in every `meta.json`; see `streamProtocolFields` in
  `anthropic/contract_test.go`.

## Strict mode

The unknown-field check only sees keys the structs lack. Strict mode also
catches a known field whose JSON type changed (Go's decoder skips it and
returns only the first error, which adapters often swallow), new values in
enum-like fields, drift inside regions the structs cannot see, and shape
changes between captures. A provider declares it once:

```go
var contractStrict = &contracttest.Strict{
	Docs:    contractDocs,  // func(c Case) []Doc: the body's JSON documents and the Go type each decodes into
	Enums:   ContractEnums, // exported: the spec-diff check reads them
	Decoded: []contracttest.Decoded{
		{Path: "$.content[].citations", Type: reflect.TypeFor[[]anthropicCitation]()},
		{Path: "content_block_delta:$.delta", Tag: "type", Types: map[string]reflect.Type{
			"text_delta": reflect.TypeFor[textDelta](), "input_json_delta": reflect.TypeFor[inputJSONDelta](),
		}},
	},
	Watched: nil,                          // []Watched{{Path, Keys}}: opaque objects with a known key set
	Free:    []string{"$.content[].input"}, // arbitrary by design: tool input / arguments
	Ignore:  streamProtocolFields,          // unknown fields that carry nothing by protocol, path -> reason
}
```

and calls `contractStrict.Check(t, c)` once per case **instead of**
`AssertKnownPaths` (it calls that itself).

- **`Docs`** returns, for a JSON body, one `Doc{Root: "$", Data: body, Type}`;
  for a stream, one `Doc` per event in order (as the codec splits it),
  rooted `"<event>:$"`; for an error body, one rooted at
  `contracttest.ErrorRoot` (`"error:$"`), the same root a mid-stream error
  event gets, so both share paths, allowlist entries and enums. A `Doc`
  with a nil `Type` is not decoded by the adapter (reported opaque); a
  `Doc` whose data is not JSON by design (`[DONE]`) gets a nil `Type`.
- **Type checks.** The walk follows encoding/json's rules and reports every
  value of the wrong kind: `$.usage.input_tokens: got string, struct wants
  integer`, a fractional number into an integer field, an object or array
  where a scalar is expected. `,string` fields must be strings; a type with
  `UnmarshalJSON` or `UnmarshalText` holding a scalar is checked by calling
  it; `json.RawMessage` and `any` accept anything. Every document (and every
  `Decoded` region) is also `json.Unmarshal`ed into its type, and an error no
  mismatch explains fails as a walker gap. A mismatch fails unless
  `meta.json` lists the path under `allowed_type_mismatches`, with the same
  staleness rule as unknown fields.
- **Enums.** `EnumSpec{Spec, Paths, Values}`: every value found at one of
  `Paths` anywhere in the body, modeled or not, must be in `Values`; null is
  not a value. `Spec` names where the values come from (an OpenAPI schema,
  a doc page). A `"*:"` prefix matches every event root (`"*:$.type"` is
  every stream event's type). The values are the provider's documented set,
  not what the adapter handles: an unhandled documented value is the
  unknown-shape check's job. When the provider documents a new value, add
  it. After a full run the suite logs every enum path no fixture reached,
  because a mistyped path (a map's values are at `.*`, not `.key`) silently
  checks nothing.
- **Opaque regions.** Where the walk has no Go type to follow
  (`json.RawMessage`, `any`, the values of a `map[string]any`, a custom
  unmarshaler holding an object), it reports the path, unless a declaration
  covers it:
  - `Decoded{Path, Type}`: the adapter decodes the region later, with Type;
    or `Decoded{Path, Tag, Types}` for a tagged union, where an occurrence
    whose tag has no type stays opaque and is reported as
    `path [tag=value]`, and a non-object or a missing tag is a type mismatch;
  - `Watched{Path, Keys}`: an object whose keys are known though no type
    models it; a new key is an unknown field;
  - `Free`: arbitrary by design (tool input), not reported, mutated or
    shape-diffed. Do not declare a modeled region Free.

  Once every case has run, the union of opaque paths is compared with
  `testdata/contracts/opaque.golden.json` (written by `-update`): a new
  unwatched region shows up in review. The Anthropic list is the stream
  payloads the parser skips (`content_block_delta:$.delta
  [type=thinking_delta]`, ...) and the web-search result content.
- **Shape diffs**: see below.

`Strict.Report(c)` returns the same findings without asserting, for tools.

## Shape diffs and the cross-model check

`Report.Shape` maps every path to the JSON kinds seen there (array elements
unioned under `[]`, inside opaque regions too, not inside Free ones).
`ShapeDiff(base, new)` returns `{Added, Removed, TypeChanged}`; null next
to another kind is not a change, but a path that became null-only (or
stopped being) is. `Strict.Check` diffs a case's shape in two situations:

- **Live mode**: against the committed fixture of the same case.
- **`cloned_from`**: against the same case of the model it names, from the
  repo. A brand-new model's first capture is compared with a reviewed model
  instead of becoming its own baseline unseen.

`Removed` and `TypeChanged` paths fail unless `meta.json` lists them (or a
parent) under `optional_fields` with a reason; a removed object is reported
once, not once per child. `Added` paths the structs do not model are unknown
fields already; the ones they model are logged. When the baseline is
synthetic the message says so: if the new capture is right, promote it.

## Error cases

An error case is a case whose `case.json` has a `status` of 400 or more. The
replay server answers with that status, `headers` and the error body
(`response.json`, or `response.txt` for a body that is not JSON). The test
calls the adapter as usual and asserts:

1. a golden of the canonical error, `AssertGolden(t, c.GoldenPath(),
   contracttest.ErrorGolden(err))`: status, type, code, message, param, and
   `retry_after` when the error carries a retry hint (a `RetryAfter()
   time.Duration` method);
2. the upstream request golden, as for any case;
3. `Strict.Check`: unknown fields against the adapter's error struct, type
   mismatches (a numeric `code` where the struct has a string is how an
   OpenRouter 429 becomes a 502), and the error-type enum (`Docs` roots
   the body at `ErrorRoot`);
4. `AssertSaneError(t, c, err)`: an error came back, it is a
   `*models.APIError` with a message, an upstream 4xx stays a 4xx (a 502
   is retried, failed over and counts against the circuit breaker), a 429
   stays a 429, a 5xx stays a 5xx, and a `retry-after` / `retry-after-ms`
   header becomes a retry hint. Known failures go in `case.json`'s
   `sanity_gaps`, with a gap ID, as for any allowlist.

Do not fix the adapter in the same change that adds an error case: allowlist
what it gets wrong. Mark cases nothing real can induce `"recordable":
false`. `CONTRACT_COVERAGE.md` counts every provider key's error cases.
Among the Anthropic suite's are `stream_error_overloaded` (an overloaded
error mid-stream, not recordable), `error_rate_limit` (429 with
`retry-after` and `anthropic-ratelimit-*`, not recordable; gap X3) and
`error_invalid_request` (400 from an over-limit `max_tokens`, recordable).

## Mutation self-test

`Strict.AssertMutationsDetected(t, c, check)` makes "every path in every
fixture is watched" a tested claim. From `c`'s report it derives:

- an unknown key in every object a struct (or `Watched`) models;
- a type flip of every modeled node (string to number, anything else to a
  string), and `1.5` in every integer field;
- `"__probe__"` at every path an `EnumSpec` covers;
- for a stream, an extra event of type `"__probe__"` after the first.

Each distinct (mutation, path, Go type) runs once, at its first occurrence.
Opaque and Free regions are not mutated. `check` is the provider's whole
per-case check **without goldens** (a golden is not a drift detector in
live mode); it first runs on the unmutated body as a control that must
pass, then on every mutant, against a `testing.TB` that records failures.
The test fails for every mutant `check` let through, with its path. Mutants
use a copy of the meta that records nothing, and skip shape diffs and stale
gap checks, so they cannot disturb the real case's bookkeeping.

A provider whose stream is not SSE sets `Strict.Codec` to a `Codec{Decode,
Encode}` for its framing (Bedrock's event stream). `Strict.MaxMutations`
caps the mutations per case (default 400, sampled evenly; the unknown event
is always kept). Every suite logs its totals: as of this writing the six
suites detect all of about 6,100 mutations over their 371 cases (the
Anthropic suite: 1,000 over 41 cases, in about half a second, two under
`-race`).

## Unknown-shape reports (the silent-drop fix)

`internal/providers/common/shape.go` gives adapters a way to say "I skipped
something I did not understand":

```go
shape := common.NewShapeReporter(ctx, "anthropic", p.id) // once per upstream response
shape.Report(common.ShapeUnknownBlock, block.Type)       // nil-safe
```

Kinds are a closed set: `unknown_event`, `unknown_block`, `unknown_delta`,
`unmapped_finish_reason`, `malformed_payload`. In production a report never
fails the request. It logs a warning (at most once a minute per distinct
provider/kind/value), and `main` counts it as
`agentcc_provider_unknown_shape_total{provider,kind}` when Prometheus is
enabled — the upstream value is in the log, never in a label. Each distinct
report counts once per response.

Tests observe reports through the context:

```go
ctx, shapes := common.WithShapeRecorder(context.Background())
resp, err := p.ChatCompletion(ctx, c.Request)
contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
```

A shape the adapter is known to drop is listed in `allowed_unknown_shapes`
with its reason, under the same staleness rule as fields.

## Golden normalization

`AssertGolden` marshals the value (using its `MarshalJSON`, so the golden is
the wire form), sorts keys, indents two spaces, and does not HTML-escape. It
scrubs values that change run to run:

- a non-zero number under a `created` key → `"<nonzero>"` (zero stays 0, so a
  missing timestamp still shows);
- an `id` of the form `<prefix>-<16+ digits>` (the gateway's time-based IDs:
  `gen-…`, `bedrock-…`, `cohere-…`) → `"<prefix>-<generated>"`;
- any key passed as `scrubKeys` → `"<scrubbed>"`.

## Live mode

A scheduled job can record fresh responses from the real APIs and replay them
through the same tests:

```sh
AGENTCC_CONTRACT_FIXTURES_DIR=/path/to/recordings \
  go test ./internal/providers/anthropic/ -run Contract
```

The directory is laid out `<provider>/<model-dir>/<case>/` (`<model-dir>` is
`ModelDir(<model-id>)`, as in the repo) and needs only the response file per
case, plus a `case.json` where the repo's no longer describes the recording;
`cmd/contract-recorder -out` writes exactly this (see "Recording real
fixtures"). In live mode:

- cases come from the recording directory, not `testdata/contracts`;
- a missing `request.json` or `case.json` falls back to the repo's same-named
  case, and a missing `meta.json` to the repo's same-named model (or, for a
  model the repo has never seen, an empty allowlist with `source:
  "recorded"`). A named upload sits beside the `request.json` in use. A
  recording's own `case.json` replaces the repo's whole, so the
  recorder writes a complete one: the repo's, with the live `status`,
  `headers` or `content_type`. It writes one for every error case (its
  status and headers are the live ones), for every case of a cloned model
  whose template case has one (a new model has no repo case to fall back
  to), and when a `response.bin` or `response.txt` came back with another
  media type;
- a recording of a case whose `case.json` says `"recordable": false` is
  ignored: nothing real induces that case, so the recording answers a
  different question (the recorder never sends one);
- strict mode diffs each case's shape against the committed fixture of the
  same case (see "Shape diffs");
- `golden.json` is compared only if the recording directory has one — live
  text differs every run — while `upstream_request.golden.json` sits beside
  the `request.json` in use, so a repo request is still pinned;
- unknown fields, unknown shapes, event types and sanity are all checked, so a
  new field or block type in a live response fails the job;
- stale allowlist entries and known sanity gaps that do not occur are not
  reported, since a live response need not reproduce every shape a fixture
  pinned (a prompt the model answers instead of blocking, say).

To promote a recording, copy it into `testdata/contracts/<model-dir>/<case>/`,
set `source: "recorded"` and `recorded_at`, run with `-update`, and review the
golden diff. Recording in place with `cmd/contract-recorder` (no `-out`) does
the copy and the `meta.json` edit for you.

## Recording real fixtures

`cmd/contract-recorder` replaces synthetic bodies with real ones. For each
selected case it builds the provider's adapter through the gateway's own
registry (`providers.NewRegistry`, presets included), sends the case's
`request.json` to the real API through the capability method its `case.json`
endpoint names, as the gateway's handler for that route would (see the
endpoint table under "case.json"; `chat` without one), and saves the upstream
body byte for byte. A fixture is then exactly what the provider returns for
exactly the request the gateway sends.

How it captures: adapters build their own `http.Transport`, and the only hook
their config offers is `DialContext`. The recorder points the adapter at
`http://<real host><real path>` and dials every connection to an in-memory
teeing proxy (`net.Pipe`; no port is opened). The proxy re-sends the request
over https to the same host and path with the same headers, streams the
answer back, and keeps a copy of the body. Host, path, headers and body are
unchanged, so a Bedrock SigV4 signature (which does not cover the scheme)
still verifies; `sigv4_test.go` checks that with an independent verifier. The
proxy reads the body to the end even when the adapter stops early, and lets
its own transport undo gzip, so the fixture is the plain body. It forwards
only to the configured host, and never follows redirects.

Run it from `agentcc-gateway/`:

```sh
go run ./cmd/contract-recorder -dry-run                 # the plan per endpoint, what is skipped, which keys are missing
go run ./cmd/contract-recorder -provider anthropic      # record in place
go run ./cmd/contract-recorder -provider anthropic -model 'claude-haiku-*' -case 'stream_*'
go run ./cmd/contract-recorder -provider openai -endpoint speech,transcription
go run ./cmd/contract-recorder -out /path/to/recordings # live-mode layout, every provider that has a key
go run ./cmd/contract-recorder -out /path/to/recordings -endpoint images -max-models 1 -max-cases 1
```

| flag | meaning |
|---|---|
| `-provider` | comma-separated adapter names (default: every provider with fixtures) |
| `-model`, `-case` | comma-separated real model IDs / case names; `path.Match` globs allowed |
| `-endpoint`, `-exclude-endpoint` | comma-separated `case.json` endpoints to keep / to drop (a case without `case.json` is `chat`); unknown names are an error |
| `-max-cases N` | at most N cases per model and endpoint: `text_basic`, `stream_text`, the tool cases, the thinking cases, then other 2xx cases by name, then error cases (0: no cap) |
| `-max-models N` | at most N models per provider and endpoint, in model-directory order (0: no cap) |
| `-rotate K` | with `-max-models`, start K places into each endpoint's model list (wrapping), so a scheduled job passing a day or week number reaches every model over time |
| `-out <dir>` | write the live-mode layout, `<dir>/<provider>/<model-dir>/<case>/`, instead of recording in place; the repo is not touched |
| `-from <id> -as <id>` | clone a model's cases into a new model ID, record them against it, and write `cloned_from` (needs one `-provider`) |
| `-dry-run` | print the plan; nothing is sent or written |
| `-base-url` | send one provider's cases to a different base URL (with that provider's credentials) |
| `-timeout` | per case, default 3m |

The caps count per endpoint, so capping cannot starve an endpoint:
`-max-models 1 -max-cases 1` records one case of every endpoint of every
provider. Not-recordable cases are never sent and do not count against them.

Credentials come from the environment and are never written anywhere:

| provider | variables |
|---|---|
| anthropic | `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| openai, models keyed `<preset>@<model>` | `<PRESET>_API_KEY`: `GROQ_API_KEY`, `PERPLEXITY_API_KEY`, `MISTRAL_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY` for the presets the suite uses today |
| gemini | `GEMINI_API_KEY` (Google AI Studio) |
| gemini, models keyed `vertex@<model>` | `VERTEX_PROJECT` and `VERTEX_ACCESS_TOKEN` (from `gcloud auth print-access-token`; it lasts an hour); optional `VERTEX_LOCATION`, default `global` |
| cohere | `COHERE_API_KEY` |
| azure | `AZURE_OPENAI_KEY` and `AZURE_OPENAI_ENDPOINT`; the fixtures' model IDs must be deployment names on that resource. Optional `AZURE_OPENAI_API_VERSION` replaces every case's own api-version |
| bedrock | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (optional `AWS_SESSION_TOKEN`); region `AWS_REGION`, else `AWS_DEFAULT_REGION`, else `us-east-1` |

The openai suite files an OpenAI-compatible upstream as
`<preset>@<upstream model>` (e.g. `groq@openai/gpt-oss-120b`), where the
preset is a `providers.KnownProviders` entry; those cases are recorded against
the preset's base URL and API path, with the preset's key. The gemini suite
files the same adapter in Vertex AI mode as `vertex@<model>`, recorded with the
Vertex AI variables above. No other `@` is split: it is part of real model IDs
elsewhere.

A provider (or preset) whose variables are missing is skipped with a message
and does not fail the run. Exit status: 0 when every selected case was
recorded (or skipped for want of a key), 1 when any case failed, 2 on bad
usage, 3 when no recordable case matched the selection, so nothing was sent
(a capped pass over endpoints a provider may not have treats 3 as success).

What gets sent:

- Each case through its endpoint's capability method
  (`cmd/contract-recorder/dispatch.go`): the request type decoded from
  `request.json`, the upload for `transcription` and `translation`, the
  method, path, query and body of a passthrough request, and the case's
  model ID as the URL model of a `genai_*` call. A streamed answer is read
  to the end. An adapter without the endpoint's capability fails the case;
  so does one that sends nothing upstream (the Anthropic, Gemini, Cohere and
  Bedrock `ListModels` answer from config, so their `list_models` cases need
  `"recordable": false`), and a multipart case whose named upload is not in
  the repo.
- Each case with the adapter configured as its suite configures it: an
  azure case at the `api-version` its committed
  `upstream_request.golden.json` pins (the suite calls preview-only
  deployments such as `gpt-image-1` at a preview version), unless
  `AZURE_OPENAI_API_VERSION` is set; a gemini `vertex@<model>` case in
  Vertex AI mode.
- Never a case whose `case.json` says `"recordable": false`. The recorder
  prints a `skipped` line for each, and their committed synthetic bodies
  stay.

What gets written:

- In place: each case's response file, replaced, keeping the kind it has
  (`response.json`, `.sse`, `.eventstream`, `.bin`, `.txt`). A case without
  one yet (a new case, or a clone) gets the kind its `Content-Type` implies:
  `text/event-stream` `.sse`, the AWS event stream `.eventstream`, JSON
  `.json`, other `text/*` `.txt`, anything else `.bin` (an error body that
  is not JSON `.txt`). When every recordable case of a model was recorded,
  its `meta.json` becomes `source: "recorded"`, `recorded_at: <today, UTC>`,
  and nothing else in it changes; not-recordable cases stay synthetic, and
  their `case.json` says so. Recording only some cases (`-case`,
  `-endpoint`, a cap) leaves `meta.json` alone, because it describes all of
  them; finish the model before committing. Then review `notes`, which may
  still describe synthetic bodies (the recorder reminds you, for a model and
  for each error case). A Bedrock stream's `response.eventstream.json` (the
  hand-written source the bedrock suite generates synthetic streams from) is
  deleted, because a recorded body has no source and `-update` would
  regenerate the binary over it.
- `case.json` changes only where the recording says something new: an error
  case's `headers` become the response's whitelisted headers (below), a
  `response.bin` or `response.txt` whose media type changed gets the new
  `content_type`, and a 2xx case answered with another 2xx gets that
  `status`. Its `endpoint`, `notes`, `recordable` and `sanity_gaps` are
  kept. A media-type change on a JSON or stream file is a warning instead:
  the file kind already fixes how it replays.
- With `-out`: the response file, plus `case.json` where live mode must not
  fall back to the repo's (see "Live mode"): always for an error case, and
  for the changes above. Live mode takes `request.json`, a named upload and
  `meta.json` from the repo.
- A 2xx answer never overwrites an error case, nor an error a 2xx case. An
  error case (`case.json` `status` 400-599) is saved only when upstream
  answers with exactly that status, and a 2xx case only when it answers
  2xx. Anything else is printed (redacted), not saved, and makes the exit
  status 1, so a provider that starts accepting a request it used to
  reject, or the reverse, fails the drift job. A 2xx body the adapter fails
  on *is* saved, with a warning: that is the drift the contract test exists
  to report. Unknown-shape reports made during recording are printed as
  warnings.
- Recordable error cases: induce the status with `request.json` itself
  (Anthropic's `error_invalid_request` asks for `max_tokens` over the
  model's limit), or with an `upstream_patch.json` when the gateway would
  not send the bad value (see below). Rate limits, overloads and 5xx cannot
  be induced on purpose: mark them `"recordable": false`.
- Response headers are saved for error cases only, and only these:
  `retry-after`, `retry-after-ms`, `x-ratelimit-*`, `anthropic-ratelimit-*`,
  `x-amzn-ErrorType`, `x-request-id` and `request-id`: what retry logic and
  clients act on, and the ID a provider's support asks for. The recorded set
  replaces the committed one; a header the committed `case.json` already
  lists keeps its spelling, new ones are lower-cased. Organisation and
  project IDs, cookies and everything else are dropped.
- Credentials: any configured credential value found in a body or a saved
  header is replaced with `REDACTED`, with a warning. Error bodies, saved
  headers and printed failures are also scrubbed of anything
  credential-shaped (bearer tokens, `sk-` keys masked or not, `gsk_` keys,
  Google `AIza` keys, AWS access key IDs). Providers do not echo credentials
  in 2xx bodies.

The recorder is the one non-test importer of this package: it uses
`ModelDir`, `ReadCaseSpec`, `ReadInputFile`, `DecodePassthrough`,
`ResponseFiles` / `DefaultContentType` / `IsStreamFile`, the file-name
constants and `Meta`, so its layout cannot drift from `Discover`'s. (The
import registers `-update` on the global flag set; the recorder parses its
own.)

### Requests the canonical path cannot express

The recorder sends what the gateway sends. When a case needs a provider knob
the canonical request cannot carry yet, add `upstream_patch.json` to the case:
a JSON merge patch (RFC 7396) applied to the outbound body during recording
only. Example: the Anthropic adapter drops `thinking` (gap R1), so the
`thinking` cases recorded as they are would come back without thinking blocks.
Their fix is

```json
{"thinking": {"type": "enabled", "budget_tokens": 2048}}
```

Contract tests ignore the file, and `upstream_request.golden.json` still pins
what the gateway itself sends, so the gap stays visible. Delete the patch once
the adapter forwards the knob. The same patch can induce a recordable error
case's status with a value the gateway would never send. Bedrock signs the
body, and a transcription or translation body is a multipart form, so a patch
there fails the case.

### A new model version, end to end

When a provider ships a model (say `gemini-4.1-argon` after `gemini-4-argon`):

1. Record the previous version's cases against the new ID:

   ```sh
   go run ./cmd/contract-recorder -provider gemini -from gemini-4-argon -as gemini-4.1-argon
   ```

   This creates `testdata/contracts/<ModelDir(new id)>/` with each case's
   `request.json` (only `model` changed, key order kept; a native GenAI body,
   whose model is in the URL, and a passthrough request, unchanged),
   whichever of `case.json`, a named upload and `upstream_patch.json` it
   has, the recorded response, and a
   `meta.json` with `source: "recorded"`, `cloned_from: "gemini-4-argon"` and
   the previous version's allowlists. A suite in strict mode then diffs every
   case's shape against the old model's, so what the new model added,
   dropped or retyped shows up even where the structs accept it. A case
   marked `"recordable": false` is not sent: once at least one case has
   recorded, it is copied unchanged, still synthetic, with `case.json` notes
   saying so (re-author it if the new model answers differently). An error
   case is saved only if the new model answers with the same status.
   `-case` clones a subset, `-max-cases` caps it. For whatever the new
   version changed, add cases by copying a case directory and recording it
   in place. To try it without touching the repo, add `-out <dir>` (which
   leaves not-recordable cases out, since live mode ignores them) and run
   `AGENTCC_CONTRACT_FIXTURES_DIR=<dir> go test ./internal/providers/gemini/ -run Contract`.
2. Write the goldens: `go test ./internal/providers/gemini/ -run Contract -update`.
3. Run again without `-update`, and review the whole diff: fixtures, goldens
   and `meta.json`. The goldens are what clients will receive from this model.
4. Triage what fails:
   - an unknown field or shape: translate it in the adapter (then `-update`
     again), or allowlist it in the new `meta.json` with a reason
     (`known gap: ...` when content is lost);
   - a stale allowlist entry, one the new model no longer produces: delete it;
   - an unknown event type or a non-canonical finish reason: fix the parser;
   - (strict mode) a type mismatch: fix the struct or allowlist it under
     `allowed_type_mismatches`; an enum value: add it to the `EnumSpec` if
     the provider documents it; a removed or retyped path in the
     cross-model diff: list it under `optional_fields` if the model only
     sometimes sends it, otherwise it is drift to handle.
5. Rewrite `notes` to say what the version changed, and commit.

## Scheduled drift check

`.github/workflows/agentcc-provider-contract-drift.yml` runs weekly (Monday
05:23 UTC) and on demand, with an optional comma-separated provider filter.
It records every provider whose secret is set into `$RUNNER_TEMP/contracts`
with `-out`, in two passes over every endpoint that has fixtures:

- text endpoints (chat, embeddings, rerank, responses, the native
  passthroughs, token counting, model lists): every recordable case;
- media endpoints (`images`, `speech`, `transcription`, `translation`,
  `ocr`), which cost more per call: at most `media_max_models` models per
  provider and endpoint and `media_max_cases` cases per model and endpoint
  (dispatch inputs, default 1 and 1; 0 is no cap), rotated by the week
  number so every model is reached over time.

`assistants` and `vector_stores` are not recorded: their calls create
objects in the account. A pass that matches nothing (exit 3) is fine. The
gemini suite's `vertex@<model>` cases are skipped: Vertex AI needs a
short-lived access token, which a repository secret cannot hold.

It then runs each recorded provider's `TestContract` in live mode against
that directory. Goldens are skipped, because live text differs every run.
Unknown fields, unknown shapes, unknown event types, canonical finish
reasons, error mappings and sanity are all checked against the committed
allowlists, so a provider changing a model's response shape fails the job,
and so does an error case whose status changed (the recorder refuses to save
it). The recordings are uploaded as an artifact on every run, pass or fail,
for diffing or promotion.

It does not run on pull requests: it calls paid APIs and needs secrets. The
committed fixtures run on every PR in the gateway CI's `make test`.

Repository secrets, each optional; a provider without one is skipped, but the
job fails when nothing at all was recorded:

| secret | provider |
|---|---|
| `ANTHROPIC_API_KEY` | anthropic |
| `OPENAI_API_KEY` | openai |
| `GROQ_API_KEY`, `PERPLEXITY_API_KEY`, `MISTRAL_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY` | the openai suite's `<preset>@<model>` models |
| `GEMINI_API_KEY` | gemini |
| `COHERE_API_KEY` | cohere |
| `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` | azure |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | bedrock (repository variable `AWS_REGION`, default `us-east-1`) |

The keys need access to every model that has fixtures. A retired model fails
the job (upstream 404) until its directory is removed or replaced.

When it fails, download the artifact and reproduce locally with
`AGENTCC_CONTRACT_FIXTURES_DIR=<artifact dir> go test ./internal/providers/<provider>/ -run Contract`.
Then triage as in step 4 above; to keep a recording as a fixture, record it in
place with the recorder.

## New models

Fixtures exist only for models someone added. `cmd/model-watch` notices the
rest: it lists each provider's models through the provider's own list-models
API (Anthropic, OpenAI, Gemini, Cohere, Groq, Mistral, Cerebras, Bedrock's
`ListFoundationModels` + `ListInferenceProfiles`; a checked static list for
Perplexity; Azure has deployments, not models, Vertex AI needs a short-lived
token, and the aggregators Together, Fireworks and OpenRouter list hundreds
of third-party routes, so those are not listed), with the recorder's
credentials, and diffs them against
`internal/providers/contractwatch/known_models.json`.

That registry gives every listed model ID exactly one status: `covered` (its
model directory here exists), `alias` (`alias_of` a covered snapshot), or
`ignored` (with a mandatory reason; `ignore_rules` globs cover classes such as
fine-tunes). Models the provider's own metadata rules out (none of the
methods or endpoints the adapter parses, a fine-tune, deprecated, an
announced shutdown) need no entry. Anything else is **NEW**: chat models,
and embeddings, image, speech, transcription, rerank and OCR models too,
since each of those endpoints has covered models.

```sh
go run ./cmd/model-watch -offline     # registry vs these directories; no network
go run ./cmd/model-watch -check       # list, diff, plan; exit 1 on NEW models
```

`go test ./internal/providers/contractwatch/` runs the offline half on every
PR: **adding, renaming or removing a model directory fails it until the
registry agrees**. Add the model as `{"id": "<model-id>", "status": "covered"}`
in its provider's section (for `<preset>@<model>` fixtures, the preset's
section, and for `vertex@<model>` the vertex section, without the prefix)
and run `go run ./cmd/model-watch -fmt`.

For each NEW model, `model-watch` picks the covered model to clone from: one
of the same kind (chat, embeddings, images, speech, transcription, rerank,
OCR or completions, from the listing's metadata or the ID; a covered model's
kind is its cases' endpoints), then the same Bedrock API, vendor and family;
thinking support when the API states it; a shared tier word such as `flash`,
`argon`, `sol`, `opus`; the nearest version. So step 1 of "A new model
version, end to end" becomes:

```sh
go run ./cmd/contract-recorder -provider gemini -from gemini-4-argon -as gemini-4.1-argon
```

which writes `"cloned_from": "gemini-4-argon"` into the new `meta.json`, so
the cross-model shape diff compares the new model's first capture with the
template's.

`.github/workflows/agentcc-provider-new-models.yml` runs this daily: it
records each NEW model from its template into `-out` (capped by
`max_new_models` and `max_cases`; model-watch picks the template's
recordable cases, text and tool cases first, and passes them as `-case`),
runs the provider's
`TestContract` in live mode on the recording, re-records the newest covered
model of each family (capped by `max_newest_models`, rotating daily), writes
the results to the job summary, and opens or updates one tracking issue per
NEW model with the steps above. It fails while any model is NEW; a registry
entry is what clears it. RETIRED (a covered model upstream no longer lists)
is reported, not failed: its fixtures can no longer be re-recorded, and the
weekly drift job will fail on it. See `cmd/model-watch/README.md`.

## Official-spec checks (contractspec)

Fixtures only show what someone recorded. `internal/providers/contractspec`
checks every adapter against the provider's **official machine-readable
spec**: OpenAI and Anthropic OpenAPI, Gemini and Vertex discovery, Bedrock
Smithy, Cohere, Azure 2024-10-21, Groq, Together and OpenRouter. It does this
offline, with no credentials and no model calls.

- **Committed subsets.** `contractspec/subsets/<provider>/<root>.json` holds
  the flattened contract of each response, stream and error body an adapter
  parses: paths in this package's `UnknownFields` syntax
  (`message_delta:$.delta.stop_reason`, `chunk:$.choices[].delta.refusal`),
  with their kinds, enum values, nullability and stream events.
  `source.json` records the provenance: the URL at a pinned commit or
  discovery revision, a sha256, and the fetch date. Full specs are never
  vendored.
- **`TestSpecCoverage`** is in each wired adapter package's `spec_test.go` and
  runs in `make test`. Every spec path must be one of:
  - mapped by the adapter's Go types (reflection, plus `Bind` for payloads
    decoded in a second step);
  - forwarded through an `Extra` map;
  - inside a declared blob;
  - allowlisted with a reason in `testdata/spec_allowlist.json`.

  It also checks enum values and events. Every stop/finish reason, block,
  delta and item type, and stream event the spec lists must be handled. The
  test proves it by calling the adapter's real code (`lookupStopReason`, the
  stream parser and its `ShapeReporter`), or by an allowlist entry. A Go type
  that cannot decode a kind the spec allows also fails. For example,
  OpenRouter's integer `error.code` going into `ErrorDetail.Code string`.

  Stale entries fail. The test logs a coverage figure per root, e.g.
  `anthropic/messages: 26/116 paths mapped, 43 in declared blobs, 45 allowlisted (27 entries) ...; 116/116 accounted`.
- **`TestSpecEnums`** compares each adapter's `EnumSpec` declarations (its
  `ContractEnums`; see "Strict mode") with the spec. A value the spec lists
  that the declaration lacks fails the test.
- **Daily drift job.** `.github/workflows/agentcc-provider-spec-drift.yml` runs
  `go run ./cmd/contract-specs -fetch -check`. It fetches each spec at its
  latest commit (`git ls-remote`, then raw at the SHA), flattens it, and diffs
  it against the committed subsets. The job fails on a new field, enum value,
  stream event, kind change or newly-nullable field, and opens or updates one
  `Provider spec drift: <provider>` issue per provider.
- **Providers without a fetchable spec** are Perplexity, Fireworks, Cerebras,
  Mistral, xAI, DeepSeek and DeepInfra. They are listed in
  `contractspec/subsets/unfetchable.json` with the reason. Where a Stainless or
  Speakeasy spec hash is published, it is tracked as a change signal.

The spec allowlist uses the same gap IDs as `meta.json`, so one ID names one
loss across fixtures and the spec. `known gap S1` covers both a fixture's
`unmapped_finish_reason refusal` and the spec's `$.stop_reason = refusal`.
Spec findings that no fixture shows get new IDs in the same sequence.

When the drift job fails, run these from `agentcc-gateway/`:

```sh
go run ./cmd/contract-specs -fetch -update -provider <p>       # re-pin the subset; review its git diff
go test ./internal/providers/... -run SpecCoverage             # see what the adapter does not map or handle
```

Then map each finding in the parser, or allowlist it with a reason. If a
response shape changed, add a fixture for it (see "Recording real
fixtures"). To adopt the check in another
adapter package, copy `anthropic/spec_test.go` or `openai/spec_test.go`, run
`CONTRACTSPEC_DRAFT=1 go test ./internal/providers/<p>/ -run SpecCoverage` to
draft the allowlist, and replace its `TODO` reasons. See
`internal/providers/contractspec/README.md`.

## Live integration tests

`internal/server/integration_test.go` (build tag `integration`) drives the
whole gateway HTTP handler against real APIs; each test skips unless its key
is set. The native adapters, Anthropic and Gemini, are covered for a plain
completion, a stream, a forced tool call and a streamed tool call. Each test
checks for a canonical finish reason, non-empty output (unless cut off with
`length`), usage, and a tool call with an id, a name and JSON arguments.

```sh
ANTHROPIC_API_KEY=... GEMINI_API_KEY=... \
  go test -tags integration ./internal/server/ -run 'TestIntegration_(Anthropic|Gemini)' -v
```

The models default to aliases that follow each provider's current small model
(`claude-haiku-4-5`, `gemini-flash-latest`); override them with
`AGENTCC_IT_ANTHROPIC_MODEL` and `AGENTCC_IT_GEMINI_MODEL`. These are smoke
tests of the full path; response shapes are pinned by the contract tests.

## Adding a model version

With an API key, the recorder's `-from` / `-as` does steps 1 and 2 with real
responses; see "A new model version, end to end" above.

1. Create `testdata/contracts/<model-dir>/meta.json` with empty allowlists.
2. Add cases: copy `request.json` from the previous version's cases and set
   `model`; add the response body (recorded if you can; otherwise authored
   from the provider's API reference, `source: "synthetic"`, with the doc
   pages named in `notes`). Cover at least text, tool use, and their streaming
   forms, plus whatever the version changed.
3. `go test ./internal/providers/<provider>/ -run Contract -update` to write
   the goldens, then run again without `-update`.
4. For each unknown field or shape reported: translate it, or allowlist it
   with a reason.

## Adding contract tests to another provider

Copy `anthropic/contract_test.go`. Replace the response struct, the stream
envelope and payload structs, the known event set, and the
`responseHasOutput` / `streamHasOutput` rules. Wire `common.ShapeReporter`
into the adapter where it skips unknown blocks, events, deltas and finish
reasons.

To adopt strict mode, the mutation self-test and error cases in a suite that
already exists:

1. Split the test into `runCase(t testing.TB, c) out` (replay server,
   provider, call), `assertGoldens(t, c, out)`, and `checkCase(t
   testing.TB, c, out)` (everything else). Build the provider from a
   `testing.TB`, not with a `*testing.T` helper, so mutants can run it.
2. Declare `Strict`: write `Docs` from the dispatch your stream audit does
   today, turn each `UnknownFieldsAt` on a raw payload into a `Decoded`
   entry, move protocol-field filters to `Ignore`, and declare tool
   arguments `Free`. Replace the `AssertKnownPaths` call with
   `contractStrict.Check(t, c)`.
3. Declare an exported `[]EnumSpec` from the provider's spec: finish or
   stop reasons, block/part/item types, delta and event types, roles,
   tiers, statuses, error types.
4. Run with `-update` and review: `opaque.golden.json` (is each region
   really unwatched?), new type mismatches and enum violations (fix the
   declaration, or allowlist the gap with a new ID).
5. Call `contractStrict.AssertMutationsDetected(t, c, func(t testing.TB, m
   contracttest.Case) { checkCase(t, m, runCase(t, m)) })` after the real
   checks. Every undetected mutant is a blind spot to wire or declare.
6. Add error cases: in `checkCase`, for `c.IsError()` call
   `AssertSaneError` instead of failing on the adapter's error, and golden
   `ErrorGolden(err)`. Root the error body at `ErrorRoot` in `Docs`.
