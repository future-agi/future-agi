"""Forward-only, release-specific hosted migration phases.

Selecting a hosted profile never invokes this module. Ops must independently
approve the synthetic rehearsal, backup, direct connection and quiet window.
Runtime rollout, key rotation and worker resumption are intentionally not SQL
phases and cannot be inferred from a successful command.

Schema stops at SCHEMA_BOUNDARY (0046_org_config_db_defaults), before encryption
and the webhook credential migrations. Backfill reaches all installed app leaves,
including RELEASE_BOUNDARY, and is the ONLY phase that applies webhook 0075-0077.
It also applies 0090 and 0091, which add the request log's nullable error_code
and error_source columns. Adding them is an expand-only change: the previous code
never names those columns. That does not let previous-image processes keep
running through backfill; see the precondition below.
Check refuses any pending plan; schema reruns after backfill are forward-only noops.

Backfill has one precondition: before it starts, every web and worker process of
the previous backend image is at zero, and an operator count of running
previous-image processes printed 0. The webhook fence is part of the same
precondition (AGENTCC_WEBHOOK_AT_REST_STALE_WORKER_FENCE): fence ALL
AgentccWebhook readers/writers, drain, and confirm stale workers terminated BEFORE
0075_webhook_credential_columns. Keep the fence through 0076 encryption and 0077
plaintext removal. Workers running this code start only after backfill.
--quiesced attests this precondition, and the command refuses backfill --apply
without it; --compatible-runtime-revision binds the approved new-code revision.
The command does not stop processes, establish the fence or resume workers.
"""

from django.core.management.base import CommandError

from tfc.migration_bridge import LEGACY_APPLIED_NAMES

SCHEMA_BOUNDARY = ("prism", "0046_org_config_db_defaults")
# The single prism leaf this image installs: the hosted phases refuse an
# image whose prism graph ends anywhere else. A new prism migration moves it.
RELEASE_BOUNDARY = ("prism", "0091_request_log_error_source")
PHASES = ("schema", "backfill", "check")


def phase_targets(loader, phase):
    if phase not in PHASES:
        raise CommandError("Unknown hosted migration phase")
    leaves = loader.graph.leaf_nodes()
    prism_leaves = [node for node in leaves if node[0] == "prism"]
    if prism_leaves != [RELEASE_BOUNDARY]:
        raise CommandError(
            "Hosted phase contract does not match this image's prism graph: "
            f"RELEASE_BOUNDARY in agentcc/hosted_migrations.py is {RELEASE_BOUNDARY[1]}, "
            f"the image's prism leaf is {', '.join(name for _, name in prism_leaves) or 'none'}. "
            "A prism migration was added or removed without moving RELEASE_BOUNDARY."
        )
    if phase == "schema":
        # Re-running the phase after backfill must not plan a reverse migration
        # to the earlier boundary. Its dependencies have already been applied.
        return [] if SCHEMA_BOUNDARY in loader.applied_migrations else [SCHEMA_BOUNDARY]
    return leaves


def validate_plan(loader, plan, phase):
    if any(backwards for _, backwards in plan):
        raise CommandError("Hosted phases never reverse applied migrations")
    known = set(loader.disk_migrations) | set(LEGACY_APPLIED_NAMES.values())
    if any(
        node[0] == "prism" and node not in known for node in loader.applied_migrations
    ):
        raise CommandError(
            "Unknown applied prism migration; applied-state review required"
        )
    before = set(loader.graph.forwards_plan(SCHEMA_BOUNDARY))
    if phase == "schema" and any(
        migration.app_label == "prism"
        and (migration.app_label, migration.name) not in before
        for migration, _ in plan
    ):
        raise CommandError("Schema phase would cross the encryption boundary")
    if phase == "backfill" and SCHEMA_BOUNDARY not in loader.applied_migrations:
        raise CommandError("Schema phase must complete before the backfill")
    if phase == "check" and plan:
        raise CommandError("Installed migration graph is not fully applied")
