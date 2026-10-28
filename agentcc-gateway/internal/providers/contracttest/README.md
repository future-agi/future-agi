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

## Layout

Each provider package holds its own fixtures, so its tests can reach
unexported types:

```
internal/providers/<provider>/
  contract_test.go                       TestContract (see anthropic/ for the template)
  testdata/contracts/<model-dir>/        contracttest.ModelDir(<model-id>)
    meta.json                            provenance + allowlists, one per model
    <case>/
      request.json                       canonical models.ChatCompletionRequest the gateway receives
      response.json | response.sse | response.eventstream
                                         raw upstream body, exactly one of these
      golden.json                        canonical output, written by -update
      upstream_request.golden.json       what the gateway sent upstream, written by -update
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
  body; `response.eventstream` a binary AWS event-stream body (Bedrock). The
  file chooses `Case.IsStream` and the replay `Content-Type`, and
  `request.json`'s `stream` must agree.
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

Unknown keys in `meta.json` are an error, so a typo cannot silently disable an
allowlist.

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
server and calls `ChatCompletion` or `StreamChatCompletion`, then asserts:

1. **Canonical output golden** (`golden.json`): the response, or for a stream
   `{"chunks", "error", "assembled"}` where `assembled` is what an OpenAI
   client would end up with.
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
  `R`, Gemini `G` (`GR` request side), OpenAI `O`, Groq `GQ`, Perplexity `P`,
  Azure `A`, Bedrock `B`, Cohere `K`. IDs are unique across providers.
- The allowlist cannot rot. When every case of a model has been audited in one
  run, an entry that no case produced fails the test. A `-run` filter that
  selects only some cases skips this check, and so does live mode.
- Fields that carry nothing by protocol for every model (e.g. Anthropic's
  `message_start` `stop_reason`, always null) belong in the provider's test
  code, not in every `meta.json`; see `streamProtocolFields` in
  `anthropic/contract_test.go`.

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
case; `cmd/contract-recorder -out` writes exactly this (see "Recording real
fixtures"). In live mode:

- cases come from the recording directory, not `testdata/contracts`;
- a missing `request.json` falls back to the repo's same-named case, and a
  missing `meta.json` to the repo's same-named model (or, for a model the repo
  has never seen, an empty allowlist with `source: "recorded"`);
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
`request.json` through it to the real API, and saves the upstream body byte
for byte. A fixture is then exactly what the provider returns for exactly the
request the gateway sends.

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
go run ./cmd/contract-recorder -dry-run                 # what would be recorded, where, and which keys are missing
go run ./cmd/contract-recorder -provider anthropic      # record in place
go run ./cmd/contract-recorder -provider anthropic -model 'claude-haiku-*' -case 'stream_*'
go run ./cmd/contract-recorder -out /path/to/recordings # live-mode layout, every provider that has a key
```

| flag | meaning |
|---|---|
| `-provider` | comma-separated adapter names (default: every provider with fixtures) |
| `-model`, `-case` | comma-separated real model IDs / case names; `path.Match` globs allowed |
| `-out <dir>` | write `<dir>/<provider>/<model-dir>/<case>/response.*` instead of recording in place; the repo is not touched |
| `-from <id> -as <id>` | clone a model's cases into a new model ID and record them against it (needs one `-provider`) |
| `-dry-run` | print the plan; nothing is sent or written |
| `-base-url` | send one provider's cases to a different base URL (with that provider's credentials) |
| `-timeout` | per case, default 3m |

Credentials come from the environment and are never written anywhere:

| provider | variables |
|---|---|
| anthropic | `ANTHROPIC_API_KEY` |
| openai | `OPENAI_API_KEY` |
| openai, models keyed `<preset>@<model>` | `<PRESET>_API_KEY`, e.g. `GROQ_API_KEY`, `PERPLEXITY_API_KEY` |
| gemini | `GEMINI_API_KEY` (Google AI Studio; Vertex AI is not supported) |
| cohere | `COHERE_API_KEY` |
| azure | `AZURE_OPENAI_KEY` and `AZURE_OPENAI_ENDPOINT` (optional `AZURE_OPENAI_API_VERSION`); the fixtures' model IDs must be deployment names on that resource |
| bedrock | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (optional `AWS_SESSION_TOKEN`); region `AWS_REGION`, else `AWS_DEFAULT_REGION`, else `us-east-1` |

The openai suite files an OpenAI-compatible upstream as
`<preset>@<upstream model>` (e.g. `groq@openai/gpt-oss-120b`), where the
preset is a `providers.KnownProviders` entry; those cases are recorded against
the preset's base URL and API path, with the preset's key. The split applies
only to the openai provider and known OpenAI-format presets, since `@` is part
of real model IDs elsewhere (Vertex).

A provider (or preset) whose variables are missing is skipped with a message
and does not fail the run. The exit code is 1 if any case failed, 2 on bad
usage.

What gets written:

- In place: each case's `response.json`, `response.sse`, or (Bedrock streams)
  `response.eventstream`, replacing the response file that was there. When
  every case of a model was recorded, its `meta.json` becomes
  `source: "recorded"`, `recorded_at: <today, UTC>`, and nothing else in it
  changes. Recording only some cases (`-case`) leaves `meta.json` alone,
  because it describes all of them; finish the model before committing.
  Then review `notes`, which may still describe synthetic bodies. A Bedrock
  stream's `response.eventstream.json` (the hand-written source the bedrock
  suite generates synthetic streams from) is deleted, because a recorded body
  has no source and `-update` would regenerate the binary over it.
- With `-out`: response files only. Live mode takes `request.json` and
  `meta.json` from the repo.
- A non-2xx answer is never saved: the recorder prints the status and the
  start of the body, carries on, and exits 1. A 2xx body the adapter fails on
  *is* saved, with a warning: that is the drift the contract test exists to
  report. Unknown-shape reports made during recording are printed as warnings.
- Any credential value found in a body is replaced with `REDACTED`, with a
  warning. Providers do not echo credentials in 2xx bodies; error bodies,
  which can (OpenAI's 401 shows a masked key), are printed redacted and never
  saved. Response headers, which carry organisation IDs, are not saved.

The recorder is the one non-test importer of this package: it uses `ModelDir`,
the file-name constants and `Meta`, so its layout cannot drift from
`Discover`'s. (The import registers `-update` on the global flag set; the
recorder parses its own.)

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
the adapter forwards the knob. Bedrock signs the body, so a patch there fails
the case.

### A new model version, end to end

When a provider ships a model (say `gemini-4.1-argon` after `gemini-4-argon`):

1. Record the previous version's cases against the new ID:

   ```sh
   go run ./cmd/contract-recorder -provider gemini -from gemini-4-argon -as gemini-4.1-argon
   ```

   This creates `testdata/contracts/<ModelDir(new id)>/` with each case's
   `request.json` (only `model` changed, key order kept), any
   `upstream_patch.json`, the recorded response, and a `meta.json` with
   `source: "recorded"` and the previous version's allowlists. `-case`
   clones a subset. For whatever the new version changed, add cases by
   copying a case directory and recording it in place. To try it without
   touching the repo, add `-out <dir>` and run
   `AGENTCC_CONTRACT_FIXTURES_DIR=<dir> go test ./internal/providers/gemini/ -run Contract`.
2. Write the goldens: `go test ./internal/providers/gemini/ -run Contract -update`.
3. Run again without `-update`, and review the whole diff: fixtures, goldens
   and `meta.json`. The goldens are what clients will receive from this model.
4. Triage what fails:
   - an unknown field or shape: translate it in the adapter (then `-update`
     again), or allowlist it in the new `meta.json` with a reason
     (`known gap: ...` when content is lost);
   - a stale allowlist entry, one the new model no longer produces: delete it;
   - an unknown event type or a non-canonical finish reason: fix the parser.
5. Rewrite `notes` to say what the version changed, and commit.

## Scheduled drift check

`.github/workflows/agentcc-provider-contract-drift.yml` runs weekly (Monday
05:23 UTC) and on demand, with an optional comma-separated provider filter.
It records every provider whose secret is set into `$RUNNER_TEMP/contracts`
with `-out`, then runs each recorded provider's `TestContract` in live mode
against that directory. Goldens are skipped, because live text differs every
run. Unknown fields, unknown shapes, unknown event types, canonical finish
reasons and sanity are all checked against the committed allowlists, so a
provider changing a model's response shape fails the job. The recordings are
uploaded as an artifact on every run, pass or fail, for diffing or promotion.

It does not run on pull requests: it calls paid APIs and needs secrets. The
committed fixtures run on every PR in the gateway CI's `make test`.

Repository secrets, each optional; a provider without one is skipped, but the
job fails when nothing at all was recorded:

| secret | provider |
|---|---|
| `ANTHROPIC_API_KEY` | anthropic |
| `OPENAI_API_KEY` | openai |
| `GROQ_API_KEY`, `PERPLEXITY_API_KEY` | the openai suite's `groq@...` / `perplexity@...` models |
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
