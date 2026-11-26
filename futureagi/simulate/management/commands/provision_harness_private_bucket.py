from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from simulate.services.harness_storage import ensure_private_bucket


class Command(BaseCommand):
    help = "Create and verify the dedicated private source bucket on bundled MinIO."

    def handle(self, *args, **options):
        if settings.STORAGE_BACKEND != "minio":
            raise CommandError(
                "Cloud source buckets must be provisioned with their cloud access policies."
            )
        ensure_private_bucket()
