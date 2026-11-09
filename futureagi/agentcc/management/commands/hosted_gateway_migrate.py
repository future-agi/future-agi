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
from agentcc.services.gateway_key_operator import (
    KEY_PHASES,
    add_key_arguments,
    key_operation,
)


class Command(BaseCommand):
    help = "Plan/check hosted schema or backfill phases; --apply requires explicit operator assertions"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("phase", choices=PHASES + KEY_PHASES)
        add_key_arguments(parser)
        add_schema_deadline_argument(parser)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--quiesced", action="store_true")
        parser.add_argument("--compatible-runtime-revision", default="")

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
        if options["apply"] and phase == "check":
            raise CommandError("The check phase is read-only")
        if options["apply"]:
            if not options["quiesced"]:
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
        lock_timeout = options["key_lock_timeout_ms"] if key_op else 2000
        read_only = (
            " -c default_transaction_read_only=on"
            if key_op and not options["apply"]
            else ""
        )
        connection.settings_dict["OPTIONS"] = {
            **prior_options,
            "connect_timeout": 2,
            "options": f"-c lock_timeout={lock_timeout} -c statement_timeout=30000{read_only}",
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
        executor = MigrationExecutor(
            connection, progress_callback=MigrationProgress(self.stdout)
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
