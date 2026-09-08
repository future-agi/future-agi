"""Service layer for annotation operations shared by API and internal AI workflows."""

import uuid

import structlog

from model_hub.models.choices import SourceChoices

logger = structlog.get_logger(__name__)


# Map annotation label types to column data types
LABEL_TYPE_TO_DATA_TYPE = {
    "categorical": "text",
    "text": "text",
    "numeric": "float",
    "rating": "float",
    "star": "float",
}


def process_annotation_columns(
    annotation,
    labels,
    label_requirements=None,
    trigger_auto_annotate=False,
    organization_id=None,
):
    """Create columns for annotation labels on the annotation's dataset.

    This is the shared logic extracted from AnnotationsViewSet.process_new_annotaion.
    Both the API view and internal AI workflows call this.

    Args:
        annotation: Annotations instance
        labels: Queryset or list of AnnotationsLabels
        label_requirements: Optional dict mapping label_id -> required (bool)
        trigger_auto_annotate: Whether to trigger async auto-annotation
        organization_id: Org ID string for auto-annotation task

    Returns:
        Number of columns created
    """
    from model_hub.models.develop_dataset import Column

    if label_requirements is None:
        label_requirements = {}

    columns_created = 0

    for label in labels:
        # Validate categorical input columns belong to same dataset
        if label.type == "categorical":
            input_columns = label.settings.get("inputs", [])
            if input_columns:
                for col_id in input_columns:
                    try:
                        col = Column.objects.get(id=col_id, deleted=False)
                        if col.dataset != annotation.dataset:
                            raise ValueError(
                                f"Input column '{col.name}' does not belong to the annotation's dataset"
                            )
                    except Column.DoesNotExist:
                        pass

        auto_annotate = label.settings.get("auto_annotate", False)
        data_type = LABEL_TYPE_TO_DATA_TYPE.get(label.type, "text")
        responses = annotation.responses

        for response_idx in range(responses):
            column_name = f"{annotation.name}:{label.name}:{response_idx}"
            column = Column.objects.create(
                id=uuid.uuid4(),
                name=column_name,
                data_type=data_type,
                source=SourceChoices.ANNOTATION_LABEL.value,
                dataset=annotation.dataset,
                source_id=f"{annotation.id}-sourceid-{label.id}",
            )
            annotation.columns.add(column)
            annotation.dataset.column_order.append(str(column.id))
            annotation.dataset.column_config[str(column.id)] = {
                "is_frozen": False,
                "is_visible": True,
            }
            annotation.dataset.save()
            columns_created += 1

        if auto_annotate and trigger_auto_annotate and organization_id:
            try:
                from model_hub.tasks import generate_annotations_task

                generate_annotations_task.apply_async(
                    args=(label.id, annotation.id, organization_id)
                )
            except Exception as e:
                logger.warning(f"Failed to trigger auto-annotation: {e}")

    # Update summary with label requirements
    if not isinstance(annotation.summary, dict):
        annotation.summary = {}
    annotation.summary["label_requirements"] = label_requirements
    annotation.lowest_unfinished_row = 0
    annotation.save()

    return columns_created
