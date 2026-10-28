# Developer update safety

This gate catches **honest developer mistakes** in the base OSS Standalone
unattended-update path. It is a developer aid, **not a security boundary** or a
proof that arbitrary Python, Go, shell or SQL is safe. Deliberate obfuscation,
split/concatenated SQL literals, aliases, `getattr`, and computed SQL are outside
the keyword scan's threat model. A reviewer still checks actual behavior.

A passing PR or a declaration does not enable an update. The safety net is the
real-data upgrade/restore rehearsal, signed exact-transition evidence, host
capacity/time admission, runtime deadline/stall/disk guards, a sealed cold backup
and automatic restoration of that backup and the previous images on failure.
Unqualified releases remain notify-only. Some ambiguities require manual
recovery rather than guessing. Legacy/manual CLI behavior is unchanged.

## What fails and what to change

| Change | Required action |
| --- | --- |
| Models changed without migrations | `makemigrations --check --dry-run` fails. Add a migration. |
| Existing migration edited/deleted | Deployed histories are immutable. Add a new migration. |
| New non-noop `RunPython` or `RunSQL` | Add a reviewed module-level `UPDATE_SAFETY` declaration, including small tables, read-only payloads and imported helpers. Only exact Django forward/reverse noop pairs are exempt. |
| Risky schema operation on a registered existing high-volume table | Declare its resolved physical tables and risks. Real Django loader/model state and backend field parameters resolve these, not filename or class-name guesses. |
| Changed production source containing a schema/mutation statement in either blob | Add an exact before/after hash-bound human review to `clickhouse-change-reviews.json`. |
| New statement source outside the scan roots | Register its path/root in `clickhouse-sources.json`, then add its review. |
| Required env variable with no unattended value | Supply a Compose default or a real generator/entrypoint in `env-generators.json`, and document it in `env-reference.toml`. |
| Unsupported Compose service, mount, named volume, profile or restart policy | Update `automatic-layout.json`, runtime support and backup/restore coverage together. Only `unless-stopped` and string `"no"` restart policies are supported. |
| Unknown active app, squash/replacement, custom `MIGRATION_MODULES`, or unclassified operation | Update the fixed mapping/classifier and its tests, or keep that layout unsupported. |

Declarations cannot authorize a manual script, new credentials or an operator
step during an automatic update. Those transitions need genuinely unattended
bootstrap support and real qualification, or stay notify-only.

## Run the checks

Use the PR's actual base, normally `origin/dev`. The gate compares merge-base to
the working tree, including untracked files. Do not use `HEAD` to hide PR changes.

```sh
# Repository root, Python 3.9+ stdlib, serial and daemon-free.
python3 -m unittest discover -s deploy/tests -p test_update_safety_gate.py -v
DOCKER_CONTEXT=colima-fagi-update python3 -I scripts/check_update_safety.py \
  --static --base origin/dev --json

# From futureagi/, in the frozen real backend environment.
export DJANGO_SETTINGS_MODULE=tfc.settings.settings EE_LICENSE_KEY= ENV_TYPE=local
export NO_STARTUP_DB_MUTATIONS=true FI_SKIP_CH25_MIGRATION=1 LITELLM_MODE=PRODUCTION
uv run --no-sync python -m unittest discover -s tests -p test_update_safety_gate_django.py -v
uv run --no-sync python manage.py makemigrations --check --dry-run
uv run --no-sync python -I ../scripts/check_update_safety.py \
  --static --django --base origin/dev --json
uv run --no-sync python -I ../scripts/check_update_safety.py --calibrate --json
```

Do not run unittest discovery with `-I`: it removes import roots. Direct gate
scripts and child interpreters reading isolated historical sources use `-I`.
The required Backend CI aggregate depends on `update-safety`. That job installs
`uv sync --frozen --no-dev`, runs both gate test modules serially, runs real OSS
`makemigrations --check --dry-run`, and checks real production `INSTALLED_APPS`
and `MigrationLoader`. `tfc.settings.test` deliberately enables EE and is not the
OSS app inventory. Python, Go, SQL, shell, bootstrap and deployment changes must
trigger the job. Upgrade/restore E2E remains a separate requirement.

Layout validation renders all profiles with Compose >=2.30 `config` only,
without shell or `.env` overrides. It does not start containers or use the daemon.
`--compose-model FILE` accepts an equivalent pre-rendered model for fixtures;
it is not runtime admission. Local Docker commands select only the dedicated
`colima-fagi-update` context.

## Migration declarations and schema risks

Add a module-level **literal dictionary** to the new migration. This example is
only a format illustration; substitute the real reviewer and review discussion.
Never invent approval or copy example identities as evidence.

```python
UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:tracer_trace"],
    "mitigation": "Backfill idempotently in batches of 500 with a resumable cursor; measure duration, WAL/temp growth and restoration in the real rehearsal.",
    "reviewer": "reviewer-login",
    "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
    "large_volume_rehearsal": True,
}
```

All seven fields are required. Unknown/missing fields, duplicate keys or values,
nonliteral/nested/duplicate declarations, wildcard or empty tables, invalid risk
classes, review URLs or types fail. `schema_version` is integer `1`, not boolean;
`large_volume_rehearsal` is boolean `True`, not integer. Mitigation is 30–2000
characters. `reviewer` is an actual GitHub login and `review` is an actual PR
review-comment URL. These references are auditable, not machine proof of human
approval. Required code review verifies the mitigation and complete table roster.

Tables use `postgres:table_name` or `clickhouse:table_name`. Declare every actual
affected table, including unregistered small/non-model tables. No wildcard or
sentinel identities are accepted. The gate does not infer arbitrary code's table
roster. Risk classes are `type_change`, `not_null`, `volatile_default`, `index`,
`constraint`, `data_migration`, `table_rewrite`, `clickhouse_rewrite`,
`clickhouse_mutation`, `clickhouse_change`.

Every non-noop `RunPython`/`RunSQL` needs `data_migration`, regardless of payload
spelling. Only exact Django pairs are exempt:

```python
migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop)
migrations.RunSQL(migrations.RunSQL.noop, migrations.RunSQL.noop)
```

A missing reverse, user-defined noop or overridable subclass is not exempt.
`SeparateDatabaseAndState` and `RunSQL.state_operations` are traversed too.

On registered existing tables the gate resolves type/collation changes (including
inbound FKs), FK targets/`to_field`, NOT NULL additions without `db_default`,
nullability tightening, callable/volatile defaults, indexes, constraints,
generated columns and rewrites/destructive operations. **A nullable positive
integer column adds a backend CHECK that validates existing rows**: it needs
`constraint` even with `null=True` or constant `db_default=0`. Backend-generated
CHECK additions/changes, `AddConstraintNotValid` and `ValidateConstraint` are
classified. Unchanged existing CHECKs on help-text-only edits are not new risks.
A Python `default=0` is not `db_default=0`. Unmanaged/proxy aliases of a physical
registered table do not certify it as empty. Prefer **add a nullable column,
backfill in batches, add the index in its own migration**.

## ClickHouse statement review, not client/path review

`clickhouse-sources.json` defines production scan roots, not blanket review
requirements. A keyword scan of Python, Go, SQL, shell and extensionless
entrypoints finds ALTER, CREATE, DROP, RENAME, TRUNCATE, OPTIMIZE, ATTACH/DETACH,
ALTER TABLE UPDATE/DELETE mutations and lightweight DELETE FROM. Whole-line
source/SQL comments are ignored; literal statement-looking text is otherwise a
conservative alarm, not a SQL parser. Both base and candidate blobs are scanned,
so additions, removals and deletions of statements cannot disappear silently.

**Client calls/imports, MergeTree mentions, SELECT-only builders and path
registration alone require no declaration.** A changed file with a real schema
statement still needs one hash-bound review even if that particular edit is a
comment: the deliberate unit of review is the exact source blob. Split strings
and deliberately indirect code are outside this aid's threat model, not a reason
to turn every client call into a review requirement.

There is **no test-filename exemption**: production
`simulate/services/test_executor.py` and management commands with `_test` names
are scanned. Unpinned exclusions must name a concrete, verified test directory,
not `**/test_*.py`, `**/*_test.go` or wildcard test directories. Colocated test
files and genuinely non-ClickHouse false positives need an inspected exact blob
SHA-256 and rationale; an edit invalidates that pin. Django migration files route
to the immutable-history/real-loader declaration checks instead of requiring a
duplicate ClickHouse review. SQL helpers outside migration packages are scanned.

For each changed statement-bearing production file add this shape to
`deploy/update/clickhouse-change-reviews.json`:

```json
{
  "path": "futureagi/tracer/services/clickhouse/v2/schema/example.sql",
  "before_sha256": "<exact base blob SHA-256, or null for an addition>",
  "after_sha256": "<exact candidate blob SHA-256, or null for a deletion>",
  "declaration": {
    "schema_version": 1,
    "risk_classes": ["clickhouse_change", "clickhouse_mutation"],
    "tables": ["clickhouse:spans"],
    "mitigation": "Describe bounded mutation, lock and disk behavior and the actual upgrade/restore rehearsal for this change.",
    "reviewer": "reviewer-login",
    "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
    "large_volume_rehearsal": true
  }
}
```

Place rows in the file's `changes` list under `schema_version: 1`. Existing
blobs use 64 lowercase hex digits, not placeholders or normalized SQL hashes.
Resolve `git merge-base origin/dev HEAD`, hash `git show BASE:path` bytes and
the working-tree bytes. A rebase/edit invalidates stale hashes. The failure
message lists **every missing path, exact hashes and detected statement lines**,
and tells the human which declaration fields to add. No automatic reviewer
identity, approval URL or mitigation is generated.

## Env and shared contracts

Every required Standalone env row and Compose interpolation needs an explicit
`:-`/`-` default (including intentional empty) or an entry in the explicit
`env-generators.json` registry. Nested substitutions are independently checked;
bare/required/conditional interpolations are not defaults. Registry entries name
variables, source path, language, exact generator/installer entrypoint symbols,
availability and rationale. Tests locate actual function **definitions**, never
calls, log strings or a generated-section label; source is not executed. Shell
generator sources with heredocs are intentionally unsupported. Fresh-install
secret generation must never rotate retained installation credentials.

`migration-apps.json` pins AppConfig labels/modules/image-relative roots and OSS
conditionals. Update the fixed host observer with it. `high-volume-tables.json`
pins growth-prone PostgreSQL/ClickHouse tables and source rationales; do not
remove tables to silence a risk. `automatic-layout.json` pins services, mounts,
profiles, named volumes and complete physical/logical backup coverage; changing
expected JSON alone does not implement runtime/restore support.

## A9 measurement scope and developer cost

The gate's JSON keeps `new_migrations` and the existing
`large_volume_rehearsal_required` declaration list (`path`, complete `tables`,
`risk_classes`, `large_volume_rehearsal`). The declaration's true flag is a
commitment to the applicable rehearsal, **not a demand to seed every declared
table at scale**. The producer must intersect pending migrations' actual touched
tables with the high-volume registry. Only that intersection gets reference-scale
seeding and signed duration/throughput/disk-amplification/WAL/temp measurements.
Every other pending migration is still measured as executed in the normal real
`migration_forward` rehearsal on the witness corpus, without host-size scaling.
Backup and restore throughput are measured in every rehearsal. Static declarations
and source hashes are never measurements or product qualification.

`--calibrate` audits v1.48.0 through v1.55.0, starting at v1.47.1, using read-only
Git history and fresh isolated real historical Django-loader processes. It reports
per release added migrations, declaration files and ClickHouse statement-review
files/counts. The review cost uses the same keyword classifier, not every edit to
a client wrapper. A separate source inventory is explicitly not loader evidence.
If the locked dependencies/settings cannot initialize a historical loader, the
command exits nonzero with `status: blocked`, a real error and **null** exact
migration declaration cost, not an invented zero. Current exact-blob exclusions
may need fresh test/non-ClickHouse inspection for historical versions; those
conservative candidates remain visible. This is developer-cost visibility, never
real large-volume qualification or a claim that Phase 1 is complete.
