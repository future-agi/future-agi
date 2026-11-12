"""Inspect and replay the Hub's dead-letter email rows (HUB-18).

    manage.py omega_hub_outbox --list            dead rows: id, template, attempts, last error (no bodies)
    manage.py omega_hub_outbox --id <uuid>       replay one dead row
    manage.py omega_hub_outbox --all-dead        replay every dead row

A replay sends the row's frozen subject and body again and writes one `outbox.replayed` audit
event. It runs as an operator job (ops/RUNBOOK.md); the startup guard refuses it elsewhere.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from omega_hub.models import EmailOutbox, EmailOutboxStatus
from omega_hub.services import audit
from omega_hub.services.outbox import dispatch


class Command(BaseCommand):
    help = "List or replay Omega Hub dead-letter email rows."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--list", action="store_true", dest="list_dead")
        group.add_argument("--id", dest="row_id")
        group.add_argument("--all-dead", action="store_true")

    def handle(self, *args, list_dead=False, row_id=None, all_dead=False, **options):
        rows = EmailOutbox.objects.filter(status=EmailOutboxStatus.DEAD).order_by("dead_at")
        if list_dead:
            for row in rows:
                self.stdout.write(json.dumps({"id": str(row.pk), "template": row.template, "attempts": row.attempts,
                                              "deadAt": row.dead_at.isoformat() if row.dead_at else None,
                                              "lastError": row.last_error}))
            self.stdout.write(f"{rows.count()} dead-letter row(s)")
            return
        if row_id:
            rows = rows.filter(pk=row_id)
        ids = [str(pk) for pk in rows.values_list("pk", flat=True)]
        if row_id and not ids:
            raise CommandError(f"dead-letter row {row_id} was not found")
        EmailOutbox.objects.filter(pk__in=ids).update(
            status=EmailOutboxStatus.PENDING,
            attempts=0,
            next_attempt_at=timezone.now(),
            dead_at=None,
            last_error="",
        )
        if ids:
            audit.record("outbox.replayed", target_type="email_outbox", target_id=ids[0] if row_id else "dead",
                         data={"rows": ids[:100], "count": len(ids)})
        for current_id in ids:
            dispatch(current_id)
        self.stdout.write(f"replayed {len(ids)} dead-letter row(s)")
