# TH-8088 Batch 2 notes

Worktree base: `f995b29c8`. All edits are inside `agentcc-gateway/`.

## P1-05 media ingress

Implemented guarded HTTPS fetches (no proxy, userinfo or credentials), three redirects with per-connection netguard checks, deadlines, streaming byte caps, SHA-256, MIME-family checks, image dimensions (PNG/JPEG/GIF/WebP), one-frame GIF validation, MP4/MOV atom validation and optional mvhd duration, WAV header/duration and MP3 header validation. Configurable denied gateway hosts and known credential query names are refused. Without a sink, verified bytes are returned; with a sink, validation errors propagate through the stream and prevent commit.

The sink is a small function interface so P1-05 can precede P1-06 without a circular dependency. Batch 3 can bind it to `artifacts.Store.Put`. Header/container verification is not a codec/playability check. BMP/TIFF/HEIC/HEIF remain fail-closed until decoders are added; MP3 duration is unknown (header validation only).

TDD: added ingress/security/header tests before implementation (initial build failed on undefined APIs). Additional GIF-frame, truncated-moov and short-lossless-WebP regressions failed before fixes.

Commands (run from `agentcc-gateway/`):

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
gofmt -l internal/video/media
go vet ./internal/video/media
go test -count=1 ./internal/video/... ./internal/providers/video/...
go test -race -count=1 ./internal/video/media
```

Final results: gofmt and vet: no output, exit 0. All six test-bearing video/provider packages passed; storetest has no standalone tests. Final suite line: `ok github.com/futureagi/agentcc-gateway/internal/providers/video/videotest 2.301s`. Race: `ok github.com/futureagi/agentcc-gateway/internal/video/media 1.441s`.

## Git limitation

The first `git add` failed with:

```text
fatal: Unable to create '/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi/.git/worktrees/impl/index.lock': Operation not permitted
```

No retries or workarounds were attempted. No commits were made. Run these exact commands from `agentcc-gateway/` in a writable checkout, preserving one commit per step:

```sh
git add .gitignore internal/video/media
git commit -m 'feat(agentcc-gateway): verify video media ingress (TH-8088 P1-05)'
git add internal/video/artifacts
git commit -m 'feat(agentcc-gateway): add video artifact stores (TH-8088 P1-06)'
```

## P1-06 artifact stores

Implemented the streaming `Store` interface with Put/Open/Delete/Stat, SHA-256 and a hard cap, disk atomic O_EXCL temporary files + rename, durable data/metadata envelope, os.Root path containment, byte ranges, private S3 PUT/range GET/HEAD/DELETE, signed ACL/SSE headers, and strict full-key prefix enforcement. Added a Redis artifact expiry zset and bounded leased sweeper; failed delete/metadata updates remain due for retry. It does not run a lifecycle worker.

The small SigV4 implementation is adapted from the cache signer rather than moved: streaming UNSIGNED-PAYLOAD and additional ACL/SSE/Range signed headers are isolated from existing cache behaviour. Cache tests remain green. S3 stages the bounded input to a 0600 temporary file, then streams that file in the network PUT: exact Content-Length and late media validation failures cannot produce partial committed S3 blobs. This uses disk proportional to concurrent upload sizes, not proportional RAM. Configure `S3Config.TempDir` for upload staging capacity. No dependencies added.

TDD: disk/S3 tests failed before implementation; the sweeper test failed before its implementation. Tests cover ranges, cap failures, cancellation/size mismatch, atomic replacement, path/symlink escape denial, S3 signed headers/prefix ownership, failed-stream no-upload, integration with the ingress sink, Redis expiry retry and competing sweeper leases.

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
gofmt -l internal/video/artifacts
go vet ./internal/video/artifacts
go test -count=1 ./internal/video/... ./internal/providers/video/...
go test -race -count=1 ./internal/video/artifacts
go test -count=1 ./internal/cache/...
```

Final results: gofmt and vet: no output, exit 0. Full suite: all seven test-bearing packages passed; final line `ok github.com/futureagi/agentcc-gateway/internal/providers/video/videotest 2.485s`. Race: `ok github.com/futureagi/agentcc-gateway/internal/video/artifacts 1.718s`. Cache: `ok github.com/futureagi/agentcc-gateway/internal/cache 0.332s`.

The repository-wide `media/` ignore rule also matches the Go package. A scoped `agentcc-gateway/.gitignore` exception makes its files reviewable and stageable normally; include that exception in P1-05.


## P1-08 BytePlus Seedance adapter

Implemented the existing Adapter contract with configured connect/read/submit/reconcile/fetch deadlines and netguard on API and CDN connections. API redirects are refused; CDN redirects have the ingress URL/connection checks and never receive the provider Authorization header.

- Prepare uses HMAC-SHA256 over org ID, key ID and end-user identity with the correlation secret. Submit checks that the persisted token agrees with that identity.
- Submit reuses capability normalization/validation, maps every supported role exactly once, forces 2.5 frame ratios to adaptive, sends reference task type for video references, validates frames and 1.x-only seed, and sets configured execution expiry. An explicit wire-field allowlist excludes callbacks, draft, edit and extend.
- Poll maps all six pinned states, preserves provider errors, reports typed completion-token usage, captures output URLs internally, and returns SchemaError for unknown/malformed responses. Added the backwards-compatible `UpstreamError.RetryAfter time.Duration` field because Batch 1's error contract could not carry HTTP Retry-After. Access-denied, synchronous rejection, 429 and 5xx errors have the required codes/retry flags; numeric and HTTP-date Retry-After are preserved.
- Fetch streams under a byte cap/deadline with no API credentials, counts attempts, and stops after 100 attempts per job/artifact in the current adapter process.
- Cancel refuses running/terminal tasks with `cancel_not_available`, pre-polls queued tasks before DELETE, and re-polls afterwards. A DELETE receipt alone is not confirmation, and unknown failure billability never releases charge.
- Reconcile scans every status by omitting a status filter, paginates at at most one request per second, and matches model, HMAC token, creation window and available request-shape fields. Exactly one match in a complete scan is found; zero/ambiguous/incomplete/changing scans are unresolved. It never claims proven absence. The window uses attempt start minus 60 seconds through submit deadline plus 60 seconds, following contract section 2's wider skew allowance.

`testdata/retrieve-official.json` preserves the official tutorial response verbatim. Source, retrieval date and SHA-256 are in `testdata/README.md`. The trailing space on its video_url line is deliberately preserved. Public documentation was fetched; no inference/provider API requests, real credentials or paid calls were used.

TDD: adapter/role/status/error/cancel/reconcile tests failed before implementation; added failing regressions for refusal-vs-cancellation classification and the exact unavailable-cancel error code before their fixes. The shared `videotest.RunContractSuite` passes for every model/operation in the seven-model BytePlus registry. Provider-specific tests cover roles, excluded fields, 1.x seed, frame arithmetic, official fixture decoding, unknown statuses, malformed JSON, error mapping, Retry-After, response caps, read/body deadlines, API redirect refusal, output byte caps, the 100-download guard, cancellation completion races, unique/zero/ambiguous reconciliation, pagination, timestamp boundaries, request shape, and the list limiter.

### Final validation (after all fixes)

The final run also caught and fixed concurrent directory creation in the disk backend. Its existing concurrency test failed on `mkdirat ...: file exists`; the fix checks each directory component on EEXIST while retaining os.Root containment. A 20-run race regression passed. Header tests additionally verified MOV/mvhd v0/v1 duration and rejected zero-dimension WebP after a failing regression.

All commands ran from `agentcc-gateway/` with:

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
```

| Exact command | Final output / result |
|---|---|
| `gofmt -l internal/video/media internal/video/artifacts internal/providers/video/byteplus internal/providers/video/errors.go` | No output; exit 0. |
| `go vet ./internal/video/media ./internal/video/artifacts ./internal/providers/video ./internal/providers/video/byteplus` | No output; exit 0. |
| `go test -count=1 ./internal/video/... ./internal/providers/video/...` | All eight test-bearing packages passed, storetest has no standalone tests. Last line: `ok github.com/futureagi/agentcc-gateway/internal/providers/video/videotest 2.660s`. |
| `go test -race -count=1 ./internal/video/media ./internal/video/artifacts ./internal/providers/video ./internal/providers/video/byteplus` | `ok .../internal/video/media 1.515s`; `ok .../internal/video/artifacts 1.721s`; `ok .../internal/providers/video 1.811s`; final line `ok github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus 4.414s`. |
| `go test -race -count=20 ./internal/video/artifacts -run TestDisk_ConcurrentAtomicReplace` | `ok github.com/futureagi/agentcc-gateway/internal/video/artifacts 2.053s`. |
| `go test -count=1 ./internal/cache/...` | `ok github.com/futureagi/agentcc-gateway/internal/cache 0.332s`. |
| `make test` | Exit 0; 89 package `ok` lines and no FAIL lines. Final lines: `PASS` and `ok github.com/futureagi/agentcc-gateway/internal/video/tariff 1.550s`. |

The full make target's temporary log was removed after inspection. Go sources have no trailing whitespace; fixture checksum matches its provenance note. No changes to go.mod/go.sum, internal/server, the lifecycle worker, or files outside agentcc-gateway. No build cache in the tree.

Complete the third commit, after the two commands above, with:

```sh
git add internal/providers/video/errors.go internal/providers/video/byteplus docs/video-generation/BATCH2-NOTES.md
git commit -m 'feat(agentcc-gateway): add BytePlus Seedance adapter (TH-8088 P1-08)'
```

## Batch 3 handoff and limits

- Wire media fetching/inline decoding to capability-specific Limits, configured gateway DeniedHosts, per-asset and total ingress contexts. Bind the sink to Store.Put with content type/retention metadata. Keep input verification before submit/budget acceptance as designed.
- Construct the disk/S3 store from startup configuration and resolve credentials through existing config/secrets. Map Object.Length/ContentRange and ErrRange to HTTP 206/416; handlers must perform tenant and retention checks before opening content.
- Schedule the separate `video:v1:artifacts:expire` index through Sweeper.Track after copying. Run bounded Sweep calls and provide an idempotent, fenced expiry callback that saves the owning job. Do not reuse blob keys or extend a tracked expiry. Job metadata GC remains separate.
- Register/wire BytePlus, persist correlation before the upstream call, and handle submit uncertainty through the existing state machine. No adapter method retries billable Submit.
- Supply Config.ListLimiter backed by the account-scoped Redis limiter for cross-replica 1 QPS. The built-in limiter is local to an adapter instance. Persist download attempts in lifecycle state for a restart/replica-wide ceiling; DownloadAttempts exposes this instance's conservative count.
- Perform output verification with the media verifier while copying, then settle usage even if copying fails. Continue to keep output ACL and failed-task billability unknown until an explicitly approved live smoke establishes them.
- The media package intentionally performs header/container validation, not playability/full decode. BMP/TIFF/HEIC/HEIF fail closed; MP3 duration remains unknown. S3 upload staging needs disk capacity up to the configured cap per concurrent upload. These are documented implementation limits, not claims of provider/media support beyond the tested paths.
- Handlers, lifecycle worker, pipeline/budget integration, metrics, admin routes and live smoke remain outside this batch. No push or deployment was performed.
