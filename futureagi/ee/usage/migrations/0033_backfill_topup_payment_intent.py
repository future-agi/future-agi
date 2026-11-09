"""Record the PaymentIntent on top-ups R's pre-2a apply_topup_pi wrote.

R's apply_topup_pi credited a PaymentIntent under the key ``stripe_pi:<pi>``
with ``stripe_ref`` <pi>, and usage/0031 added ``stripe_payment_intent_id``
with default '' and no backfill. A later session or invoice naming that
PaymentIntent did not find it, and credited the same money again (2A-b
verify, adv6). This fills the column from ``stripe_ref`` on those rows
(ruling 513.3), so the partial unique ``mc_entry_topup_one_payment_intent``
and ``fulfil_topup``'s PaymentIntent lookup both see them.

Only a ``topup`` row whose key is ``stripe_pi:<stripe_ref>``, whose column is
blank and whose ``stripe_ref`` is a PaymentIntent id (``pi_[A-Za-z0-9_]+``, at
most 255, no ``_secret_``) is filled. A row whose PaymentIntent another top-up
already holds is left blank and printed: that money was credited twice before
the link was known, and filling it would break the unique constraint. The rule
is frozen here, so later service changes never change what this did.

Reverse is a no-op: the filled ids are true, and blanking them would reopen
the double credit.
"""

import re

from django.db import DEFAULT_DB_ALIAS, migrations

from tfc.migration_database import executor_alias_scope

_PREFIX = "stripe_pi:"
_PAYMENT_INTENT_ID = re.compile(r"pi_[A-Za-z0-9_]+")
_CLIENT_SECRET = re.compile(r"_secret_", re.IGNORECASE)


def _not_fillable(key: str, ref: str) -> str | None:
    if key != _PREFIX + ref:
        return "stripe_ref is not the key's id"
    if len(ref) > 255 or not _PAYMENT_INTENT_ID.fullmatch(ref):
        return "stripe_ref is not a PaymentIntent id"
    if _CLIENT_SECRET.search(ref):
        return "stripe_ref is a client secret"
    return None


def _fill(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        db = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        Entry = apps.get_model("usage", "ManagedCreditEntry")
        rows = list(
            Entry.objects.using(db).filter(
                kind="topup",
                idempotency_key__startswith=_PREFIX,
                stripe_payment_intent_id="",
            )
            .order_by("created_at", "id")
            .values_list("pk", "idempotency_key", "stripe_ref")
        )
        filled = left = 0
        for pk, key, ref in rows:
            reason = _not_fillable(key, ref)
            if reason is None and Entry.objects.using(db).filter(kind="topup", stripe_payment_intent_id=ref).exists():
                reason = "another top-up already holds this PaymentIntent (credited twice)"
            if reason is not None:
                left += 1
                # The entry id only: a bad ref may be a client secret, never printed.
                print(f"usage.0033: top-up {pk} left without a PaymentIntent: {reason}")
                continue
            filled += Entry.objects.using(db).filter(pk=pk, stripe_payment_intent_id="").update(
                stripe_payment_intent_id=ref
            )
        if rows:
            print(f"usage.0033: {filled} top-up(s) given their PaymentIntent, {left} left blank")


class Migration(migrations.Migration):
    dependencies = [("usage", "0032_ledger_opening_buckets")]

    operations = [migrations.RunPython(_fill, migrations.RunPython.noop)]
