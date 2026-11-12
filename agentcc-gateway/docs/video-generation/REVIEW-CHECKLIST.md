# TH-8088 Phase 1 pre-review checklist

Date: 2026-10-03. Base: `dev` at `ce6af27`; Batch 4 starts at
`b784df355` on `feat/TH-8088-gateway-video-generation`.

All required final checks below passed. The known A2A full-server race remains
a separate exception; this is not a claim that the entire repository is race-free.

Commands run from `agentcc-gateway/` with the following environment for every
Go invocation, including commands launched by Make or Python:

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
```

Output blocks contain actual command output. Silent checks print a wrapper
exit-status line; no filenames or diagnostics preceded the clean formatting
and vet lines. Test runs were sequential. Neither `GOFLAGS` nor the Makefile
was changed to serialize, skip or weaken the full suite.

## Formatting — PASS

Includes all 90 changed/new Go files across Phase 1, relative to `ce6af27`,
including both final fixture repairs.

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess
root = Path.cwd().parent
tracked = subprocess.check_output(
    ['git', 'diff', '--name-only', 'ce6af27', '--', 'agentcc-gateway/**/*.go'],
    cwd=root, text=True).splitlines()
new = subprocess.check_output(
    ['git', 'ls-files', '-o', '--exclude-standard', '--', 'agentcc-gateway/**/*.go'],
    cwd=root, text=True).splitlines()
files = sorted(set(tracked + new))
r = subprocess.run(['gofmt', '-l', *[str(root / f) for f in files]],
                   capture_output=True, text=True)
print(r.stdout, end='')
print(r.stderr, end='')
print(f'gofmt -l: files={len(files)} unformatted={len(r.stdout.splitlines())} exit={r.returncode}')
raise SystemExit(r.returncode or bool(r.stdout))
PY
```

```text
gofmt -l: files=90 unformatted=0 exit=0
```

## Vet on changed packages — PASS

```sh
go vet -p 1 ./cmd/loadtest ./internal/config ./internal/metrics ./internal/plugins/budget ./internal/plugins/cost ./internal/providers/video ./internal/providers/video/byteplus ./internal/providers/video/videotest ./internal/redisstate ./internal/secrets ./internal/server ./internal/video ./internal/video/artifacts ./internal/video/capability ./internal/video/lifecycle ./internal/video/media ./internal/video/storetest ./internal/video/tariff
```

```text
go vet exit=0
```

## Required Redis-backed regression — PASS

```sh
TEST_REDIS_ADDR=127.0.0.1:36381 go test -count=1 ./internal/video/... ./internal/providers/video/... ./internal/server/... ./internal/plugins/... ./internal/metrics/... ./internal/redisstate/... ./internal/secrets/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video	4.582s
ok  	github.com/futureagi/agentcc-gateway/internal/video/artifacts	0.992s
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	0.951s
ok  	github.com/futureagi/agentcc-gateway/internal/video/lifecycle	11.908s
ok  	github.com/futureagi/agentcc-gateway/internal/video/media	0.796s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.514s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video	0.932s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	4.481s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	2.183s
ok  	github.com/futureagi/agentcc-gateway/internal/server	4.182s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/alerting	0.583s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/audit	0.969s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/auth	0.744s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/budget	1.694s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/cache	1.210s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/cost	1.737s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/credits	1.519s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/ipacl	1.238s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/logging	0.992s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/otel	0.619s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/prometheus	0.461s
?   	github.com/futureagi/agentcc-gateway/internal/plugins/quota	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit	0.678s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/rbac	0.880s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/toolpolicy	0.528s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/validation	1.720s
ok  	github.com/futureagi/agentcc-gateway/internal/metrics	0.259s
ok  	github.com/futureagi/agentcc-gateway/internal/redisstate	1.254s
ok  	github.com/futureagi/agentcc-gateway/internal/secrets	0.335s
```

## Required video/provider race run — PASS

```sh
go test -race -count=1 ./internal/video/... ./internal/providers/video/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video	7.664s
ok  	github.com/futureagi/agentcc-gateway/internal/video/artifacts	2.195s
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	4.234s
ok  	github.com/futureagi/agentcc-gateway/internal/video/lifecycle	18.887s
ok  	github.com/futureagi/agentcc-gateway/internal/video/media	2.257s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	2.494s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video	1.923s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	7.285s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	3.238s
```

## Full suite — PASS

```sh
make test
```

The unmodified target runs `go test ./... -v -count=1`. Final output lines:

```text
--- PASS: TestVerify_TruncatedMoov (0.00s)
=== RUN   TestVerify_WebPLosslessShortHeader
--- PASS: TestVerify_WebPLosslessShortHeader (0.00s)
=== RUN   TestVerify_MovieDurationAndWebPZeroDimensions
=== RUN   TestVerify_MovieDurationAndWebPZeroDimensions/0
=== RUN   TestVerify_MovieDurationAndWebPZeroDimensions/1
--- PASS: TestVerify_MovieDurationAndWebPZeroDimensions (0.00s)
    --- PASS: TestVerify_MovieDurationAndWebPZeroDimensions/0 (0.00s)
    --- PASS: TestVerify_MovieDurationAndWebPZeroDimensions/1 (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/media	0.788s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
=== RUN   TestBytePlusRates
--- PASS: TestBytePlusRates (0.00s)
=== RUN   TestBytePlusTokens
--- PASS: TestBytePlusTokens (0.00s)
=== RUN   TestAdaptiveUpperBound
--- PASS: TestAdaptiveUpperBound (0.00s)
=== RUN   TestUnknownTariffNotZero
--- PASS: TestUnknownTariffNotZero (0.00s)
=== RUN   TestPriceRejectsInvalidUsage
--- PASS: TestPriceRejectsInvalidUsage (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.609s
make test exit=0
```

## Import graph — PASS

Checks the transitive production/test package graph, imports and source-file
paths for `license_auth`, `license-auth`, enterprise and EE components. Shared
configuration fields are not imports of license-auth enforcement.

```sh
python3 - <<'PY'
import json, re, subprocess
raw = subprocess.check_output(
    ['go', 'list', '-deps', '-test', '-json',
     './internal/video/...', './internal/providers/video/...'], text=True)
decoder = json.JSONDecoder()
packages = []
while raw.strip():
    package, end = decoder.raw_decode(raw.lstrip())
    packages.append(package)
    raw = raw.lstrip()[end:]
blocked = re.compile(r'(^|[/_.-])(license[_-]?auth|enterprise|ee)([/_. -]|$)', re.I)
findings = []
sources = set()
for package in packages:
    names = [package.get('ImportPath', ''), package.get('Dir', '')]
    names += package.get('Imports', [])
    for field in ('GoFiles', 'CgoFiles', 'CompiledGoFiles', 'TestGoFiles', 'XTestGoFiles'):
        for filename in package.get(field, []):
            name = package.get('Dir', '') + '/' + filename
            names.append(name)
            sources.add(name)
    findings.extend(name for name in names if blocked.search(name))
for finding in sorted(set(findings)):
    print(finding)
print(f'Import graph: {len(packages)} packages (including tests), {len(sources)} source paths; forbidden imports/files={len(set(findings))}')
raise SystemExit(bool(findings))
PY
```

```text
Import graph: 312 packages (including tests), 2591 source paths; forbidden imports/files=0
```

## Config and generated-contract checks — PASS

```sh
go test -count=1 ./internal/config -run '^(TestVideoDefaults|TestVideoExample|TestVideoValidation)$' -v
```

```text
--- PASS: TestVideoDefaults (0.00s)
    --- PASS: TestVideoValidation/memory (0.00s)
=== RUN   TestVideoExample
--- PASS: TestVideoExample (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/config	0.354s
```

The example loads with video disabled; the existing validation test refuses
an enabled memory store without the explicit non-durable-store opt-in.

```sh
python3 ../scripts/generate-agentcc-gateway-contracts.py --check
```

```text
admin contract generation check exit=0
```

No admin-contract files or dependency manifests changed.

## Failures encountered and resolution

### Redirect fixture

The first required regression failed when TLS setup consumed the redirect
fixture's 30 ms deadline before it reached the redirect limit:

```text
--- FAIL: TestFetch_RedirectLimitAndDeadline (0.07s)
    --- FAIL: TestFetch_RedirectLimitAndDeadline/redirect (0.03s)
        fetch_test.go:163: error = media_fetch_timeout, want 400 media_url_refused
FAIL
FAIL	github.com/futureagi/agentcc-gateway/internal/video/media	2.300s
FAIL
```

The redirect case now uses an immediate in-memory transport while retaining
`http.Client` redirect handling, the 30 ms deadline and the exact refusal
assertion. It additionally requires exactly four transport requests (initial
request plus three followed redirects). The slow-response case still uses
real TLS. Production code was unchanged.

```sh
go test -race -count=20 ./internal/video/media -run '^TestFetch_RedirectLimitAndDeadline$'
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video/media	2.050s
```

### BytePlus body-deadline fixture

The first required race run reached the other previously noted short-deadline
fixture failure:

```text
--- FAIL: TestFetch_StreamCapDeadlineAndDownloadLimit (0.08s)
    --- FAIL: TestFetch_StreamCapDeadlineAndDownloadLimit/body_deadline (0.03s)
        adapter_test.go:390: context deadline exceeded
FAIL
FAIL	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	5.313s
FAIL
```

The fixture now establishes a reusable TLS connection through a 204 warmup
response before the body-deadline request. The 25 ms fetch deadline, real TLS
body read, and `context.DeadlineExceeded` assertion are unchanged. This makes
the assertion test body timeout instead of handshake speed.

```sh
go test -race -count=20 ./internal/providers/video/byteplus -run '^TestFetch_StreamCapDeadlineAndDownloadLimit$'
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	3.003s
```

Both required regression and race commands were rerun after both repairs;
their complete passing outputs appear above.

### First full-suite run: local Redis responsiveness

The first `make test` run then failed in four packages with Redis ping/I/O
timeouts and short-lease timing failures. Representative diagnostics and
actual package/final output lines:

```text
    cluster_test.go:123: expected 1 node, got 0 (ok=false)
    handlers_video_test.go:268: redis ping: context deadline exceeded
    store_redis_test.go:85: video_store_unavailable: i/o timeout
FAIL	github.com/futureagi/agentcc-gateway/internal/redisstate	13.605s
FAIL	github.com/futureagi/agentcc-gateway/internal/server	40.527s
FAIL	github.com/futureagi/agentcc-gateway/internal/video	46.207s
FAIL	github.com/futureagi/agentcc-gateway/internal/video/lifecycle	57.226s
FAIL
make: *** [test] Error 1
make test exit=2
```

A subsequent local health check showed Redis responding, no blocked clients,
no rejected connections or evictions, and healthy AOF writes:

```text
Redis PING: 20/20 passed; min_ms=0.547 max_ms=17.896
connected_clients:1
blocked_clients:0
aof_enabled:1
aof_last_write_status:ok
aof_delayed_fsync:0
rejected_connections:0
evicted_keys:0
Load-test Redis keys remaining: 0
```

The exact unchanged `make test` command was rerun with the build cache warm
and passed (final output above). No Redis configuration, test timeout, lease
TTL, package selection, Go parallelism flag or Makefile was changed for this
rerun. The observed failure was transient; its precise host/Redis scheduling
cause was not established.

## Known exception: pre-existing A2A race

Batch 3 reports `TestA2AReturnImmediatelyAndCancelUnderV1WithAuth` racing
between unchanged `internal/a2a/server.go:279-280`
(`runMessageSendWithPipeline`) and `:419-420` (`handleTasksCancel`). Its
recorded broad-server race run ended with:

```text
FAIL github.com/futureagi/agentcc-gateway/internal/server 2.573s
```

That is historical output from [BATCH3-NOTES.md](BATCH3-NOTES.md), not a
reproduction in this batch. The broad server race command was not rerun; A2A
and `internal/server/server_test.go` have no diff. The requested video/provider
race gate above passes. Fixing A2A is outside this brief.

## Final whitespace, scope and credential checks — PASS

```sh
git diff --check
check_status=$?
printf 'git diff --check exit=%d\n' "$check_status"
exit "$check_status"
```

```text
git diff --check exit=0
```

```text
Secret scan: 13 changed/new source and documentation files; 0 credential-pattern matches
```

The changed/new source and documentation files were scanned for PEM private
keys, AWS access-key IDs, provider secret-key shapes and GitHub token shapes;
no credential-pattern matches were found. This targeted pattern scan does not
claim to detect every possible secret format.

Only `agentcc-gateway/`, root `CHANGELOG.md` and root `INSTALLATION.md`
changed. `go.mod`, `go.sum`, A2A, the existing server test, and generated
contracts are unchanged. No in-tree build cache or temporary load artifact
directories remain. The load namespace was empty after cleanup. No real
provider calls, credentials, pushes or generated admin-contract edits were
used. The load result is in [LOADTEST.md](LOADTEST.md); commit handoff is in
[BATCH4-NOTES.md](BATCH4-NOTES.md).
