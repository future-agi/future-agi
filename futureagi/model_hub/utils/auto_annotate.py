import re

import pandas as pd
import structlog
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q

from accounts.models.organization import Organization

from tfc.ee_stub import _ee_stub

# from ee.agenthub.feedback_agent_updated.feedback_agent import FeedbackAgent
try:
    from ee.agenthub.deterministic_agent.deterministic_agent import (
        DeterministicAgent,
    )
except ImportError:
    DeterministicAgent = _ee_stub("DeterministicAgent")

# from ee.agenthub.feedback_agent_updated.utils import data_formatter, inputs_list, process_examples, retrieve_avg_rag_based_examples
# from ee.agenthub.feedback_agent_updated.utils import RAG
from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager

logger = structlog.get_logger(__name__)
from model_hub.models.choices import (
    AnnotationTypeChoices,
    CellStatus,
    StatusType,
)
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import get_error_message
try:
    from ee.usage.models.usage import APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import count_tiktoken_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import count_tiktoken_tokens, log_and_deduct_cost_for_api_request

# from ee.agenthub.feedback_agent_updated.utils import delete_table


RAG_TXT = "Rag"


@temporal_activity(time_limit=3600, queue="tasks_l")
def generate_annotations_task(
    annotation_label_id, annotation_id, org_id=None, row_id=None
):
    logger.info("Auto Annotation task started")
    annotation_label = AnnotationsLabels.objects.get(id=annotation_label_id)
    annotation = Annotations.objects.get(id=annotation_id)
    auto_annotation = AutoAnnotation(annotation_label, annotation)
    if org_id:
        org = Organization.objects.get(id=org_id)
    auto_annotation.generate_annotations(row_id, org)
    logger.info("Auto Annotation task ended")


def replace_dynamic_ids(value: str, row):
    """
    Replaces all dynamic IDs in the given string with corresponding cell values
    """
    if not isinstance(value, str):
        return value

    # Find all placeholders in the format {{column_id}}
    matches = re.findall(r"\{{(.*?)\}}", value)

    for match in matches:
        try:
            cell = Cell.objects.get(column__id=match, row=row)
            value = value.replace(f"{{{{{match}}}}}", str(cell.value))
        except ObjectDoesNotExist as e:
            raise ValueError(
                f"Cell with column_id={match} and row={row} not found."
            ) from e

    return value


def _get_input_values(inputs: list, dataset_id, row_id):
    outputs = []
    for col_id in inputs:
        cell = Cell.objects.get(dataset_id=dataset_id, col_id=col_id, row_id=row_id)
        if cell:
            outputs.append(str(cell.value))
        else:
            outputs.append("")

    return outputs


def dataset_to_dataframe(dataset_id, source_id):
    try:
        dataset = Dataset.objects.get(id=dataset_id)
    except Dataset.DoesNotExist:
        raise ValueError(f"Dataset with ID {dataset_id} does not exist.")  # noqa: B904

    # Get all columns for the dataset EXCEPT those matching the source_id
    columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
        Q(source=source_id)
        | Q(
            source__in=[
                "evaluation",
                "evaluation_tags",
                "evaluation_reason",
                "experiment",
                "experiment_evaluation",
                "experiment_evaluation_tags",
                "optimisation_evaluation",
                "annotation_label",
                "optimisation_evaluation_tags",
            ]
        )
    )
    column_map = {column.id: column.id for column in columns}

    data = []
    if source_id:
        # Get all feedback cells matching the criteria
        feedback_cells = Cell.objects.filter(
            column__source_id=source_id,
            feedback_info__has_key="update",
            deleted=False,
            feedback_info__update=True,
        ).select_related("row")

        # For each feedback cell, create a separate row in the dataframe
        for feedback_cell in feedback_cells:
            # Get all cells for this row first (excluding source_id columns)
            base_cells = (
                Cell.objects.filter(row=feedback_cell.row)
                .exclude(column__source=source_id)
                .select_related("column")
            )

            # Create base row data from non-source columns
            row_data = {}
            for cell in base_cells:
                if cell.column_id in column_map:
                    row_data[column_map[cell.column_id]] = cell.value

            # Add feedback-specific fields
            row_data["description"] = feedback_cell.feedback_info.get("description", "")
            row_data["label_value"] = feedback_cell.value

            data.append(row_data)

    # Add feedback-specific columns to the column list
    columns_list = list(column_map.values()) + ["description", "label_value"]

    df = pd.DataFrame(data, columns=columns_list)
    return df


class AutoAnnotation:
    def __init__(self, annotation_label, annotation):
        self.annotation_label = annotation_label
        self.annotation = annotation

    def generate_annotations(self, row_id=None, org=None):
        dataset = None
        get_fewshots = EmbeddingManager()
        try:
            dataset = self.annotation.dataset
            if not dataset:
                raise Exception("Dataset not found")

            rows = Row.objects.filter(dataset=dataset, deleted=False)
            columns = Column.objects.filter(
                source_id=f"{self.annotation.id}-sourceid-{self.annotation_label.id}",
                dataset=dataset,
                deleted=False,
            ).update(status=StatusType.RUNNING.value)

            # Pre-check usage before starting the annotation loop
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            usage_check = check_usage(str(org.id), BillingEventType.AUTO_ANNOTATION)
            if not usage_check.allowed:
                raise ValueError(usage_check.reason or "Usage limit exceeded")

            for _index, row in enumerate(rows):
                if (
                    self.annotation_label.type
                    == AnnotationTypeChoices.CATEGORICAL.value
                ):
                    payload = {
                        "rule_prompt": replace_dynamic_ids(
                            self.annotation_label.settings["rule_prompt"], row
                        ),
                        "choices": [
                            option["label"]
                            for option in self.annotation_label.settings.get(
                                "options", []
                            )
                        ],
                        "multi_choice": self.annotation_label.settings["multi_choice"],
                        "inputs": _get_input_values(
                            self.annotation_label.settings["inputs"], dataset.id, row.id
                        ),
                        "input_type": get_fewshots.inputs_type_list(
                            self.annotation_label.settings.get("inputs", [])
                        ),
                        "few_shot": self.annotation_label.settings.get("few_shot", []),
                    }

                    inputs = payload["inputs"]
                    input_type = payload["input_type"]
                    for input_item, input_type_item in zip(
                        inputs, input_type, strict=False
                    ):
                        if input_type_item == "image":
                            tokens = count_tiktoken_tokens("", input_item)
                        else:
                            tokens = count_tiktoken_tokens(input_item)
                        api_call_type = APICallTypeChoices.AUTO_ANNOTATION.value
                        config = {}
                        config["input_tokens"] = tokens
                        log_and_deduct_cost_for_api_request(
                            org,
                            api_call_type,
                            config,
                            source="auto_annotate",
                            workspace=dataset.workspace,
                        )

                    Cell.objects.filter(
                        column__source_id=f"{self.annotation.id}-sourceid-{self.annotation_label.id}",
                        row_id=row.id,
                    ).exclude(feedback_info__annotation__verified=True).exclude(
                        feedback_info__annotation__has_key="user_id"
                    ).update(
                        status=CellStatus.RUNNING.value, value=None
                    )

                    if self.annotation_label.settings["strategy"] == RAG_TXT:
                        # Get cell values for each input column ID
                        cell_inputs = []
                        for column_id in self.annotation_label.settings["inputs"]:
                            try:
                                cell = Cell.objects.get(
                                    row=row, column_id=column_id, deleted=False
                                )
                                cell_inputs.append(cell.value)
                            except Cell.DoesNotExist:
                                raise ValueError(  # noqa: B904
                                    f"Cell not found for column_id={column_id} and row={row.id}"
                                )
                        processed_few_shots = []
                        few_shots = get_fewshots.retrieve_avg_rag_based_examples(
                            self.annotation.id,
                            cell_inputs,
                            self.annotation_label.settings["inputs"],
                            organization_id=self.annotation.dataset.organization.id,
                            workspace_id=(
                                self.annotation.dataset.workspace.id
                                if self.annotation.dataset.workspace
                                else None
                            ),
                        )
                        processed_few_shots = get_fewshots.process_examples(
                            few_shots,
                            self.annotation_label.settings["inputs"],
                            "feedback_comment",
                            "feedback_value",
                        )
                        payload["few_shot"] = processed_few_shots

                    agent = DeterministicAgent()
                    self.run_model(agent, payload, row)

                    # Emit cost-based usage event after agent completes
                    try:
                        try:
                            from ee.usage.schemas.event_types import BillingEventType
                        except ImportError:
                            BillingEventType = None
                        try:
                            from ee.usage.schemas.events import UsageEvent
                        except ImportError:
                            UsageEvent = None
                        try:
                            from ee.usage.services.config import BillingConfig
                        except ImportError:
                            BillingConfig = None
                        try:
                            from ee.usage.services.emitter import emit
                        except ImportError:
                            emit = None

                        actual_cost = 0
                        if hasattr(agent, "llm") and agent.llm:
                            actual_cost = getattr(agent.llm, "cost", {}).get(
                                "total_cost", 0
                            )
                        credits = BillingConfig.get().calculate_ai_credits(actual_cost)

                        emit(
                            UsageEvent(
                                org_id=str(org.id),
                                event_type=BillingEventType.AUTO_ANNOTATION,
                                amount=credits,
                                properties={
                                    "source": "auto_annotate",
                                    "annotation_id": str(self.annotation.id),
                                    "row_id": str(row.id),
                                    "raw_cost_usd": str(actual_cost),
                                },
                            )
                        )
                    except Exception:
                        pass

                # models for other types of annotations aren't supported at the moment, only deterministic model is available
                else:
                    raise Exception(
                        "Only Categorical labels are supported for auto annotation at the moment"
                    )

            columns = Column.objects.filter(
                source_id=f"{self.annotation.id}-sourceid-{self.annotation_label.id}",
                dataset=dataset,
                deleted=False,
            ).update(status=StatusType.COMPLETED.value)

        except Exception as e:
            columns = Column.objects.filter(
                source_id=f"{self.annotation.id}-sourceid-{self.annotation_label.id}",
                dataset=dataset,
                deleted=False,
            )
            for column in columns:
                rows = Row.objects.filter(dataset=dataset, deleted=False)
                for row in rows:
                    cell, _ = Cell.objects.get_or_create(
                        column=column,
                        row=row,
                        dataset=dataset,
                        deleted=False,
                        defaults={"status": CellStatus.ERROR.value, "value": None},
                    )
                    if not cell.feedback_info.get("annotation", {}).get(
                        "verified"
                    ) and not cell.feedback_info.get("annotation", {}).get("user_id"):
                        cell.status = CellStatus.ERROR.value
                        cell.value_infos["reason"] = get_error_message(
                            "FAILED_TO_GENERATE_ANNOTATION"
                        )
                        cell.value = None
                        cell.save()
            logger.error("Error in auto annotation", e)

    def run_model(self, agent, payload, row):
        response = None
        column = list(
            Column.objects.filter(
                source_id=f"{self.annotation.id}-sourceid-{self.annotation_label.id}"
            ).all()
        )
        for col in column:
            cell, created = Cell.objects.get_or_create(
                row=row, column=col, dataset=self.annotation.dataset, deleted=False
            )

            if not cell.feedback_info.get("annotation", {}).get(
                "verified", False
            ) and not cell.feedback_info.get("annotation", {}).get("user_id"):
                cell.status = CellStatus.RUNNING.value
                cell.save()

                if not response:
                    response = agent.evaluate(payload)
                cell.value = response["choices"]
                cell.status = CellStatus.PASS.value
                cell.feedback_info = {
                    "annotation": {
                        "auto_annotate": True,
                        "label_id": str(self.annotation_label.id),
                        "annotation_id": str(self.annotation.id),
                        "verified": False,
                        "explanation": response["explanation"],
                    }
                }

                cell.save()
                logger.error(
                    f"Auto Annotation done for row {row.id} {cell} {cell.value}"
                )
