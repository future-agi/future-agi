"""Operator redrive; intentionally not exposed as an application-user API."""

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from model_hub.models.develop_dataset import Dataset
from model_hub.services.file_media_dispatch import retry_media_dispatch
from model_hub.views.datasets.create.file_upload import process_media_uploads


class Command(BaseCommand):
    help = (
        "Retry failed media dispatch once for a dataset in the specified organization. "
        "Requires backend operator shell access; never reimports the table."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dataset-id", type=UUID, required=True)
        parser.add_argument("--org-id", type=UUID, required=True)

    def handle(self, *args, **options):
        try:
            retry_media_dispatch(
                options["dataset_id"], options["org_id"], process_media_uploads.delay
            )
        except Dataset.DoesNotExist:
            raise CommandError(
                "Dataset not found in the specified organization"
            ) from None
        except Exception as error:
            # Dispatch errors can contain signed URLs or other private values.
            raise CommandError(
                f"Media dispatch retry did not complete ({type(error).__name__}); "
                "check dataset creation progress before retrying."
            ) from None
        self.stdout.write("Media dispatch retrigger recorded and dispatched")
