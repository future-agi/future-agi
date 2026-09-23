# Bundle Producer Contract — v1.1

**Boundary**: the environment-generation pipeline (producer, owner: Rishav) → the
`futureagi.environment-bundle.v2` bundle directory → the hosted guest (consumer,
owner: Khushal's track, frozen behavior in ALK PR #65).

**Status**: v1.1, 2026-08-27. The consumer side is DONE and live-proven (first
real voice call 2026-08-27 00:01 IST, 24 turns; first fully graded run —
1 passed / 2 failed, three real calls — 00:37 IST, all through this exact
format). The producer does not exist yet; this document is what it must emit.
The hand-authored reference bundle satisfying every rule here lives at
`futureagi/.harness-bundles/future-agi__ride-voice-agent/` on branch
`test/manual-e2e` (pushed).

**Authoritative tree**: every consumer behavior cited below is the
agent-learning-kit tree at the ALK PR #65 branch as merged into
`test/manual-e2e`. Deployed sandbox snapshots and side worktrees lag it — a
known instance: the hosted job validator that pins `manager: platform-vault` /
`purpose: target_provider` (`job.py:181-189`, codes
`hosted_secret_manager_unsupported` / `hosted_secret_purpose_invalid`) exists
in the authoritative tree but NOT in the `/tmp/alk-callrunner` worktree the
call runner was built in. Where a deployed snapshot, a stale code comment, or
an older copy of this document disagrees with that tree, the tree governs.

Every rule below marked LIVE-PROVEN cost a failed run before it was learned.
They are not theoretical.

---

## 1. What the producer emits

One directory **per repo** (not per (repo, ref) — see the warning below):

```
<owner>__<repo>/
  manifest.json          # the sealed EnvironmentBundleV2 document
  db/                    # migrations + seed files, referenced by manifest
  scenarios/<name>/      # scenario.json, setup.py, ready.py, checks/*.py
                         # (the guest reads scenario.json's own scenario_key
                         # field; the folder name is convention, not identity)
  shim/                  # any evidence shims the manifest's processes reference
```

Delivery today: the platform tars `ALK_HOSTED_BUNDLE_DIR/<owner>__<repo>/` and
unpacks it in the sandbox at `/work/bundle/`
(`hosted_harness_gateway.py:780-802` + the sandbox bootstrap command). The
gateway/delivery steps are spine §0's; the `/work/bundle` location itself is
pinned by **no frozen document** — `hosted_entrypoint.py` (~:101) says exactly
that and treats it as its own overridable placeholder. (Earlier drafts cited
spine §2b for delivery; §2b is processes — the citation was wrong.)

**WARNING — no ref in the key, no commit enforcement.** The delivery lookup
keys by repo only: `<owner>__<repo>`, no ref anywhere in the path. And nothing
on either side compares the manifest's `provenance.commit` to the job's
`source.commit_sha` — spine §2d states the rule (`commit` = the job's
`source.commit_sha`) but no layer enforces it. Consequence: a bundle authored
at commit X ships silently against a checkout of commit Y until enforcement
exists. When the tracked branch moves, the producer must re-author (or at
minimum re-verify and reseal) the bundle; do not assume anything will catch
the mismatch for you.

## 2. The manifest — authoritative model, do not re-derive

The single source of truth for the shape is
`fi.alk.harness.bundle_v2.EnvironmentBundleV2` (ALK PR #65). Validate every
produced manifest with `EnvironmentBundleV2.model_validate` before sealing —
but know that validate + seal is NOT the full gate the guest applies; §3 is.
Top-level: `schema_version` (`futureagi.environment-bundle.v2`), `name`,
`runtime`, `processes[]`, `capabilities`, `readiness[]`, `seed`, `files[]`,
`provenance`, `metadata`, `digest`.

### 2.1 Digests — call the sealer, never reimplement (MUST)
- `digest` = `seal_bundle_v2(manifest)` — the byte-exact §2d construction.
- `seed.stores[].baseline.inputs_digest` = `compute_inputs_digest(root,
  migrations, seed_files, engine=..., version=...)`.
- Which edit re-keys what: an edit to any **migration or seed file** re-keys
  that store's `inputs_digest` (its inputs are exactly the listed
  migrations + seed files plus `engine:version` — `bundle_v2.py:576-605`); an
  edit to **any file listed in `files[]`** (checks, scenario.json, the shim,
  seeds — anything) changes that file's sha256 and therefore re-keys the
  whole-bundle `digest`. Reseal after every change. (LIVE-PROVEN: an unsealed
  edit fails guest preflight.)

### 2.2 files[] — exhaustive listing, no symlinks (LIVE-PROVEN)
- EVERY physically present file except the root `manifest.json` must appear in
  `files[]` with its real sha256 + size — an unlisted file is a preflight
  failure (`bundle_file_unlisted`, `process_preflight.py:249-282`). That
  includes strays: a `.DS_Store` or a `__pycache__/` directory that sneaks in
  fails preflight. We hit this live — it cost a run. Build the bundle in a
  clean staging directory and list what you walk, not what you meant to ship.
- Symlinks are forbidden anywhere in the tree (`bundle_symlink_forbidden`).
- Every file a process or store references must (also) be listed; migrations
  and seeds get a second store-scoped check (`seed_file_missing` /
  `seed_file_unlisted`).

### 2.3 Processes — build and run mechanics (LIVE-PROVEN rules)
- Builds run UNPRIVILEGED in a writable copy of the source at
  `/work/build/<name>/`, once per job; `run_command` execs once per world with
  cwd = that same build tree. The snapshot's own interpreters and the ALK
  runtime (`/opt/alk/`, immutable — spine §0's guest filesystem layout) are
  not writable by builds. Every pip/npm process MUST create its own
  environment inside the build tree (`python3 -m venv .venv` →
  `.venv/bin/pip install ...`) and run through it; relative `.venv/...` paths
  work in both build and run. Plain `pip install` fails with EACCES.
  (Heads-up: spine §2b's own example for the `agent` process still shows a
  bare `["pip", "install", "-r", "requirements.txt"]` build step — that
  example is stale against this venv rule; do not copy it.)
- `build_commands` and `run_command` are argv lists exec'd directly — NO
  shell. `&&`, `$VAR`, globs, and pipes do not work, and `$VAR` is **not
  expanded** in argv. If a value must reach the command line (a port flag,
  say), use the `sh -c` pattern the reference bundle uses:
  `run_command: ["sh", "-c", "exec .venv/bin/uvicorn alk_shim:app --host
  0.0.0.0 --port \"$PORT\""]` with `PORT: "{{PORT_tools-api}}"` in
  `environment`.
- `sudo`, `apt`/`apt-get`/`apt-cache`, `apk`, `dpkg`, `yum`, `dnf`, `pacman`
  are banned as build steps (`build_requires_root`,
  `process_preflight.py:111-113, 426-434`) — nothing ever runs as root.

### 2.4 Placeholders — the closed vocabulary
`environment` values are templates over EXACTLY six token shapes
(`process_preflight.py:360-424`; renderer `process_runtime.py:359-434`):
- `{{WORLD_INDEX}}` — 0-based world number
- `{{WORLD_DIR}}` — this process's per-world writable scratch directory
- `{{DB_NAME}}` — the per-world database name, always `w<N>`
- `{{PORT_<proc>}}` / `{{HOST_<proc>}}` — the named process's per-world port /
  host (`localhost` in V1); `<proc>` must name a process in this bundle
- `{{<CONFIGURATION_NAME>}}` — the rendered address of a capability (e.g.
  `{{DATABASE_URL}}`, `{{TOOLS_API_URL}}`)

Anything else inside `{{...}}` is `unknown_placeholder` — except a token
naming a declared capability whose `configuration_name` is null, which is
rejected as `capability_unresolved` instead. `build_environment`
takes **no placeholders at all** — any `{{...}}` there is rejected outright,
never resolved.

### 2.5 Ports, readiness probes, and W
- Port allocation is provisioner-owned. Each process must LISTEN on the port
  its own `{{PORT_<name>}}` renders to — wire it into the process's
  environment (the reference bundle's `PORT` + `sh -c` pattern above). Nothing
  may assume a port number.
- `fixed_port` is the escape hatch for repos that hardcode a listen port. It
  is honored exactly, forces effective parallelism to 1 when W>1
  (`parallelism_degraded`), and must avoid the provisioner's reserved bands
  14000-14099 / 15000-15799 / 24000-24099 / 25000-25799
  (`fixed_port_reserved`, `process_preflight.py:127-143, 502-517`).
- `started_check` (only for `source` processes with no capability) declares
  exactly ONE of `port: true` — a boolean that selects a TCP-accept probe on
  the process's OWN allocated port, never a literal port number — or
  `log_marker: "<substring>"` scanning the captured output; declaring both or
  neither fails validation
  (`started_check_requires_exactly_one_of_port_or_log_marker`,
  `bundle_v2.py:130-148`). Default timeout 30s. All other health checking is
  capability-level `readiness`.
- W (the world count) comes from the JOB — `job.runtime.parallelism`
  (`hosted_entrypoint.py::resolve_parallelism`) — never from the bundle. The
  bundle only has to survive any W in 1..8.

### 2.6 Users — role-assigned by rule, not enforced at runtime today
(corrects v1.0's "loud warning, not fatal", which described the LOCAL lane)
- The declared `user` is FIXED by role and validated at the model layer
  (`user_assignment_invalid`, `bundle_v2.py:452-469`): the `control_service`
  process gets `svc-agent`, every other `source` process gets `svc-tools`,
  every `managed` engine gets `svc-data`. `svc-control` is the harness's own
  user and may never appear in a bundle. There is nothing to choose here —
  author exactly this assignment.
- What actually happens at spawn, in the authoritative tree:
  `ProcessRuntimeProvider` itself defaults `require_declared_user=True`
  (`process_runtime.py:3493`), under which an unresolvable declared user is a
  typed FATAL `spawn_failed`, not a warning (`process_runtime.py:547-561`).
  The hosted entrypoint's default wiring, however, currently constructs the
  provider with `require_declared_user=False` and a resolver that always
  returns None (`hosted_entrypoint.py:1203-1207`), because the Daytona sandbox
  pins every process to one fixed OS user and rejects per-process user
  switches — so on today's hosted path every process runs uniformly as the
  sandbox user and the declared users are validated but not enforced. The
  observed live-run log line ("declares user=svc-tools but it is not
  resolvable ... running unprivileged") is that wiring plus deployed-snapshot
  skew; a stale comment at `hosted_entrypoint.py:1551` claims the opposite of
  the deps default. The authoritative tree governs.
- Producer takeaway, unchanged under either behavior: author the fixed role
  assignment, and do NOT depend on per-process user isolation existing.

### 2.7 Voice dispatch identity — there is NO net; render it unique anyway
(corrects v1.0, which claimed "W>1 with a static name is a preflight failure"
— that claim is FALSE; no such check exists anywhere)
- At contract time no such rule existed anywhere; the consumer now carries
  the blocker-#24 guard (`agent_name_not_world_unique`: parallelism > 1 plus
  a `LIVEKIT_AGENT_NAME` lacking EITHER `{{WORLD_INDEX}}` OR `{{JOB_ID}}`
  fails preflight — `c3-call-affinity.md` v0.4 §2.1) —
  pending merge and §2e table amendment. At W>1 with a static agent name, every world's
  agent registers under the SAME LiveKit identity and dispatch lands on an
  arbitrary world's agent — silent cross-world evidence contamination, no
  error raised anywhere. The only guard that exists is within-world: a world
  whose processes render more than one distinct `LIVEKIT_AGENT_NAME` gets no
  dispatch identity at all, and the call aborts pre-dial with a typed error
  (`process_runtime.py:955-975`; `call_runner.py:181-190, 526-533`).
- This hazard is unreachable today only because the platform currently
  hardcodes parallelism=1 (blocker #22: the job serializer defaults it to 1 —
  `simulate/serializers/harness_job.py:152` — the create path never sends
  more, and the UI has no field).
- The missing cross-world guard is tracked as **blocker #24**; the guest track
  will add the preflight check before W>1 ships.
- The producer MUST render a name unique per world AND per job —
  `LIVEKIT_AGENT_NAME: "agent-{{JOB_ID}}-w{{WORLD_INDEX}}"` — as defense in
  depth, not because a net exists. The template MUST carry BOTH
  `{{WORLD_INDEX}}` (world-scoped uniqueness) AND `{{JOB_ID}}` (job-scoped
  uniqueness): all jobs share one LiveKit server, so a name missing
  `{{JOB_ID}}` cross-contaminates dispatches ACROSS concurrent jobs even
  when it is world-unique. At requested W>1 the now-landing
  `agent_name_not_world_unique` guard REJECTS any `LIVEKIT_AGENT_NAME`
  template lacking EITHER placeholder (`c3-call-affinity.md` v0.4 §2.1 —
  the guard was widened from WORLD_INDEX-only to require both).
- Worker-knob env identity (pointer, not re-legislated here): a conformant
  LiveKit-worker process MUST also read the harness-set worker knobs —
  `FI_LOAD_THRESHOLD`, `FI_NUM_IDLE_PROCESSES`, and `FI_WORKER_HEALTH_PORT` —
  into its `WorkerOptions` (livekit-agents 1.7.1 exposes no env/CLI override,
  so the agent code must do the reading). The mandate, values, and the
  per-process scope are owned by `c1-world-port-model.md` v1.3 §3/§4 (and
  `c3-call-affinity.md` v0.4 §5); see them for the normative rule.

### 2.8 Secrets — what the guards actually catch (LIVE-PROVEN, corrected)
- The agent process declares `secret_purposes: ["target_provider"]` when it
  consumes provider credentials; at spawn the provisioner injects every
  matching job secret under its job ALIAS as the env-var name (spine §2b) —
  the process reads e.g. `LIVEKIT_API_KEY` because the job's ref was named
  that. The platform launch path normalizes user-supplied credentials to
  `manager: platform-vault`, `purpose: target_provider` refs, and the
  authoritative `job.py` rejects any other vocabulary (see the Authoritative
  tree note above).
- NEVER inline a resolved secret value anywhere in the bundle — but do not
  lean on the guards to catch you, because they are narrower than v1.0
  claimed ("the sealer's guard rejects it" was wrong on both counts):
  - The manifest guard runs in the MODEL VALIDATOR, not in `seal_bundle_v2`
    (`bundle_v2.py:561-569` → `bundle.py:161-173`), and it is key-name
    pattern matching only (`bundle.py:141-143`: keys containing
    api_key / api_secret / authorization / credential / password /
    private_key / secret / token). A resolved credential sitting under an
    innocuous key passes SILENTLY. Sealing proves nothing about secrets.
  - The bundle's FILES are scanned by guest preflight for fixed shapes only
    (`process_preflight.py:288-309`, reusing `bundle.py:145-152`): PEM
    private-key headers, `ghp_`/`github_pat_` tokens, `AKIA...` keys,
    `sk-...` tokens; forbidden file names `.env`, `.env.local`, `.npmrc`,
    `.pypirc`, `credentials`, `id_rsa`; forbidden suffixes `.pem`, `.key`,
    `.p12`, `.pfx`. Two consequences, both real: (a) a novel secret shape
    sails straight through — keeping secrets out is the producer's job, not
    the scanner's; (b) the scan FALSE-POSITIVES on fakes — a seed or fixture
    file carrying an `sk-...`-shaped string, even an obviously fake one,
    fails preflight (`secret_in_bundle`). Do not emit `.env`-shaped files or
    `sk-`/`AKIA`/`ghp_`-shaped strings anywhere in the bundle, even as
    fixture data.

### 2.9 The run environment is scrubbed
A process receives ONLY: its rendered `environment` + `build_environment`
(merged raw) + its purpose-matched secrets (alias as var name) + the
provisioner's unconditional PATH prepend (`/work/build/<name>/.venv/bin` and
`/work/build/<name>/node_modules/.bin`) + a fixed ambient allowlist — `PATH`,
`HOME`, `LANG`, `TZ`, `TMPDIR`, plus `LC_*` (`process_runtime.py:461-494`).
Nothing else survives. If the repo needs a variable, the bundle must set it.

### 2.10 Build tree is shared across worlds; scratch is per world
The build tree `/work/build/<name>/` is built once per JOB and shared by every
world, treated as read-only at run time by convention. Anything a process
writes at run time must go to its per-world scratch `{{WORLD_DIR}}` =
`/work/worlds/w<N>/<name>/`. A process that writes into its own build tree is
corrupting every other world's copy of itself.

### 2.11 Evidence seam (LIVE-PROVEN — cost two full call cycles)
`runtime.evidence_seam` picks how tool-call evidence is captured. Zero captured
calls = typed `evidence_missing`, scenario errored. There is no silent pass.
- `http_tool`: NO capture surface exists anywhere. Do not emit it until one
  does (open contract question, Azain).
- `tool_trace`: the guest reads the world postgres table `_alk_tool_trace`
  (`name text, arguments jsonb, result jsonb, ok boolean, error text,
  at double precision` — `at` is EPOCH SECONDS, not a timestamp). The
  producer MUST therefore make the environment write it: create the table in
  the schema migration AND instrument the tools API. The working pattern is a
  bundle-carried ASGI shim (`shim/alk_shim.py` in the reference bundle):
  build step copies it into the build tree (note: the reference bundle's
  `cp /work/bundle/...` hardcodes the delivery path §1 says is pinned by no
  frozen document — a producer cloning the pattern inherits that coupling,
  loudly, as a `build_failed` if the path moves), run command boots
  `uvicorn alk_shim:app`; it wraps the repo's app, records every non-health
  call, and is fail-open (a recording failure never fails the tool call).

### 2.12 What the tool-trace reader tolerates — and silently mangles
The column LIST in §2.11 is not enough; the reader degrades silently on type
drift (`call_runner.py:407-470`):
- Before every dial the guest DELETEs all rows from `_alk_tool_trace`
  (`call_runner.py:407-410`, best-effort). The table must be writable by the
  world DB role the guest connects with, and setup-phase writes are NOT
  evidence — they are wiped before the call starts.
- Per-row degradation on read, all silent: a non-string or empty `name` drops
  the row; non-dict `arguments` become `{}`; a non-numeric `at` becomes
  `0.0` — so authoring `at` as `timestamptz` "works" as a table but silently
  zeroes EVERY timestamp and destroys call ordering evidence; a NULL `ok`
  reads as `false` (the call grades as refused/failed). String results
  truncate at 2000 chars.
- Any whole-read failure (missing table, connection refused) degrades to zero
  calls → `evidence_missing`. Match the DDL types exactly; the reference
  schema's `CREATE TABLE _alk_tool_trace` block is the template.

### 2.13 Scenarios
- Layout per scenario: `scenarios/<name>/scenario.json` + `setup.py` +
  `ready.py` + `checks/*.py`.
- What the guest actually consumes from `scenario.json` (this supersedes the
  reference bundle's own `NOTE_TO_HUMAN_REVIEWER` line, which is STALE — it
  predates the call-runner wire and claims only scenario_key/sub_goals are
  read; do not propagate it):
  - `scenario_key` — read from the file's OWN field, never the folder name
    (`scenario_source.py:242-247`; `call_runner.py:200-229`). Must be
    non-empty (`scenario_source.py:360-364`).
  - `instruction` — REQUIRED non-empty; a missing/blank instruction is a
    typed `CallAborted` (`call_runner.py:222-225`). It does double duty: it
    is the simulated caller's situation AND the target agent's system prompt
    (`call_runner.py:296-299, 335-340`).
  - `persona` — the caller's identity; `role` is forced to `"customer"`, and
    `fixture.phone` (when present) becomes the caller's phone metadata
    (`call_runner.py:280-296`).
  - `fixture` — seeded-world facts surfaced to the simulator; `tests`
    (optional) becomes the caller's outcome, defaulting to "Complete the
    requested task and close naturally." (`call_runner.py:298-299`).
  - `sub_goals` — MUST list at least one name. A scenario declaring zero
    sub-goals fails `check_broken` — and only AFTER the paid voice call has
    already run (`hosted_scheduler.py:1690-1697`). Names must be plain
    filename components — no `/`, `\`, `.` or `..`
    (`scenario_source.py:202-213`).
- **LOUD WARNING — a missing check file is a silent always-pass.** A sub-goal
  with no `checks/<name>.py` file is treated as a JUDGED goal whose
  placeholder check always reports "held"
  (`scenario_source.py:262-277, 99-107`). Nothing warns. A typo'd check
  filename therefore becomes a sub-goal that silently always passes. The
  producer MUST emit a check file per sub-goal unless a judged-only goal is
  explicitly intended (the reference bundle's `polite_and_clear` is a
  deliberate example). An existing-but-EMPTY check file is the opposite: a
  typed load failure, never a vacuous pass.
- Entry conventions (`scenario_source.py:109-157`;
  `hosted_scheduler.py:427-452`): `setup.py` defines `setup(world)`,
  `ready.py` defines `ready(world)`, `checks/<name>.py` defines
  `check(world, calls)`. `setup.py`/`ready.py` are OPTIONAL — a missing file
  is a no-op success, as is a present-but-empty one. Return conventions:
  `None`, `True`, or an empty/whitespace string = held (ready / passed); a
  non-empty string = a complaint sentence (not ready / not held);
  `check` returning `False` = not held; ANY other return value is
  `ready_broken`/`check_broken`. `check` receives a read-only world handle
  and a calls list that is guaranteed NON-EMPTY (coverage guarantee — an
  empty list becomes `evidence_missing` before checks ever run).

### 2.14 Artifacts
- `artifacts.level` gates uploads per Channel 3's level table. `traces`
  FORBIDS call recordings (transcripts still upload); the platform's create
  page now defaults to `traces-and-recordings`, which admits them. The level
  is still not a visible control — treat the platform default, not a user
  choice, as what a run will carry.

## 3. The guest's real gate — run `preflight_bundle` before shipping

v1.0 named `model_validate` + `seal_bundle_v2` as the pre-ship gate. That is
necessary but weaker than what the guest actually runs. The guest's gate is
`preflight_bundle` (`process_preflight.py:147`), executed before any
provisioning; the producer's pre-ship gate MUST be the same function, invoked
against the staged bundle directory with the job shape it will run under
(today: `parallelism=1` and the launch path's `{alias: "target_provider"}`
secret-ref map). Its closed rule set, beyond model validation:

1. Digest verification: schema_version checked, every `files[]` sha256 +
   size re-hashed from disk, whole-bundle digest recomputed
   (`bundle_file_missing` / `bundle_file_changed` /
   `bundle_digest_mismatch`).
2. Filesystem walk: no symlinks anywhere; every physically present file
   except the root manifest listed (§2.2).
3. Secret scan over every walked file (§2.8's fixed shapes,
   `secret_in_bundle`).
4. The on-disk manifest re-validated: unknown fields anywhere →
   `unknown_field`; a manifest that drifted from the loaded one →
   `bundle_manifest_drifted`.
5. Job-and-files rules (`process_preflight.py:196-205`):
   - placeholder vocabulary, including build_environment's total ban (§2.4);
   - root-requiring build commands banned (`build_requires_root`, §2.3);
   - secret purposes matched TWO-SIDED (`process_preflight.py:437-456`): a
     job ref with `purpose: target_provider` that NO process claims is
     `secret_unclaimed` — so a producer can fail a job whose refs it never
     saw — and a process claiming `target_provider` with no such job ref is
     `secret_missing`;
   - `depends_on` graph resolved and acyclic (`depends_on_unresolved` /
     `depends_on_cycle`);
   - engine catalog pins: postgres **16**, redis **7**, rabbitmq **3.13**,
     exactly (`engine_unsupported`, `process_preflight.py:73-77`);
   - `fixed_port` outside the reserved bands (§2.5, `fixed_port_reserved`);
   - every postgres-protocol capability has a `seed.stores` entry
     (`seed_missing`, `process_preflight.py:520-531`);
   - `_alk_conformance` is reserved: the identifier may not appear
     (case-insensitively, outside SQL comments) in ANY migration or seed
     file (`reserved_name`, `process_preflight.py:97-102, 534-559` — note
     the scan has no lexer, so the name inside a string literal trips it
     too);
   - every migration/seed file on disk AND listed, and each store's
     `inputs_digest` recomputed from disk against the recorded value
     (`inputs_digest_mismatch`).
6. `kind: process` requires at least one postgres-protocol capability
   (`no_sql_store`).
7. Resource sanity: ≤ 100 processes (`process_count_exceeded`); the JOB's
   parallelism within 1..8 (`parallelism_out_of_range`) — W is the job's,
   never the bundle's (§2.5).

## 4. Failure behavior at this boundary

- Producer emits an invalid manifest → guest stops loudly at
  `validating_environment` with a typed code. The job fails; the producer
  owns manifest validity (run §3's gate before shipping).
- Producer emits a build that cannot complete inside the egress allowlist →
  typed `build_failed` with the pip/npm stderr. Producer owns build
  self-sufficiency; the platform owns the allowlist (§5).
- Evidence seam emitted but not satisfied by the environment (table absent,
  shim missing, type drift per §2.12) → `evidence_missing` per scenario.
  Producer owns the pairing.
- Retry truth (corrects v1.0's blanket "nothing retries"):
  `evidence_missing` is the ONLY retryable scenario failure code
  (`_RETRYABLE_CODES`, `hosted_scheduler.py:314`). Such a scenario is retried
  exactly once on a fresh world — and because the empty-evidence discovery
  happens only after the call completes
  (`hosted_scheduler.py:1683-1688`), EACH occurrence has already burned a
  full voice call. A broken evidence pairing wastes two real calls per
  scenario before it errors. Every other failure at this boundary is
  deterministic and does not retry. (Worlds turning unhealthy mid-scenario
  get the spine §5 one-retry too, but that is not a bundle defect.)

## 5. Egress — the MUST binds the platform, not the producer

The bundle has no egress field. Build/network access is limited to the domain
allowlist the PLATFORM assembles at sandbox launch
(`hosted_harness_gateway.py:425-433`): a base list (settings
`ALK_HOSTED_BASE_EGRESS_DOMAINS`) ∪ the job's
`security.allowed_egress_domains` ∪ the platform host. Therefore:
- Producer obligation: DECLARE what its builds and processes must reach (the
  package hosts its build steps download from, any model-download hosts), so
  the platform side can carry them.
- Platform baseline (owner: Azain) MUST include `pypi.org` +
  `files.pythonhosted.org`, plus the npm registry hosts when a node bundle
  appears and `huggingface.co` + `*.huggingface.co` for livekit model
  downloads.
- Current state: that baseline lives ONLY in a local `.env`
  (blocker #16) — no deployed default ships it. Unowned upstream until it
  does.
- What is proven: the successful graded runs' voice media went TURN-over-TLS
  THROUGH the domain allowlist — the gateway's open-network escape hatch
  (`ALK_HOSTED_EGRESS_OPEN`) was NOT set during those runs. Do not weaken the
  allowlist posture on the theory that voice needed it; it did not.

## 6. Forbidden

- Resolved secrets anywhere in the bundle — and no reliance on the guards to
  catch a slip (§2.8: the model guard is key-name-only, the file scan is
  fixed-shape-only; neither is airtight).
- Secret-SHAPED strings and files, even fake ones (fixture `sk-...` tokens,
  `.env`-named files) — the preflight scan cannot tell fake from real (§2.8).
- Reimplementing digest math (import the two functions).
- Emitting `http_tool` (until a capture surface exists).
- Compose/docker delegation — v2 carries topology inline; there is no docker
  in the sandbox (Azain's PR #66 removes it from the snapshot).

## 7. Versioning

This document versions independently of the spine. Amendments append to the
changelog; ambiguities become amendments, never guesses.

**Changelog**
- v1.2 (2026-08-27): round-2 corrections — recordings-inclusive platform
  default (§2.14), the now-landing `agent_name_not_world_unique` guard
  (§2.7), `capability_unresolved` nuance (§2.4), shim path coupling (§2.11).
- v1.1 (2026-08-27): corrections from the cold review, each verified against
  the authoritative tree:
  - Retracted the false claim that W>1 with a static agent name is a
    preflight failure — no such guard exists; documented the real hazard,
    the platform's current parallelism=1 default, and blocker #24 (§2.7).
  - Replaced the model_validate+seal pre-ship gate with the guest's real
    gate, `preflight_bundle`, and enumerated its closed rules (§3).
  - Corrected the secret-guard attribution (model validator, not the
    sealer) and documented both its key-name blindness and the file scan's
    fake-token false positives (§2.8, §6).
  - Corrected the svc-* user story: the "loud warning" describes the local
    lane; documented the provider's fatal-by-default behavior, the hosted
    wiring's current opt-out, and the snapshot-skew caveat (§2.6).
  - Added the silent always-pass warning for sub-goals with no check file
    (§2.13).
  - Reworded the egress baseline as a platform-owned MUST, noted its
    local-.env-only current state, and recorded that the graded runs did NOT
    use the open-network flag (§5).
  - Corrected bundle addressing to repo-only (no ref) and warned that
    provenance.commit vs. job commit_sha is stated by spine §2d but
    unenforced (§1).
  - Made the files[] rule explicit: every present file listed, symlinks
    forbidden, stray .DS_Store/__pycache__ fails preflight (§2.2).
  - Fixed §4's "nothing retries": evidence_missing is the one retryable
    code, one retry on a fresh world, a full voice call burned per
    occurrence.
  - Documented what the guest really reads from scenario.json (own
    scenario_key, required instruction doubling as situation + system
    prompt, persona/fixture/tests) and flagged the reference bundle's stale
    NOTE_TO_HUMAN_REVIEWER (§2.13).
  - Fixed the delivery citation (spine §0, not §2b; /work/bundle pinned by
    no frozen document) (§1).
  - Named the authoritative tree and the /tmp-worktree job.py validator
    skew (header).
  - Added the tool-trace reader's delete-before-dial and per-row
    type-degradation rules (§2.12).
  - Scoped which edits re-key which digest (§2.1); replaced the
    `/opt/alk-venv` path with spine §0's layout terms (§2.3); noted
    setup.py/ready.py are optional no-ops when missing (§2.13); documented
    argv non-expansion, the sh -c port-binding pattern, and the stale plain
    pip example in spine §2b (§2.3).
  - New subsections: closed placeholder vocabulary (§2.4), port binding and
    started_check semantics with W-from-job (§2.5), run-environment scrub
    (§2.9), shared build tree vs per-world scratch (§2.10).
- v1.0 (2026-08-27): initial contract, distilled from the first live
  end-to-end voice runs (blockers #15–#21) and the hand-authored reference
  bundle.
