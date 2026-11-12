"""Local development seed (SPEC §14.1): the maintainer organisation, the `future-agi` publisher
(tier official), maintainers A and B, and a Hub token for A on `future-agi`. Idempotent.

The Hub ships dark (READINESS 3.13). A local dev Hub is opened to everyone (S3, 100%) here, so the
dev stack and its e2e users (including user C, in an organisation of their own) reach it.

Refuses to run outside a local environment (ENV_TYPE local or test, with DEBUG or the test
settings). Prints a JSON summary; the token appears once.
"""

import json
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.hard_erase_issuance_entry import issuance_entry
from accounts.services.identity_issuance import issue_membership
from omega_hub import conf
from omega_hub.models import Publisher
from omega_hub.permissions import maintainer_permission
from omega_hub.services import tokens
from omega_hub.signals import grant_context
from tfc.constants.levels import Level

ORG_NAME = conf.DEV_MAINTAINER_ORG_NAME


def ensure_member(email: str, name: str, org: Organization, password: str):
    with issuance_entry(organizations=[org.pk], emails=[email]):
        users = get_user_model().objects
        user = users.filter(email=email).first()
        if user is None:
            user = users.create_user(
                email=email,
                password=password,
                name=name,
                organization=org,
                organization_role="Admin",
            )
        membership, _ = issue_membership(
            OrganizationMembership.no_workspace_objects,
            "get_or_create",
            user=user,
            organization=org,
            defaults={"role": "Admin", "level": Level.ADMIN, "is_active": True},
        )
        workspace = Workspace.no_workspace_objects.filter(
            organization=org, is_default=True
        ).first()
        if workspace is None:
            workspace = Workspace.objects.create(
                name="Hub maintainers",
                organization=org,
                is_default=True,
                is_active=True,
                created_by=user,
            )
        issue_membership(
            WorkspaceMembership.no_workspace_objects,
            "get_or_create",
            user=user,
            workspace=workspace,
            defaults={
                "role": "Workspace Admin",
                "level": Level.WORKSPACE_ADMIN,
                "is_active": True,
                "organization_membership": membership,
            },
        )
        return user


def _open_hub_for_local_dev() -> None:
    from omega_rollout import ops
    from omega_rollout import products as rollout
    from omega_rollout.models import ProductRollout

    row = ProductRollout.objects.filter(product=rollout.HUB).first()
    reason = "local Hub dev seed (omega_hub_seed_dev)"
    if row is None or row.stage != rollout.S3:
        ops.set_stage(rollout.HUB, rollout.S3, actor=ops.SEED, reason=reason)
    if row is None or row.percentage != 100:
        ops.set_percentage(rollout.HUB, 100, actor=ops.SEED, reason=reason)


class Command(BaseCommand):
    help = "Seed the Omega Hub for local development (ENV_TYPE local or test, with DEBUG, only)."

    def add_arguments(self, parser):
        parser.add_argument("--email-a", default="omega-hub-a@futureagi.local")
        parser.add_argument("--email-b", default="omega-hub-b@futureagi.local")
        parser.add_argument(
            "--password",
            default=None,
            help="Password for A and B (default: random, printed).",
        )
        parser.add_argument(
            "--token-file",
            default=None,
            help="Write A's Hub token here (mode 0600) instead of printing it.",
        )

    @transaction.atomic
    def handle(self, *args, email_a, email_b, password, token_file, **options):
        if not conf.is_local_dev():
            raise CommandError(
                "omega_hub_seed_dev runs only locally: ENV_TYPE local or test, with DEBUG (or the test settings)"
            )
        password = password or secrets.token_urlsafe(12)
        org = Organization.objects.filter(name=ORG_NAME).first()
        new_org = org is None
        if new_org:
            org = Organization(name=ORG_NAME)
        with issuance_entry(
            organizations=[] if new_org else [org.pk],
            new_organizations=[org.pk] if new_org else [],
            emails=[email_a, email_b],
        ):
            if new_org:
                org.save(force_insert=True)
            a = ensure_member(email_a, "Hub maintainer A", org, password)
            b = ensure_member(email_b, "Hub maintainer B", org, password)
            perm = maintainer_permission()
            for user in (a, b):
                if not user.user_permissions.filter(pk=perm.pk).exists():
                    with grant_context(via="seed", by=None):
                        user.user_permissions.add(perm)
            publisher = Publisher.objects.filter(handle="future-agi").first()
            if publisher is None:
                publisher = Publisher.objects.create(
                    handle="future-agi",
                    kind=Publisher.Kind.ORGANIZATION,
                    owner_org=org,
                    display_name="Future AGI",
                    tier=Publisher.Tier.OFFICIAL,
                    website="https://futureagi.com",
                    bio="Official Omega Hub entries, written and reviewed by Future AGI.",
                )
            _, plaintext = tokens.create_token(a, publisher, "dev seed", 30)
            summary = {
                "maintainerOrgId": str(org.id),
                "maintainerA": email_a,
                "maintainerB": email_b,
                "password": password
                if options.get("password") is None
                else "(as given)",
                "publisher": publisher.handle,
                "tokenA": None,
            }
            if token_file:
                import os

                with open(token_file, "w") as handle:
                    handle.write(plaintext + "\n")
                os.chmod(token_file, 0o600)
                summary["tokenFile"] = token_file
            else:
                summary["tokenA"] = plaintext
            _open_hub_for_local_dev()
            summary["next"] = f"export OMEGA_HUB_MAINTAINER_ORG_ID={org.id}"
            self.stdout.write(json.dumps(summary, indent=2))
