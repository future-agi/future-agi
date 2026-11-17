"""Email every publisher with a listed entry when a new rules version deploys (SPEC §6.8). Once per
rules version unless --force; writes one `rules.notified` audit event. The daily schedule passes
--if-needed, so a version that was already announced is a quiet no-op rather than a failed run."""

from django.core.management.base import BaseCommand, CommandError

from omega_hub import rules
from omega_hub.models import AuditEvent, Entry, Publisher
from omega_hub.services import audit, notify


class Command(BaseCommand):
    help = "Tell publishers about the current Omega Hub rules version."

    def add_arguments(self, parser):
        parser.add_argument("--summary", required=True, help="What changed, in plain words.")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--if-needed", action="store_true", help="Exit quietly when this version was announced.")

    def handle(self, *args, summary, force, if_needed=False, **options):
        version = rules.current_version()
        if AuditEvent.objects.filter(action="rules.notified", target_id=str(version)).exists() and not force:
            if if_needed:
                self.stdout.write(f"rules version {version}: publishers were already told")
                return
            raise CommandError(f"publishers were already told about rules version {version} (use --force)")
        publishers = Publisher.objects.filter(entries__status=Entry.Status.LISTED, suspended_at__isnull=True).distinct()
        count = 0
        for publisher in publishers:
            notify.rules_changed(publisher, summary, version)
            count += 1
        audit.record("rules.notified", target_type="rules", target_id=version, data={"publishers": count, "summary": summary[:500]})
        self.stdout.write(f"rules version {version}: told {count} publishers")
