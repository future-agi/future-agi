"""Bounded, restartable gateway-domain transition under the operator lock.

Caller owns a dedicated direct PostgreSQL session, the shared hosted-phase
advisory lock, approved quiescence and authorization. Each mutation batch is a
separate transaction; no signals, config push, timestamps or active pointers
are changed. A failure can leave committed target-key batches: remain quiesced
and rerun with the SAME key pair, then verify in a fresh target-key process.
"""

import time
from collections import Counter

from django.core.management.base import CommandError
from django.db import transaction

from agentcc.services.gateway_key_inventory import inventory


class TransitionRunner:
    def __init__(
        self,
        connection,
        crypto,
        *,
        batch_size=100,
        max_batches=1000,
        deadline_seconds=300,
    ):
        self.connection = connection
        self.crypto = crypto
        self.batch_size = batch_size
        self.max_batches = max_batches
        self.deadline = time.monotonic() + deadline_seconds

    def _budget(self):
        remaining = int((self.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise CommandError(
                "Gateway transition deadline reached; remain quiesced and resume"
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                [str(min(30000, remaining))],
            )

    def _query(self, store, after):
        query = (
            store.model._base_manager.using(self.connection.alias)
            .order_by("pk")
            .only("pk", *store.fields)
        )
        return query.filter(pk__gt=after) if after is not None else query

    def _scan(self, stores, crypto):
        counts = Counter()
        for store in stores:
            after = None
            while True:
                self._budget()
                rows = list(self._query(store, after)[: self.batch_size])
                if not rows:
                    break
                for row in rows:
                    self._budget()
                    _, kinds = store.transform(row, crypto)
                    counts.update(kinds)
                    counts["rows"] += 1
                after = rows[-1].pk
        return counts

    def run(self, *, apply=False, verify=False):
        if (
            self.connection.vendor != "postgresql"
            or self.connection.in_atomic_block
            or not self.connection.get_autocommit()
        ):
            raise CommandError(
                "Gateway transition requires an independent direct PostgreSQL session"
            )
        if apply and verify:
            raise CommandError("Gateway key verification is read-only")
        stores = inventory()
        # Full inventory, not just active rows or rows in the first batch. Any
        # corruption/missing table/unsupported storage refuses before any write.
        before = self._scan(stores, self.crypto.verifier() if verify else self.crypto)
        result = {
            "scope": "gateway-fernet-v1",
            "inventory_stores": len(stores),
            "rows": before["rows"],
            "source": before["source"],
            "target": before["target"],
            "empty": before["empty"],
            "changed_rows": 0,
            "committed_batches": 0,
            "complete": not apply,
            "verified": verify,
        }
        if not apply:
            return result
        for store in stores:
            after = None
            while True:
                self._budget()
                with transaction.atomic(using=self.connection.alias):
                    rows = list(
                        self._query(store, after).select_for_update(nowait=True)[
                            : self.batch_size
                        ]
                    )
                    if not rows:
                        break
                    updates = []
                    for row in rows:
                        self._budget()
                        changes, _ = store.transform(row, self.crypto)
                        if changes:
                            updates.append((row.pk, changes))
                    if updates and result["committed_batches"] >= self.max_batches:
                        return result  # incomplete, never a qualification receipt
                    for pk, changes in updates:
                        self._budget()
                        # Bypass model.save and all post_save/push hooks. Locked
                        # columns only; tenant/version/policy bindings stay put.
                        changed = (
                            store.model._base_manager.using(self.connection.alias)
                            .filter(pk=pk)
                            .update(**changes)
                        )
                        if changed != 1:
                            raise CommandError(
                                "Gateway inventory changed while quiesced"
                            )
                if updates:
                    result["changed_rows"] += len(updates)
                    result["committed_batches"] += 1
                after = rows[-1].pk
        after = self._scan(stores, self.crypto.verifier())
        if (
            after["rows"] != before["rows"]
            or after["target"] != before["source"] + before["target"]
            or after["empty"] != before["empty"]
        ):
            raise CommandError("Gateway inventory changed while quiesced")
        result.update(complete=True, verified=True)
        return result
