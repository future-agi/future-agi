from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptVersion
from tfc.middleware.workspace_context import get_current_organization


def assign_labels_to_version(template_version_id, label_ids, user, workspace):
    _org = get_current_organization() or user.organization

    with transaction.atomic():
        try:
            template_version = PromptVersion.objects.select_for_update().get(
                id=template_version_id,
                original_template__organization=_org,
                deleted=False,
            )
        except PromptVersion.DoesNotExist:
            raise Exception("Template version does not exist")

        labels = PromptLabel.no_workspace_objects.filter(
            Q(organization=_org, workspace=workspace)
            | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value),
            id__in=label_ids,
            deleted=False,
        )

        if len(labels) != len(label_ids):
            valid_label_ids = set(labels.values_list("id", flat=True))
            provided_label_ids = set(label_ids)
            missing_ids = provided_label_ids - valid_label_ids
            raise Exception(
                f"Some labels not found or are not allowed to be assigned: {list(missing_ids)}"
            )

        template_version.labels.through.objects.filter(
            promptversion_id=template_version.id
        ).delete()

        template_version.labels.through.objects.filter(
            promptlabel_id__in=label_ids,
            promptversion__original_template=template_version.original_template,
            promptversion__deleted=False,
        ).exclude(promptversion_id=template_version.id).delete()

        template_version.labels.add(*labels)
        template_version.updated_at = timezone.now()
        template_version.save(update_fields=["updated_at"])

    return
