# judge-probe (overlay on `good`)

Probes the judge from inside a submission's keyless test (SPEC §13, READINESS 3.19): for the test
process's every ancestor up to PID 1 (the node test runner, the CLI, the judge shell) it records the
ancestor's uid and tries to write `fd/1`, read `environ` and read `mem`; then it sends SIGUSR1 to the
CLI, signal 0 to PID 1, and tries Node's inspector port. The test fails on purpose, with
`PROBES <json>` as its assertion message, so the results come back through the judge's own frame in
the R14 finding. `sandbox.test.mjs` asserts that every ancestor is either the judge (uid 0: the CLI
and PID 1) or entry code (uid 10001: the test runner), and that every probe at a judge process was
refused.
