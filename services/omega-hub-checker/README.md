# omega-hub-checker

The Omega Hub's sandboxed checker (SPEC §4). It pulls check jobs from the registry
(`futureagi/omega_hub`), judges each upload with a vendored, pinned `@omega/hub`, runs the entry's
keyless test and demo in Docker with the network off, and posts an `omega-hub-check-report/v1`
report plus the canonical bundle. It never imports or executes submission code itself, and it holds
no database or storage credentials: only `OMEGA_HUB_CHECKER_TOKEN`.

## Steps

`unpack` → `manifest` (R1-R13 vendored, U1-U8) → `licence` → `secrets` → `dependencies` →
`static-scan` (TypeScript AST) → `sandbox-run` (R14, R15 in the runner) → `capabilities` (and the
`observed` facts the reviewer checks the disclosure against) → `diff` → `similarity`.

## Run it locally (Colima)

```sh
C=<omega commit>; D=$TMPDIR/omega-hub-src-$(printf %.9s "$C")
mkdir -p "$D" && git -C <omega checkout> archive "$C" | tar -x -C "$D"
node scripts/vendor-omega-hub.mjs --omega-dir "$D" --commit "$C"   # the vendored rules
DOCKER_CONTEXT=colima runner/build.sh "$D" "$C"                      # omega-hub-runner:<commit7>
npm ci
OMEGA_HUB_URL=http://127.0.0.1:8740 OMEGA_HUB_CHECKER_TOKEN=… \
  OMEGA_HUB_RUNNER_IMAGE=omega-hub-runner:$(printf %.7s "$C") DOCKER_CONTEXT=colima node src/main.mjs --drain
npm test                                                             # unit tests + the Docker tests
```

Set `OMEGA_HUB_CHECKER_HEALTH_PORT` for the private `/healthz` and `/metrics`
listener. It binds `0.0.0.0` by default so a container probe can reach it; set
`OMEGA_HUB_CHECKER_HEALTH_HOST` when the runtime needs a narrower interface.
`/healthz` judges an idle checker by its polling loop: 503 when the loop has
not run within `OMEGA_HUB_CHECKER_HEALTH_TIMEOUT_MS` (default: three poll
intervals, at least 30 seconds), even if the process is still alive. A busy
checker is judged by its job instead, since a normal check parks the loop for
minutes: 503 only when the job has had no claim or registry-accepted heartbeat
within `OMEGA_HUB_CHECKER_JOB_HEALTH_TIMEOUT_MS` (default 15 minutes, the
registry's lease). That makes `/healthz` safe as a liveness probe: it restarts a
wedged checker and never one in the middle of a check. The checker health
listener must stay on the private operations network.

`scripts/omega-hub/dev-up.sh` at the repository root does all of this for a local dev stack.

## The runner

`runner/Dockerfile` builds node 22 plus the Omega workspace at one commit (the CLI, the `@omega/*`
packages and `scripts/hub/preload.mjs`), plus the judge below. Each job runs in one `ohc-<job8>` container:
`--network none --read-only --user 0:0 --cap-drop ALL --cap-add SETUID --cap-add SETGID
--security-opt no-new-privileges --pids-limit 256 --memory 1g --cpus 1`, tmpfs `/tmp` and `/work`,
the canonical bundle over stdin, no bind mounts and no host environment. The container starts as the
judge; every piece of entry code runs as uid 10001 with no capabilities (below). The total time limit
is 240 s; past it the container is killed and the step fails.

Until the hub-cli lane's C1 lands, the pinned CLI has no `check --only R14,R15`; the checker runs
`omega hub check <id> --json` and keeps only the R14 and R15 findings (it has already run R1-R13
itself, with `version` and `disclosure` checked by U1 and U2 instead).

## The judge (where the verdict comes from)

The checker never reads the verdict from anything entry code can write, and entry code cannot reach
the processes that produce it (SPEC §13, "a submission tampers with its own judge"; READINESS 3.19).
This differs from the literal `sh -c` command and `--user` of SPEC §4.4; the other isolation flags are
unchanged.

- **Two uids.** The judge (PID 1's shell and the node that runs `omega hub check`) runs as root with
  only `CAP_SETUID` and `CAP_SETGID`, under no-new-privileges. The judge node `--require`s
  `/opt/omega-hub-judge/children.cjs` (`runner/judge/children.cjs`; on its argv, never in
  `NODE_OPTIONS`), which starts every child of the CLI as uid and gid 10001; `omega hub check` runs
  every test and recording as such a child. A `setuid` from root to 10001 empties every capability
  set, so entry code has none, and the kernel (runc) or gVisor's Sentry refuses it the judge's
  `/proc/<pid>/fd`, `environ` and `mem`, `ptrace` and signals, because they belong to another uid.
- **Why not execute-only any more.** The judge used to run as uid 10001 from execute-only copies of
  dash and node (mode 0711), relying on non-dumpability. gVisor loads an executable only when the
  caller may read it, so that judge could not start under runsc (the rig run of 2026-10-21 ended
  every upload in `check_error`). The judge's files are open source and hold no secret; what must
  hold is that entry code cannot change or replace them and cannot forge the result:
  - every file the judge loads (`/bin/sh`, `/usr/local/bin/node`, `/omega/**`, the preload) is
    root-owned on the read-only root filesystem; the image has no setuid or setgid file (the build
    fails otherwise);
  - the judge's environment is the three `--env` values above, set before any entry code exists, and
    it calls `/bin/sh`, `/usr/local/bin/node` and `setpriv` from a `PATH` with no writable directory,
    so `PATH`, `LD_PRELOAD`, `NODE_OPTIONS` and planted files in `/tmp` or `/work` do not reach it;
    the judge node computes its global module folders with `HOME` on the read-only
    `/opt/omega-hub-judge/home` (then hands `HOME=/tmp` to entry code, as before);
  - the bundle is unpacked as uid 10001 (`setpriv`); in a check the CLI writes no file, so no
    symlink an entry plants is ever written through; there is no secret in the container to read.
- PID 1 is `/bin/sh -c <JUDGE_SCRIPT>` (`src/judge.mjs`). It reads a per-job nonce (16 random bytes,
  hex) from the first line of stdin, ahead of the bundle; checks its own preconditions (uid 0,
  `CapEff` exactly `CAP_SETUID|CAP_SETGID`, no-new-privileges, `setpriv` and the preload present, else
  exit 71); unpacks as uid 10001; runs `node --disable-sigusr1 --require children.cjs
  …/cli/dist/index.js hub check <id> --json` (no inspector on SIGUSR1); and prints
  `omega-hub-judge/v1 begin <nonce>`, the CLI's stdout, and `omega-hub-judge/v1 end <nonce> <exit
  code>`, with shell builtins only. The nonce never leaves PID 1's memory, which uid 10001 cannot read.
- **The judge preflight** (`src/preflight.mjs`, run by `bin/start.sh` before the worker in every
  deploy shape): one container with exactly an upload's flags proves, on the runner daemon in use,
  that the judge loads, that a child started the CLI's way runs as 10001:10001 with empty capability
  sets, and that it is refused `setuid(0)`, signals to the judge and PID 1, the judge's `environ`,
  `mem` and `fd/1`, PID 1's `fd/1`, and writes to the judge's files. A runner image or daemon where
  that does not hold stops the checker (exit 65, or 69 when the daemon did not run it).
- `readFrame` reads stdout only and accepts exactly one frame with the nonce, nothing else on stdout,
  exactly one `omega-hub-check/v1` result inside, an exit code of 0 or 1 that agrees with `ok`, and a
  container exit code equal to it. Anything else blocks `sandbox-run` with rule `judge` (a killed CLI
  shows as "exited with code 137"). Set-up exits 70-72 without a frame are infrastructure errors.
- Two containers per job, both through the judge: the **test run** (`ohc-<job8>`) keeps only R14,
  because the keyless test is the first entry code it runs; the **demo run** (`ohc-<job8>-demo`) gets
  the same canonical bundle without the `test` file and keeps only R15, so the CLI reads `demo.cast`
  before any entry code runs. Without the split, a test could rewrite `demo.cast` to match whatever
  `main.mjs` prints and pass R15 with a recording that is not the uploaded one. Each run has the
  240 s limit; the step's `ms` is their sum.
- `src/main.mjs` refuses to start on a runner image whose label `org.futureagi.omega-hub.judge` is not
  `2` (this judge); an image with the execute-only judge (`1`) must be rebuilt with `runner/build.sh`.
- The static scan flags `/proc` paths (`proc-access`) and `process.kill`/`process._debugProcess`
  (`signals-processes`) for the reviewer.

Proofs, against the real image: `test/sandbox.test.mjs` runs the judge preflight on the daemon, and
under runsc two copies of the image whose judge shell or judge node its runtime uid may execute but
not read (both refused: the test fails if the judge is not loadable by its uid under gVisor);
`forge-verdict` (writes a passing result into every stdout it can open, SIGUSR1 then SIGKILL to the
CLI: refused, `checks_failed` with its real R14 and R15), the same without the kill, `judge-probe`
(every ancestor's uid, `fd/1`, `environ` and `mem`, SIGUSR1 to the CLI, signal 0 to PID 1, and the
inspector: every probe at a judge process refused), and a test that rewrites `demo.cast` at run time
(R15 still fails as stale). With `OMEGA_HUB_EXPECT_RUNTIME=runsc` the suite fails unless the daemon's
default runtime is gVisor. `test/judge.test.mjs` covers the frame rules and the judge script;
`test/preflight.test.mjs` the preload and the preflight's verdicts.

## Required sandbox tests

The Docker tests skip when no runner image with the judge is built. When `OMEGA_HUB_RUNNER_IMAGE`,
`OMEGA_HUB_REQUIRE_SANDBOX` or `CI` is set they fail instead, so the network-off and judge proofs
cannot look green without having run: a CI job that runs `npm test` must build the runner first.
