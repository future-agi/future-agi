"""Load the disposable-email list into BlockedEmailDomain.

accounts/disposable_domains.py is refreshed every Thursday by
.github/workflows/refresh-disposable-domains.yml, which opens a PR. Once that
PR ships, the bootstrap job runs this command after ``migrate`` on every
deploy, so the table always matches the list the image was built with:

    python manage.py sync_disposable_domains            # apply
    python manage.py sync_disposable_domains --dry-run  # show counts only

Managed cloud only: a self-hosted install accepts any address, so on one the
command does nothing. entrypoint.sh runs it in every bootstrap job, cloud or
self-hosted, and this check is what keeps it a no-op on self-hosted installs.

Only rows whose source is the disposable-email list are added or removed.
A domain the team added in Django admin is never changed or deleted, even
when the list also has it or later drops it. Running it twice changes nothing
the second time.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.disposable_domains import DISPOSABLE_EMAIL_DOMAINS
from accounts.models.blocked_email_domain import (
    BlockedEmailDomain,
    BlockedEmailDomainSource,
    normalize_email_domain,
)
from accounts.utils import EXTRA_DISPOSABLE_EMAIL_DOMAINS
from tfc.ee_loader import is_cloud_env

BATCH_SIZE = 1000


def listed_domains():
    domains = {
        normalize_email_domain(domain)
        for domain in DISPOSABLE_EMAIL_DOMAINS | EXTRA_DISPOSABLE_EMAIL_DOMAINS
    }
    domains.discard("")
    return domains


def sync_disposable_domains(*, dry_run=False):
    """Returns (added, removed). Rows added in admin are left alone."""
    listed = listed_domains()
    existing = dict(BlockedEmailDomain.objects.values_list("domain", "source"))

    to_add = sorted(domain for domain in listed if domain not in existing)
    to_remove = sorted(
        domain
        for domain, source in existing.items()
        if source == BlockedEmailDomainSource.DISPOSABLE_LIST and domain not in listed
    )
    if dry_run:
        return len(to_add), len(to_remove)

    with transaction.atomic():
        # ignore_conflicts: a row added in admin between the read above and
        # this insert keeps its manual source.
        BlockedEmailDomain.objects.bulk_create(
            [
                BlockedEmailDomain(
                    domain=domain, source=BlockedEmailDomainSource.DISPOSABLE_LIST
                )
                for domain in to_add
            ],
            batch_size=BATCH_SIZE,
            ignore_conflicts=True,
        )
        for start in range(0, len(to_remove), BATCH_SIZE):
            BlockedEmailDomain.objects.filter(
                source=BlockedEmailDomainSource.DISPOSABLE_LIST,
                domain__in=to_remove[start : start + BATCH_SIZE],
            ).delete()
    return len(to_add), len(to_remove)


class Command(BaseCommand):
    help = "Load accounts/disposable_domains.py into the blocked email domains table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many domains would be added and removed, change nothing.",
        )

    def handle(self, *args, **options):
        if not is_cloud_env(settings.CLOUD_DEPLOYMENT):
            self.stdout.write(
                "sync_disposable_domains: not managed cloud, nothing to do"
            )
            return
        dry_run = options["dry_run"]
        added, removed = sync_disposable_domains(dry_run=dry_run)
        verb = "would add" if dry_run else "added"
        verb_removed = "would remove" if dry_run else "removed"
        self.stdout.write(
            f"sync_disposable_domains: {verb} {added}, {verb_removed} {removed}"
        )
