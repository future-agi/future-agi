import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_dataset import Column, Dataset
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class Annotations(BaseModel):
    class SummaryStatus:
        RUNNING = "Running"
        COMPLETED = "Completed"

    class SummaryAnnotationType:
        AUTO = "Auto"
        MANUAL = "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    assigned_users = models.ManyToManyField(User, related_name="annotations")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="annotations_org"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotations",
        null=True,
        blank=True,
    )
    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, null=True, blank=True
    )

    labels = models.ManyToManyField(
        "AnnotationsLabels", related_name="annotation_label", blank=True
    )
    columns = models.ManyToManyField(
        Column, related_name="annotation_columns", blank=True
    )
    static_fields = models.JSONField(default=list, blank=True)
    response_fields = models.JSONField(default=list, blank=True)
    responses = models.IntegerField(default=1)
    summary = models.JSONField(
        default=dict, blank=True, null=True
    )  # storing label_required
    lowest_unfinished_row = models.IntegerField(default=0)

    def validate_static_fields(self):
        if self.static_fields:
            if not isinstance(self.static_fields, list):
                raise ValidationError("static_fields must be a list")

            valid_types = ["plain_text", "markdown"]
            valid_views = ["default_collapsed", "default_open"]

            for field in self.static_fields:
                if not isinstance(field, dict):
                    raise ValidationError("Each static field must be a dictionary")

                column_id = field.get("column_id")
                if not column_id:
                    raise ValidationError("Each static field must have a column_id")

                try:
                    column = Column.objects.get(id=column_id, deleted=False)
                    if column.dataset != self.dataset:
                        raise ValidationError(
                            f"Column ID {column_id} does not belong to the dataset"
                        )
                except Column.DoesNotExist as e:
                    raise ValidationError(
                        f"Column ID {column_id} is not valid or is deleted"
                    ) from e

                if "type" not in field or field["type"] not in valid_types:
                    raise ValidationError(
                        f"type must be one of: {', '.join(valid_types)}"
                    )

                if "view" not in field or field["view"] not in valid_views:
                    raise ValidationError(
                        f"view must be one of: {', '.join(valid_views)}"
                    )

    def validate_response_fields(self):
        if self.response_fields:
            if not isinstance(self.response_fields, list):
                raise ValidationError("response_fields must be a list")

            valid_types = ["plain_text", "markdown"]
            valid_views = ["default_collapsed", "default_open"]
            valid_edit = ["editable", "not_editable"]

            for field in self.response_fields:
                if not isinstance(field, dict):
                    raise ValidationError("Each response field must be a dictionary")

                column_id = field.get("column_id")
                if not column_id:
                    raise ValidationError("Each response field must have a column_id")

                try:
                    column = Column.objects.get(id=column_id, deleted=False)
                    if column.dataset != self.dataset:
                        raise ValidationError(
                            f"Column ID {column_id} does not belong to the dataset"
                        )
                except Column.DoesNotExist as e:
                    raise ValidationError(
                        f"Column ID {column_id} is not valid or is deleted"
                    ) from e

                if "type" not in field or field["type"] not in valid_types:
                    raise ValidationError(
                        f"type must be one of: {', '.join(valid_types)}"
                    )

                if "view" not in field or field["view"] not in valid_views:
                    raise ValidationError(
                        f"view must be one of: {', '.join(valid_views)}"
                    )

                if "edit" not in field or field["edit"] not in valid_edit:
                    raise ValidationError(
                        f"edit must be one of: {', '.join(valid_edit)}"
                    )

    def validate_assigned_users(self):
        organization = self.organization
        responses = self.responses

        users = self.assigned_users.all()
        if responses > len(users):
            raise ValidationError(
                "Responses cannot be greater than the number of annotaters"
            )

        for user in users:
            if user.organization != organization:
                raise ValidationError(
                    f"User {user.name} does not belong to the organization."
                )

    def validate_labels(self):
        organization = self.organization
        for label in self.labels.all():
            if label.organization != organization:
                raise ValidationError(
                    f"Label {label.name} does not belong to the organization."
                )

    def clean(self):
        super().clean()
        self.validate_static_fields()
        self.validate_response_fields()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Annotation Task {self.id}"


def validate_annotation_type_choice(value):
    valid_choices = [choice.value for choice in AnnotationTypeChoices]
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid annotation type choice. Valid choices are: {', '.join(valid_choices)}"
        )


class AnnotationsLabels(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    type = models.CharField(
        max_length=255,
        choices=AnnotationTypeChoices.get_choices(),
        validators=[validate_annotation_type_choice],
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="annotations_labels"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="annotations_labels",
        null=True,
        blank=True,
    )
    # New fields for type-specific settings
    settings = models.JSONField(default=dict, blank=True)
    description = models.TextField(null=True, blank=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="annotations_labels",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True, null=True)
    allow_notes = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "type", "project", "workspace"],
                condition=Q(deleted=False),
                name="unique_active_label_org_name_type_project",
            )
        ]

    def validate_settings(self):
        if self.settings:
            if self.type == AnnotationTypeChoices.NUMERIC.value:
                required_fields = {"min", "max", "step_size", "display_type"}
                if not all(field in self.settings for field in required_fields):
                    raise ValidationError(
                        f"Numeric type requires {required_fields} settings"
                    )
                for field in ["min", "max", "step_size"]:
                    if self.settings.get(field) is None:
                        raise ValidationError(f"{field} cannot be null")
                    if not isinstance(self.settings.get(field), int | float):
                        raise ValidationError(f"{field} must be an float")
                if self.settings["min"] >= self.settings["max"]:
                    raise ValidationError("min must be less than max")
                if not isinstance(
                    self.settings.get("display_type"), str
                ) or self.settings["display_type"] not in ["slider", "button"]:
                    raise ValidationError(
                        "display_type must be either 'slider' or 'button'"
                    )

            elif self.type == AnnotationTypeChoices.TEXT.value:
                required_fields = {"placeholder", "max_length", "min_length"}
                if not all(field in self.settings for field in required_fields):
                    raise ValidationError(
                        f"Text type requires {required_fields} settings"
                    )
                if "placeholder" in self.settings and not isinstance(
                    self.settings["placeholder"], str
                ):
                    raise ValidationError("placeholder must be a string")
                if "max_length" in self.settings and not isinstance(
                    self.settings["max_length"], int
                ):
                    raise ValidationError("max_length must be an integer")
                if "min_length" in self.settings and not isinstance(
                    self.settings["min_length"], int
                ):
                    raise ValidationError("min_length must be an integer")
                if (
                    "min_length" in self.settings
                    and "max_length" in self.settings
                    and self.settings["min_length"] >= self.settings["max_length"]
                ):
                    raise ValidationError("min_length must be less than max_length")

            elif self.type == AnnotationTypeChoices.CATEGORICAL.value:
                required_fields = {
                    "rule_prompt",
                    "multi_choice",
                    "options",
                    "auto_annotate",
                    "strategy",
                }
                if not all(field in self.settings for field in required_fields):
                    raise ValidationError(
                        f"Categorical type requires {required_fields} settings"
                    )
                if not isinstance(self.settings["options"], list):
                    raise ValidationError("options must be a list")
                for option in self.settings["options"]:
                    if not isinstance(option, dict) or "label" not in option:
                        raise ValidationError(
                            "Each option must be a dictionary with a 'label' field"
                        )
                if len(self.settings["options"]) < 2:
                    raise ValidationError("There must be at least two options")
                if not isinstance(self.settings["multi_choice"], bool):
                    raise ValidationError("multi_choice must be a boolean")
                if not isinstance(self.settings["rule_prompt"], str):
                    raise ValidationError("rule_prompt must be a string")
                if self.settings["strategy"] not in ["Rag", None]:
                    raise ValidationError("Strategy must be 'Rag' or None.")
                if self.settings["strategy"] == "Rag":
                    pass
                    # if "query" not in self.settings or not isinstance(
                    #     self.settings["query"], str
                    # ):
                    #     raise ValidationError(
                    #         "When strategy is 'Rag', 'query' must be a string and is required."
                    #     )
                    # if "query_col" not in self.settings or not isinstance(
                    #     self.settings["query_col"], str
                    # ):
                    #     raise ValidationError(
                    #         "When strategy is 'Rag', 'query_col' must be a string and is required."
                    #     )
                if not isinstance(self.settings["auto_annotate"], bool):
                    raise ValidationError("auto_annotate must be a boolean")

            elif self.type == AnnotationTypeChoices.STAR.value:
                required_fields = {"no_of_stars"}
                if not all(field in self.settings for field in required_fields):
                    raise ValidationError(
                        f"Star type requires {required_fields} settings"
                    )
                if not isinstance(self.settings["no_of_stars"], int):
                    raise ValidationError("no_of_stars must be an integer")
                if self.settings["no_of_stars"] < 1:
                    raise ValidationError("no_of_stars must be greater than 0")

            elif self.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
                pass

            else:
                raise ValidationError(f"Invalid annotation label type: {self.type}")

    def clean(self):
        super().clean()
        validate_annotation_type_choice(self.type)
        self.validate_settings()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Annotation Label {self.id}"
