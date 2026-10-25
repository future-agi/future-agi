"""Report, finish or (explicitly) reverse the encryption of model-hub keys at rest.

Migration 0125 encrypts every model-hub key not stored as ciphertext.
This command runs the same code (the migration's own functions) for what the
migration could not do at its moment:

    manage.py model_hub_keys_at_rest --check
        Counts rows with a key still in the old encoding and rows whose key this
        deployment cannot decrypt (never a value), and says when
        INTEGRATION_ENCRYPTION_KEY is unset, made up for this process or not a
        valid Fernet key (never its value). Fails when any row is not
        encrypted, so a deploy step can gate on it.

    manage.py model_hub_keys_at_rest --encrypt
        Encrypts the rest: rows an old pod wrote during a rolling deploy, or every
        row of a local install that ran the migration before
        INTEGRATION_ENCRYPTION_KEY was set. Idempotent.

    manage.py model_hub_keys_at_rest --to-legacy --confirm
        The only way back: stores every key this deployment decrypts in the
        legacy encoding so an earlier release reads it, after which
        ``migrate model_hub 0124`` may run. Refuses without --confirm.
"""

import importlib

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

MIGRATION = "model_hub.migrations.0125_encrypt_model_hub_keys_at_rest"


class Command(BaseCommand):
    help = (
        "Report (--check), finish (--encrypt) or explicitly reverse "
        "(--to-legacy --confirm) the encryption of model-hub keys at rest."
    )

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument(
            "--check",
            action="store_true",
            help="Count rows not encrypted or not decryptable; fail if any is not encrypted.",
        )
        action.add_argument(
            "--encrypt",
            action="store_true",
            help="Encrypt every key still in the old encoding (idempotent).",
        )
        action.add_argument(
            "--to-legacy",
            action="store_true",
            help="Store every key in the old encoding again, for a rollback. Needs --confirm.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm --to-legacy: the keys are stored without encryption.",
        )

    def handle(self, *args, **options):
        migration = importlib.import_module(MIGRATION)
        if options["check"]:
            legacy, unreadable, checked = migration.count(apps)
            self.stdout.write(
                f"model_hub keys at rest: {legacy} not encrypted, {unreadable} "
                f"cannot be decrypted, {checked} checked."
            )
            problem = migration.key_problem()
            if problem:
                # --encrypt cannot encrypt anything until the key is valid.
                self.stdout.write(
                    f"model_hub keys at rest: INTEGRATION_ENCRYPTION_KEY {problem}; "
                    "keys are saved in the old encoding until it is fixed."
                )
            if legacy:
                fix = (
                    f"INTEGRATION_ENCRYPTION_KEY {problem}. {migration.key_fix()}"
                    if problem
                    else "run `manage.py model_hub_keys_at_rest --encrypt`."
                )
                raise CommandError(
                    f"{legacy} model-hub key row(s) are not encrypted; {fix}"
                )
            return
        if options["encrypt"]:
            migration.forwards(apps, None, out=self.stdout.write)
            return
        if not options["confirm"]:
            raise CommandError(
                "--to-legacy stores every model-hub key without encryption. It exists "
                "only to roll back to an earlier release. Add --confirm to run it."
            )
        migration.to_legacy(apps, out=self.stdout.write)
