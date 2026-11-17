"""Operate the Hub's bundle storage (HUB-19): status, provisioning, versions and restore.

    manage.py omega_hub_storage status                 read-only; exit 1 when the bucket is not ready
    manage.py omega_hub_storage provision              create or complete the bucket's configuration
    manage.py omega_hub_storage versions --key K       list a key's retained versions
    manage.py omega_hub_storage restore --key K --version V

`restore` and `versions` need object access only, so an operator holding the Hub's object-scoped
credential can run them; `provision` may need bucket-admin rights (ops/iam/provision-policy.json).
Every subcommand runs as an operator job (ops/RUNBOOK.md); the startup guard refuses it elsewhere.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from omega_hub import conf
from omega_hub.services import storage, storage_provision


class Command(BaseCommand):
    help = "Show, provision, list versions of, or restore a version in the Omega Hub's storage."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("status", help="Read-only bucket check; exit 1 when it is not ready.")
        sub.add_parser("provision", help="Create or complete the bucket (versioning, object lock, lifecycle).")
        versions = sub.add_parser("versions", help="List the retained versions of one key.")
        versions.add_argument("--key", required=True)
        restore = sub.add_parser("restore", help="Make a retained version the current object again.")
        restore.add_argument("--key", required=True)
        restore.add_argument("--version", dest="version_id", required=True)

    def handle(self, *args, action, **options):
        store = storage.get_store()
        if action in ("status", "provision"):
            if conf.storage_kind() != "object":
                storage_provision.remember({"ready": True})
                self.stdout.write(json.dumps({"kind": conf.storage_kind(), "ready": True}))
                return
            try:
                result = store.provision() if action == "provision" else store.status()
            except storage_provision.StorageNotProvisioned as exc:
                storage_provision.remember({"ready": False})
                raise CommandError(str(exc)) from exc
            # Uploads and checker claims pause while the bucket is known not ready; a fix confirmed
            # here resumes them now rather than at the next five-minute operations check.
            storage_provision.remember(result)
            self.stdout.write(json.dumps(result, sort_keys=True))
            if not result.get("ready"):
                raise CommandError(f"bucket {result.get('bucket')} is not ready: {'; '.join(result['problems'])}")
            return
        key = options["key"]
        if action == "versions":
            found = store.versions(key)
            self.stdout.write(f"{key}: {len(found)} versions")
            for item in found:
                self.stdout.write(item)
            return
        version_id = options["version_id"]
        if version_id not in store.versions(key):
            raise CommandError(f"{key} has no retained version {version_id}")
        store.restore(key, version_id)
        self.stdout.write(f"restored {key} from version {version_id}")
