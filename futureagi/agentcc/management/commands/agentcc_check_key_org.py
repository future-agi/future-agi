"""Report, and with --fix repair, API keys bound to an org other than their own.

A key's metadata names only its own org and no reserved name
(``RESERVED_KEY_METADATA``). The command lists each key whose metadata does
not, and each key created from another org. Dry run by default.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from accounts.models.organization_membership import OrganizationMembership
from agentcc.models import AgentccAPIKey
from agentcc.services.auth_bridge import bound_key_metadata
from agentcc.validators import reserved_key_metadata


class Command(BaseCommand):
    help = (
        "Find API keys whose metadata names another org or carries reserved "
        "names (authority such as type or role, or identity such as id or "
        "name), and keys created from another org. Dry run by default; --fix "
        "binds the metadata back to the key's org and drops the reserved names."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true", help="Repair the metadata of the keys found."
        )
        parser.add_argument("--org", help="Only check this organization's keys.")

    def handle(self, *args, **options):
        keys = AgentccAPIKey.no_workspace_objects.select_related(
            "user", "project"
        ).order_by("created_at")
        if options["org"]:
            try:
                keys = keys.filter(organization_id=uuid.UUID(options["org"]))
            except ValueError as exc:
                raise CommandError("--org must be an organization ID") from exc
        keys = list(keys)

        repairable = []
        to_review = []
        in_org = self._creator_in_org(keys)
        for key in keys:
            problems = _metadata_problems(key)
            if problems:
                repairable.append(key)
                self._report(key, problems)
            attribution = _attribution_problems(key, in_org)
            if attribution:
                to_review.append(key)
                self._report(key, attribution)

        if options["fix"]:
            for key in repairable:
                key.metadata = bound_key_metadata(key.metadata, key.organization_id)
                key.save(update_fields=["metadata", "updated_at"])
            self.stdout.write(
                f"Repaired {len(repairable)} key(s). The gateway takes the repaired "
                "metadata at its next key sync (AGENTCC_SYNC_INTERVAL) or restart."
            )
        elif repairable:
            self.stdout.write(
                f"{len(repairable)} key(s) need repair. Run again with --fix to "
                "bind their metadata to their own org."
            )
        if to_review:
            self.stdout.write(
                f"{len(to_review)} key(s) to review by hand: each was created from "
                "another org. --fix leaves them as they are."
            )
        if not repairable and not to_review:
            self.stdout.write("No API key needs repair.")

    def _report(self, key, problems):
        self.stdout.write(
            f"{key.gateway_key_id} ({key.name}, org {key.organization_id}): "
            + "; ".join(problems)
        )

    @staticmethod
    def _creator_in_org(keys):
        """(user_id, org_id) pairs where the user belongs to the org."""
        user_ids = {key.user_id for key in keys if key.user_id}
        pairs = set(
            OrganizationMembership.no_workspace_objects.filter(
                user_id__in=user_ids, is_active=True
            ).values_list("user_id", "organization_id")
        )
        # A user's own organization counts as a membership.
        pairs.update(
            (key.user_id, key.user.organization_id) for key in keys if key.user_id
        )
        return pairs


def _metadata_problems(key):
    metadata = key.metadata if isinstance(key.metadata, dict) else {}
    problems = []
    org_id = metadata.get("org_id")
    if org_id is not None and str(org_id) != str(key.organization_id):
        problems.append(f"metadata org_id {org_id} is not the key's org")
    reserved = [k for k in reserved_key_metadata(metadata) if k != "org_id"]
    if reserved:
        problems.append(f"reserved metadata {', '.join(reserved)}")
    return problems


def _attribution_problems(key, in_org):
    problems = []
    if key.user_id and (key.user_id, key.organization_id) not in in_org:
        problems.append(f"created by a user outside the key's org ({key.user_id})")
    if key.project_id and key.project.organization_id != key.organization_id:
        problems.append(f"its project {key.project_id} is in another org")
    return problems
