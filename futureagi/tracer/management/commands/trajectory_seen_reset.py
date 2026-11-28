"""Make every collector forget which trajectory items it has stored.

Run after restoring ClickHouse to an earlier point: the collectors' seen sets
may still list items persisted after the backup, and a collector trusting them
would write spans that point at items the restore removed.

It sets a new ``traj:epoch`` (collectors that read the epoch stop reusing
anything seen under the old one within seconds), then removes every
``traj:seen:*`` key with SCAN and UNLINK. Prints counts only. After it, each
active item is written once more, which costs storage, not data.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from tracer.services.trajectory_seen_contract import open_seen_redis, reset_seen_store


class Command(BaseCommand):
    help = "Start a new trajectory seen-store epoch and clear all seen sets."

    def handle(self, *args, **options):
        client = open_seen_redis()
        try:
            _epoch, removed = reset_seen_store(client)
        finally:
            client.close()
        self.stdout.write(f"New seen-store epoch set; removed {removed} seen-set keys.")
