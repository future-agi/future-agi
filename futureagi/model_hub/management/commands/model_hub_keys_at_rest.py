"""Report, finish or (explicitly) reverse RF19's encryption of model-hub keys.

Migration 0125 encrypts every model-hub key stored in the old base64 encoding.
This command runs the same code (the migration's own functions) for what the
migration could not do at its moment:

    manage.py model_hub_keys_at_rest --check
        Counts rows with a key still in the old encoding and rows whose key this
        deployment cannot decrypt (never a value), and says when
        INTEGRATION_ENCRYPTION_KEY is unset, made up for this process or not a
        valid Fernet key (never its value). Fails when any row is not
        encrypted, so a deploy step can gate on it. Also names every key an
        earlier release wrapped (see --repair-wrapped), without failing on
        them: no command can bring those back.

    manage.py model_hub_keys_at_rest --encrypt
        Encrypts the rest: rows an old pod wrote during a rolling deploy, or every
        row of a local install that ran the migration before
        INTEGRATION_ENCRYPTION_KEY was set. Idempotent.

    manage.py model_hub_keys_at_rest --repair-wrapped [--confirm]
        A process of an earlier release that saves a row this release
        encrypted wraps the stored ``enc::`` token in the old encoding, and the
        app then refuses that key. The same stored form comes from a person
        typing another row's stored token as a key into such a process. The
        token names no row (it holds only ``{"v": key}``), so nothing proves
        a wrapped token is its own row's, and unwrapping one could hand an
        organization another organization's key. Every wrapped key is
        therefore refused and left as it is: the command names each one by
        model, row id, organization id and field (never a token or a key), so
        its organization can enter the key again. --confirm writes only what
        it can prove, which is nothing today.

    manage.py model_hub_keys_at_rest --to-legacy --confirm
        The only way back: stores every key this deployment decrypts in the old,
        weaker encoding so a release before RF19 reads it, after which
        ``migrate model_hub 0124`` may run. Refuses without --confirm.
"""

import importlib

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

MIGRATION = "model_hub.migrations.0125_encrypt_model_hub_keys_at_rest"

# Why a wrapped key is refused. Neither names a value.
NOT_PROVEN = (
    "the token it holds names no row, so it cannot be proven to be this row's own"
)
NOT_DECRYPTABLE = "the token it holds is not one this deployment can decrypt"


def wrapped_keys(migration):
    """Every key field holding a value an earlier release wrapped: the old
    encoding of an ``enc::`` token, which the reader refuses (the migration's
    and ``key_at_rest.read``'s read-once rule). One ``(model, row id,
    organization id, field, reason)`` per row and field, soft-deleted rows
    included, in table and primary-key order. Reads only."""
    manager = migration._credential_manager()
    prefix = migration.ENCRYPTED_PREFIX
    found = []
    for app, model_name, fields in migration.KEY_COLUMNS:
        model = apps.get_model(app, model_name)
        rows = (
            model._base_manager.filter(migration._has_value(fields))
            .order_by("pk")
            .values_list("pk", "organization_id", *fields)
        )
        for pk, org_id, *values in rows.iterator(chunk_size=migration.BATCH_SIZE):
            for name, value in zip(fields, values, strict=True):
                reasons = set()
                for stored in migration._leaves(value):
                    kind, clear = migration._read_once(manager, stored)
                    if kind != migration.LEGACY or not migration._is_token(clear):
                        continue
                    reasons.add(_why_refused(manager, clear[len(prefix) :]))
                if reasons:
                    reason = min(reasons, key=_REASON_ORDER.index)
                    found.append((model_name, str(pk), str(org_id), name, reason))
    return found


# The reason a field gives when its strings differ: the one an operator acts on
# first.
_REASON_ORDER = (NOT_DECRYPTABLE, NOT_PROVEN)


def _why_refused(manager, token):
    """Why a wrapped ``token`` is refused. Its clear key is decrypted only to
    tell the two cases apart, and never kept."""
    try:
        data = manager.decrypt(token.encode("utf-8"))
    except Exception:
        return NOT_DECRYPTABLE
    if isinstance(data, dict) and isinstance(data.get("v"), str):
        return NOT_PROVEN
    return NOT_DECRYPTABLE


class Command(BaseCommand):
    help = (
        "Report (--check), finish (--encrypt), list keys an earlier release "
        "wrapped (--repair-wrapped) or explicitly reverse (--to-legacy "
        "--confirm) the encryption of model-hub keys at rest."
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
            "--repair-wrapped",
            action="store_true",
            help=(
                "List every key an earlier release wrapped, by row and "
                "organization; with --confirm, rewrite those proven to be "
                "their row's own (none can be proven today)."
            ),
        )
        action.add_argument(
            "--to-legacy",
            action="store_true",
            help="Store every key in the old encoding again, for a rollback. Needs --confirm.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help=(
                "Confirm --to-legacy (the keys become readable by anyone who "
                "reads the table) or --repair-wrapped."
            ),
        )

    def _report_wrapped(self, found):
        """One line per refused key: model, row id, organization id, field and
        the reason. Never a token or a key."""
        for model_name, pk, org_id, name, reason in found:
            self.stdout.write(
                f"model_hub keys at rest: refused {model_name} {pk} "
                f"(organization {org_id}) {name}: {reason}; its organization "
                "must enter the key again."
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
            found = wrapped_keys(migration)
            if found:
                self.stdout.write(
                    f"model_hub keys at rest: {len({f[:2] for f in found})} row(s) "
                    "hold a key an earlier release wrapped; the app cannot read "
                    "them and no command can recover them."
                )
                self._report_wrapped(found)
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
        if options["repair_wrapped"]:
            problem = migration.key_problem()
            if problem:
                self.stdout.write(
                    f"model_hub keys at rest: INTEGRATION_ENCRYPTION_KEY {problem}; "
                    "a wrapped token cannot be checked against it."
                )
            found = wrapped_keys(migration)
            rows = len({f[:2] for f in found})
            self.stdout.write(
                f"model_hub keys at rest: {rows} row(s) hold a key an earlier "
                "release wrapped."
            )
            self._report_wrapped(found)
            if options["confirm"]:
                # Rewrite only a key proven to be its row's own. A token holds
                # no row identity, so no wrapped key can be proven and none is
                # written.
                self.stdout.write(
                    f"model_hub keys at rest: 0 row(s) repaired, {rows} refused; "
                    "nothing was written."
                )
            else:
                self.stdout.write(
                    "model_hub keys at rest: listed only; nothing was written."
                )
            return
        if not options["confirm"]:
            raise CommandError(
                "--to-legacy stores every model-hub key in the old base64 "
                "encoding, which anyone who reads the table can decode. It exists "
                "only to roll back to a release before RF19. Add --confirm to run it."
            )
        migration.to_legacy(apps, out=self.stdout.write)
