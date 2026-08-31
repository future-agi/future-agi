# PROPOSAL — Image-backed processes for the hosted runtime

Status: DRAFT for the Khushal/Azain/Rishav decision call. NOT part of the
spine. If accepted, lands as hosted-execution-seams.md **v2.0 or v1.11 per
§K A2**, rebased onto **v1.10** — it supersedes the compose-runtime proposal
circulated as a v1.5-based "v1.6" while absorbing its intent (arbitrary
service stacks) and its admission ideas.

Evidence base: the podman spike of 2026-08-25
(`.claude/harness-alk/podman-spike-2026-08-25.md`). Read its scope
literally: it ran as **root**, from the stock `quay.io/podman/stable`
image, in a scratch sandbox the spike created for itself — i.e. **outside**
a custom snapshot and **outside** a hosted job's egress policy. What it
proves is nested rootless feasibility, two-world isolation, and timings.
What it does not prove is called out at each claim below and collected in
§M.

Section convention: `§A`…`§M` are THIS document; `spine §N` is
hosted-execution-seams.md **v1.10**. The two numbering spaces are never
mixed. Where v1.10 changed something this amendment touches — `provision`'s
`bundle_dir`, §4.2's wipe+respawn form of `empty`, and §2f's `seed_failed` /
`store_statement_failed` — the proposal is written against the v1.10
wording, not v1.9's.

## Known defects (round-3 survivors, shipped as-is)

Three cold reviews against spine v1.10: findings 1–54 were applied in
rounds 1–2 and round 3's three MAJORs (55–57) before circulation, leaving
the six below to ship as-is under the round-3-final rule (fix only what
changes what §K asks the leads to accept); minors 64–73 are unstamped and
live in the round-3 report
(`.claude/harness-alk/reports/amendment-review-r3.md`).

| # | Sev | Consequence in one line |
|---|---|---|
| 58 | MODERATE | Secrets passed as `-e NAME=value` land in a world-readable argv, defeating `secret_purposes` scoping the native path preserves. |
| 59 | MODERATE | Adding `provenance.resolved_images` re-keys every sealed bundle digest; sealer and verifier must ship together and the document does not say so. |
| 60 | MODERATE | The memory-admission inequality's "available" term has no value, owner or measurement item, so `image_budget_exceeded` is not implementable. |
| 61 | MODERATE | No rule tells translation when to emit `fixed_port` instead of rejecting, so one hardcoded-port service either collapses the job to W=1 or fails preflight, arbitrarily. |
| 62 | MODERATE | Tiering rule 0 has no criterion for a repo-owned service published as an image, risking loss of `svc-agent`/build/evidence or a source-run with no tree. |
| 63 | MODERATE | `credential_rotate` is specified two contradictory ways and no placeholder can carry the credential the rotation client must authenticate with. (The key is now `post_readiness_statements` and its exec mechanism reads one way — `podman exec`, §C/§D.4 — as a side effect of the finding-55 edit; the placeholder half of the defect stands.) |

## §A What this changes, in one paragraph

Today a hosted process spec runs either a catalog engine or a command from
the customer checkout. A translated compose service outside the engine
catalog fails preflight with `engine_unsupported` (spine §2b/§2e); the
separate `compose_not_hosted` code fires only when a hosted job declares
`kind: compose` (spine §2a). This amendment gives a spec a SECOND
materialization: `image` — run the declared OCI image as a rootless podman
container. Compose files remain descriptions: the environment stage still
parses them and emits specs; no compose tool (docker compose OR
podman-compose) ever executes; orchestration (worlds, ports, readiness,
baseline, reset, teardown) remains the provisioner's. There is no Docker
daemon anywhere — podman is daemonless and runs unprivileged.

**Coverage claim, stated honestly.** V1 admits **curated stores plus
arbitrary stateless images** — not "anything":

- *Stores* are admitted only from a curated store-profile table (§C), and
  only on the protocols the spine already defines end to end
  ({postgres, redis, amqp}). The win over v1.10 is the **version** axis:
  postgres 14, redis 6, rabbitmq 4 stop being `engine_unsupported`
  (subject to §K S1).
- *Stateless services* (a vendored tools API, a mock, a worker shipped as
  a published image) are admitted for **any** image **whose listen port is
  env- or argv-configurable, or which declares `fixed_port`** — their port,
  env and command pass through from what the compose file already declares,
  and translation carries them into the spec. A compose service that only
  says `ports: ["3000:3000"]` around a hardcoded listener gives translation
  nothing to rewrite and no profile to fall back on (§C), so it is either
  a `fixed_port` spec at W=1 or `image_port_unconfigurable` (§F.1).
- A store on a **new protocol** (Mongo, Kafka) is NOT covered by V1. A
  profile row is necessary but not sufficient — see §E.4 for the spine
  work it additionally requires, and §K B2.

## §B Spec shape (delta to spine §2b `processes`)

A process spec gains **one** new field, `image`, and a third `kind`.
No other field is added **to a process spec** anywhere in this proposal —
in particular no per-service memory field (§H sources those numbers from
§C instead). One field is added outside `processes`: the tag→digest map in
`provenance` (§I.3, §J.18), which is the only carrier the resolved-image
record has.

```json
{
  "name": "postgres",
  "kind": "image",
  "image": "docker.io/library/postgres@sha256:<64-hex>",
  "run_command": ["postgres", "-c", "max_connections=50"],
  "environment": { "PGTZ": "UTC" },
  "user": "svc-data",
  "depends_on": []
}
```

The example deliberately carries **no port variable**: this is a profiled
store, so its port comes from the profile (`port_mechanism`, §C) and a
port variable here would be ignored — see "Port precedence" below.

**Field rules (against real v1.10 names — there is no `command`, no `args`,
no `env`, no `ports`, and no process-level `readiness` in spine §2b):**

- `image` (string) — REQUIRED on `kind: image`, FORBIDDEN on
  `kind: source` and `kind: managed`. A digest-pinned OCI reference
  (`@sha256:`); tags are rejected (§F).
- `run_command` — REQUIRED on `kind: source` (unchanged), FORBIDDEN on
  `kind: managed` (unchanged), OPTIONAL on `kind: image`, where it is the
  **container command override**: the argv list is appended after the image
  reference in `podman run`. It is never a host command on an image spec.
- `build_commands` — FORBIDDEN on `kind: image`
  (`image_build_commands_forbidden`). V1 never builds an image; compose
  `build:` contexts are out of scope (§G rule 1, §K S2).
- `environment` — unchanged, including the closed placeholder vocabulary
  (spine §2b). Rendered values are passed as `-e` to `podman run`.
- `secret_purposes` — unchanged in meaning. Spine §2b injects every alias
  whose ref carries a listed purpose "at spawn … under the alias as the
  env-var name"; on an image-backed spec that means **additional `-e`
  flags on `podman run`**, alongside the rendered `environment`. Secrets
  are never written into the image, never into a mounted file, and never
  into the frozen baseline.
- `depends_on` — unchanged meaning.
- `fixed_port` — unchanged in meaning and **legal on `kind: image`**. It is
  the spine's existing escape hatch for a service that hardcodes its listen
  port; the provisioner honors it exactly and effective parallelism drops
  to 1 (`parallelism_degraded`, `reason: fixed_port`, spine §2b/§5), and
  spine §2e's `fixed_port_reserved` still rejects a value inside the
  formula bands [14000,14099] ∪ [15000,15799]. A spec that declares one is
  therefore **admitted at W=1, not rejected** by
  `image_port_unconfigurable` (§F.1).
- `started_check` — unchanged in meaning and **permitted on a `kind: image`
  spec with no capability** (a vendored worker, a mock consumer — squarely
  inside §A's coverage). The `port` variant dials the spec's OWN allocated
  port, which under host networking (§D.2) is the port the container binds;
  the `log_marker` variant scans the container's captured output. Without
  this, `depends_on` on a capability-less image would resolve "immediately
  after spawn" (spine §2b) and gate nothing. Spine §2b's scoping sentence
  is amended in §J.17.
- `working_directory`, `build_environment` — FORBIDDEN on `kind: image`
  (`unknown_field` is not the right code; they are known fields in the
  wrong shape → `spec_backend_ambiguous`).
- Readiness is NOT a process field and stays where spine §2b puts it: the
  capability-level `readiness` section (spine §2d), with one normative
  change for image-backed capabilities in §D.7 and the authorship split
  stated there.

**`spec_backend_ambiguous`** fires when `kind`, `image`, `engine` and
`run_command` do not form exactly one of the three legal shapes —
`source` (`run_command`, no `image`, no `engine`), `managed`
(`engine`+`version`, no `image`, no `run_command`), `image` (`image`, no
`engine`, optional `run_command`) — **or when any field this section
forbids for the declared `kind` is present** (`build_environment` or
`working_directory` on an image spec; `image` on a source or managed
spec). `build_commands` on an image spec keeps its own code.

**Catalog check.** `kind: image` carries no `engine`/`version`, so the
spine §2b managed-engine catalog check does not apply to it and
`engine_unsupported` cannot fire on an image-backed spec. Which specs the
translation stage is allowed to route to `kind: image` is §G; what remains
of `engine_unsupported` is §G.2.

**`user` mapping (extends the spine §2b rule, still enforced by
`user_assignment_invalid`).** One new row, closed: **every `kind: image`
spec declares `user: svc-data`** — stores and stateless images alike.
Rationale, and the constraint it resolves: rootless podman keeps its image
store per unix user, so two container-running users would mean two image
stores and no layer sharing (§I.1). V1 therefore has exactly ONE podman
user. `svc-data` already owns every managed engine and every per-world
data directory, so it is the one that generalizes. An image's own internal
`USER` still applies inside the container's userns. The control service is
never image-backed in V1 (§G rule 0), so `svc-agent` is unaffected.

**Instancing and ports.** Image-backed specs run **once per world,
always** — there is no job-shared image container (the transient baseline
container of §E.2 exists only during the build stage and is removed before
`provision`). They therefore take the per-world port formula
`15000 + 100*world_index + ordinal` and **never** the 14xxx job-shared
band, with the spine's one existing exception: a spec that declares
`fixed_port` binds that port in the single world W=1 leaves. `ordinal` is
the spec's 0-based index in `processes`, unchanged. The port the formula
yields is the port the SERVICE ITSELF binds, because image-backed
containers run with host networking (§D.2).

**Port precedence, so translation has exactly one rule.** For a **profiled
store** the provisioner renders the profile's `port_mechanism` (§C) and
that is the only source: a port variable in the spec's `environment` is
**ignored, not an error**, so a translated compose file that happened to
set `PGPORT` cannot fight the provisioner and translation need not strip
it. For a **stateless** spec, which has no profile, the spec's own
`environment` / `run_command` is the only source and must carry
`{{PORT_<name>}}` unless the spec declares `fixed_port` (§F.1).

## §C Image profile table (new spine §2b.3)

The per-image knowledge that §B, §D, §E and §F all depend on is one
artifact, not five scattered assumptions.

**Shape.** A curated, harness-owned table keyed by well-known store image
(repository, not digest), each row:

| key | meaning |
|---|---|
| `port_mechanism` | how the service is told its listen port — env var or argv flag (e.g. `PGPORT`, `--port`) |
| `data_dir` | the in-container path that holds durable state (e.g. `/var/lib/postgresql/data`) |
| `credential_env` | the init env that sets the store's **baseline** password (e.g. `POSTGRES_PASSWORD`); consumed by the image only while it initializes an empty data dir, so it never sets a per-world credential (§D.4); `null` for stores with no auth |
| `post_readiness_statements` | ordered argv list, exec'd inside the container with `podman exec` after the §D.7 readiness gate, that makes the copied baseline THIS world's: it sets this world's credential (e.g. `ALTER ROLE harness PASSWORD '{{CREDENTIAL}}'`) **and** renames the baseline database to this world's `{{DB_NAME}}`, since an init-time `POSTGRES_DB` is as inert against a copied data directory as `POSTGRES_PASSWORD` is (§D.4). Statements run in listed order; `null` for a store with no auth and no per-world database, which then has no cross-world credential boundary (§D.4, §K B1) |
| `seed_client` | the client invocation **inside the image** that applies one migration/seed file (e.g. `psql`, `redis-cli`), rendered with `{{SEED_FILE}}` |
| `readiness_probe` | the probe **mechanics** the provisioner uses for this image; the bundle still authors the capability's `readiness` entry and cannot override the mechanics (§D.7) |
| `default_memory_mb` | admission input for §H — an estimate, currently UNMEASURED (§I.4) |

Profile templates (`post_readiness_statements`, `seed_client`) use a
harness-internal placeholder set — `{{CREDENTIAL}}`, `{{DB_NAME}}`,
`{{SEED_FILE}}`, `{{PORT}}` — which is NOT the spine §2b bundle
placeholder vocabulary and is never rendered against bundle input. No
bundle can reach it. `{{DB_NAME}}` renders the same per-world value
spine §2b's bundle placeholder does (`w<N>`), computed by the provisioner
from the world index; it is spelled the same because it must be the same
database `{{DATABASE_URL}}` points at.

**Authorship, location, format and version.** The table is authored and
versioned with the provisioner (Khushal's lane), shipped in the snapshot at
`/opt/alk/image-profiles.json` (spine §0: `/opt/alk` is the immutable ALK
runtime), and read from that path by both the provisioner and the
translation stage, so authoring-time rejects are possible before preflight.
It is NOT bundle-declarable: a bundle cannot introduce or override a
profile. Minimal schema:

```json
{
  "version": 1,
  "stateless_default_memory_mb": null,
  "profiles": {
    "docker.io/library/postgres": {
      "protocol": "postgres",
      "port_mechanism": { "kind": "env", "name": "PGPORT" },
      "data_dir": "/var/lib/postgresql/data",
      "credential_env": "POSTGRES_PASSWORD",
      "post_readiness_statements": [
        ["psql", "-U", "harness", "-d", "postgres", "-c",
         "ALTER ROLE harness PASSWORD '{{CREDENTIAL}}'"],
        ["psql", "-U", "harness", "-d", "postgres", "-c",
         "ALTER DATABASE harness RENAME TO {{DB_NAME}}"]
      ],
      "seed_client": ["psql", "-U", "harness", "-f", "{{SEED_FILE}}"],
      "readiness_probe": { "kind": "postgres_query" },
      "default_memory_mb": null
    }
  }
}
```

`stateless_default_memory_mb` is `STATELESS_DEFAULT_MEMORY_MB` (§H) — it
lives here so admission reads one file and one version. Both it and every
`default_memory_mb` are `null` until §I.4.1 measures them; a `null` is an
implementation blocker, not a default of zero.

**Version skew.** Both this table and the pre-baked digest manifest
(`/opt/alk/prebaked-digests.json`, §I.3) carry a top-level `version`. A
reader that does not support the version it finds **refuses** rather than
guessing: the translation stage raises
`image_metadata_version_unsupported` (§F.1) at authoring time. The
snapshot and the stage that reads it ship together; a skew is a release
error, and failing it loudly at the earliest bundle-side stage costs one
job instead of producing a bundle whose profile assumptions are wrong.

**Who needs a profile.**

- A **stateful** spec — one that backs a capability whose `protocol` is a
  store protocol, or that any `seed.stores[].capability` resolves to —
  MUST match a profile row. No row → `image_profile_missing` (§F).
- A **stateless** spec needs no profile. Its port, environment and command
  pass through from the compose file the translation stage read; the
  provisioner treats it as an opaque long-running process.

This is the honest V1 answer, and it is what makes the §A coverage claim
"curated stores + arbitrary stateless images" rather than "anything".
Adding a store row is a harness change with a review, not a customer
action; adding a row for a **new protocol** additionally requires the
spine work in §E.4.

## §D Runtime semantics (new spine §2b.2)

1. **Engine.** Rootless podman (daemonless), run by `svc-data` (§B), one
   image store (§I.1). Container name `w{world_index}-{spec.name}`,
   subject to the spine §2b name guard (`process_name_invalid`).
2. **Networking.** `--network=host` ONLY. Rootless port-forwarding (`-p`)
   and bridged networking are PROHIBITED — the spike found both broken or
   flaky nested, and host networking matches the provisioner's native-port
   allocation model. A spec whose service cannot be told its listen port
   and which declares no `fixed_port` is not hostable
   (`image_port_unconfigurable`, §F).
3. **Mounts — exactly two, both provisioner-chosen, neither
   bundle-declarable.**
   - The world scratch `/work/worlds/w<N>/<name>/` is mounted rw at the
     **identical path inside the container**, so `{{WORLD_DIR}}` (spine
     §2b) renders to a path that is valid on both sides.
   - For a store only: the world's data directory
     `/work/worlds/w<N>/<name>/data` is mounted rw at the profile's
     `data_dir` (§C).

   Nothing else is mounted — not `/work/source`, not `/work/build`, not
   `/run/futureagi`. **The one exception to "nothing else", stated
   explicitly:** seed inputs are delivered into the baseline container with
   `podman cp` during the build stage (§E.2), read from `provision`'s
   `bundle_dir` (spine §4, v1.10) and never from the checkout. They are
   copied, never mounted, and only into the transient baseline container —
   never into a world container. Secrets reach a container only as `-e`
   flags (§B), never as a mount.

   **Id-mapping and ownership, stated because rootless makes it
   non-obvious.** Under rootless podman the container's uid 0 maps to
   `svc-data` and every other in-container uid maps into `svc-data`'s
   subuid range, so a store image running as its own internal `USER`
   writes both mounts as a **mapped** uid that is not a host user. V1's
   rule: **every ownership operation on these paths — create, chown, copy,
   freeze, delete — is performed by `svc-data` itself through
   `podman unshare`**, i.e. inside the same user namespace, so mapped
   ownership is written and preserved rather than translated. `svc-control`
   never touches these trees: it cannot chown into `svc-data`'s subuid
   range without privilege, and a copy that flattens the mapping yields a
   container that cannot start. This is **unproven** — the spike never
   bind-mounted a host data directory; its reset result is explicitly
   structural ("fresh container = fresh data dir"), i.e. container-local
   storage — and is §M item 3.
4. **Credentials — normative, per world.** An image-backed store's
   capability address for world N carries a credential that is world N's
   alone. This is load-bearing, not hygiene: host networking means every
   world's store port is reachable from every other world (spike finding 5,
   and the same property the native process model already has), so the
   credential is the only thing that separates them, and the conformance
   canary would not catch a cross-world connection made with a
   legitimately-issued job-wide credential.

   **The mechanism, spelled out because the obvious one does not work.**
   The profile's `credential_env` (§C) sets the **baseline** credential
   ONLY. Every official store image consumes it during first-boot
   initialization — `POSTGRES_PASSWORD` at `initdb` against an empty
   `PGDATA`, `RABBITMQ_DEFAULT_PASS` when the mnesia dir is absent — so
   against the already-initialized data directory §E.3 copies into each
   world it is **inert**. The per-world credential is therefore applied
   **after start**, by the profile's `post_readiness_statements` (§C),
   exec'd inside the container with `podman exec` once the §D.7 readiness
   gate has passed: once per world at `provision`, and again after **every**
   `reset` (a reset re-copies the baseline datadir, which restores the
   baseline credential with it). **The same list carries the per-world
   database name**, because the identical inertness argument applies to
   `POSTGRES_DB`: the copied directory holds the database the baseline was
   initialized with (`harness`, §E.2 step 2), and the rename statement is
   what makes it `w<N>` — so `{{DB_NAME}}`, and therefore
   `{{DATABASE_URL}}` (spine §2b, §J.4), is only real because the list
   runs. The world is reported `ready`, and its address rendered, only
   after every statement in the list succeeds. The one case where
   `credential_env` does carry the world's own credential is the unseeded
   path (§E.1), whose data directory is created fresh at every start so
   the image's init consumes it each time; there the list is a no-op the
   provisioner may skip, and no per-world database name is at stake either
   (a postgres-protocol store is always seeded, §E.1). It is the
   **copied** directory — the only directory a seeded store ever runs on —
   that makes the post-readiness list unavoidable. A statement the
   store rejects is `store_statement_failed` (spine §2f, v1.10 — already
   scoped to provisioner-issued statements after readiness; §J.20 widens
   its wording to image-backed stores).

   **Where there is no boundary at all.** A profile with
   `post_readiness_statements: null` has no credential separation between
   worlds — concretely redis, which the spine §2b catalog runs with
   "no auth (localhost only)". Under host networking "localhost only" no longer
   implies "one world only". This residual is put to the leads in §K B1
   rather than papered over.

   **The native path has the same defect and this amendment carries the
   fix (§J.4, §K B1).** Spine §2b's catalog says "password generated per
   **job**"; making it per **world** is not a one-word edit:
   - `template_database` (postgres, engine job-shared): one server cannot
     hold W passwords for one role, so it needs per-world **roles**
     (`harness_w<N>`) plus per-world GRANTs so world N's credential cannot
     open `w<M>`, and `{{DATABASE_URL}}` must render the world's role
     rather than the catalog's single `harness`. Database-level GRANTs
     convey CONNECT/CREATE/TEMP only, so each per-world role additionally
     needs **membership in the object-owning role**
     (`GRANT harness TO harness_w<N>`) or ownership transfer of the seeded
     objects — otherwise `harness_w<N>` connects to `w<N>` and can read
     nothing the template seeded.
   - `datadir_copy` (postgres per-world, rabbitmq always): the engine is
     restored from a seeded data directory, so it comes up with the
     baseline password — the same inertness as above, fixed the same way,
     by a post-start rotation, **at `provision` and again after every
     `reset`**, before the world is reported (or re-reported) ready, since
     every reset restores that same directory (§J.21).
   - redis: no auth, so nothing to rotate (see above).
5. **Reset — two paths, one of them measured.**
   - A store with **no seed inputs** (§E.1) resets as `podman rm -f` by
     name → discard that world's data directory → `podman run` against a
     fresh one → readiness. This is the spine §4.2 `empty` recipe in the
     **wipe + respawn** form v1.10 explicitly permits as the stronger
     guarantee, and it is exactly the operation the spike measured:
     **~1s** (RESET_MS=970 in Daytona; 1002–1091 ms in the local rig).
     Evidenced, not extrapolated.
   - A **seeded** store resets as `podman rm -f` → delete and re-copy that
     world's data directory from the frozen baseline → `podman run` (§E.3).
     The datadir-copy cost has **not been measured**, and it is paid on
     every reset, for every world, for every scenario. For this path treat
     ~1s as a floor, not the number (§K B4).

   Attribution, exactly: `reset_wiped=0` is the **local rig's** result
   ("Wipe was proven locally"); the Daytona prong contributed the timing,
   and its own post-reset read tripped on the postgres init race — the same
   evidence §D.7 rests on. The wipe is proven locally and structurally, not
   in Daytona.
6. **Teardown / close.** `podman rm -f` every container of the job;
   idempotent. Failure raises `container_remove_failed`, whose handling
   depends on when it happens (§F, §D.9, §E.3).
7. **Readiness — normative change for image-backed capabilities.** The
   capability's `readiness` probe must observe **N ≥ 2 consecutive
   successes separated by `interval_seconds`** before the world is
   `ready`, and the same rule applies to post-reset sentinel verification.
   This is not caution: official store images run a temporary init server
   during first boot, and the spike's one trailing failure was exactly
   that race — a single `select 1` passed against the entrypoint's temp
   server and the next client landed in the restart window. A single-probe
   gate is unsound against these images.

   **Who authors what.** The `readiness` entry itself stays where spine §2d
   puts it: bundle-authored, per capability, carrying `timeout_seconds` and
   `interval_seconds` — Rishav authors it for an image-backed capability
   exactly as for any other, and spine §2e still resolves
   `readiness[].capability`. What the bundle does NOT author is the probe's
   **mechanics**: for a capability backed by a profiled store the
   provisioner uses the profile's `readiness_probe` (§C), and a bundle
   cannot override it. The N≥2 rule is the provisioner's and applies
   regardless of the authored interval.
8. **Liveness — UNPROVEN.** Proposed mechanism: one `podman wait
   <container>` waiter per container; its completion is the container's
   exit and is treated as process exit. The spike tested no supervision
   behaviour at all, and `podman stats` returned nothing (cgroup
   delegation absent nested), which removes the systemd-cgroup route. With
   detached `podman run` the provisioner is not conmon's parent, so there
   is no child-exit signal — hence a waiter rather than a wait(2). This
   item is a named follow-up spike (§M), not a settled claim.
9. **Recreate is always remove-first.** `provision` is idempotent and
   reconciling (spine §4.1), so a partial provision can leave a container
   holding the fixed name. Recreate is defined as `podman rm -f <name>`
   then `podman run`; a name conflict is never surfaced as
   `container_start_failed`.
10. **`container_port` (spine §2d) stays informational.** With real
    containers under host networking it now looks authoritative and still
    is not: the port the service binds is the allocated port rendered
    through `{{PORT_<name>}}` / the profile's `port_mechanism`. Unchanged
    from v1.10, restated because the temptation is new.

## §E Baseline, seed and reset for image-backed stores

### E.1 Strategy — split by whether the store ships seed inputs

- A store **with** `migrations` / `seed_files` in `seed.stores` is
  **`datadir_copy` only**. `template_database` requires a live shared
  engine (excluded by the per-world rule, §B) and `empty` would discard the
  seed. Any other pairing is `seed_strategy_unsupported` (spine §2c's rule,
  §2e's code).
- A store with **no `seed.stores` entry** keeps the spine's own rule,
  unchanged: spine §2c already says a redis/amqp capability needs an entry
  "only if the repo ships seed state for them; otherwise their baseline is
  implicitly `empty` (reset = flush) and they run per-world". For an
  image-backed store that implicit `empty` baseline is realized as the wipe
  + respawn form spine §4.2 permits (v1.10): **no baseline is built** (§E.2
  does not run for it), each world starts against a fresh data directory,
  and reset is `podman rm -f` + `podman run` — the ~1s path the spike
  actually measured (§D.5). This is both the most common image-backed case
  and the one §A's version-axis win rests on (an unseeded redis 6), so
  routing it through `seed_strategy_unsupported` would have rejected the
  headline case.
- A postgres-protocol store always has a `seed.stores` entry (spine §2c,
  `seed_missing`), so it is always the first bullet.

Mirrored in §J.13.

### E.2 Baseline creation (spine §5 step 3, once per job)

Runs **only for a store with seed inputs** (§E.1); an unseeded store has no
baseline step at all.

1. Pull the digest-pinned image (§F codes on failure).
2. Start **one** transient baseline container per such store spec, named
   `base-{spec.name}`, with a fresh data dir under the build tree created
   per §D.3's ownership rule, and the profile's `credential_env` set to the
   job's baseline credential. **The baseline is initialized as the
   harness's own user and database** — for the postgres profile,
   `POSTGRES_USER=harness` and `POSTGRES_DB=harness`, which is the user
   and the database step 4's `seed_client` targets (`psql -U harness`,
   whose default database is the like-named one) and the database §E.3's
   post-readiness statements later rename to that world's `w<N>` (§C,
   §D.4); a profile's `seed_client` and `post_readiness_statements` are
   authored against exactly these. This is a build-stage container, not a
   job-shared world container — §B's per-world rule is unaffected.
3. Wait for readiness under the 2-consecutive-successes rule (§D.7).
4. Deliver `migrations` then `seed_files` (spine §2c, listed order) into
   the container with `podman cp`, resolving their paths against
   `provision`'s `bundle_dir` — the verified bundle root, never the
   checkout (spine §4, v1.10) — and apply them with the profile's
   `seed_client`, exec'd **inside** the container (`podman exec`). No
   snapshot-side client is required and no bundle path is mounted. A step
   that exits nonzero is `seed_failed` (spine §2f, v1.10).
5. Verify the store's `sentinel` (spine §2c).
6. Clean stop: `podman stop` (graceful, so the store flushes), then remove
   the container.
7. **Freeze**: the data dir is sealed at a named host path,
   `/work/build/<name>/baseline/`, owned by the mapped uids the container
   wrote it with, and is immutable for the rest of the job. The seal — like
   every later copy of it — is performed by `svc-data` through
   `podman unshare` (§D.3).

`inputs_digest` reuse (spine §2c) applies to the frozen directory: it is
recorded on the build output as that baseline's identity, and a matching
digest on an attempt retry reuses the frozen directory instead of
re-running steps 1–7; a mismatch forces a rebuild and emits
`baseline_inputs_changed`. **Construction delta:** spine §2c's byte-exact
`inputs_digest` ends with `<engine>:<version>\n`, which an image-backed
store does not have. For `kind: image` stores the trailing component is
`image:<full digest-pinned reference>\n` (§J.13) — which is only stable if
the digest is resolved before the digest is computed (§I.3).

### E.3 Provision and reset

- `provision(instances=W)`, per world, in this order:
  1. **Data directory.** Seeded store: copy
     `/work/build/<name>/baseline/` → `/work/worlds/w<N>/<name>/data`.
     Unseeded store: create that path empty. Either way the operation is
     performed by `svc-data` through `podman unshare`, preserving mapped
     ownership (§D.3).
  2. `podman run` — mounts per §D.3, allocated port per §B, and
     `credential_env` per §C: the **baseline** credential for a seeded
     store, that **world's** credential for an unseeded one, whose data
     directory is fresh (§D.4).
  3. Readiness under the §D.7 rule.
  4. The profile's `post_readiness_statements` for this world, in listed
     order (§C, §D.4) — this world's credential **and** the rename that
     makes the copied baseline database this world's `w<N>`, which is what
     `{{DATABASE_URL}}` renders. Required for a seeded store, skippable
     for an unseeded one. The world's capability address is rendered, and
     the world reported `ready`, only after every statement succeeds.
- `reset(world)`: `podman rm -f w{N}-{name}` (always remove-first, §D.9)
  → delete that world's data dir and, for a seeded store, re-copy it from
  the frozen baseline (unseeded: recreate it empty) → `podman run` →
  readiness (§D.7) → `post_readiness_statements` (§D.4) → the store's
  `sentinel`
  must pass (spine §4.2); a sentinel failure marks the world `unhealthy`.
- **Reset-time failures are world outcomes, never job outcomes.** `reset`
  runs inside spine §5 step 4, stage **`running`**, where the spine's
  contract is not a job failure: a `container_start_failed`,
  `container_remove_failed` or `store_statement_failed` raised during reset
  marks that world `unhealthy` (spine §3 transitions), the scenario is
  retried exactly once on another world, the sick world is re-provisioned
  in the background, and the job FAILS only if ready worlds reach 0 (spine
  §5.4). Only baseline-stage and `provision`-stage failures fail the job
  directly. §F.2's stage column carries this split.

### E.4 Conformance gate — the honest scope

The spine §4 2-world canary and post-reset verification are identical for
image-backed stores **of an already-supported protocol** ({postgres,
redis, amqp}). They are NOT available for any other protocol, because
everything they stand on is defined per-protocol for exactly three engines:

- `sentinel` shapes (spine §2c): postgres `{query, expected}`, redis
  `{key, expected}`, rabbitmq `{queue, expected_depth}` — a Mongo or Kafka
  store has no shape and fails `sentinel_shape_mismatch` /
  `store_protocol_unsupported`.
- Reserved canary names (spine §2c) are enumerated for those three only;
  the gate has nothing defined to create in Mongo/Kafka.
- Address shapes: spine §2f's `unsupported_capability_protocol` exists
  precisely because a protocol with no defined address shape cannot render
  a `{{…}}` capability address.
- Spine §2e rule 6 (`no_sql_store`) still requires at least one
  postgres-protocol capability, so a **pure-Mongo stack is rejected
  regardless of this amendment**.

Supporting a store on a new protocol is therefore a named work item —
extend the sentinel vocabulary, the reserved-name list, the address-shape
table, and decide `no_sql_store`'s fate — not a profile row. §K B2.

## §F Admission

### F.1 Preflight additions (spine §2e)

All are stage `validating_environment`, domain `environment`, never
retried (spine §2e). "Inserts at" names the position in spine §2e's
ordered checklist.

| check | code | inserts at |
|---|---|---|
| `kind`/`image`/`engine`/`run_command` are not exactly one legal shape, or a `kind`-forbidden field is present (`working_directory`/`build_environment` on an image spec; `image` on a source/managed spec) | `spec_backend_ambiguous` | rule 5 |
| `image` reference is not digest-pinned (`@sha256:` required; tags rejected) | `image_not_pinned` | rule 5 |
| `build_commands` present on a `kind: image` spec | `image_build_commands_forbidden` | rule 5 |
| a stateful image-backed spec whose image matches no §C profile row | `image_profile_missing` | rule 5 |
| the spec's service cannot be told its listen port — no profile `port_mechanism` (store), or no `{{PORT_<name>}}` in `environment`/`run_command` (stateless spec backing a ported capability) — **and** the spec declares no `fixed_port` | `image_port_unconfigurable` | rule 5 |
| translation carried a privileged request through: `privileged`, `devices`, `cap_add`, host mounts, host PID/IPC, unconfined seccomp/AppArmor, or an external network | `image_privilege_requested` | rule 4 |
| the image's registry is outside the allowlist | `image_registry_not_allowed` | rule 5 |
| image-backed memory demand exceeds the job budget even at W=1 (§H) | `image_budget_exceeded` | rule 7 |
| the `/opt/alk` profile table or pre-baked digest manifest carries a `version` the reading stage does not support (§C, §I.3) | `image_metadata_version_unsupported` | — (raised by translation at authoring time, before preflight; same stage and domain) |

`image_registry_not_allowed` is **vocabulary reserved; policy pending
§K B3** — it is added now so that, if an allowlist is adopted, the
violation fails deterministically and cheaply at
`validating_environment` rather than as a pull failure at
`building_environment`.

Rule 4's wording is amended, not merely extended: it must now distinguish
bundle-**requested** privilege (rejected) from provisioner-**chosen**
mechanism (host networking and the two bind mounts of §D.3, which no
bundle can ask for or opt out of). Exact wording in §J.7.

### F.2 Build/run additions (spine §2f)

| code | condition | domain | stage |
|---|---|---|---|
| `image_pull_failed` | registry unreachable, DNS failure, or timeout | `connectivity` (retryable) | `building_environment` (provisioner pull); `validating_environment` when raised by authoring-time tag→digest resolution (§I.3) |
| `image_unavailable` | deterministic: manifest/digest not found, digest mismatch, or auth denied | `environment` (NEVER retried) | `building_environment` (provisioner pull); `validating_environment` when raised by authoring-time tag→digest resolution (§I.3) |
| `container_start_failed` | `podman run` failed or the container exited before readiness | `infrastructure` if the spec is a store; `agent` if it is a stateless customer image | `building_environment` for baseline (§E.2) and `provision` (§E.3); **`running`** for `reset`, where it is a world outcome, not a job failure (§E.3) |
| `container_remove_failed` | `podman rm -f` failed | `infrastructure` | `building_environment` for baseline and `provision`; **`running`** for `reset` (world outcome); at `close` see below |

The pull code is split deliberately: spine §4.6 routes connectivity and
infrastructure into `retry.retryable_domains` by default, and a registry
**auth** failure is deterministic — retrying it burns attempts and hides
the cause. The same two codes cover an authoring-time tag→digest
resolution failure (§I.3); because that failure is a bundle-side fault
raised before any build runs, it reports from `validating_environment` —
the earliest stage the platform's closed stage vocabulary has for
bundle-side faults, and the stage §2e failures already use.

`spawn_failed` (spine §2f) no longer covers image-backed specs;
`container_start_failed` replaces it for them, with the same
store/customer domain split `spawn_failed` uses for managed/source (§J.8).
Two v1.10 codes already fit image-backed work and are NOT re-added:
`seed_failed` covers a `seed_client` step that exits nonzero (§E.2 step 4),
and `store_statement_failed` covers a provisioner-issued statement the
store rejects after readiness — including any of the
`post_readiness_statements` (§D.4), for which §J.20 widens its wording.

`container_remove_failed` **at `close`**: an event plus a gateway-visible
note only. It never flips an already-terminal job to FAILED — spine §5.5
seals and exits 0, spine §4.4 makes `close` idempotent, and sandbox
deletion (spine §0 step 8) is the outer cleanup guarantee.

### F.3 Out of V1

- **Private-registry credentials.** Only publicly pullable images admit.
- **Compose `build:` contexts.** The customer's own service is source-run
  (§G rule 0); any other `build:` service keeps its existing reject.

Both are milestone questions, not design questions (§K S2).

## §G Tiering rule (normative)

The backend choice is the TRANSLATION STAGE's, per service, in this order.
Rule 0 is first precisely so that rules 1–3 cannot capture the customer's
own service:

0. **Is this the repo's own source tree?** (the agent, its tools, anything
   the compose model builds from a local context, anything the generated
   runtime plan derives from the checkout) → **source-run**, as today.
   This holds even when the service is published as an image
   (`image: myco/agent@sha256:…` with no `build:`): the control service
   must keep `svc-agent`, `build_commands`, `working_directory`, and the
   evidence seam. Only after rule 0 says no does catalog membership get
   asked.
1. Service matches the engine catalog at a supported major → **native
   process** (keeps `template_database` reset at ~ms and job-shared engine
   memory).
2. Service matches the catalog at an **unsupported** major →
   **image-backed** (subject to §K S1).
3. Service outside the catalog → **image-backed**, subject to §C (a store
   needs a profile row; a stateless image does not) and §E.4 (a store on a
   new protocol is not V1).

Rules 0–3 are disjoint by construction and evaluated in order.

**What `engine_unsupported` means after this amendment**, stated under
both answers to §K S1:

- If image fallback is **ON by default**: rule 2 makes
  `engine_unsupported` unreachable on the hosted path for images that
  admit; it survives only as the reject for a catalog engine at an
  unsupported major whose image also fails §C/§E.4 — i.e. it becomes a
  fallback-of-the-fallback, and its message must name both the catalog and
  the profile table.
- If image fallback is **opt-in**: `engine_unsupported` keeps its v1.10
  meaning for jobs that did not opt in, and its message gains a pointer to
  the opt-in.

The provisioner treats both backends behind one spec model; schedulers and
graders cannot tell them apart.

## §H Capacity admission

Image-backed specs are per-world (§B), so their memory scales with W.

**No per-service memory field is added.** The numbers come from §C:
`default_memory_mb` for a profiled store, and for a stateless image the
single harness constant **`STATELESS_DEFAULT_MEMORY_MB`**, published as
`stateless_default_memory_mb` at the top level of the same
`/opt/alk/image-profiles.json` artifact (§C) so admission reads one file
under one `version`. Its value is set by §I.4.1 and is unmeasured today,
exactly like `default_memory_mb`. A bundle cannot declare memory — spine
§2b's `unknown_field` rule stands.

**The budget reserves headroom for everything else.** Available =
`runtime.memory_mb` (spine §1) − reserved headroom for the ALK runtime,
the simulator, the agent process, and every native/source process
(themselves per-world). Admission requires:

> Σ over image-backed specs of (`default_memory_mb` × W) ≤ available

If exceeded, W is lowered — **never silently** — and below W=1 it is
`image_budget_exceeded` (§F.1).

**Degradation announcement (delta to spine §5).** Lowering W uses the
spine's existing channel, with two contract changes the outbound-channels
consumers must be told about:

- new reason token `image_budget` in the closed `reason` vocabulary
  (currently `conformance_gate_failed | fixed_port`);
- `effective` may now be **any value in `1..W`**, where the v1.10 payload
  documents a literal `1`.

The `parallelism_degraded` event remains the announcement; `build.json`
records the effective W in addition, never instead.

**Known limitations, both real.** Cgroup delegation is absent in nested
rootless (spike: `podman stats` returned nothing), so per-container hard
caps are unreliable — capacity is enforced by admission math and sandbox
sizing, not by limits. And the `default_memory_mb` /
`STATELESS_DEFAULT_MEMORY_MB` inputs are currently **unmeasured** in both
spike prongs ("Memory-per-world unmeasured in the rig"; "per-world RSS
again unmeasured"). Measuring them is a prerequisite (§I.4), not a detail.

## §I Prerequisites

### I.1 Snapshot (Azain's lane) — checklist

- **podman preinstalled**, version floor **≥ 5.8.4** (the spike's
  version); subuid/subgid ranges for `svc-data` baked in.
- **Exactly one container user: `svc-data`** (§B). Rootless podman keeps
  its image store per user; a second container-running user means a second
  store and no layer sharing.
- **A slot for the container store in the spine §0 layout**:
  `/work/containers/` as podman's graphroot, owned by `svc-data`; plus a
  writable runroot — proposed `/run/alk-containers/` (owner `svc-data`,
  mode 0700), exported as that user's `XDG_RUNTIME_DIR`, because nested
  rootless has no systemd-managed `/run/user/<uid>` to fall back on
  (§J.11).
- **Storage driver pinned** to `fuse-overlayfs` in `storage.conf`, with
  confirmation that the graphroot's filesystem supports overlay upper dirs
  (no silent vfs fallback).
- **`/dev/fuse` present** (verified default in Daytona today; the snapshot
  build must not remove it) and `newuidmap`/`newgidmap` file capabilities
  intact — an xattr-stripping build step breaks rootless silently.
- **Disk budget reconciled with pre-pull.** Org cap is 10 GiB max/sandbox
  (spike). Image layers, the frozen baseline, W per-world data dirs, build
  trees and the artifact spool all share it. Proposed split: the image
  store is capped at **3 GiB**, leaving ≥7 GiB for everything else; the
  pre-pull set is whatever fits under 3 GiB, in that order of preference.
  The unpacked sizes of the candidate images are unmeasured — measuring
  them is part of §I.4, and the pre-pull list cannot be fixed before then.
- **The two `/opt/alk` metadata artifacts ship with the snapshot**:
  `image-profiles.json` (§C) and `prebaked-digests.json` (§I.3), each
  carrying the `version` the skew rule keys on.
- **Pre-pull is a cut to cold start, not a correctness requirement**;
  pulls at env-creation remain the fallback. It only pays off if the
  digests match (§I.3).

### I.2 Platform egress allowlist (Azain's lane) — a spine §1 amendment

The spike's registry-egress result was obtained in a scratch sandbox that
was **not** running under a hosted job's egress policy. Hosted jobs run
under `runtime.network_policy: live`, whose egress is "platform base
allowlist (model providers, STT/TTS providers, LiveKit, object storage,
ingestion endpoints) ∪ `security.allowed_egress_domains`" (spine §1).
**Registry hosts are not in that list.** As the policy stands today, every
pull in production fails.

Prerequisite: extend the platform base allowlist with the registry
domains — `registry-1.docker.io`, `auth.docker.io`,
`production.cloudflare.docker.com` or their equivalents for whichever
registries are admitted. This is the same decision as the registry-policy
question, so they are one question, not two (§K B3).

### I.3 Tag→digest resolution (Rishav's lane)

- The **translation stage** resolves tag→digest at **authoring time** and
  records the resolved digest-pinned reference in the bundle's
  `processes[].image`. That field is the record.
- **The tag it came from is recorded in `provenance`.** Spine §2d's
  `provenance` object gains `resolved_images`: a map from each image
  reference as authored (usually a tag) to the digest-pinned reference now
  in `processes[].image` (§J.18). This is the only carrier — the
  provisioner that writes `build.json` sees nothing but digests otherwise —
  and `build.json` echoes the map from there.
- Resolution **prefers digests the snapshot publishes as pre-baked**. The
  snapshot ships that manifest at `/opt/alk/prebaked-digests.json`, and
  translation picks one of its digests when the tag resolves to it. Without
  this, a freshly resolved digest misses the pre-pull entirely and pays a
  cold pull anyway — the pre-pull benefit evaporates in the common case.
  Minimal schema:

```json
{
  "version": 1,
  "baked": [
    { "repository": "docker.io/library/postgres",
      "digest": "sha256:<64-hex>",
      "tags_at_bake": ["16"],
      "unpacked_bytes": 0 }
  ]
}
```

  `unpacked_bytes` is what §I.1's 3 GiB split is reconciled against; a
  `version` this stage does not support is
  `image_metadata_version_unsupported` (§C, §F.1).
- **Ordering.** Resolution happens **before** `inputs_digest` is computed,
  because §E.2's trailing component embeds the digest-pinned reference: a
  digest resolved afterwards would produce an `inputs_digest` the
  provisioner recomputes differently and rejects
  (`inputs_digest_mismatch`).
- **Attempt retries reuse the recorded digest.** The bundle is the record;
  a retry re-reads it and re-resolves neither the digest nor the
  `inputs_digest`, so a moving tag cannot change what a retry runs.
- Resolution is a guest-side registry round trip, so it is subject to §I.2
  as well. Failures raise the §F.2 codes from stage
  `validating_environment` (§F.2), which is where every other bundle-side
  fault is reported.

### I.4 Measurements that must exist before implementation

1. Per-world RSS for each profiled store image **and for a representative
   stateless service** (feeds §C `default_memory_mb`,
   `STATELESS_DEFAULT_MEMORY_MB`, and §H; unmeasured in both spike prongs).
2. Reset cost with a **seeded** baseline copy, per store, at realistic
   seed sizes — the second path in §D.5, §K B4. (The unseeded path is
   already measured.)
3. Unpacked image sizes for the pre-pull candidate set against the 3 GiB
   store budget (§I.1).

## §J Spine statements this amends

A reader diffing v1.10 should not have to discover these. Each row gives
the exact replacement wording.

1. **spine §0** — "No Docker inside the sandbox. No network runtime
   provider exists." → *"No Docker daemon inside the sandbox: no docker
   socket, no privileged containers, no DinD. A daemonless rootless
   container runtime (podman) is present and is the provisioner's second
   process backend (§2b.2). No network runtime provider exists."*
2. **spine §1 `source.kind`** — "`image` is NOT a hosted kind: rejected at
   platform admission (`image_source_not_hosted`); there is no container
   runtime in the guest." → *"`image` is NOT a hosted kind: rejected at
   platform admission (`image_source_not_hosted`) — the hosted path's unit
   of work is a source checkout, and an image-only source has no tree to
   build, seed, or attribute evidence to. (The rationale is no longer the
   absence of a container runtime; §2b.2 adds one for process specs.)"*
3. **spine §1 `runtime.network_policy`** — the base allowlist enumeration
   gains *"container registries (registry + auth + layer-CDN hosts for the
   admitted registries)"* (§I.2).
4. **spine §2b catalog table + the `{{DATABASE_URL}}` sentence** — the
   per-job credential becomes per-world, and the mechanism differs by
   strategy (§D.4). postgres row: "role `harness`, password generated per
   job" → *"under `template_database`: one role per world, `harness_w<N>`,
   each with GRANTs on its own `w<N>` database only **plus membership in
   the object-owning role (`GRANT harness TO harness_w<N>`) or ownership
   transfer of the seeded objects — database-level GRANTs alone do not
   reach tables**, passwords generated per world; under `datadir_copy`:
   role `harness`, password generated per world and applied — together
   with the statement that names that world's database `w<N>` — by
   post-start rotation after readiness, at `provision` and after every
   `reset`, because a restored data directory carries the baseline
   password and the baseline database name."* rabbitmq row
   (`datadir_copy` only): *"user `harness`, password generated per world,
   applied by post-start rotation at `provision` and after every
   `reset`."* The following sentence,
   "`{{DATABASE_URL}}` renders with the catalog role, the generated
   password, …" → *"`{{DATABASE_URL}}` renders with that world's role and
   password, the allocated port, and `{{DB_NAME}}`."* redis is unchanged
   and remains without auth — the one store with no credential boundary in
   either backend (§K B1). This is a change to the native path that the
   amendment carries deliberately: the cross-world reachability it
   documents already applies there.
5. **spine §2b `user` rule** — gains *"every `kind: image` spec →
   `svc-data`"*, enforced by the same `user_assignment_invalid` (§B).
6. **spine §2b instancing/ports** — gains *"`kind: image` specs run once
   per world, always, and take the per-world formula
   `15000 + 100*world_index + ordinal`; they are never job-shared and
   never take the 14xxx band. `fixed_port` applies to them exactly as to
   `source` processes, with the same degradation to W=1."*
7. **spine §2e rule 4** — "No privileged requests: bundles cannot request
   users, mounts, host networking, or devices (there are no fields for
   them; unknown fields fail preflight)." → *"No privileged requests from
   the bundle: bundles cannot request users beyond the service set,
   mounts, host networking, devices, or capabilities — there are no fields
   for them, unknown fields fail preflight, and a translated compose
   service that carried any of them is `image_privilege_requested`. Host
   networking and the two bind mounts of §2b.2 are provisioner-chosen for
   every image-backed spec: no bundle can request them, and none can opt
   out."*
8. **spine §2f `spawn_failed`** — "a `source` or `managed` process failed
   to start" → *"a `source` or `managed` process failed to start (an
   image-backed spec raises `container_start_failed` instead)"*.
9. **spine §5 `parallelism_degraded` payload** — reason vocabulary gains
   `image_budget`; *"`effective` is any integer in `1..requested`"*
   replaces the literal `1` (§H).
10. **spine §6 ownership row** — "admission (feasibility, caps, `image`
    rejection)" → *"admission (feasibility, caps, `source.kind: image`
    rejection, registry-allowlist policy)"*. `image` rejection now means
    the spine §1 source kind only; image-backed process specs are admitted
    at spine §2e preflight, which is Khushal's row.
11. **spine §0 filesystem layout** — gains two lines:
    `/work/containers/      rootless podman graphroot (svc-data — §2b.2)`
    and
    `/run/alk-containers/   podman runroot + XDG_RUNTIME_DIR (svc-data, 0700 — §2b.2)`
    (§I.1).
12. **spine §2d `readiness`** — gains *"for a capability backed by a
    `kind: image` spec, the probe must observe ≥2 consecutive successes
    separated by `interval_seconds`; the same rule governs post-reset
    sentinel verification. The bundle authors the entry's timeouts and
    intervals as for any capability; the probe's mechanics come from the
    §2b.3 profile and cannot be overridden by a bundle."* (§D.7).
13. **spine §2c** — gains a sentence keyed on `kind` rather than a catalog
    row (an image-backed store has no catalog row to extend — it carries no
    `engine`/`version` — and the per-engine strategy lists live in the
    **§2b** catalog table, not in §2c): *"For a `kind: image` store the
    permitted strategy is `datadir_copy` when the store declares
    `migrations`/`seed_files`, and the implicit `empty` baseline of this
    section when it has no `seed.stores` entry at all; any other pairing is
    `seed_strategy_unsupported`."* The `inputs_digest` construction gains
    *"for a `kind: image` store the trailing component is
    `image:<digest-pinned reference>\n` in place of `<engine>:<version>\n`"*
    (§E).
14. **spine §0 opening paragraph** — "ALK's in-sandbox provisioner consumes
    it and starts every service as a plain process on localhost." →
    *"ALK's in-sandbox provisioner consumes it and starts every service on
    localhost: as a plain process, or — for a `kind: image` spec — as a
    rootless container sharing the host network namespace (§2b.2). Every
    address is still localhost and there is still no network runtime
    provider."*
15. **spine §0 users gloss** — "`svc-data` (every `managed` engine)" →
    *"`svc-data` (every `managed` engine, every `kind: image` container,
    and the rootless podman that runs them)"* (§B).
16. **spine §2b mechanics heading** — "**Build and run mechanics
    (copy-based — no mounts, no privileges):**" → *"**Build and run
    mechanics (copy-based for `source` and `managed` — no mounts, no
    privileges; a `kind: image` spec adds exactly two provisioner-chosen
    bind mounts and no privileges — §2b.2):**"*
17. **spine §2b `started_check`** — "(optional; only for `source`
    processes with no capability)" → *"(optional; only for `source` or
    `kind: image` processes with no capability)"*; the rest of the rule,
    including the port-variant semantics, is unchanged (§B).
18. **spine §2d `provenance`** — the tuple gains `resolved_images`:
    *"`{source_kind, repository, commit, source_digest, generator,
    generator_version, adopted_files, generated_files, resolved_images}`,
    where `resolved_images` maps each image reference as authored (usually
    a tag) to the digest-pinned reference recorded in `processes[].image`;
    empty when no spec is `kind: image`."* (§I.3).
19. **spine §2e failure-code table — declared CLOSED, and this amendment
    extends it.** Added contract-rule codes: `spec_backend_ambiguous`,
    `image_not_pinned`, `image_build_commands_forbidden`,
    `image_profile_missing`, `image_port_unconfigurable`,
    `image_privilege_requested`, `image_registry_not_allowed`,
    `image_budget_exceeded`, `image_metadata_version_unsupported` (§F.1).
20. **spine §2f failure-code table — declared CLOSED, and this amendment
    extends it.** Added: `image_pull_failed`, `image_unavailable`,
    `container_start_failed`, `container_remove_failed` (§F.2). Two v1.10
    codes are reused rather than duplicated: `seed_failed` already covers
    an image store's `seed_client` step (§E.2), and
    `store_statement_failed`'s wording widens from "a managed store" to
    *"a managed or image-backed store"* so it covers a rejected
    post-readiness statement (§D.4) as well as sentinel and canary probes.
21. **spine §4.2 `reset` recipes** — **two of the three** native recipes
    stand unchanged, including v1.10's *"flush MAY be implemented as a
    data-dir wipe + engine respawn (the stronger guarantee; a bare flush
    command is engine-dependent)"*. The native `datadir_copy` recipe gains
    the rotation §J.4 requires — "stop that world's engine instance,
    restore its data directory, restart engine + `source` processes" →
    *"stop that world's engine instance, restore its data directory,
    restart the engine, **re-apply that world's credential**, restart
    `source` processes"* — because a restored data directory carries the
    baseline password, and under postgres the baseline database name with
    it (§D.4, §J.4). A fourth sentence is added: *"For a `kind: image`
    store the recipes are the container form of the same two.
    `datadir_copy`: `podman rm -f` that world's container, delete and
    re-copy its data directory from the frozen baseline, `podman run`,
    readiness under the §2d rule, re-apply that world's post-readiness
    statements (credential and `w<N>` database name), then
    restart its `source` processes. `empty`: `podman rm -f`, discard the
    data directory, `podman run` against a fresh one — i.e. the wipe +
    respawn form above, which is the only form offered for image-backed
    stores. After every reset the store's `sentinel` must pass, unchanged."*
    (§D.5, §E.3).
22. **spine §5 step 3** — "start stores → run migrations + seeds → freeze
    baseline" → *"start stores (a `managed` engine as a process; a
    `kind: image` store with seed inputs as a transient `base-<name>`
    container — §2b.2) → run migrations + seeds (for an image store,
    `podman cp` from `bundle_dir` then the profile's `seed_client` inside
    the container) → freeze baseline (for an image store, seal its data
    directory at `/work/build/<name>/baseline/`) → conformance gate →
    `provision(instances=W)`."* A store with no `seed.stores` entry has no
    baseline step in either backend (§E.1).

**Both extended tables are declared closed in v1.10.** A consumer that
matches exhaustively on the §2e or §2f code list — the gateway's failure
router above all — breaks on an unknown code, so the table extension and
its consumers must ship in the same release. This is called out here
rather than left to be discovered.

Unchanged and restated only because the temptation is new: spine §2d's
`container_port` remains informational in the hosted path (§D.10).

## §K Open questions

### Blocking — these gate implementation, not polish

- **B1 — Per-world credentials and the per-world database: what the
  mechanism actually costs.** §D.4 makes image-backed store credentials
  per-world, and the obvious mechanism does not work (an init-time
  `credential_env` is inert against a copied, already-initialized data
  directory — and `POSTGRES_DB` is inert for the identical reason).
  Accepting the rule means accepting all of:
  1. a **seventh profile key** `post_readiness_statements` (§C) and an
     ordered statement step exec'd inside the container after readiness —
     once per world at `provision` and again after **every** `reset` —
     carrying both this world's credential **and** the statement that
     makes its database `w<N>`, without which `{{DATABASE_URL}}` renders a
     database that exists in no world;
  2. on the native path under `template_database`, **per-world postgres
     roles** `harness_w<N>` plus per-world GRANTs **plus membership in the
     object-owning role (`GRANT harness TO harness_w<N>`) or ownership
     transfer of the seeded objects, since database-level GRANTs alone do
     not reach tables**, and `{{DATABASE_URL}}`
     rendering the world's role — a change to the catalog table, the
     address shape, and the seeding path (§J.4);
  3. on the native path under `datadir_copy` (postgres per-world,
     rabbitmq), the same **post-start rotation**, at `provision` and after
     every `reset`, for the same reason (§J.21);
  4. redis, which the catalog runs with no auth: either accept that it has
     **no** cross-world boundary in either backend, or require
     `requirepass`/ACL rotation and amend the catalog's "no auth" row.
  The alternative is to accept the image half only and knowingly keep the
  native hole. Either way this is the work being approved — not a one-word
  table edit.
- **B2 — What does a new store protocol actually cost?** Mongo/Kafka need
  new sentinel shapes, reserved canary names, address shapes, and a
  decision on `no_sql_store` (§E.4). In this milestone, a later one, or
  never?
- **B3 — Registry domains in the platform base egress allowlist.** Without
  the spine §1 amendment in §I.2 every pull fails in production. Which
  registries — docker.io only, docker.io+quay.io, or any public registry?
  The allowlist answer and the registry-policy answer are the same answer.
- **B4 — The seeded reset cost is still unmeasured.** §E.1 splits the
  strategies: an unseeded image store rides the spine's implicit `empty`
  baseline, whose reset is the ~1s wipe + respawn the spike actually
  measured; a **seeded** store is `datadir_copy` only, and §D.5 states
  plainly that its datadir-copy cost has not been measured. Accept
  `datadir_copy`-only for seeded stores on that basis, or block on the
  measurement (§I.4.2)?
- **B5 — The curated profile table.** §C makes V1 coverage "curated stores
  + arbitrary stateless images", not "anything". Confirm that trade,
  confirm the table's owner (proposed: the provisioner's lane), agree the
  row-add process and its turnaround, and confirm it ships as a versioned
  `/opt/alk` artifact whose skew is a hard refusal (§F.1
  `image_metadata_version_unsupported`).

### Smaller open choices

- **S1 — Catalog-major mismatch routing** (§G rule 2): image fallback ON
  by default, or opt-in per job? The answer decides which of §G's two
  statements about `engine_unsupported` is the true one, and it is the one
  open item that **gates translation code** — it cannot ride to a later
  call even though it is small.
- **S2 — Private registries and `build:` contexts** (§F.3): which
  milestone?

### Assumed unless someone objects

- **A1 — Ownership** follows spine §6 as amended in §J.10: provisioner
  image backend and §C profile table = Khushal; translation emission and
  digest resolution = Rishav; snapshot baking, the two `/opt/alk` metadata
  artifacts, and the egress allowlist = Azain.
- **A2 — Version**: lands as spine **v2.0** after P11 stabilizes; **v1.11**
  behind a feature flag is the alternative (v1.10 is already published, so
  the minor-version option has moved up by one).

## §L What this is NOT — and what it costs against the compose proposal

- **Not a compose runtime**: no compose file is ever executed, no
  project-level orchestration is delegated.
- **Not DinD**: no daemon, no docker socket, no privileged mode anywhere.
- **Not a change to tonight's scope**: the demo runs process-only; this is
  the next milestone, gated on the call.

The delta against the circulated compose proposal (v1.6) is real — it does
run one rootless Docker daemon and does delegate project-level
orchestration, so "no daemon, smaller surface, orchestration stays ours"
is a genuine difference. Two things cut the other way and belong in the
same comparison:

- **Isolation is a regression.** v1.6 gives every world its own project
  network, DNS namespace and volumes, and states plainly that no world can
  address another world's service name, network, volume or evidence path
  — enforced by `compose_cross_world_reference`. This proposal's
  host-networking-only rule (§D.2) makes every world's store reachable
  from every other world (spike finding 5). The whole mitigation is §D.4's
  per-world credentials: a credential boundary, not a network boundary,
  and weaker — and absent entirely for a store with no auth (redis, §K B1).
  The spike names "per-role users" alongside per-world credentials, but
  they are not part of this mitigation: they separate roles, not worlds
  (spine §0: "All worlds share these uids in V1 — process-level
  cross-world isolation is NOT claimed"), and by §B every `kind: image`
  spec runs as the same `svc-data` anyway. What this proposal preserves is
  parity with the **existing** process model; it does not match v1.6 on
  this axis.
- **V1 coverage is strictly smaller.** v1.6's V1 covers `build:` contexts,
  Dockerfile-only repos via a generated compose document, and implicitly
  private images. This proposal's V1 excludes all three (§F.3), and its
  store coverage is a curated table (§C). "Same coverage goal" is
  defensible as a goal; at the milestone being decided, v1.6 covers more.

## §M Named risks and the follow-up spike

Three items are load-bearing and untested. They are one short spike
together, and none should be treated as settled before it runs. The spike
gates implementation of §D and §E; it does not gate Azain's §I.1 snapshot
work, which supplies the configuration the spike needs and can start now.

1. **Non-root exec in a custom snapshot.** The local prong needed
   `seccomp=unconfined`, `apparmor=unconfined` and `--device /dev/fuse` on
   the outer container; the Daytona prong ran as **root** from the stock
   `quay.io/podman/stable` image. Production execs as `svc-control` in a
   **custom** snapshot and runs podman as `svc-data`. Whether nested
   rootless works in that configuration is the one thing that has to be
   true and the one configuration not tested.
2. **Exit observation (§D.8).** Confirm `podman wait` as the liveness
   bridge under the same configuration: what its latency is, what happens
   when a container exits between readiness probes, and whether the waiter
   survives provisioner restarts.
3. **Bind-mounted host data directories under the rootless userns
   mapping (§D.3).** Write, freeze, copy and re-run: does a store image
   running as its own internal `USER` start against a host directory
   sealed and re-copied by `svc-data` through `podman unshare`, with
   ownership preserved? The spike never bind-mounted a host data dir — its
   reset result is explicitly structural, i.e. container-local storage —
   so the entire §D.3/§E storage model rests on this.

Carried from the spike as known, not risk: cgroup delegation is absent
nested, so per-container caps are unreliable (§H).
