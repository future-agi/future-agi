from collections import defaultdict

from rest_framework import serializers

from accounts.models import User
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Row
from tracer.models.project import Project


class AnnotationsLabelsSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), many=False, required=False
    )
    trace_annotations_count = serializers.IntegerField(read_only=True, required=False)
    annotation_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = AnnotationsLabels
        fields = [
            "id",
            "name",
            "type",
            "organization",
            "settings",
            "project",
            "description",
            "allow_notes",
            "created_at",
            "trace_annotations_count",
            "annotation_count",
        ]
        read_only_fields = ["organization"]

    def validate(self, attrs):
        """Ensure `name` is unique within the same project and type.

        A label with the same `name` (case-insensitive) and `type` cannot
        coexist inside the same `project`. If `project` is `None` we still
        enforce uniqueness across global labels (projectless).
        """

        # Fetch the incoming / existing values.
        name = attrs.get("name", getattr(self.instance, "name", None))
        label_type = attrs.get("type", getattr(self.instance, "type", None))
        project = attrs.get("project", getattr(self.instance, "project", None))

        organization = attrs.get("organization")

        # Attempt to fetch organisation from request context if not supplied
        # directly (typical in API usage).
        if organization is None and "request" in self.context:
            user = getattr(self.context["request"], "user", None)
            organization = getattr(user, "organization", None)

        # Build the queryset to check for duplicates.
        duplicate_qs = AnnotationsLabels.objects.filter(
            name__iexact=name,
            type=label_type,
            deleted=False,
        )

        # We only want to enforce uniqueness within the *same* organisation. If
        # we cannot confidently determine the organisation (e.g. serializer used
        # outside the request cycle) we skip this validation to avoid false
        # positives.
        if organization is not None:
            duplicate_qs = duplicate_qs.filter(organization=organization)

            # Projects: either match the specific project, or look for projectless labels.
            if project is None:
                duplicate_qs = duplicate_qs.filter(project__isnull=True)
            else:
                duplicate_qs = duplicate_qs.filter(project=project)

            # Exclude the current instance during updates
            if self.instance is not None:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)

            if duplicate_qs.exists():
                raise serializers.ValidationError(
                    "A label with this name and type already exists in the selected project."
                )

        return attrs


class AnnotationsSerializer(serializers.ModelSerializer):
    assigned_users = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()
    label_requirements = serializers.SerializerMethodField()
    lowest_unfinished_row = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()

    class Meta:
        model = Annotations
        fields = [
            "id",
            "name",
            "assigned_users",
            "organization",
            "labels",
            "columns",
            "static_fields",
            "response_fields",
            "dataset",
            "summary",
            "created_at",
            "responses",
            "lowest_unfinished_row",
            "label_requirements",
        ]
        read_only_fields = ["organization"]

    def create(self, validated_data):
        labels = self.initial_data.get("labels", [])
        instance = super().create(validated_data)
        if labels:
            instance.labels.set(AnnotationsLabels.objects.filter(id__in=labels))
        return instance

    def update(self, instance, validated_data):
        labels = self.initial_data.get("labels", None)
        instance = super().update(instance, validated_data)
        if labels is not None:
            instance.labels.set(AnnotationsLabels.objects.filter(id__in=labels))
        return instance

    def get_labels(self, obj):
        labels = list(obj.labels.all().values("id", "name"))
        return [{"id": label["id"], "name": label["name"]} for label in labels]

    def preload_cells(self, obj, rows, labels, require_user_id=True):
        source_ids = [f"{obj.id}-sourceid-{label.id}" for label in labels]

        qs = Cell.objects.filter(
            dataset=obj.dataset,
            deleted=False,
            row__in=rows,
            column__source_id__in=source_ids,
            value__regex=r"^(?!\s*$).+",
        )

        if require_user_id:
            qs = qs.filter(feedback_info__annotation__has_key="user_id")

        return qs.select_related("row", "column")

    def get_lowest_unfinished_row(self, obj):
        current_user = self.context["request"].user
        rows = Row.objects.filter(dataset=obj.dataset, deleted=False).order_by("order")
        all_labels = list(obj.labels.all())
        cells_qs = self.preload_cells(obj, rows, all_labels, require_user_id=False)

        # Map from (row_id, label_id) to list of cells
        cells_map = defaultdict(list)
        for cell in list(cells_qs):
            source_id = cell.column.source_id
            prefix = f"{obj.id}-sourceid-"
            if source_id.startswith(prefix):
                label_id = source_id[len(prefix) :]
                # label_id = cell.column.source_id.split('-')[-1]
                cells_map[(cell.row_id, label_id)].append(cell)

        for row in list(rows):
            for label in all_labels:
                key = (row.id, str(label.id))
                related_cells = cells_map.get(key, [])

                user_has_completed = any(
                    cell.feedback_info.get("annotation", {}).get("user_id")
                    == str(current_user.id)
                    for cell in related_cells
                )

                if not user_has_completed:
                    total_cells = sum(
                        "user_id" in cell.feedback_info.get("annotation", {})
                        for cell in related_cells
                    )
                    if total_cells < obj.responses:
                        return row.order

        return rows.first().order if rows else None

    def get_assigned_users(self, obj):
        if isinstance(obj, dict):
            assigned_users = obj.get("assigned_users", [])
        else:
            assigned_users = obj.assigned_users.all()

        return (
            [
                {"id": user.id, "name": user.name, "email": user.email}
                for user in assigned_users
            ]
            if assigned_users
            else []
        )

    def get_summary(self, obj):
        if not isinstance(obj, Annotations):
            return {"completed": 0, "total": 0}

        labels = list(obj.labels.all())
        rows = list(Row.objects.filter(dataset=obj.dataset, deleted=False))
        total_rows = len(rows)

        if not rows or not labels:
            return {"completed": 0, "total": total_rows}

        if obj.summary is None:
            obj.summary = {}

        if "label_requirements" not in obj.summary:
            obj.summary["label_requirements"] = {}
            obj.save()

        cells_qs = self.preload_cells(obj, rows, labels, require_user_id=True)

        # Precompute
        row_label_counts = defaultdict(lambda: defaultdict(int))
        user_row_label_counts = defaultdict(lambda: defaultdict(int))

        user_id = str(self.context["request"].user.id)
        prefix = f"{obj.id}-sourceid-"

        for cell in cells_qs:
            source_id = cell.column.source_id
            if not source_id.startswith(prefix):
                continue
            label_id = source_id[len(prefix) :]
            row_label_counts[cell.row_id][label_id] += 1

            if cell.feedback_info.get("annotation", {}).get("user_id") == user_id:
                user_row_label_counts[cell.row_id][label_id] += 1

        # Precompute "auto-complete" labels
        auto_complete_labels = {
            str(lid)
            for lid, required in obj.summary["label_requirements"].items()
            if not required
        }

        # Counters
        user_completed_rows = 0
        completed_rows = 0

        for row in rows:
            row_counts = row_label_counts.get(row.id, {})
            user_row_counts = user_row_label_counts.get(row.id, {})

            # --- User-specific completion ---
            if all(user_row_counts.get(str(label.id), 0) > 0 for label in labels):
                user_completed_rows += 1

            # --- Global completion ---
            if all(
                str(label.id) in auto_complete_labels
                or row_counts.get(str(label.id), 0) >= obj.responses
                for label in labels
            ):
                completed_rows += 1

        if obj.lowest_unfinished_row != completed_rows:
            obj.lowest_unfinished_row = completed_rows
            obj.save(update_fields=["lowest_unfinished_row"])

        return {
            "completed": user_completed_rows,
            "total": total_rows,
        }

    def get_label_requirements(self, obj):
        if isinstance(obj.summary, dict):
            return obj.summary.get("label_requirements", {})
        return {}


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        ref_name = "DevelopAnnotationsUser"
        fields = ["id", "email", "name", "organization_role", "is_active", "is_staff"]


class AnnotationSummaryHeaderSerializer(serializers.Serializer):
    dataset_coverage = serializers.FloatField(required=False, allow_null=True)
    completion_eta = serializers.FloatField(required=False, allow_null=True)
    overall_agreement = serializers.FloatField(required=False, allow_null=True)


class AnnotationSummaryResultSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.JSONField(), default=list)
    annotators = serializers.ListField(child=serializers.JSONField(), default=list)
    header = AnnotationSummaryHeaderSerializer(required=False)


class AnnotationSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = AnnotationSummaryResultSerializer()


class AnnotationProjectVersionMapperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Annotations
        fields = ["id", "name", "organization", "labels", "created_at"]
        read_only_fields = ["organization"]
