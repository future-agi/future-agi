"""Explicit hosted phases; no hook, shell, --fake or reverse operation."""

import argparse
import json
import os
import re
import time

from django.core.management.base import BaseCommand, CommandError
from django.core.management.sql import emit_post_migrate_signal
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from agentcc.hosted import deployment_profile, validate_hosted_configuration
from agentcc.hosted_migration_progress import (
    MigrationProgress,
    add_schema_deadline_argument,
    schema_deadline_seconds,
    schema_phase_deadline,
)
from agentcc.hosted_migrations import PHASES, phase_targets, validate_plan
from agentcc.hosted_seed_state import verify_seed_state
from agentcc.management.commands.agentcc_check_request_log_indexes import (
    check_request_log_indexes,
)
from agentcc.services.gateway_key_operator import (
    KEY_PHASES,
    add_key_arguments,
    key_operation,
)

# The update's stop check as a phase, so the hosted Job (which runs only
# hosted_gateway_migrate <phase>) can run it after schema and before backfill:
# agentcc_check_request_log_indexes's lines and exit, in a read-only session. It
# is not part of check, which refuses a pending plan and runs after backfill.
VERIFY_INDEXES = "verify-indexes"


# A phase's PostgreSQL session limits, in milliseconds (0: none). A plan (no
# --apply) and the check phase run beside a live system and keep short limits.
# An applied phase on a quiesced system (--apply --quiesced) has nothing live to
# wait behind, so a long migration is not cancelled part-way: no statement limit,
# and a 60-second lock wait so a forgotten live writer surfaces as an error rather
# than an endless wait. The schema phase's wall-clock deadline
# (--schema-deadline-seconds) stays the outer bound. Key phases keep their own lock
# wait (--key-lock-timeout-ms).
LIVE_LOCK_TIMEOUT_MS = 2000
LIVE_STATEMENT_TIMEOUT_MS = 30000
QUIESCED_LOCK_TIMEOUT_MS = 60000


def session_limits(options, key_op):
    """(lock_timeout, statement_timeout) in milliseconds for the phase's session."""
    if key_op:
        return options["key_lock_timeout_ms"], LIVE_STATEMENT_TIMEOUT_MS
    if options["apply"] and options["quiesced"]:
        return QUIESCED_LOCK_TIMEOUT_MS, 0
    return LIVE_LOCK_TIMEOUT_MS, LIVE_STATEMENT_TIMEOUT_MS


def session_limits_text(connection):
    """The statement limit and lock wait in force on ``connection``'s session, as
    PostgreSQL reports them, for each migration's started line."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW statement_timeout")
        statement = cursor.fetchone()[0]
        cursor.execute("SHOW lock_timeout")
        lock = cursor.fetchone()[0]
    return f"statement_timeout={statement} lock_timeout={lock}"


class Command(BaseCommand):
    help = (
        "Plan/check hosted phases: schema stops at 0046_org_config_db_defaults; "
        "backfill reaches all installed app leaves and is the ONLY phase applying "
        "webhook migrations 0075-0077; check refuses any pending plan. "
        "All phases are forward-only; schema reruns after backfill are noops. "
        "Before backfill starts, every web and worker process of the previous "
        "backend image is at zero and an operator count of them printed 0. That "
        "includes AGENTCC_WEBHOOK_AT_REST_STALE_WORKER_FENCE: fence all "
        "AgentccWebhook readers/writers, drain and confirm stale workers terminated "
        "BEFORE 0075. Keep the fence through 0076 encryption and 0077 plaintext "
        "removal; start new-code workers only after backfill. --apply requires "
        "explicit operator assertions, and backfill --apply refuses without "
        "--quiesced; this command does not stop processes, fence or resume workers. "
        "verify-indexes is read only: the request-log index check "
        "(agentcc_check_request_log_indexes), run after schema and before backfill."
    )
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("phase", choices=PHASES + (VERIFY_INDEXES,) + KEY_PHASES)
        add_key_arguments(parser)
        add_schema_deadline_argument(parser)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--quiesced",
            action="store_true",
            help=(
                "Assert approved write/worker/schedule quiescence. For backfill, also "
                "attest that every web and worker process of the previous backend "
                "image is at zero (an operator count printed 0), including "
                "AGENTCC_WEBHOOK_AT_REST_STALE_WORKER_FENCE: all readers/writers "
                "fenced, drained and stale workers terminated before webhook 0075."
            ),
        )
        parser.add_argument(
            "--compatible-runtime-revision",
            default="",
            help=(
                "Approved new-code runtime revision for backfill; keep the fence "
                "through webhook 0076/0077 and resume only new-code workers afterward."
            ),
        )

    def handle(self, *args, **options):
        from model_hub.apps import explicit_management_mutation_authorized

        if deployment_profile() != "hosted":
            raise CommandError("This operator command requires the hosted profile")
        validate_hosted_configuration()
        if not explicit_management_mutation_authorized(
            ["manage.py", "hosted_gateway_migrate"]
        ):
            raise CommandError(
                "Hosted phases require the dedicated bootstrap/operator mechanism"
            )
        phase = options["phase"]
        # call_command keyword options bypass argparse's type conversion.
        try:
            schema_seconds = schema_deadline_seconds(options["schema_deadline_seconds"])
        except argparse.ArgumentTypeError as error:
            raise CommandError(str(error)) from None
        key_op = key_operation(options) if phase in KEY_PHASES else None
        if options["apply"] and phase in ("check", VERIFY_INDEXES):
            raise CommandError(f"The {phase} phase is read-only")
        if options["apply"]:
            if not options["quiesced"]:
                if phase == "backfill":
                    # An old-image process writing between two backfill
                    # migrations can make the later one refuse; recovery is a
                    # restore. Backfill starts only with that image at zero.
                    raise CommandError(
                        "Backfill needs every web and worker process of the previous "
                        "backend image stopped first; confirm the count of running "
                        "old-image processes is 0, then pass --quiesced"
                    )
                raise CommandError(
                    "Assert approved write/worker/schedule quiescence with --quiesced"
                )
            revision = os.environ.get("AGENTCC_HOSTED_RELEASE_REVISION", "")
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise CommandError(
                    "AGENTCC_HOSTED_RELEASE_REVISION must bind the reviewed image"
                )
            if (
                phase == "backfill"
                and options["compatible_runtime_revision"] != revision
            ):
                raise CommandError(
                    "Backfill requires the approved compatible runtime revision"
                )
        if "default_direct" not in connections.databases or not os.getenv(
            "PG_DIRECT_HOST"
        ):
            raise CommandError(
                "PG_DIRECT_HOST/default_direct is required; never migrate through a pooler"
            )
        connection = connections["default_direct"]
        if connection.vendor != "postgresql":
            raise CommandError("Hosted phases require PostgreSQL")
        connection.close()
        prior_options = connection.settings_dict.get("OPTIONS", {}).copy()
        lock_timeout, statement_timeout = session_limits(options, key_op)
        read_only = (
            " -c default_transaction_read_only=on"
            if (key_op and not options["apply"]) or phase == VERIFY_INDEXES
            else ""
        )
        connection.settings_dict["OPTIONS"] = {
            **prior_options,
            "connect_timeout": 2,
            "options": (
                f"-c lock_timeout={lock_timeout} "
                f"-c statement_timeout={statement_timeout}{read_only}"
            ),
        }
        started = time.monotonic()
        try:
            with schema_phase_deadline(schema_seconds if phase == "schema" else None):
                if phase == "schema":
                    self.stdout.write(f"schema-phase deadline_seconds={schema_seconds}")
                    self.stdout.flush()
                self.run_phase(connection, phase, key_op, options)
        except CommandError:
            raise
        except Exception:
            raise CommandError(
                "Hosted phase failed; preserve applied-state metadata and rehearse recovery"
            ) from None
        finally:
            # Session-level advisory lock is released even on partial backfill failure.
            connection.close()
            connection.settings_dict["OPTIONS"] = prior_options
            if phase == "schema":
                self.stdout.write(
                    f"schema-phase elapsed_seconds={time.monotonic() - started:.3f}"
                )
                self.stdout.flush()

    def run_phase(self, connection, phase, key_op, options):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtext('futureagi-hosted-phases'))"
            )
            if not cursor.fetchone()[0]:
                raise CommandError("Another hosted migration operator holds the lock")
        if phase == VERIFY_INDEXES:
            # Under the operator lock, so it never reads an index a running
            # schema phase is still building.
            check_request_log_indexes(connection, self.stdout.write)
            return
        executor = MigrationExecutor(
            connection,
            progress_callback=MigrationProgress(
                self.stdout, limits=lambda: session_limits_text(connection)
            ),
        )
        executor.loader.check_consistent_history(connection)
        if key_op:
            # Key transitions are not schema/backfill phases and never migrate.
            targets = phase_targets(executor.loader, "check")
            validate_plan(executor.loader, executor.migration_plan(targets), "check")
            from agentcc.services.gateway_key_transition import TransitionRunner

            crypto, revision = key_op
            result = TransitionRunner(
                connection,
                crypto,
                batch_size=options["key_batch_size"],
                max_batches=options["key_max_batches"],
                deadline_seconds=options["key_deadline_seconds"],
            ).run(apply=options["apply"], verify=phase == "key-verify")
            self.stdout.write(
                json.dumps(
                    {
                        **result,
                        "revision": revision,
                        "phase": phase,
                        "apply": options["apply"],
                    },
                    sort_keys=True,
                )
            )
            if not result["complete"]:
                raise CommandError(
                    "Gateway batch limit reached; remain quiesced and resume with the same keys"
                )
            self.stdout.write(
                "Gateway-only inventory; shared key retirement and runtime resumption NOT authorized"
            )
            return
        targets = phase_targets(executor.loader, phase)
        plan = executor.migration_plan(targets)
        validate_plan(executor.loader, plan, phase)
        self.stdout.write(f"phase={phase} pending={len(plan)} apply={options['apply']}")
        if options["apply"]:
            post_migrate_state = executor.migrate(targets, plan=plan)
            # The original loader's applied set predates this run.
            refreshed = MigrationExecutor(connection)
            if refreshed.migration_plan(targets):
                raise CommandError("Phase did not reach its target")
            if phase == "backfill":
                # Backfill is the final schema-changing phase (all app leaves).
                # Match Django's migrate, including no-op apply for recovery:
                # receivers remain responsible for their idempotent seed data.
                post_migrate_state.clear_delayed_apps_cache()
                emit_post_migrate_signal(
                    options.get("verbosity", 1),
                    False,
                    connection.alias,
                    stdout=self.stdout,
                    apps=post_migrate_state.apps,
                    plan=plan,
                )
                verify_seed_state(post_migrate_state.apps, connection.alias)
        elif phase == "check":
            verify_seed_state(executor.loader.project_state().apps, connection.alias)
        result = (
            "Phase applied"
            if options["apply"]
            else (
                "Schema graph checked"
                if phase == "check"
                else "Phase planned, not applied"
            )
        )
        self.stdout.write(
            result
            + "; no worker resumption, key rotation or release activation performed"
        )
