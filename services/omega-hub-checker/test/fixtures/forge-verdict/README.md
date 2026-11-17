# forge-verdict (overlay on `good`)

A submission that tries to forge its own verdict (SPEC §13, "a submission tampers with its own
judge"). Its keyless test really fails, and `main.mjs` exits 3 so the keyless run fails too. Before
failing, the test writes a passing `omega-hub-check/v1` result and a judge frame with a guessed nonce
into every process's stdout and stderr it can open, sends SIGUSR1 to the CLI (Node's inspector
switch), and then SIGKILLs the CLI. The CLI runs as root and entry code as uid 10001 (READINESS
3.19), so the judge's streams and the signals are refused; the checker must end in `checks_failed`
with the entry's real R14 and R15 failures, never `in_review`. `sandbox.test.mjs` also runs it with
`const KILL = false`.
