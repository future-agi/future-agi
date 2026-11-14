# Hosted Harness Execution — Seam Contracts (v1.15)

**Date:** 2026-08-25 · **Status:** FROZEN — conform, don't redesign
**Owner / defects:** Khushal. Amendments are published as a new version of
THIS file with a dated changelog entry; consumers pin the version they built
against.
**Changelog:** 1.15 (2026-08-25) — §2f: the producer resolves the domain —
typed provisioner errors carry code AND `FailureDomain` across the §4 seam
(rows like `spawn_failed` split on managed-vs-source, a fact only the
provisioner has; consumers were re-deriving domains from codes and had to
guess that split). Batch clarifications, same version: §0 layout blesses
`/work/outbound-spool/` (the durable event spool already in use); §2b —
the `fixed_port` degrade is announced only when requested W > 1 (the
event schema forbids the W=1 payload), and the rabbitmq management
listener (`amqp+10000`) extends `fixed_port_reserved` to
`[24000,24099] ∪ [25000,25799]`; §2e adds `bundle_manifest_missing`
(produced but unlisted); §5.4 sanctions the provision-fast-path (skipping
the immediate `reset` on a just-provisioned world — state-equivalent);
§5.5 the 120s flush window starts at the terminal decision on every
path, not only cancel; §4.5b records the serialized-reset throughput cap
as accepted for V1.
1.14 (2026-08-25) — §0.6: exit code `4` added — terminal
state reached but the terminal event undeliverable on the final drain
(channel failed or terminal rejected): previously this shape had no row,
and the guest's only options were `0` (claims a flush that did not
happen — the run's evidence silently lost, no retry) or a crash code
(claims no terminal was reached). Gateway handling = identical to crash
(infrastructure retry, fresh channels); the distinction is diagnostic.
1.13 (2026-08-25) — §5.4: pool exhaustion caused by a
deterministic fault passes the underlying §2f code through. When the final
failed re-provision attempt of EVERY unhealthy world carries the same §2f
code whose §2f domain is `environment` or `agent` (the never-retried
domains), the job fails in stage `running` with that code and domain —
`world_pool_exhausted` → `infrastructure` remains the code for every other
exhaustion (mixed codes, any infrastructure-domain failure). Consequence:
the scheduler MUST preserve typed provider error codes across the §4 seam
(flattening them re-classifies a never-retry fault as retryable and burns
every whole-job attempt on an identical failure). The stage-`running` set
stays closed: it is `world_pool_exhausted` plus the §2f table's
`environment`/`agent` rows.
1.12 (2026-08-25) — §4.5b: `healthy` is explicitly in the
serialized set (the port's real `healthy` demotes state — it writes, so it
rides the same one-call-in-flight rule). §3: transition
`preparing → unhealthy` added (a failed reset or health probe before the
world's first ready — it was reachable and unlisted).
1.11 (2026-08-25) — §4: the port's concurrency contract
stated (NOT reentrant — the scheduler serializes provider calls; it was
silent, and W>1 is built on this port); scheduler-demotes-state made
normative (providers hand out live `EnvironmentRuntime` objects; the
scheduler's demotion to `unhealthy` is the signal `provision()`
reconciles on). §5.4: "ready worlds reach 0" is declared only after
in-flight re-provisioning completes without restoring a world — never on
an instantaneous snapshot; job-level stage-`running` failure vocabulary
added (closed): `world_pool_exhausted` → `infrastructure`.
1.10 (2026-08-25) — §4: `provision` gains `bundle_dir`
(the verified bundle directory — the SAME path preflight ran against;
§2c seed/migration paths resolve against it, never against the checkout:
reading them from `source` would execute bytes the §2e hash/secret/symlink
checks never saw). §4.2 `empty`: flush MAY be implemented as data-dir wipe
+ engine respawn (stronger guarantee; flush-only is engine-dependent).
§2f: `seed_failed` and `store_statement_failed` added (they cross the
outbound seam and had no row). 1.9 (2026-08-25) — §2e: `fixed_port_reserved` added
(a `fixed_port` inside the formula bands [14000,14099]∪[15000,15799] is
rejected at preflight — it would collide with an allocated port).
1.8 (2026-08-25) — §2f: closed build/run failure-code
table added (`runtime_unsupported`, `build_failed`, `spawn_failed`,
`depends_on_timeout`, `depends_on_unresolved`, `depends_on_cycle`,
`source_tree_unavailable`) with §4.6 domains — these cross the outbound
seam and had no table. §0: `runtime_unsupported` is best-effort at build
time (argv[0] interpreter sniffing; a `requires-python`/`engines`
mismatch surfacing as a nonzero install is `build_failed`); process
`name` constrained to `^[a-z0-9][a-z0-9_-]*$`; `/work/managed/<proc>/`
added to the layout block. §2b: `started_check.port` names the process
whose allocated port is dialed (not a literal), so it honors the port
formula and `fixed_port`. 1.7 (2026-08-25) — §2d: the bundle `digest` construction
specified byte-exactly (it was unspecified; the consumer existed before
any producer); `configuration_name` may not collide with the placeholder
vocabulary or the `PORT_`/`HOST_` forms (`configuration_name_reserved`).
§0: a missing interpreter is a BUILD-time failure, not
preflight (`runtime_unsupported` moves; no manifest field carries an
interpreter demand). §2e: `inputs_digest` verification added to item 5;
failure-code table added (+`configuration_name_reserved`,
`sentinel_shape_invalid`). 1.6 (2026-08-25) — §2b `user`: bundles may
name only the
service users (svc-agent/svc-tools/svc-data); `svc-control` is the
harness's own and never appears in a bundle. §2d: `configuration_name`
uniqueness is unconditional (simpler than reference-scoped). §2 preamble:
the manifest carries a top-level `name`. 1.5 (2026-08-24) — attempt
registration (§0 step 2a),
cancel signal + pre-delete hold, capabilities token corrected (per-attempt,
four endpoints), `evidence_seam` bundle field, `no_sql_store` preflight,
§5 step 3.5 (generation + proof + pre-allocation), degrade reasons
trimmed. 1.4 (2026-08-24) — exit code 3 (fenced/superseded), seed
null-resolution rule, `parallelism_degraded` payload nesting per the
outbound-channels contract. 1.3 (2026-08-24) — registry rows for the
world-handle and outbound-channels contracts, now written. 1.2
(2026-08-24) — add the
contract registry (§7). 1.1
(2026-08-24) — pin `scenario_count` hosted range; align §5.4 with the
scenario contract's `setup(world)`/`ready(world)` mechanism. 1.0
(2026-08-24) — initial frozen version.

This document is standalone. If you need information that is not in this
file to build your side, that is a contract defect — report it, don't guess.

## Glossary

- **Gateway** — backend service (Azain) that drives the Daytona API from
  outside the sandbox.
- **Guest** — everything inside the Daytona sandbox: ALK entrypoint, stages,
  provisioner, environment processes, simulator.
- **Bundle** — the environment description (§2) produced by ALK's stages
  (Rishav) and consumed by the provisioner (Khushal).
- **Baseline** — the sealed post-migrate+seed datastore state every world is
  cloned from and reset to.
- **World** — one isolated, parallel instance of the environment (its own
  logical DB copy + its own `source` processes). NOTE: unrelated to the
  existing `fi.alk.harness.world` package ("generated worlds" behind
  synthetic tools); for hosted runs, §2c `seed`/baseline supersedes
  `world/snapshot.py` as the reset mechanism.
- **W** — `job.runtime.parallelism` = `provision(instances=…)` = number of
  worlds = number of concurrent simulation calls. One symbol, one number.

---

## §0 Topology and sandbox invocation

ALK runs **inside** the Daytona sandbox. The gateway drives the Daytona API
from outside. The bundle never crosses a network: ALK's in-sandbox
provisioner consumes it and starts every service as a plain process on
localhost. No Docker inside the sandbox. No network runtime provider exists.

```
gateway ──Daytona API──▶ [ sandbox: entrypoint → stages → provisioner
                            → W worlds → simulate → grade → seal ]
        ◀──outbound only── events / results / artifacts
```

### Invocation contract (gateway → guest)

The gateway performs, in order:

0. **Register the attempt with the platform** (step "2a" in the
   outbound-channels contract's numbering): the platform records
   `(organization, job_id, attempt_id, attempt_number, fence,
   expires_at)` and mints the attempt token — this row is what ingestion
   validates bearers and fences against, and registering attempt N+1 is
   what advances the supersession high-water mark.
1. **Source acquisition happens in the gateway, never in the guest.** The
   gateway clones the repo (GitHub installation token stays outside the
   sandbox, revoked after checkout) and uploads the checkout to
   `/work/source/` (owner `svc-control`, read-only to all `svc-*` users).
2. Upload the job payload (§1) to `/work/job.json` (owner `svc-control`,
   mode 0600). `/work/job.json` is the provisioner's job-identity and
   configuration source; `svc-control` may read it, customer users may not.
3. Resolve every SecretRef in the job and upload the resolved map to
   `/run/futureagi/secrets.json` (owner `svc-control`, mode 0600) as
   `{ "<alias>": "<value>", ... }` — aliases are the keys of
   `agent.secret_refs`. **Lifetime rule:** the provisioner loads this file
   into memory at startup and deletes it immediately after loading, before
   any customer process starts. The in-memory map lives for the whole job —
   `reset` restarts and `provision` reconciliations re-inject from memory.
4. Upload the platform capability file to
   `/run/futureagi/capabilities.json` (owner `svc-control`, mode 0600):
   the per-attempt bearer — valid for the whole attempt until its
   expiry, NOT one-use — plus the four endpoint URLs
   (events/results/artifacts/scenarios). Schema, expiry obligation, and
   handling rules are owned by the outbound-channels contract.
5. Exec, as `svc-control`, working directory `/work`:
   `python -m fi.alk.harness.hosted_entrypoint /work/job.json --source /work/source --output /work/artifacts`
6. **Exit-code contract:** `0` = the job reached a terminal state
   (COMPLETED, FAILED, or CANCELED) and flushed its outbox — read the
   result from the event stream, not the exit code. `3` = the guest was
   fenced out or its token expired (outbound-channels contract): the
   attempt is superseded — the gateway records it as superseded, never as
   a retryable infrastructure failure. `4` = the job reached a terminal
   state but could NOT deliver the terminal event (the events channel
   failed, or the platform rejected the terminal, on the final drain) —
   the outbox is provably unflushed, so exit `0` would silently lose the
   whole run's evidence; the gateway treats `4` exactly like a crash (an
   `infrastructure` failure, retried with fresh channels) but the
   distinct code lets operators tell evidence-loss from a crash (v1.14).
   Any other non-zero = the guest crashed before reaching a terminal
   state; the gateway records an `infrastructure` failure. Stdout is
   diagnostics only, never parsed.
7. To cancel or on TTL: write `/run/futureagi/cancel.json`
   `{"reason": "user_canceled" | "ttl_exceeded"}`, send SIGTERM to the
   entrypoint, and wait — the gateway MUST NOT delete the sandbox before
   the guest exits or the 120s flush window elapses.
8. Delete the sandbox, verify it is gone, persist the cleanup receipt.

**Implementation delta (`hosted_entrypoint.py` / `executor.py`):** current code
exits 0 only on COMPLETED — it must change to exit 0 on **any terminal
stage**. `GitHubSourceAcquirer` and the `acquiring_source` stage are
local-SDK-only: in hosted mode the source is already present and no
checkout credential ever enters the guest.

Guest filesystem layout (guaranteed by the base snapshot):

```
/opt/alk/                 ALK runtime, immutable
/work/source/             uploaded checkout, read-only
/work/build/<proc>/       per-process build tree (copied, writable — §2b)
/work/managed/<proc>/     job-shared managed-engine data dir (§2b)
/work/worlds/w<N>/<proc>/ per-world writable scratch ({{WORLD_DIR}}) +
                          per-world managed-engine data dir
/work/artifacts/          artifact spool
/work/outbound-spool/     durable event spool (outbound-channels contract)
/run/futureagi/           secrets.json (transient), capabilities.json
```

Users guaranteed by the base snapshot: `svc-control` (ALK + provisioner),
`svc-agent` (the agent process), `svc-tools` (every other `source`
process), `svc-data` (every `managed` engine). Bundles may not name other
users. All worlds share these uids in V1 — process-level cross-world
isolation is NOT claimed; the sandbox is the isolation boundary.
Interpreters/binaries guaranteed by the base snapshot: python 3.11 and
3.12, node 20 and 22, git, ffmpeg, plus the §2b engine catalog. The
manifest carries no interpreter-demand field (the source is not embedded
in the bundle, so preflight cannot see `.python-version`/`engines`); a
repo needing an interpreter the snapshot lacks fails at BUILD time —
the build step's failure is reported `runtime_unsupported`, naming what
the snapshot ships.

---

## §1 Platform → guest — `futureagi.harness-job.v1` (complete)

```json
{
  "schema_version": "futureagi.harness-job.v1",
  "job_id": "uuid",
  "run_id": "uuid",
  "execution": "hosted",
  "source": {
    "kind": "github | archive | remote",
    "repository": "org/repo",
    "ref": "branch-tag-or-sha",
    "commit_sha": "resolved-40-hex-sha",
    "installation_id": "github-app-installation-id | null",
    "archive_artifact_id": null,
    "endpoint": null,
    "visibility": "public | private"
  },
  "agent": {
    "connector": "livekit | vapi | retell | auto",
    "config": { "non_secret_key": "value" },
    "secret_refs": {
      "LIVEKIT_API_KEY": {
        "manager": "platform-vault",
        "key": "secret-id",
        "version": null,
        "purpose": "target_provider"
      }
    }
  },
  "scenario_count": 10,
  "seed": 1234,
  "runtime": {
    "isolation": "dedicated_vm",
    "cpu_units": 4,
    "memory_mb": 8192,
    "parallelism": 3,
    "concurrency_weight": 1,
    "max_duration_seconds": 3600,
    "network_policy": "live"
  },
  "security": {
    "untrusted_source": true,
    "read_only_source": true,
    "allow_privileged": false,
    "allow_host_runtime_control": false,
    "allowed_egress_domains": []
  },
  "retry": {
    "max_infrastructure_attempts": 2,
    "initial_backoff_seconds": 1,
    "max_backoff_seconds": 15,
    "retryable_domains": ["infrastructure", "connectivity"]
  },
  "artifacts": {
    "level": "full",
    "retention_days": 30,
    "allow_bundle_download": true,
    "max_artifact_bytes": 1073741824
  },
  "platform_run_id": "optional",
  "metadata": {}
}
```

Field rules:

- `source.kind` — `image` is NOT a hosted kind: rejected at platform
  admission (`image_source_not_hosted`); there is no container runtime in
  the guest. `commit_sha` is resolved by the gateway and **required** for
  `kind: github`; it is the source of `provenance.commit` (§2d) — the
  uploaded checkout has no `.git` directory, so the guest cannot resolve
  refs itself.
- `agent.secret_refs` — the **alias** (dict key) is the environment-variable
  name the value is injected under. `purpose` is from the closed
  vocabulary: `target_provider` (agent/tool provider creds),
  `source_checkout` (gateway-only; never uploaded to the guest). Any other
  purpose string is a submission error. `manager` — V1 legal value:
  `platform-vault`; the **gateway** resolves all refs before upload (§0
  step 3) and the guest never performs secret-manager resolution
  (`fi.alk.harness.secrets.resolve_worker_secrets` is not on the hosted
  path). `version: null` = latest at resolution time; the resolved value is
  pinned for the job's lifetime.
- `runtime.parallelism` — integer 1..8 (8 = hard cap; the per-world port
  ranges and V1 resource classes support no more). **Feasibility rule,
  enforced at platform admission:** for voice jobs, `parallelism` must be
  ≤ `cpu_units`; violations are rejected (never clamped). Effective
  parallelism can still degrade to 1 at runtime (§4 conformance gate,
  `fixed_port`), announced by a `parallelism_degraded` event (§5).
  `ExecutionPolicy.max_parallel_cases` is ignored at this seam —
  `parallelism` supersedes it for hosted jobs.
  **Implementation delta (`fi/simulate/runtime/spec.py`):** `RuntimeRequirements`
  has NO `parallelism` field today and pydantic would silently drop it. It
  gains `parallelism: int = Field(default=1, ge=1, le=8)`, and
  `HarnessJob`'s validators reject hosted jobs with `parallelism >
  cpu_units`. Without this change, every job silently runs W=1.
- `runtime.isolation` — V1 legal value: `dedicated_vm` only.
- `runtime.network_policy` — V1 legal value: `live` only: egress =
  platform base allowlist (model providers, STT/TTS providers, LiveKit,
  object storage, ingestion endpoints) ∪ `security.allowed_egress_domains`.
- `runtime.concurrency_weight` — integer 1..10, default 1: platform
  queue-admission weight across jobs; unrelated to `parallelism`.
- `scenario_count` — hosted admission range: 1..10 in V1 (the generator's
  own range is wider; admission caps it, rejected not clamped).
- `seed` — the gateway guarantees a concrete integer: a null seed in the
  submission is resolved to a random integer and recorded in the uploaded
  `job.json`, so every run is reproducible from its own job record.
  Determinism rule: scenario `i` (0-based, in `ProvenScenarioSet` order)
  uses `seed + i`, independent of W and of which world runs it.
- `agent.connector: auto` — resolved by the guest's understand stage
  **before** bundle authoring; the bundle records the resolved connector in
  `metadata.connector`. Rishav's stage never sees an unresolved `auto`.
- Hosted jobs never contain local filesystem paths. Private GitHub uses
  `installation_id`, never a PAT. Raw secret values anywhere in this
  payload are a submission error — only SecretRefs cross this boundary.

---

## §2 ALK stages → in-sandbox provisioner — `futureagi.environment-bundle.v2`

A directory/archive: `manifest.json` + the environment files. The
manifest carries a top-level `name` (a short slug for the environment,
carried over from bundle-v1). **A hosted
bundle does NOT embed the repository source** — `/work/source` is
authoritative. `files` covers only
migrations, seed files, and generated artifacts; every file has sha256 +
size; the whole bundle has a content `digest`. The §2e secret scan is
scoped to the bundle's own files — scanning the customer's repo is an
admission concern outside this seam.

### §2a Runtime kinds

```json
"runtime": { "kind": "process", "control_service": "agent",
             "evidence_seam": "http_tool" }
```

- `evidence_seam` (`"http_tool" | "tool_trace"`, required for
  `kind: process`) — how tool evidence is captured: the harness-routed
  tools API, or the `HARNESS_AGENT_TOOL_TRACE` file the agent writes.
  Authored by the env-creation stage, which knows the repo; consumed by
  the scheduler and the world handle (see the world-handle contract).

- Hosted jobs that run an environment use `kind: "process"`.
- `kind: "external"` is reserved for `source.kind: remote` ONLY (the whole
  target lives on customer infra): `processes` and `seed` are omitted and
  evidence is labeled non-isolated by the platform.
- **External connectors are decoupled from environment kind:** a
  `vapi`/`retell` job whose repo ships a backend (tools API + datastore)
  is still `kind: process` — the `agent` process entry is simply omitted
  (the voice transport is external; the environment is not). Worlds,
  seeds, resets, and final-state grading all apply.
- `kind: "compose"` remains legal for **local SDK runs only**; a hosted
  job with `kind: compose` fails preflight (`compose_not_hosted`).
- An adopted `docker-compose.yml` is source material for translation; it
  appears only in `provenance.adopted_files`, never executed.
- **Repos with no compose file** go through the same door: the generated
  runtime plan (`generated_runtime.py`) is emitted as `processes` entries.
  Its Dockerfile-style install lines map onto ordered `build_commands`
  steps (§2b); lines requiring root (`apt-get`, system packages) fail
  preflight (`build_requires_root`); `ENV` lines map onto
  `build_environment`. No Dockerfile is generated for hosted runs;
  `generated-runtime.json` is retained as provenance only.

**Implementation delta (breaking changes to `bundle.py`):**
`BUNDLE_SCHEMA_VERSION` becomes `futureagi.environment-bundle.v2`
(`…bundle.v1` bundles are rejected by the hosted provisioner); the rule
"process runtime requires `command`" is replaced by "process runtime
requires a non-empty `processes` section" (`command` is removed);
`document` survives only for `kind: compose` (local); `services` (name
list) is dropped — derive names from `processes`; the `services/source/`
embed is dropped (see §2 preamble). `control_service` is unchanged.

### §2b `processes` (required when `kind: process`)

```json
"processes": [
  {
    "name": "postgres",
    "kind": "managed",
    "engine": "postgres",
    "version": "16",
    "user": "svc-data",
    "depends_on": []
  },
  {
    "name": "tools-api",
    "kind": "source",
    "working_directory": "services/tools-api",
    "build_commands": [["npm", "ci"]],
    "run_command": ["node", "server.js"],
    "environment": {
      "DATABASE_URL": "{{DATABASE_URL}}",
      "PORT": "{{PORT_tools-api}}",
      "TMPDIR": "{{WORLD_DIR}}"
    },
    "secret_purposes": [],
    "user": "svc-tools",
    "depends_on": ["postgres"]
  },
  {
    "name": "agent",
    "kind": "source",
    "working_directory": ".",
    "build_commands": [["pip", "install", "-r", "requirements.txt"]],
    "run_command": ["python", "agent/agent.py", "start"],
    "environment": {
      "DATABASE_URL": "{{DATABASE_URL}}",
      "TOOLS_API_URL": "{{TOOLS_API_URL}}",
      "LIVEKIT_AGENT_NAME": "agent-{{JOB_ID}}-w{{WORLD_INDEX}}"
    },
    "secret_purposes": ["target_provider"],
    "user": "svc-agent",
    "depends_on": ["postgres", "tools-api"]
  }
]
```

Unknown keys in a process entry are a preflight error (`unknown_field`).
Every process `name` matches `^[a-z0-9][a-z0-9_-]*$`
(`process_name_invalid`) — the name is path-joined into build/scratch
directories, so it may never carry `/`, `..`, or an absolute form.

**Build and run mechanics (copy-based — no mounts, no privileges):**

- The provisioner **copies** each `source` process's `working_directory`
  tree from `/work/source/<working_directory>` to `/work/build/<name>/`,
  chowned to the process's `user`. `build_commands` (an ordered list of
  argv steps, each exec'd directly — no shell, so `&&`, `$VAR`, globs, and
  pipes do not work; multi-step installs are multiple entries) run there
  once **per job**. `run_command` is exec'd once **per world** with cwd
  `/work/build/<name>/` — the build tree is treated as read-only at run
  time by convention; per-world writable scratch is
  `/work/worlds/w<N>/<name>/`, exposed as `{{WORLD_DIR}}`.
- Optional `build_environment` (plain dict, no placeholders): extra env
  for build steps, also merged into the run env. The provisioner always
  prepends `/work/build/<name>/.venv/bin` and
  `/work/build/<name>/node_modules/.bin` to `PATH` for both build and run.
- Optional `fixed_port` (integer): for repos that hardcode a listen port
  the environment cannot override. The provisioner honors it exactly —
  and any bundle containing a `fixed_port` forces effective parallelism
  to 1, announced by `parallelism_degraded` (`reason: fixed_port`) ONLY
  when requested W > 1 — at W=1 nothing degrades and the event's own
  schema (`1 <= effective < requested`, outbound-channels) forbids the
  payload (v1.15). A fixed port can exist in only one world. Prefer
  env-driven ports whenever the repo allows; `fixed_port` is the escape
  hatch, not the default. The rabbitmq management listener binds
  `amqp_port + 10000`, so the reserved `fixed_port_reserved` bands extend
  to `[24000,24099] ∪ [25000,25799]` (v1.15).

**Instancing and ports:**

- `source` processes run once per world. `managed` engines run once per
  **job** when their store's baseline strategy is `template_database`;
  once per **world** when it is `datadir_copy`, `empty`, or when the
  engine has no `seed.stores` entry at all (per-world is the only safe
  default — a shared engine reset by one world would corrupt the others).
- Port allocation (provisioner-owned; nothing else may assume a port
  except via `fixed_port`): per-world processes get
  `15000 + 100*world_index + ordinal`; job-shared engines get
  `14000 + ordinal`. `ordinal` = the process's 0-based index in the
  `processes` array as authored. Ranges support W ≤ 8 and ≤ 100 processes.

**Wiring:**

- `environment` values are templates. **Closed placeholder vocabulary** —
  anything else inside `{{…}}` is a preflight error (`unknown_placeholder`):
  - `{{WORLD_INDEX}}` — 0-based world number (anything that must differ
    per world: LiveKit agent name, log prefixes).
  - `{{WORLD_DIR}}` — this process's per-world writable scratch directory.
  - `{{PORT_<name>}}` / `{{HOST_<name>}}` — the named process's per-world
    port / host (host is always `localhost` in V1).
  - `{{DB_NAME}}` — the per-world database name, always `w<N>` (under
    `datadir_copy` the provisioner configures each per-world engine's
    database to `w<N>` as well — one rule, both strategies).
  - `{{<CONFIGURATION_NAME>}}` — the rendered address of any capability
    (e.g. `{{DATABASE_URL}}`, `{{TOOLS_API_URL}}`).
- `secret_purposes` — which of the job's SecretRefs this process receives:
  at spawn, the provisioner injects every alias whose ref's `purpose` is
  listed, under the **alias** as the env-var name. Preflight errors, both
  scoped to `purpose: target_provider` only (gateway-only purposes are
  exempt): a `target_provider` ref no process lists (`secret_unclaimed`);
  a listed purpose no ref supplies (`secret_missing`).
- `user` — from the snapshot's SERVICE users only: the control service
  gets `svc-agent`; every other `source` process gets `svc-tools`; every
  `managed` engine gets `svc-data`. `svc-control` is the harness's own
  user and may never appear in a bundle.
- `depends_on` — wait-until-ready ordering: the dependent starts only
  after the dependency's capability `readiness` probe passes (or its
  `started_check`, or immediately after spawn if it has neither). Timeout
  = that probe's `timeout_seconds` (default 30s for `started_check`/none).
  Cycles and unknown names are preflight errors.
- `started_check` (optional; only for `source` processes with no
  capability): `{"port": true, "timeout_seconds": 30}` waits for a TCP
  accept on the process's OWN allocated port (the port formula, or its
  `fixed_port`) — the value selects the port-probe variant, it is not a
  literal port number; or `{"log_marker": "listening",
  "timeout_seconds": 30}` scans the process's captured output.
- Health/readiness is otherwise declared ONLY in the capability-level
  `readiness` section (§2d). Processes never carry health checks.

**Managed-engine catalog (V1 — the snapshot ships exactly these):**

| engine | version | default role / auth | db name | strategies |
|---|---|---|---|---|
| postgres | 16 | role `harness`, password generated per job | `w<N>` per world | `template_database`, `datadir_copy` |
| redis | 7 | no auth (localhost only) | — | `datadir_copy`, `empty` |
| rabbitmq | 3.13 | user `harness`, password generated per job | — | `datadir_copy` |

`version` is a major-version pin; an engine or version not in the catalog
fails preflight (`engine_unsupported`) naming what the snapshot ships.
`{{DATABASE_URL}}` renders with the catalog role, the generated password,
the allocated port, and `{{DB_NAME}}`.

### §2c `seed` (required for stores used by a `kind: process` run)

```json
"seed": {
  "stores": [
    {
      "capability": "database",
      "migrations": ["db/schema.sql"],
      "seed_files": ["db/seed.sql"],
      "baseline": { "strategy": "template_database",
                    "inputs_digest": "sha256:<64-hex>" },
      "sentinel": { "query": "SELECT count(*) FROM riders",
                    "expected": "12" }
    }
  ]
}
```

- Which capabilities need a store entry: capabilities whose `protocol` is
  `postgres` — always (`seed_missing` otherwise). `redis`/`rabbitmq
  (amqp)` capabilities — only if the repo ships seed state for them;
  otherwise their baseline is implicitly `empty` (reset = flush) and they
  run per-world (§2b).
- Permitted strategies per engine: see the §2b catalog table. Any other
  pairing: preflight error (`seed_strategy_unsupported`).
- `migrations` / `seed_files`: bundle-relative paths, hashed in `files`,
  applied in listed order.
- `inputs_digest` — computed at authoring time, byte-exact construction:
  sha256 over the concatenation, for each file in `migrations` then
  `seed_files` in listed order, of `<relative_path>\n<content_length>\n<content_bytes>`,
  followed by `<engine>:<version>\n`. Consumer: the provisioner records it
  on the build output as the baseline identity for attempt-retry reuse; a
  mismatch between a cached baseline and the bundle's digest forces a
  rebuild and emits `baseline_inputs_changed`.
- `sentinel` — a read-only check plus its exact expected value (string
  compare) against the freshly seeded baseline; authored by the bundle
  producer, who knows the seed data. Per-protocol shapes:
  postgres `{query, expected}`; redis `{key, expected}`; rabbitmq
  `{queue, expected_depth}`. Required for every store entry. Used by the
  conformance gate (§4) and by post-reset verification.
- Reserved canary names — migrations/seeds must not create them
  (`reserved_name`): table `_alk_conformance` (postgres), key
  `_alk_conformance` (redis), queue `_alk_conformance` (rabbitmq).

### §2d Capabilities, readiness, files, provenance

- `capabilities` — authored slugs matching `^[a-z][a-z0-9_]*$`, each with
  `protocol`, `service` (a `processes` name), `container_port`, and a
  `configuration_name`. `configuration_name` is **unique across
  capabilities unconditionally**, may not collide with the fixed
  placeholder vocabulary (`WORLD_INDEX`/`WORLD_DIR`/`DB_NAME`) or match
  `^(PORT|HOST)_` (`configuration_name_reserved` — a collision would
  make the capability's address unspellable or silently misrender), and
  is additionally **non-null** whenever
  referenced by any process
  `environment` or `seed.stores` entry (`configuration_name_duplicate` /
  `capability_unresolved` preflight errors; `seed.stores[].capability` and
  `readiness[].capability` must resolve into `capabilities`).
  `container_port` = the port the process is told to listen on (i.e. what
  `{{PORT_<name>}}` renders to at runtime is authoritative; this field is
  informational in the hosted path, kept for local-compose compatibility,
  and nullable for `kind: process` bundles).
- `readiness` — per capability: protocol-aware probe
  (`{capability, path?, timeout_seconds, interval_seconds}`).
- `files` — every bundle file: `{path, sha256, size}`; whole-bundle
  `digest`, constructed byte-exactly as: sha256 over
  `json.dumps(manifest_minus_digest_and_files, sort_keys=True,
  separators=(",", ":"), ensure_ascii=False)` encoded UTF-8, then for
  each `files[]` record IN LISTED ORDER the canonical dump of
  `{path, sha256, size}` (same json.dumps settings) prefixed by its
  byte length as 8 bytes big-endian. `sha256:` prefix on the hex.
  Property stated so nobody trips on it: the hash covers the NORMALIZED
  model dump, so adding an optional manifest field re-keys every
  previously sealed bundle's digest — sealer and verifier must ship
  together. The single normative implementation is
  `bundle_v2.seal_bundle_v2()`; producers call it, never reimplement.
- `provenance` — `{source_kind, repository, commit, source_digest,
  generator, generator_version, adopted_files, generated_files}`;
  `commit` = the job's `source.commit_sha` (§1).

### §2e Pre-provision verification (the complete checklist)

The provisioner verifies, in order, before starting anything:
1. `schema_version` is `…bundle.v2`; bundle `digest` and every file sha256
   match.
2. No symlinks, no `..` path traversal, no absolute paths in `files`.
3. No secret material **in the bundle's own files**: no `.env`, key/cert
   files, or high-entropy secret-scan hits (`secret_in_bundle`).
4. No privileged requests: bundles cannot request users, mounts, host
   networking, or devices (there are no fields for them; unknown fields
   fail preflight).
5. Placeholder vocabulary (environment AND build_environment — the
   latter rejects any `{{…}}` at all), capability resolution, secret
   purposes, depends_on graph, engine catalog, seed strategies,
   sentinels, reserved names, the §2b user-assignment rule, and
   `inputs_digest` recomputed from the listed seed inputs against the
   recorded value (`inputs_digest_mismatch`) — all rules named in §0
   and §2a–§2d.
6. A `kind: process` bundle must declare at least one postgres-protocol
   capability (`no_sql_store`) — scenario setup/checks and the
   conformance canary are defined against it.
7. Resource sanity: process count ≤ 100; W from the job within 1..8.
Failures are typed preflight errors, reported as a FAILED terminal
state with `FailureDomain.ENVIRONMENT` in
`HarnessStage.VALIDATING_ENVIRONMENT` — never a crash.

**§2e failure-code table (closed; these cross the outbound seam):**
contract-rule codes — `compose_not_hosted`, `engine_unsupported`,
`no_sql_store`, `seed_missing`, `seed_strategy_unsupported`,
`sentinel_shape_mismatch`, `store_protocol_unsupported`,
`capability_engine_mismatch`, `store_service_not_managed`,
`reserved_name`, `unknown_placeholder`, `unknown_field`,
`secret_in_bundle`, `secret_unclaimed`, `secret_missing`,
`build_requires_root`, `user_assignment_invalid`,
`configuration_name_duplicate`, `configuration_name_required`,
`configuration_name_reserved`, `sentinel_shape_invalid`,
`capability_unresolved`, `service_unresolved`,
`control_service_unresolved`, `process_name_duplicate`,
`inputs_digest_mismatch`; mechanical codes — `bundle_schema_unsupported`,
`bundle_manifest_invalid`, `bundle_manifest_missing` (the manifest file
is absent entirely — producers emitted it but the table lacked the row,
v1.15), `bundle_manifest_drifted`,
`bundle_digest_mismatch`, `bundle_digest_invalid`,
`inputs_digest_invalid`, `file_sha256_invalid`, `source_digest_invalid`,
`bundle_file_missing`, `bundle_file_changed`, `bundle_file_unlisted`,
`bundle_symlink_forbidden`, `bundle_path_unsafe`,
`depends_on_unresolved`, `depends_on_cycle`, `seed_file_missing`,
`seed_file_unlisted`, `process_count_exceeded`,
`parallelism_out_of_range`, `evidence_seam_required`,
`processes_required`, `processes_and_seed_forbidden`,
`document_only_for_compose`, `compose_runtime_requires_document`,
`build_command_step_empty`, `started_check_requires_exactly_one_of_port_or_log_marker`,
`resolved_secret_forbidden`, `capability_slug_invalid`,
`process_name_invalid`, `fixed_port_reserved`.

## §2f Build/run failure-code table (closed; these cross the outbound seam)

Raised by the provisioner during build and process startup (stage
`building_environment` unless noted), each mapped to a `FailureDomain`
per §4.6. The PRODUCER resolves the domain: a typed provisioner error
carries both its code AND its resolved `FailureDomain` across the §4
seam, because rows like `spawn_failed` split on facts only the
provisioner knows (managed vs source); consumers (scheduler, entrypoint)
MUST read the carried domain and never re-derive it from the code alone
(v1.15):

- `source_tree_unavailable` — a process's `working_directory` is absent
  or not a directory in the checkout → `environment` (deterministic
  authoring fault; NOT retried).
- `build_failed` — a `build_commands` step exited nonzero or timed out
  (includes a `requires-python`/`engines` mismatch surfacing as a failed
  install) → `agent`.
- `runtime_unsupported` — a build step's argv[0] names an interpreter the
  snapshot lacks (best-effort argv[0] detection; the common mismatch case
  is `build_failed`) → `environment`.
- `spawn_failed` — a `source` or `managed` process failed to start →
  `infrastructure` if a managed engine, `agent` if source.
- `port_not_consumable` — a process declared `fixed_port_consumable` did
  not honor its per-world allocated port at effective W>1 (detected by the
  gate declared-port listener check, or a consumable process's world-≥1
  bind death naming a declared port) → `agent` (terminal, NOT a degrade;
  the agent must read its assigned port env or the run must request
  `parallelism=1`) (v1.16, D28 post-freeze reconciliation).
- `depends_on_timeout` — a dependency did not become ready within its
  probe/`started_check` timeout (stage `building_environment`) →
  `infrastructure`.
- `unsupported_capability_protocol` — a capability's protocol has no
  defined address shape at this seam → `environment`.
- `seed_failed` — a §2c migration or seed step exited nonzero against the
  freshly started store (customer-authored content; deterministic) →
  `environment` (NOT retried) (v1.10).
- `store_statement_failed` — a managed store errored or rejected a
  provisioner-issued statement (CREATE/DROP/ALTER DATABASE, sentinel or
  canary probe) after passing readiness; the statements are the harness's
  own, so a deterministic failure is a harness/engine fault →
  `infrastructure` (retryable) (v1.10).

---

## §3 Provisioner output — `EnvironmentRuntime` (per world)

```json
{
  "runtime_id": "opaque-string",
  "world_index": 0,
  "bundle_digest": "sha256:<input digest>",
  "state": "preparing | ready | unhealthy | stopped",
  "endpoints": {
    "database": { "capability": "database", "protocol": "postgres",
                  "address": "postgresql://harness:<pw>@localhost:14000/w0",
                  "configuration_name": "DATABASE_URL" },
    "tools":    { "capability": "tools", "protocol": "http",
                  "address": "http://localhost:15001",
                  "configuration_name": "TOOLS_API_URL" }
  },
  "metadata": {}
}
```

| field | req | who reads it |
|---|---|---|
| `runtime_id` | ✓ | opaque handle; nothing parses it |
| `world_index` | ✓ | scheduler (call assignment, room naming), events. New field, required (not in the current `runtime.py` model) |
| `bundle_digest` | ✓ | events/receipts provenance |
| `state` | ✓ | scheduler; legal transitions: preparing→ready, preparing→unhealthy (failed reset or health probe before first ready; v1.12), ready→unhealthy (process death/failed probe/failed sentinel), unhealthy→ready (only via re-provision reconcile), any→stopped (close) |
| `endpoints` | ✓ | scheduler + simulator; addresses always localhost. **Guest-internal:** `address` may carry credentials — any outbound projection (events, receipts, artifacts) redacts userinfo (`postgresql://harness:***@…`); `EnvironmentRuntime` is never serialized outbound whole |
| `metadata` | opt | diagnostics only; no consumer may depend on keys |

Deltas vs the current `runtime.py` model: `provider` is removed (no remote
provider exists at this seam); `RuntimeProvider.name` (the class attribute)
is retained for logging only.
Example addresses follow the §2b formulas (job-shared postgres: 14000 +
ordinal 0; per-world tools-api in world 0: 15000 + 1).

---

## §4 Provider port (implemented by the in-sandbox provisioner)

```python
class RuntimeProvider(Protocol):
    name: str

    async def provision(
        self, bundle: EnvironmentBundle, *,
        source: Path,                 # /work/source checkout root
        bundle_dir: Path,             # verified bundle root — the same path
                                      # preflight ran against; §2c seed and
                                      # migration paths resolve against THIS,
                                      # never against `source` (v1.10)
        work_directory: Path,         # /work (job.json lives here — §0.2)
        contract: AgentContract | None = None,
        instances: int = 1,
    ) -> list[EnvironmentRuntime]: ...   # ordered by world_index, len == instances

    async def reset(self, runtime: EnvironmentRuntime, *,
                    work_directory: Path) -> None: ...

    async def healthy(self, runtime: EnvironmentRuntime, *,
                      work_directory: Path) -> bool: ...

    async def close(self, *, work_directory: Path) -> None: ...
```

Semantics:

1. `provision` is idempotent for the job identity (read from
   `/work/job.json`) and **reconciles to exactly `instances` ready
   worlds**: on retry it completes or replaces partial/unhealthy worlds
   and never duplicates. A sick world mid-job is recovered by calling
   `provision` again (there is no per-world close). Secrets for respawned
   processes come from the in-memory map (§0.3).
2. `reset(world)` restores the sealed baseline for that world only.
   `template_database`: drop + recreate the world's logical DB from the
   template (shared engine keeps running), restart the world's `source`
   processes. `datadir_copy`: stop that world's engine instance, restore
   its data directory, restart engine + `source` processes. `empty`:
   flush, restart `source` processes — flush MAY be implemented as a
   data-dir wipe + engine respawn (the stronger guarantee; a bare flush
   command is engine-dependent) (v1.10). After every reset the store's
   `sentinel` must pass; a sentinel failure marks the world `unhealthy`.
3. `healthy` = declared `readiness` probes, not "process is running."
4. `close` is idempotent and hard-cleans all worlds: processes, data
   directories, build trees, `/run/futureagi/secrets.json` if still
   present.
5. Cancellation and timeout always invoke `close`.
5b. Concurrency (v1.11): the port is NOT reentrant. The scheduler
   serializes provider calls — at most one
   `provision`/`reset`/`healthy`/`close` in flight at any moment
   (`healthy` demotes state — it writes, so it is in the set; v1.12);
   the provider may assume no concurrent invocation. Known consequence
   (v1.15, accepted for V1): serialized resets cap effective throughput —
   with slow readiness probes a reset can hold the serialization for up
   to the probe timeout, globally; a per-call provider budget is a
   possible future amendment, not a current obligation. Demotion (v1.11): providers hand out live
   `EnvironmentRuntime` objects, and the scheduler MAY set a world's
   `state` to `unhealthy` on that object — that demotion is the signal
   the next `provision()` reconciles on.
6. Failure typing uses the `FailureDomain` enum in `job.py` —
   `{agent, simulator, environment, connectivity, infrastructure, grading,
   platform_sync}`: engine/process/filesystem failures during provisioning
   → `infrastructure` (retryable per job policy); egress/DNS failures →
   `connectivity` (retryable); bundle rule violations →
   `environment` in stage `validating_environment` (never retried);
   customer process exits nonzero deterministically → `agent` (never
   retried). Do not invent domain or stage names not in the enums.

**Conformance gate (governs W>1; runs once per attempt, after baseline
freeze, before provision results are used):** 2-world canary using the
first store by protocol preference postgres > redis > rabbitmq — create
the reserved `_alk_conformance` object in world 0 with a marker value →
assert it does not exist in world 1 → `reset` both → assert it is gone in
world 0 and every store's `sentinel` passes in both worlds. (§2e's
`no_sql_store` rule guarantees the gate always has a postgres store to
canary — the zero-store case cannot reach it.) Record pass/fail on the
build output. Fail → effective parallelism 1 + `parallelism_degraded`
(`reason: conformance_gate_failed`). Loud, never silent.

**Implementation delta (breaking changes to `runtime.py`):** `provision` gains
`instances` and returns a list; `close` loses its `runtime` argument and
closes everything. `LocalComposeRuntimeProvider` must be updated in the
same change and must **raise a typed error** for `instances > 1` until it
supports worlds — never silently run one. Scope warning: `cli.py`,
`build.py`, and `chat.py` today **bypass the port** via
`provision.provision_if_present(...)`; routing them through the port is
part of this change and is refactor-sized, not signature-sized.

---

## §5 Lifecycle (normative timeline)

1. Gateway: create sandbox → uploads (§0 steps 1–4) → exec entrypoint.
2. Guest: parse + validate `job.json` → understand stage (resolves
   `connector: auto`) → contract stage → bundle authored (§2) →
   **§2e preflight**.
3. Provisioner: build once (per-process `build_commands` into
   `/work/build/<name>/`) → start stores → run migrations + seeds →
   **freeze baseline** (record `inputs_digest` + achieved-baseline
   reference on the **build output**: a `build.json` artifact in
   `/work/artifacts`, also summarized in a `validating_environment` stage
   event, type `baseline_frozen`) → conformance gate →
   `provision(instances=W)`.
3.5. Scenario generation + proof (stages `generating_scenarios` /
   `validating_scenarios`): the generation stage's acceptance gates run
   against world 0, reset from the sealed baseline — the same baseline
   every run world restores, so proofs transfer by construction (the
   world-handle contract governs the handle at both moments). Then the
   guest performs scenario pre-allocation (provision + begin) against
   the platform per the outbound-channels contract; failure after
   retries fails the job here (`validating_scenarios`, domain
   `platform_sync`).
4. Scheduler (stage `running`): queue of `scenario_count` scenarios over W
   worlds; scenario `i` uses RNG seed `job.seed + i`. Per scenario, on its
   world: `reset` (a world handed out directly from a just-completed
   `provision()` MAY skip this reset — it is state-equivalent, both paths
   materialize from the sealed baseline, v1.15) → run the scenario's
   `setup(world)` delta and verify its
   `ready(world)` precondition (the scenario contract's mechanism; the
   `world` handle is wired to this world instance's endpoints) → run the
   call → grade + final-state observation → emit result receipt → next.
   A world that turns `unhealthy` mid-scenario: that scenario is retried
   **exactly once** on another world — this per-scenario retry is fixed
   and independent of `retry.max_infrastructure_attempts`, which governs
   whole-job attempts. The sick world is re-provisioned in the background;
   if ready worlds reach 0 the job FAILS in stage `running` — declared
   only after in-flight re-provisioning has completed without restoring
   any world, never on an instantaneous snapshot of world states (v1.11).
   Job-level failure codes for stage `running` (closed; they cross the
   outbound seam in the terminal event's `failure.code`):
   `world_pool_exhausted` → `infrastructure` (v1.11), EXCEPT when the
   final failed re-provision attempt of every unhealthy world carries the
   same §2f code whose §2f domain is `environment` or `agent` — then the
   job fails with that code and domain (a deterministic fault must not be
   re-reported as retryable; the scheduler preserves typed provider error
   codes across the §4 seam rather than flattening them). Mixed codes or
   any infrastructure-domain failure in that final round →
   `world_pool_exhausted`. The closed set is therefore
   `world_pool_exhausted` ∪ the §2f rows whose domain is
   `environment`/`agent` (v1.13).
5. Terminal: seal artifacts → flush outbox → exit 0. The 120s flush
   window starts when the terminal outcome is decided, on EVERY path —
   the non-cancel terminal (COMPLETED/FAILED) budgets its seal + flush +
   `close` against the same window the cancel path uses; outbound-channels
   already states this and the spine now matches (v1.15). On cancellation
   or TTL: stop launching scenarios → bounded seal/flush → `close` →
   exit 0 with terminal stage `canceled` in the event stream (TTL expiry
   is `canceled` with `reason: ttl_exceeded` — there is no separate
   timeout stage in the enum).
6. Gateway: delete sandbox → verify absent → cleanup receipt.

Degradation is announced by a `parallelism_degraded` event (stage
`validating_environment`) whose **payload** is `{ "requested": W,
"effective": 1, "reason": "conformance_gate_failed | fixed_port" }` —
the event envelope (type, ids, sequence) and all
event/result/artifact schemas are owned by the outbound-channels contract.

---

## §6 Ownership

| Piece | Owner |
|---|---|
| §0 gateway steps, snapshot layout/users/binaries, source acquisition | Azain |
| §1 authoring job payloads; admission (feasibility, caps, `image` rejection) | Azain |
| §2 schema (this file) | Khushal (backend-defined) |
| §2 population — compose→process + generated-runtime translation, seed extraction, sentinels, capabilities, `fixed_port` detection | **Rishav's stages** |
| §2e preflight, §3, §4, §5 steps 3–5 (provisioner, worlds, scheduler) | Khushal |
| §5 steps 1, 6; ingestion of events/results/artifacts | Azain |

Out of scope here: the Rishav↔Karthik `AgentContract` seam; the scenario
contract; the outbound-channels contract. See §7 for where those live.

---

## §7 Contract registry (start here)

Every interface in the hosted harness lives in exactly one document with
exactly one owner. This section is the index; each document versions
independently and consumers pin the version they built against.

| Contract | Document | Owner | Status |
|---|---|---|---|
| Gateway↔guest invocation, job schema, bundle, provider port, lifecycle | THIS file | Khushal | frozen |
| Scenario generation (scenario/persona/sub-goal models, on-disk layout, provision call) | Scenario Generation Contract | Karthik | in review |
| `world` handle interface (`setup(world)` / `ready(world)` / `check(world, calls)` surface, provided by the scheduler to scenario code) | `world-handle-interface.md` | Khushal | frozen |
| Outbound channels (event / result-receipt / artifact-manifest schemas, guest → platform) | `outbound-channels.md` | Khushal ↔ Azain | frozen |
| `AgentContract` (what generation reasons over) | Rishav ↔ Karthik's own document | Rishav | not a backend seam |

A question one of these documents cannot answer is a defect in that
document — report it to its owner; don't guess, and don't answer it in a
different document.
