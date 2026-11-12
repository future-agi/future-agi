"""Audited operator bootstrap of the reserved Official publisher, with no account/key creation."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from omega_hub import conf
from omega_hub.models import Publisher
from omega_hub.services import audit


class Command(BaseCommand):
    help = "Ensure the Official future-agi publisher belongs to an existing active staff user's organization."

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-email",
            required=True,
            help="Existing active staff user with an organization; recorded as the audit actor.",
        )

    @transaction.atomic
    def handle(self, *args, owner_email, **options):
        # Lock the existing user so ownership/staff status cannot change during bootstrap.
        owner = (
            get_user_model()
            .objects.select_for_update()
            .filter(email=owner_email.strip())
            .first()
        )
        if (
            owner is None
            or not owner.is_staff
            or not owner.is_active
            or not owner.organization_id
        ):
            raise CommandError(
                "--owner-email must name an existing active staff user with an organization"
            )
        org_id = owner.organization_id
        configured_org = conf.maintainer_org_id()
        if configured_org and str(configured_org) != str(org_id):
            raise CommandError(
                "The owner's organization must match OMEGA_HUB_MAINTAINER_ORG_ID"
            )

        # The unique handle serializes concurrent creates; get_or_create's collision lookup
        # retains select_for_update. Never accept a caller-selected handle or create an org.
        publisher, created = Publisher.all_objects.select_for_update().get_or_create(
            handle="future-agi",
            defaults={
                "kind": Publisher.Kind.ORGANIZATION,
                "owner_org_id": org_id,
                "display_name": "Future AGI",
                "tier": Publisher.Tier.OFFICIAL,
                "website": "https://futureagi.com",
                "bio": "Official Omega Hub entries, written and reviewed by Future AGI.",
            },
        )
        if publisher.deleted or publisher.is_erased or publisher.is_suspended:
            raise CommandError(
                "Bootstrap cannot restore a deleted, erased or suspended publisher"
            )
        if publisher.owner_org_id not in (
            None,
            org_id,
        ) or publisher.owner_user_id not in (None, owner.pk):
            raise CommandError(
                "Bootstrap cannot transfer a publisher from another owner"
            )

        required = {
            "kind": Publisher.Kind.ORGANIZATION,
            "tier": Publisher.Tier.OFFICIAL,
            "owner_org_id": org_id,
            "owner_user_id": None,
        }
        changed = {
            field: getattr(publisher, field)
            for field, value in required.items()
            if getattr(publisher, field) != value
        }
        for field in changed:
            setattr(publisher, field, required[field])
        if changed:
            # Preserve the existing profile, entries, grants and the timestamps on a no-op.
            publisher.save(update_fields=[*changed, "updated_at"])
        audit.record(
            "publisher.official_ensured",
            target_type="publisher",
            target_id=publisher.pk,
            actor=owner,
            publisher=publisher,
            data={
                "created": created,
                "changedFields": sorted(changed),
                "ownerOrgId": str(org_id),
                "previous": {
                    field: str(value) if value is not None else None
                    for field, value in changed.items()
                },
            },
        )
        self.stdout.write(
            f"Official future-agi publisher ensured: {publisher.pk}; owner organization: {org_id}"
        )
