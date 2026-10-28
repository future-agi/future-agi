# Developer update safety

This gate protects the **base OSS Standalone** automatic-update path. A passing
PR, a declaration, or a static calibration is **not** product qualification and
does not enable an update. An exact-transition signed proof, real large-volume
measurements, host admission, a cold backup and the automatic restore path are
still required. Legacy metadata and manual CLI semantics are unchanged.

## What fails, and why

| Change | Gate and required action |
| --- | --- |
| Model changes without migrations | `makemigrations --check --dry-run` fails. Add the migration. |
| Editing/deleting an existing migration | Fails: deployed migration histories are immutable. Add a new migration. |
| A new `RunPython` or `RunSQL` operation | Requires an in-file reviewed declaration, **even for a small table, a read-only query, a helper, or code that looks harmless**. Only exact Django forward/reverse noop pairs are exempt. |
| Schema/storage changes on a registered high-volume table | The real Django loader and historical model state resolve physical table names, field types and relationships. Declare every detected risk and table. |
| Any edit to a registered ClickHouse schema/client source | Requires a hash-bound review entry in `clickhouse-change-reviews.json`. This includes comments, routine CREATE/SELECT changes, renames and deletions. There is no DDL parser or "safe SQL" exemption. |
| A newly discovered, unregistered DDL/client source | Add the path/glob and rationale to `clickhouse-sources.json`, then declare its change. Coverage scans Python, Go, SQL, shell and extensionless sources. |
| A required environment variable without an unattended value | Supply a Compose default or register its real generator and entrypoint in `env-generators.json`; keep the env reference accurate. |
| Unsupported Compose services, mounts, volumes, profiles or restart policies | Update `automatic-layout.json`, runtime support, backup/restore coverage and qualification **together**. An arbitrary new volume is not automatically backed up. |
| Unsupported migration layout or an unclassified operation | Fail closed. Update the explicit classifier/mapping and tests; do not hide the operation in a helper. |

The gate deliberately **does not attempt to understand arbitrary Python, Go,
shell or SQL behavior**. Aliases, `getattr`, imports, list mutation, concatenated
SQL, heredocs and comments cannot earn a data operation or registered schema
runner an exemption. Human review establishes the actual affected table roster
and mitigation; real upgrade/restore tests establish behavior. Declarations do
not authorize manual scripts or operational steps during automatic updates.

## Run the checks

Use the PR's real base (normally on `dev`), not `HEAD` or a convenient release.
The comparison is merge-base to the working tree, including untracked files.

```sh
# Repository root: Python 3.9+ standard library; serial, no containers started.
python3 -I -m unittest discover -s deploy/tests -p test_update_safety_gate.py -v
DOCKER_CONTEXT=colima-fagi-update python3 -I scripts/check_update_safety.py \
  --static --base origin/dev --json

# From futureagi/, in the frozen backend environment.
export DJANGO_SETTINGS_MODULE=tfc.settings.settings EE_LICENSE_KEY= ENV_TYPE=local
export NO_STARTUP_DB_MUTATIONS=true FI_SKIP_CH25_MIGRATION=1 LITELLM_MODE=PRODUCTION
uv run --no-sync python -I -m unittest discover -s tests -p test_update_safety_gate_django.py -v
uv run --no-sync python manage.py makemigrations --check --dry-run
uv run --no-sync python -I ../scripts/check_update_safety.py \
  --static --django --base origin/dev --json
uv run --no-sync python -I ../scripts/check_update_safety.py --calibrate --json
```

Static layout validation uses Docker Compose >=2.30 `config` only: the Docker
daemon is not used. All profiles are rendered with shell and `.env` overrides
removed. `--compose-model FILE` accepts the equivalent pre-rendered JSON for
fixtures, not a release or installation admission bypass. In the dedicated local
test environment, Docker commands must select `colima-fagi-update`, never the
shared/default daemon.

Backend CI's required `Backend Tests Pass` aggregate depends on `update-safety`.
That job runs the serial fixture tests, production-OSS loader, makemigrations,
layout/env/source checks and historical calibration. Production settings are
intentional: `tfc.settings.test` enables EE and is not the OSS app inventory.
Python, Go, SQL, shell, scripts, bin and deploy changes must trigger the gate;
Update CI must preserve the same coverage. Upgrade/restore E2E is a separate
required integration, not replaced by the fast gate.

## Declare a migration

Add one **module-level literal dictionary** to the new migration. Replace the
illustrative reviewer and discussion link below with the actual review. Do not
copy an example approval as evidence.

```python
UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:tracer_trace"],
    "mitigation": "Use an idempotent cursor backfill in batches of 500; commit bounded ID sets and measure WAL, disk growth and restore time.",
    "reviewer": "reviewer-login",
    "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
    "large_volume_rehearsal": True,
}
```

All seven fields are required. Unknown/missing fields, duplicate dictionary keys,
nonliteral or nested/class-level declarations, duplicate declarations, unknown
risk classes, malformed lists, empty tables, duplicate values, wildcard tables,
invalid review URLs and non-boolean rehearsal values fail. `schema_version` must
be the integer `1`, not a boolean. Mitigation is 30–2000 characters. `reviewer` is
a GitHub login; `review` is a PR review-comment URL of the form above. These are
auditable references, **not machine proof that a person approved**. Required PR
review must verify them and the complete mitigation/table roster.

Table identities are `postgres:table_name` or `clickhouse:table_name`, with
unqualified SQL identifiers. Include **every actual affected table**, including
small and non-model tables; a registry entry is not needed just to declare a
small table. There are no wildcard/sentinel table identities. The gate does not
claim to infer arbitrary data-operation effects. For schema operations it also
requires the machine-resolved high-volume tables/classes to be covered.

Risk classes: `type_change`, `not_null`, `volatile_default`, `index`, `constraint`,
`data_migration`, `table_rewrite`, `clickhouse_rewrite`, `clickhouse_mutation`, and
`clickhouse_change`. All non-noop `RunPython`/`RunSQL` operations require
`data_migration`. ClickHouse source change reviews require `clickhouse_change`,
plus any more specific classes the reviewer identifies. Existing in-file
ClickHouse comment headers are **not** a substitute for the change registry.

The only data-operation exemptions are these **pairs**:

```python
migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop)
migrations.RunSQL(migrations.RunSQL.noop, migrations.RunSQL.noop)
```

A missing reverse operation, a user-defined noop, a noop in only one direction,
or a subclass with overridable database behavior is not exempt. Real-loader
operation objects, not the spelling of the source, establish these identities.
`SeparateDatabaseAndState` is not a hiding place for undeclared nested operations.

On high-volume existing tables, schema review covers backend storage/type/size/
collation changes (including inbound FKs), FK target/`to_field` changes even when
storage types match, new NOT NULL columns **without `db_default`**, nullability
tightening, callable/volatile defaults, indexes, constraints, generated storage,
column/table renames and destructive/rewrite changes. A Python `default=0` is
not `db_default=0`. Unmanaged/proxy models that alias a registered physical table
do not turn that table into a fresh empty table. Metadata-only help text changes
and nullable expansion without other risk remain routine. Unknown/custom schema
operations fail until explicitly classified. Prefer **add a nullable column,
backfill in batches, add the index in its own migration**.

## Declare a ClickHouse source change

`clickhouse-sources.json` lists explicit paths/globs and rationales. It includes
schema data, schema runners, bootstrap/management entrypoints, client wrappers
and conservative client-call candidates in Python, Go, SQL and shell. Changing a
registered file requires review regardless of how it constructs or executes SQL.
The previous-base registry is checked too: deleting a registration cannot hide
a previously registered runner's change.

The repository coverage test searches for DDL keywords, MergeTree, ClickHouse
client/driver imports and client calls. This is an **inventory alarm**, not a SQL
or program parser and not a completeness theorem for deliberately indirect code.
Add new entrypoints/helpers/schema paths to the registry as part of review.
Tests/migrations are separately covered; non-ClickHouse discovery exclusions
require a rationale and a SHA-256 of one exact source blob. Changing such a file
invalidates the exclusion until the new contents are reviewed. A wildcard
non-product exclusion must not be used to hide shipping schema code.

For each changed schema/client file add an entry to
`deploy/update/clickhouse-change-reviews.json`:

```json
{
  "schema_version": 1,
  "changes": [
    {
      "path": "futureagi/tracer/services/clickhouse/v2/schema/example.sql",
      "before_sha256": "<SHA-256 of the merge-base blob, or null for an added file>",
      "after_sha256": "<SHA-256 of the candidate blob, or null for a deleted file>",
      "declaration": {
        "schema_version": 1,
        "risk_classes": ["clickhouse_change", "clickhouse_rewrite"],
        "tables": ["clickhouse:spans"],
        "mitigation": "Describe the bounded expansion/backfill, lock and disk behavior, and exact upgrade and restore rehearsals for this change.",
        "reviewer": "reviewer-login",
        "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
        "large_volume_rehearsal": true
      }
    }
  ]
}
```

This is a **format illustration**, not valid approval or a copyable hash. Use
64 lowercase hex characters for each existing blob. Hash exact bytes, not a
normalized or parsed version. Resolve the merge-base with
`git merge-base origin/dev HEAD`; hash the bytes from `git show BASE:path` for the
old version and the actual working-tree file for the candidate. Additions use
JSON `null` for `before_sha256`; deletions use `null` for `after_sha256`. Both
hashes cannot be equal. Each path appears once; all entries, including older
entries, must have the closed valid schema. A further edit or rebase that changes
either blob invalidates the review entry. Do not substitute an in-file comment,
reuse an unrelated review, or invent an approval.

## Environment values: defaults or explicit generators

Every Compose interpolation needs an explicit `:-`/`-` default, including an
intentional empty default, or a generator registered in `env-generators.json`.
Nested substitutions are checked independently. Bare `${TOKEN}`, `$TOKEN`,
`${TOKEN:?required}` and conditional `:+`/`+` substitutions are not defaults.
Every required Standalone row in `deploy/env-reference.toml` likewise needs a
Compose default or a registered generator; TOML comments and setup-specific
values cannot hide a required setting.

A generator registry row names `variables`, repository-relative `path`,
`language` (`shell` or `python`), exact generator `symbol`, exact installer
`entrypoint`, `availability` (`fresh_install` or `every_install`), and `reason`.
All fields are required and unknown fields fail. Variables cannot be registered
twice. Tests locate the **definitions**, never a call, generated-section label,
comment, `echo`/`printf` message or multiline quoted diagnostic. Sources are not
executed. Shell generator sources intentionally refuse heredocs rather than
mistaking their payload for a definition; use a small dedicated source file.

Registration is a reviewed promise about wiring and availability, not arbitrary
shell execution analysis. `gen_secret` and `gen_fernet_key` are real functions in
`bin/lib/secrets.sh`, called through `write_secrets`. Fresh-only generation must
not rotate retained installation secrets. A new credential on an existing
enrolled installation still requires an actually unattended runtime/bootstrap
transition and exact qualification; installer-only generation is not permission
for a manual intervention during an update.

## Shared contracts and large-volume evidence

* `migration-apps.json` maps actual AppConfig labels/modules and image-relative
  roots, including `always`, source-checked `if_present`, and unsupported EE
  conditionals. App labels are not directory names (`agentcc` is `prism`). Real
  `INSTALLED_APPS` and `MigrationLoader` must agree. Squashes/replacements and
  custom `MIGRATION_MODULES` fail; update the fixed host observer with the map.
* `high-volume-tables.json` retains PostgreSQL observation, dataset, evaluation,
  metering, grouping/investigation history and ClickHouse ingestion/rollup/legacy
  CDC table coverage. Each entry cites its source and growth rationale. Do not
  shrink this registry to silence a migration or omit tables from declarations.
* `automatic-layout.json` pins every Compose service, mount, profile, named
  volume and storage/bind coverage. Restart is only `unless-stopped` or the
  string `"no"`. Every durable domain must be cold-archived and restored to the
  same named volume; Redis remains ephemeral. A layout edit requires coordinated
  runtime and qualification work, not just changing expected JSON.

`--json` emits `status`, `new_migrations` and
`large_volume_rehearsal_required` entries containing `path`, `tables`,
`risk_classes`, and `large_volume_rehearsal: true`. Release qualification must
consume these requirements **including small-table data operations and
ClickHouse source reviews**, seed the declared actual tables, and retain real
per-migration duration, input rows/bytes, disk/WAL/temp growth, throughput and
memory evidence. Source hashes/review metadata do not measure those quantities.
The runtime proof format remains a separate contract: preserve its engine,
schema-qualified table identities, risk-class vocabulary and conservative
reference envelope when producing measurements.

`--calibrate` audits v1.48.0 through v1.55.0, starting at v1.47.1. It uses read-only
Git history, an isolated source directory and fresh `-I` historical-loader
processes. It reports per release the migration findings, current-policy
ClickHouse source-review cost and a separately labelled data-operation source
inventory. Released files are not retroactively edited. If dependencies or
production settings prevent a loader from initializing, **the command exits
nonzero**, retains every release's error and labels the result `blocked`.
Source-inventory counts alone cannot establish schema-risk counts or successful
loader calibration. Calibration is developer cost visibility, never product or
large-volume qualification. Full native upgrade/restore acceptance remains
required; this work must not be described as completion of Phase 1.
