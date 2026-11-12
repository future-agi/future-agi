"""``manage.py webhooks_reencrypt [--chunk N] [--dry-run]``: rewrite webhooks ciphertexts under the newest key and
report how many are left on older ones (DESIGN.md section 5.3). Exit status 1 while any is left or undecryptable."""

from django.core.management.base import BaseCommand, CommandError

from webhooks import reencrypt


class Command(BaseCommand):
    help = "Re-encrypt webhook secrets, URLs and headers under the newest WEBHOOKS_ENCRYPTION_KEYS entry."

    def add_arguments(self, parser):
        parser.add_argument("--chunk", type=int, default=reencrypt.CHUNK)
        parser.add_argument(
            "--dry-run", action="store_true", help="Only count what is left."
        )

    def handle(self, *args, chunk, dry_run, **options):
        out = reencrypt.run(chunk=chunk, dry_run=dry_run)
        left = out["left"]["total"]
        self.stdout.write(
            f"rewritten {out['rewritten']}; left on older keys {left}; "
            f"undecryptable {out['undecryptable']}"
        )
        for col, n in out["left"].items():
            if col != "total" and n:
                self.stdout.write(f"  {col}: {n}")
        if left or out["undecryptable"]:
            raise CommandError("ciphertexts remain on an older key or do not decrypt")
