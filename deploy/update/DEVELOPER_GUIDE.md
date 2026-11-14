# Developing changes that support automatic updates

Audience: engineers and reviewers changing the self-hosted product. Start with the checklist and change table; use [UPDATE_SAFETY.md](UPDATE_SAFETY.md) for the full checker specification.

**Scope and release eligibility:** this guide describes the Phase 1 implementation merged into `dev` in [PR #3623](https://github.com/future-agi/future-agi/pull/3623). These checks apply where this implementation is installed; merging it does not establish release eligibility. Unattended apply supports only the qualified base OSS Standalone Docker Compose layout and requires [published, signed proof for the exact transition](README.md#release-eligibility). The dated migration examples below are not live qualification results. Binary/systemd, Kubernetes/Helm/GitOps and distributor/air-gapped installs are outside this scope.

## 1. The rule for every developer

**An existing opted-in installation must be able to upgrade without a person running a new command, editing configuration or repairing data for that release.** Test existing installations and existing data, not just an empty database.

Most UI/API-only changes do not need an `UPDATE_SAFETY` declaration. The additional requirements concern migrations, database statements, startup/install logic, configuration, persistent storage and the updater's contracts. The lightweight Backend CI update-safety job is triggered for every changed PR path; the amount of extra work depends on what changed.

### Before requesting review

- [ ] Explain whether the change affects old data, configuration, startup, storage or recovery.
- [ ] Ship required work through supported migrations or unattended bootstrap, not a one-off operator script.
- [ ] Add missing migrations; do not rewrite deployed migration history.
- [ ] If the change needs a declaration or hash-bound statement review, supply complete table/risk information and get a real review.
- [ ] Test affected product behavior with representative old data. Include retry/interruption cases where applicable.
- [ ] Run the fast gate and real Django checks. Check the actual upgrade/restore CI result separately.
- [ ] For selected high-volume tables, make sure the rehearsal has realistic reference data and independent measurements.
- [ ] State exactly what passed, what was not run, and what remains pending. Never invent approval or describe a declaration flag as execution evidence.

**Do not “fix” a red safety gate by shrinking table scope, removing a table from the registry, editing expected contract JSON alone, or bypassing a failing rehearsal.**

## 2. What the three migrations actually change

These are product changes already in `dev` and v1.57.0. The auto-update PR did not introduce their feature behavior. Compared with that release, this branch adds only declaration metadata to `tracer/0119`; its executable migration logic is unchanged.

### `simulate/0099_harness_versioning`

**Product purpose:** version the simulation harness's environment, scenario suite and agent runtime, so a run can retain the versions it used rather than depending only on current defaults.

- Adds seven tables for environment versions, suite versions, suite members, suite validation, assembled bundles, nudges and agent-version runtimes.
- Adds references on harness jobs and conversation leases, plus indexes and uniqueness constraints. References added to existing tables are nullable; old rows are not assigned versions by this migration.
- Uses schema operations only: no `RunPython` or `RunSQL`. It does not introduce `AgentVersion` itself, which already exists.
- **Important:** schema creation is not historical-data conversion. The separate `harness_versions_backfill` management command is documented as a manual post-deployment operation, with a read-only `--check` mode. The migration does not run it.

**Developer lesson:** preserve a working legacy-data path, or integrate any required conversion into a supported, restartable unattended path. The feature owner must establish whether that backfill is optional for each supported old-data journey. A healthy server or an applied migration does not prove all historical environments have been converted. Do not add the manual command to an automatic-update runbook as a required customer step.

Sources: [migration](../../futureagi/simulate/migrations/0099_harness_versioning.py), [job pins](../../futureagi/simulate/models/hosted_harness.py), [backfill command](../../futureagi/simulate/management/commands/harness_versions_backfill.py), [launch/fallback behavior](../../futureagi/simulate/services/harness_versioning/assembly.py).

### `tracer/0119_retire_deleted_eval_memberships`

**Product purpose:** repair Error Feed issues whose deleted evaluation results still left live trace memberships, and repair accumulated count drift.

- Soft-deletes matching stale trace memberships; it does not hard-delete them. Session memberships are intentionally excluded from this retirement because their uniqueness rule would block rejoining.
- Recounts every live eval-source issue, updates changed counts/dates/impact and soft-deletes empty issues unless owned by a person or otherwise protected by their status/external issue reference.
- Writes `tracer_error_cluster_traces` and `tracer_trace_error_group`; reads `tracer_eval_logger` through joins. Those reads belong in the declaration too.
- Is one `RunPython` with `atomic=False`. The issue recount uses pages of 200, but the initial membership retirement is one UPDATE and collects affected cluster IDs into a Python set. **The entire migration is not bounded to 200 rows.**
- Its reverse is `RunPython.noop`: unapplying the Django migration record does not reconstruct the old membership/counter values.

**What auto-update work added:** a seven-field `UPDATE_SAFETY` declaration, a narrow gate exemption for adding such metadata to an otherwise unchanged old migration, and a three-table Error Feed reference fixture. The isolated rehearsal adds 100,000 rows per table, including 41,000 stale memberships and 43,000 deleted eval logs. **This fixture is CI/qualification data, not rows inserted into customer databases.**

The realistic workload exposed a native resource-reader failure when a path vanished during a live scan. The sampler and its diagnostics were fixed separately; that did not change what 0119 does to customer data. The diagnostic established a missing path, not which particular spill/WAL/log file disappeared. A completed native run, not the fix or metadata alone, establishes qualification.

**Developer lesson:** use the product's `soft_delete_eval_results()` service when deleting eval results so memberships and results stay consistent. For repair migrations, explain transaction boundaries, retries, memory use, locking and reverse behavior; include both read and write tables and test representative damaged data.

Sources: [migration and declaration](../../futureagi/tracer/migrations/0119_retire_deleted_eval_memberships.py), [deletion service](../../futureagi/tracer/services/eval_tasks/entries.py), [clustering](../../futureagi/tracer/queries/eval_clustering.py), [reference workload](../../scripts/update_rehearsal_shipping.py), [sampler](../../scripts/update_rehearsal_resources.py).

### `tracer/0120_alert_issue_state_occurrences_high_water`

**Product purpose:** prevent an occurrence-threshold alert from being queued again merely because a count decreased and then crossed the same threshold again.

- Adds `occurrences_high_water = PositiveIntegerField(default=0)` to `tracer_error_feed_alert_issue_state`.
- For example, `99 → 100 → 99 → 100` should queue only one threshold-100 delivery. The accompanying product code checks and updates this state under a per-rule/per-issue lock.
- Existing rows start at zero. There is no backfill of past alert maxima. This records queued-delivery decisions, not proof that a notification was delivered successfully.

**Developer lesson:** counts can decrease after corrections. Preserve alert deduplication state and the locked delivery path; do not reset the high-water mark whenever counts change. Reversing the schema migration drops that state and cannot unsend an external notification.

Sources: [migration](../../futureagi/tracer/migrations/0120_alert_issue_state_occurrences_high_water.py), [state model](../../futureagi/tracer/models/feed_alert.py), [delivery decisions](../../futureagi/tracer/services/feed_alerts/events.py), [down/up regression test](../../futureagi/tracer/tests/test_feed_alerts.py).

### Why both upgrade baselines are tested

At the original guide baseline, normally migrated v1.56.1 databases had all three migrations pending, while v1.57.0 already had them applied. Metadata changes do not cause Django to rerun an applied migration. Qualification checks the real applied/pending plan rather than assuming every host's database matches its version label. The separate v1.53.0 → v1.54.0 → v1.55.0 journey tests two automatic updates after one opt-in.

**Base-branch updates (2026-11-03):** `dev` through `007153995` added `simulate/0100_agent_fix`, which introduces agent-fix records and related fields without adding registered high-volume table scope. The later merge through `17649a5b7` also adds `simulate/0101` and accounts `0029`/`0030`. At that integration point, source inventories indicated seven pending migrations from v1.56.1 and two accounts migrations from v1.58.0; qualification must verify the actual ordered plan. Accounts `0030` queues publication intents for existing demo traces. Its read scope includes the registered `tracer_trace` and `tracer_observation_span` tables, so it needs a declaration and representative trace/span reference data even though its writes target a new outbox table. No declaration or fixture alone proves the upgrade. Recheck the actual plan after every base merge.

**Base-branch update (2026-11-04):** `dev` through `150ff79c2` adds fourteen tracer migrations for Insights/Instant Eval plus one usage migration. Static predecessor inventories indicate **17 pending migrations from v1.58.0 and 22 from v1.56.1**; these counts are not a live qualification result. The new `0117_instant_eval_scale` and `0118_instant_eval_answers` use non-noop `RunSQL` to set analyze reloptions on newly created tables. They still need `data_migration` declarations: fresh tables do not exempt opaque SQL from review or make DDL locks and storage cost zero. Only first-declaration metadata is added; their executable migration bodies stay unchanged. The [review discussion](https://github.com/future-agi/future-agi/pull/3289#discussion_r4465601408) is a request, not approval.

`0118` also adds an answer relation to the existing registered `tracer_eval_task` table. Qualification includes that relation target even with `db_constraint=False`, so both predecessor journeys require representative source EvalTask data and independently measured rows/bytes. This is relation coverage, **not an EvalTask backfill**: do not invent a task-mutation count or make fixture tasks runnable. The complete reference intersection is Trace/ObservationSpan/EvalTask/**APICallLog** for v1.58.0, plus the three Error Feed tables for v1.56.1. The usage migration changes choices on the unique `APICallType.name` field; qualification conservatively expands its inbound relations to the registered `usage_apicalllog` table. A choices-only change can therefore add reference-data requirements even when it does not rewrite log rows. It does not require seeding every new target-only small table. Historical source fixtures must not acquire target-only model fields merely because the current model gained them.

The [EvalTask reference fixture](../../scripts/update_rehearsal_eval_task_reference.py) supplies 100,000 tasks per scale block: 50,000 completed historical, 20,000 failed historical, 20,000 paused continuous and 10,000 soft-deleted tasks, with real project/configuration relationships and 150,000 configuration memberships. It creates no evaluation entries or run loggers, so task recovery has no work to dispatch. Bounded atomic chunks and exact source-field/membership checks detect partial or conflicting replay. Separate read-only checks verify the retained fixture at qualification boundaries; independently observed physical counts and bytes, not the planned totals, remain authoritative. This fixture and its unit tests alone do not establish native migration or restore success.

## 3. What changes require extra work?

| Your change | What to do | What the checker can establish |
| --- | --- | --- |
| UI/API logic with no upgrade-sensitive effects | Usual product tests; verify old-data/API compatibility where relevant | No blanket migration-declaration requirement |
| Django model/schema change | Add a migration and run `makemigrations --check --dry-run` | Missing migrations and supported migration layout |
| Non-noop `RunPython` or `RunSQL` | Declare `data_migration`, all affected tables and mitigation; get review | Declaration shape and presence, not arbitrary code correctness |
| Risky operation on an existing registered high-volume table | Declare detected risks/tables; arrange applicable reference rehearsal | Real Django historical state and operation classification |
| Production source containing recognized schema/mutation statements | Add exact before/after source-hash review; register new source paths | Keyword detection and review binding, not a complete SQL/program analysis |
| Environment variable or installer/bootstrap change | Document it and provide a usable default or registered unattended generator | Required metadata, Compose substitutions and generator symbols |
| Service, mount, volume, profile, restart or storage-format change | Update runtime/observer/backup/restore support and contract tests together | Whether the rendered model agrees with the declared layout |
| New app, changed app label, migration module, squash or replacement | Coordinate fixed mapping/classifier changes and tests before support | Unknown/unsupported layouts fail closed |

**History is immutable.** Add a new migration to change behavior. Do not delete, rename or modify an old migration's operations, dependencies or helpers. The only existing-file exemption is a first `UPDATE_SAFETY` assignment whose removal leaves the same module AST as the merge-base version. Existing declarations cannot be rewritten under that exemption.

**Small does not mean exempt from opaque-code review.** Every non-noop `RunPython`/`RunSQL` needs a declaration, even on small tables or for read-only payloads. Only exact Django forward-and-reverse noop pairs are exempt; a user-defined noop or missing reverse is not.

**Additive does not mean cost-free.** Indexes, constraints, type changes, NOT NULL and defaults can scan or rewrite data and take locks. A nullable positive integer field can add a CHECK constraint; making a constrained FK nullable can recreate and validate the FK. Do not judge cost only from the operation's name or `null=True`.

## 4. How to write an `UPDATE_SAFETY` declaration

Put one literal dictionary at module level in the migration. Use these exact seven fields. This is a **template, not a valid declaration to commit**: replace the placeholders with the actual scope and review discussion.

```python
UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:ACTUAL_PHYSICAL_TABLE"],
    "mitigation": (
        "Describe the actual algorithm, batches, transaction and lock behavior, "
        "rerun/recovery semantics, and applicable upgrade/restore rehearsal."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}
```

- `schema_version`: integer `1`.
- `risk_classes`: nonempty, duplicate-free list covering all detected risks. Allowed values: `type_change`, `not_null`, `volatile_default`, `index`, `constraint`, `data_migration`, `table_rewrite`, `clickhouse_rewrite`, `clickhouse_mutation`, `clickhouse_change`.
- `tables`: nonempty, duplicate-free `postgres:table_name` / `clickhouse:table_name` list. Use unqualified physical names, not Django model names, `schema.table`, wildcards or made-up sentinels. Include reads/joins and unregistered small tables as well as writes.
- `mitigation`: 30–2000 trimmed characters describing the actual implementation, not “safe migration”.
- `reviewer`: the real reviewer's GitHub login, without `@`.
- `review`: a genuine `https://github.com/future-agi/future-agi/pull/<number>#discussion_r<comment-id>` permalink. A PR URL alone or an issue comment is not the accepted format.
- `large_volume_rehearsal`: boolean `True`, a commitment to the applicable rehearsal. It is **not** evidence that the rehearsal passed and does not require every declared small table to have 100k rows.

The gate validates syntax and detected coverage. It does not contact GitHub to prove that the reviewer approved the current code, and it cannot infer all tables touched by arbitrary Python/SQL. Human review must establish those facts. Never copy another migration's reviewer/permalink as approval for your change.

Open the PR as a draft, obtain the real review discussion, then finalize the declaration and evidence before requesting completion. A pending review must remain visibly pending. See [0119](../../futureagi/tracer/migrations/0119_retire_deleted_eval_memberships.py) for a real format example, not transferable approval.

## 5. Write migrations for real installations

These are engineering recommendations and review obligations; the static gate does not prove them:

1. **Use historical state.** In data migrations prefer `apps.get_model(...)` and stable migration-local logic over importing today's mutable model/service implementation. Test the actual source schema.
2. **Design bounded work.** Prefer keyset/resumable batches for large backfills. Avoid loading all IDs or rows into memory without a justified bound. Document any full-table statement separately.
3. **Choose transactions deliberately.** A giant transaction can hold locks and grow WAL. `atomic=False` can commit partial progress; make reruns correct and test interruption. A batch size alone does not bound query cost.
4. **Stage risky schema changes where appropriate.** Often add nullable state, backfill, then add indexes/constraints separately. Measure the actual SQL behavior; Python `default` is not database `db_default`.
5. **Explain rollback accurately.** A reverse noop does not undo data. A reversible schema operation can still destroy newly written data when reversed. Test the shipping snapshot-restore path; do not substitute `migrate <old-number>`.
6. **Avoid irreversible external work in migrations.** Sending notifications, charging accounts or changing external resources cannot be undone by restoring local volumes. Call out any unavoidable effects explicitly.
7. **Test the feature, not only readiness.** A successful bootstrap does not prove historical simulation runs, Error Feed counts or notification deduplication still work. Add relevant old-data assertions.

## 6. High-volume data: who needs a fixture?

The qualification producer computes the pending migration table scope and intersects it with [high-volume-tables.json](high-volume-tables.json). **Only the registered high-volume tables touched by the pending migrations need reference-scale seeding.** Other migrations still run in the measured normal-bootstrap rehearsal on product witnesses.

For selected tables:

- Use representative relationships, value distributions and rows that actually trigger the migration's work.
- Reach at least **100,000 actual rows per selected table**, with independently measured row counts and positive table bytes. Seeder self-reports are not sufficient.
- If no supported seeder exists, work with the update maintainer to add one; otherwise qualification refuses with `high_volume_reference_unavailable`.
- Do not disable integrity checks or fill unrelated all-default rows merely to hit the number.
- If a growth-prone table is new, review its registry entry and future seeding support. Do not remove historical registrations to avoid checks.
- Keep fixtures aligned with the source schema, constraints and product behavior as models evolve.

The author owns accurate scope and representative cases; update/release maintainers own integration with the qualification runner and signed evidence. The host compares real rows **and bytes** to the qualified reference. Exceeding either can prevent unattended apply for that transition. The 100k value is a minimum reference workload, not a universal customer size limit or a guarantee that every installation fits.

## 7. Configuration, raw statements and deployment changes

### Environment and startup

Document configuration in [deploy/env-reference.toml](../env-reference.toml). Every required Standalone environment row and Compose interpolation needs an explicit `:-`/`-` default, including intentional empty values, or a registered generator in [env-generators.json](env-generators.json). Nested substitutions are checked too.

Generator entries identify actual source functions and entrypoints, availability and rationale. The checker finding those symbols does not prove they are called on an existing install. Test the retained-config path and never rotate existing secrets just to supply a missing variable.

Automatic updates retain the enrolled deployment configuration; they do not automatically replace all Compose/env files with the latest repository versions. A new mount, secret or service is not supported just because fresh installation works. Required manual commands or new user-supplied credentials cannot be waived by a declaration.

### Raw SQL and ClickHouse

Outside Django migrations, recognized production schema/mutation statements use [clickhouse-sources.json](clickhouse-sources.json) and [clickhouse-change-reviews.json](clickhouse-change-reviews.json). A review binds the path, exact merge-base blob SHA-256 and current working-tree blob SHA-256, plus a declaration. Use `null` on the absent side for an addition/deletion. Use `clickhouse_change` and the actual ClickHouse table scope for genuine ClickHouse changes.

The scan examines old and new blobs. Editing even a comment in a statement-bearing file invalidates its hash binding. SELECT-only code and client imports alone do not trigger this review. Django migrations use their own gate; do not invent ClickHouse tables for a PostgreSQL-only false positive. Follow the inspected, hash-pinned exclusion process instead. See [the technical policy](UPDATE_SAFETY.md#clickhouse-statement-review-not-clientpath-review) for exact JSON and scanner limits.

### Storage and runtime contracts

Change [automatic-layout.json](automatic-layout.json), [migration-apps.json](migration-apps.json), [deployment-files.txt](deployment-files.txt), observers and backup/restore tests together where applicable. Only `unless-stopped` and string `"no"` restart policies are supported by this layout. New state must be accounted for across PostgreSQL application data, ClickHouse, object storage, Temporal and shared files. Declare disposable state honestly.

Matching expected JSON is necessary but insufficient: implement the runtime behavior and prove recovery. Unsupported layouts stay unsupported until that work exists.

## 8. Commands to run before review

Run from your repository root, using the PR's actual base (normally `origin/dev`). Fetch the base and calibration tags if absent. The comparison is **merge-base → working tree**, including untracked files; do not use `HEAD` as a shortcut that hides PR changes.

```sh
git fetch origin dev --tags
git merge-base origin/dev HEAD

# Fast gate and its unit tests. Python 3.9+.
python3 -m unittest discover -s deploy/tests -p 'test_update_safety_gate.py' -v
python3 -I scripts/check_update_safety.py --static --base origin/dev --json
```

The static layout check uses Compose >=2.30 to render configuration, but does not start containers or use the Docker daemon. It ignores developer `.env`/shell overrides except Docker context. Fixture `--compose-model` support is not a runtime-admission shortcut.

Run real Django checks in the frozen OSS backend environment, not the EE-enabling test settings:

```sh
cd futureagi
uv sync --frozen --no-dev
export DJANGO_SETTINGS_MODULE=tfc.settings.settings EE_LICENSE_KEY= ENV_TYPE=local
export NO_STARTUP_DB_MUTATIONS=true FI_SKIP_CH25_MIGRATION=1 LITELLM_MODE=PRODUCTION
export FUTURE_AGI_TELEMETRY_DISABLED=true

uv run --no-sync python -m unittest discover -s tests -p 'test_update_safety_gate_django.py' -v
uv run --no-sync python manage.py makemigrations --check --dry-run
uv run --no-sync python -I ../scripts/check_update_safety.py \
  --static --django --base origin/dev --json
```

Run the feature's own tests as well; the commands above do not execute its business behavior. When changing the gate/classifier, also run the separate historical calibration:

```sh
uv run --no-sync python -I ../scripts/check_update_safety.py --calibrate --json
```

Do not combine `--calibrate` with `--static`/`--django`, or use `-I` with unittest discovery. `large_volume_rehearsal_required` in JSON is a requirements list, not a passed-test list.

### What counts as release evidence?

The separate [PostgreSQL probe CI](../../.github/workflows/update-postgres-probe-ci.yml) exercises the shipping table-size SQL against an isolated PostgreSQL 16 service when relevant probe/test files change. It covers actual row counts, relation identity, RLS, inheritance and timeout/refusal behavior. This is a component contract test, not a full product upgrade.

Fast checks catch mistakes; native qualification tests the real upgrade. Update CI's latest-stable → PR and one-opt-in multi-release journeys are separate from the fast gate. If latest stable already includes your migration, choose an older supported baseline that still has it pending for the relevant test.

Qualification requires source snapshot, migration forward, target readiness, failure restore, interruption restore and backup integrity suites, with measured resources and cleanup. Evidence may use an explicitly tagged whole-bootstrap envelope rather than individual migration timings. A unit suite, fresh install, bounded milestone, historical calibration or test-key package is not production release certification. Failed or incomplete qualification must remain failed/ineligible, not be relabeled as success because a release can remain notify-only.

## 9. PR handoff template

Paste this into the PR and replace every line with actual facts:

```text
Update impact: none / migrations / database statements / config / startup / storage
Source versions exercised:
Pending migrations and complete read/write table scope:
Declaration / hash-bound review files and genuine review discussion:
Existing-data behavior, retries and interruption handling:
Locks, transactions, full-table operations and resource concerns:
Required operator steps: none (or explain why this transition cannot auto-apply)
High-volume intersection and representative seed support:
Fast checks and product regression tests:
Native upgrade + failure restore + interruption/integrity evidence:
Not run / failed / pending review:
External side effects and recovery limitations:
```

The PR author owns behavior and accurate scope. The reviewer verifies mitigation, evidence and genuine approval. Update/release maintainers integrate representative qualification and sign/publish only supported exact transitions. Customers should not have to manufacture compatibility evidence or run per-release repair scripts.

## 10. Recovery boundaries worth remembering

The host checks signed release evidence and time/storage budgets, stops the old installation, seals a cold backup, then starts the target through normal bootstrap. A pre-acceptance failure can restore the previous images and snapshot when authority, capacity and integrity permit. Candidate-only writes can be discarded. In-flight work and external side effects are not necessarily recoverable.

A successful accepted target is not automatically rolled back later. Ambiguous or unsafe recovery can require an operator. There is no promise of zero downtime, zero failure or zero data loss.

## Source of truth

- [Technical gate policy](UPDATE_SAFETY.md) and [gate implementation](../../scripts/check_update_safety.py)
- [Backend CI](../../.github/workflows/backend-ci.yml) and [Update CI](../../.github/workflows/update-ci.yml)
- [Qualification producer](../../scripts/rehearse_update_compatibility.py), [executor](../../scripts/update_rehearsal_qualification.py) and [reference seeders](../../scripts/update_rehearsal_shipping.py)
- [Updater runtime and operator documentation](README.md)

This guide summarizes implemented checks and review recommendations. It does not replace the checker, feature tests, actual review or native qualification results.
