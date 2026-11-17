"""Count evaluation versions and templates whose organization does not fit
their owner.

    manage.py count_eval_version_org_stamps
        Versions of a system evaluation (owner ``system``) that carry an
        organization: how many, and how many of those are not deleted; then
        one line per version with its id, its evaluation's id and whether the
        version is deleted, sorted by version id. Then user evaluations (owner
        ``user``) with no organization: how many, and how many are not
        deleted; then one line per evaluation with its id and whether it is
        deleted, sorted by id.

Reads only: rows are read through the plain manager, so deleted rows are
counted too and shown apart; nothing is saved, no network is used, and every
query is a SELECT. Only ids, the deleted flag and integers are printed, never
a name, text, configuration, model, user or organization (not even its id).
Admitted on every deployment (``STARTUP_SAFE_MANAGEMENT_COMMANDS``).
"""

from django.core.management.base import BaseCommand

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion


def _flag(deleted):
    return "true" if deleted else "false"


class Command(BaseCommand):
    help = (
        "Count system evaluation versions that carry an organization and user "
        "evaluations that carry none (reads only; ids, deleted flags and "
        "integers)."
    )

    def handle(self, *args, **options):
        versions = list(
            EvalTemplateVersion._base_manager.filter(
                eval_template__owner=OwnerChoices.SYSTEM.value,
                organization__isnull=False,
            )
            .order_by("id")
            .values_list("id", "eval_template_id", "deleted")
        )
        live = sum(1 for _, _, deleted in versions if not deleted)
        self.stdout.write(
            f"system_template_versions_with_org={len(versions)} not_deleted={live}"
        )
        for version_id, template_id, deleted in versions:
            self.stdout.write(
                f"version_id={version_id} template_id={template_id} "
                f"deleted={_flag(deleted)}"
            )

        templates = list(
            EvalTemplate._base_manager.filter(
                owner=OwnerChoices.USER.value,
                organization__isnull=True,
            )
            .order_by("id")
            .values_list("id", "deleted")
        )
        live = sum(1 for _, deleted in templates if not deleted)
        self.stdout.write(
            f"user_templates_without_org={len(templates)} not_deleted={live}"
        )
        for template_id, deleted in templates:
            self.stdout.write(f"template_id={template_id} deleted={_flag(deleted)}")
