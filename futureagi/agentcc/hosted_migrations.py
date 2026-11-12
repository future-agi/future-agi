"""Forward-only, release-specific hosted migration phases.

Selecting a hosted profile never invokes this module. Ops must independently
approve the synthetic rehearsal, backup, direct connection and quiet window.
Runtime rollout, key rotation and worker resumption are intentionally not SQL
phases and cannot be inferred from a successful command.

Schema stops at SCHEMA_BOUNDARY (0046_org_config_db_defaults), before encryption
and the webhook credential migrations. Backfill reaches all installed app leaves,
including RELEASE_BOUNDARY, and is the ONLY phase that applies webhook 0075-0077.
Check refuses any pending plan; schema reruns after backfill are forward-only noops.

Backfill requires AGENTCC_WEBHOOK_AT_REST_STALE_WORKER_FENCE: fence ALL
AgentccWebhook readers/writers, drain, and confirm stale workers terminated BEFORE
0075_webhook_credential_columns. Keep the fence through 0076 encryption and 0077
plaintext removal; resume only new-code workers afterward. The existing --quiesced
and --compatible-runtime-revision assertions attest these operator preconditions;
the command does not establish the fence or resume workers.
"""

from django.core.management.base import CommandError

from tfc.migration_bridge import LEGACY_APPLIED_NAMES

SCHEMA_BOUNDARY = ("prism", "0046_org_config_db_defaults")
# The single prism leaf this image installs: the hosted phases refuse an
# image whose prism graph ends anywhere else.
RELEASE_BOUNDARY = ("prism", "0089_normalize_two_key_org_period")
PHASES = ("schema", "backfill", "check")


def phase_targets(loader, phase):
    if phase not in PHASES:
        raise CommandError("Unknown hosted migration phase")
    leaves = loader.graph.leaf_nodes()
    if [node for node in leaves if node[0] == "prism"] != [RELEASE_BOUNDARY]:
        raise CommandError(
            "Hosted phase contract does not match this image's prism graph"
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
