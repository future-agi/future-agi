import gc
import json
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import structlog

# from ee.agenthub.feedback_agent_updated.utils import RAG
from django.core.exceptions import ObjectDoesNotExist
from django.db import close_old_connections, models, transaction
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from agentic_eval.core.utils.functions import detect_input_type, is_uuid
from agentic_eval.core.utils.model_config import ModelConfigs
from tfc.telemetry import wrap_for_thread

logger = structlog.get_logger(__name__)
from accounts.models.organization import Organization
from accounts.utils import get_request_organization
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from agentic_eval.core_evals.fi_evals.grounded.similarity import *  # noqa: F403
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from common.utils.data_injection import normalize as _di_normalize
from analytics.utils import (
    MixpanelEvents,
    MixpanelSources,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from evaluations.constants import FUTUREAGI_EVAL_TYPES  # noqa: E402
from model_hub.models.choices import (
    CellStatus,
    ModelChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    DataTypeChoices,
    KnowledgeBaseFile,
    Row,
)
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.run_prompt import RunPrompter
from model_hub.serializers.develop_optimisation import UserEvalMetricSerializer
from model_hub.serializers.eval_runner import (
    CustomEvalTemplateCreateSerializer,
    EvalTemplateSerializer,
    EvalUserTemplateSerializer,
)
from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type
from model_hub.utils.evals import prepare_user_eval_config  # noqa: E402
from model_hub.utils.json_path_resolver import (  # noqa: E402
    parse_json_safely,
    resolve_json_path,
)
from sdk.utils.helpers import _get_api_call_type
from tfc.utils.error_codes import (
    get_error_for_api_status,
    get_error_message,
    get_specific_error_message,
)
from tfc.utils.functions import get_eval_stats
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
    from tfc.oss_stubs.usage import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import count_tiktoken_tokens, log_and_deduct_cost_for_api_request, refund_cost_for_api_call
except ImportError:
    from tfc.oss_stubs.usage import count_tiktoken_tokens
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request
    from tfc.oss_stubs.usage import refund_cost_for_api_call


def _format_messages_to_prompt_chain(messages):
    """Format a list of chat messages into a single prompt-chain string.

    Each message is rendered as ``role: content`` and joined by newlines.
    Multipart content lists are flattened to their text parts.
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            content = " ".join(text_parts)

        parts.append(f"{role}: {content}")

    return "\n".join(parts)


@transaction.atomic
def bulk_update_or_create_cells(
    rows_list, column_id, dataset_id, new_values, user_eval_metric_id=None
):
    """
    Bulk update or create cells matching the filter criteria

    Args:
        rows_list: List of row IDs to filter by
        column_id: Column ID to filter by
        dataset_id: Dataset ID to filter by
        new_values: Dictionary containing values to update/set
        user_eval_metric_id: Optional — when supplied, the write is skipped
            entirely if the user has stopped this eval mid-run. Mirrors the
            guard in EvaluationRunner._create_cell so that late Temporal
            workers can't overwrite the "User stopped evaluation" state
            set by StopUserEvalView.
    """
    # Stop guard: matches the _create_cell path in EvaluationRunner.
    if user_eval_metric_id:
        from model_hub.services.experiment_utils import is_user_eval_stopped

        if is_user_eval_stopped(user_eval_metric_id):
            return 0, 0
    # Normalize IDs to UUID for consistent key comparison
    column_id = (
        uuid.UUID(str(column_id)) if not isinstance(column_id, uuid.UUID) else column_id
    )
    dataset_id = (
        uuid.UUID(str(dataset_id))
        if not isinstance(dataset_id, uuid.UUID)
        else dataset_id
    )

    # Get existing cells that match our filters
    existing_cells = Cell.objects.filter(
        row__in=rows_list, column=column_id, dataset=dataset_id
    )

    # Create a dictionary mapping (row_id, column_id, dataset_id) -> cell
    existing_dict = {
        (cell.row_id, cell.column_id, cell.dataset_id): cell for cell in existing_cells
    }

    cells_to_update = []
    cells_to_create = []

    # Process each row to determine if update or create is needed
    for row_id in rows_list:
        key = (row_id, column_id, dataset_id)

        if key in existing_dict:
            # Update existing cell
            cell = existing_dict[key]
            for field, value in new_values.items():
                setattr(cell, field, value)
            cells_to_update.append(cell)
        else:
            # Create new cell
            new_cell = Cell(
                row_id=row_id, column_id=column_id, dataset_id=dataset_id, **new_values
            )
            cells_to_create.append(new_cell)

    # Perform bulk operations
    if cells_to_update:
        # Get all fields that need updating
        update_fields = list(new_values.keys())
        Cell.objects.bulk_update(cells_to_update, update_fields)

    if cells_to_create:
        Cell.objects.bulk_create(cells_to_create)

    return len(cells_to_update), len(cells_to_create)


def _is_knowledge_base_uuid(uuid_value):
    """
    Check if a UUID value belongs to a KnowledgeBaseFile.

    Args:
        uuid_value: The value to check (can be string, int, or UUID)

    Returns:
        bool: True if the UUID exists in KnowledgeBaseFile, False otherwise
    """
    if not uuid_value:
        return False

    # Check if it's a valid UUID format
    if not is_uuid(str(uuid_value)):
        return False

    # Check if it exists in KnowledgeBaseFile
    try:
        return KnowledgeBaseFile.objects.filter(id=str(uuid_value)).exists()
    except Exception:
        return False


def _extract_column_id_and_path(value):
    """
    Extract column ID/name and JSON path from a mapping value.

    Handles:
    - Plain column UUIDs: d3f7e8a9-1234-5678-9abc-def012345678
    - UUID with JSON path: d3f7e8a9-1234-5678-9abc-def012345678.nested.path
    - UUID with bracket index: d3f7e8a9-1234-5678-9abc-def012345678[0]
    - Plain column names: input
    - Column names with JSON path: input.year, input.nested.path
    - Column names with bracket index: images[0]

    Returns:
        tuple: (column_id_or_name, json_path) where json_path is None if not present
               For bracket notation, json_path will be the index like "[0]"
    """
    if not value or not isinstance(value, str):
        return value, None

    # Try UUID with bracket index: uuid[0]
    uuid_with_bracket_pattern = (
        r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\[\d+\])$"
    )
    match = re.match(uuid_with_bracket_pattern, value, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)

    # Try UUID pattern first: uuid.json.path
    uuid_with_path_pattern = (
        r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(.+)$"
    )
    match = re.match(uuid_with_path_pattern, value, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)

    # Check if it's a plain UUID (no path)
    plain_uuid_pattern = (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    if re.match(plain_uuid_pattern, value, re.IGNORECASE):
        return value, None

    # Handle column NAME with bracket index: images[0]
    bracket_match = re.match(r"^(.+?)(\[\d+\])$", value)
    if bracket_match:
        return bracket_match.group(1), bracket_match.group(2)

    # Handle column NAME with optional JSON path: input.year
    if "." in value:
        parts = value.split(".", 1)  # Split on first dot only
        return parts[0], parts[1]  # (column_name, json_path)

    # Plain column name (no path)
    return value, None


def _get_cell_value_with_json_path(cell, json_path):
    """
    Get cell value, applying JSON path if specified.

    Args:
        cell: The Cell object
        json_path: Optional JSON path to extract from the cell value

    Returns:
        The cell value or extracted JSON path value
    """
    if not cell or not cell.value:
        return None

    if not json_path:
        return cell.value

    # Try to parse the cell value as JSON and extract the path
    parsed_json, is_valid = parse_json_safely(cell.value)
    if is_valid and parsed_json:
        return resolve_json_path(parsed_json, json_path)

    # If not valid JSON, return the original value
    return cell.value


def _resolve_column_reference(column_ref, dataset_id):
    """
    Resolve a column reference (UUID or name) to a Column object.

    Args:
        column_ref: Column UUID or column name
        dataset_id: The dataset to search in

    Returns:
        Column object or None if not found
    """
    # Try as UUID first
    try:
        return Column.objects.get(id=column_ref)
    except (Column.DoesNotExist, ValueError):
        pass

    # Try as column name within the dataset
    try:
        return Column.objects.get(name=column_ref, dataset_id=dataset_id)
    except Column.DoesNotExist:
        return None


# Special mapping value keys for experiment eval context
_SPECIAL_MAPPING_VALUES = {"output", "prompt_chain"}


def _is_special_mapping_value(value):
    """Check if a mapping value is a special experiment-context reference."""
    return isinstance(value, str) and value.lower() in _SPECIAL_MAPPING_VALUES


def _get_agent_node_messages(eac, column):
    """
    Resolve prompt messages for a specific LLM node in an agent experiment.

    Navigates: EAC -> graph_version -> Node (via column.metadata.node_id)
    -> PromptTemplateNode -> PromptVersion -> prompt_config_snapshot.

    Returns messages (list or dict) from the node's PromptVersion, or None.
    """
    from agent_playground.models.node import Node
    from agent_playground.models.prompt_template_node import PromptTemplateNode

    node_id = None

    # Primary: get node_id from column metadata
    if column and column.metadata and isinstance(column.metadata, dict):
        node_id = column.metadata.get("node_id")

    # Fallback: match by column name suffix against node names
    if not node_id and column:
        try:
            llm_nodes = Node.objects.filter(
                graph_version=eac.graph_version,
                deleted=False,
                node_template__name="llm_prompt",
            )
            for node in llm_nodes:
                if column.name.endswith(f"-{node.name}"):
                    node_id = str(node.id)
                    break
        except Exception as e:
            logger.warning(f"prompt_chain: fallback node name matching failed: {e}")

    if not node_id:
        logger.warning(
            f"prompt_chain: could not determine node_id for column "
            f"{column.id if column else 'None'}"
        )
        return None

    try:
        ptn = PromptTemplateNode.objects.select_related("prompt_version").get(
            node_id=node_id, deleted=False
        )
    except PromptTemplateNode.DoesNotExist:
        logger.warning(f"prompt_chain: no PromptTemplateNode for node {node_id}")
        return None

    prompt_config = ptn.prompt_version.prompt_config_snapshot
    if not prompt_config:
        logger.warning(f"prompt_chain: empty prompt_config_snapshot for node {node_id}")
        return None

    return prompt_config


def _resolve_prompt_chain_from_run_prompter(runner, row):
    """
    Resolve prompt_chain from a RunPrompter's messages for base eval context.

    Uses populate_placeholders to substitute {{column_name}} variables,
    matching the per-EDT prompt_chain format (all roles, "role: content").
    """
    try:
        from model_hub.views.run_prompt import populate_placeholders

        messages = runner.run_prompter.messages
        if not messages:
            logger.warning(
                f"prompt_chain: RunPrompter {runner.run_prompter.id} has no messages"
            )
            return None

        col_id = runner.column.id if runner.column else None
        rp_config = getattr(runner.run_prompter, "run_prompt_config", {}) or {}
        populated_messages = populate_placeholders(
            messages,
            runner.dataset.id,
            row.id,
            col_id,
            model_name="",
            template_format=rp_config.get("template_format"),
        )

        return _format_messages_to_prompt_chain(populated_messages)
    except Exception as e:
        logger.error(
            f"Error resolving prompt_chain from RunPrompter "
            f"{getattr(runner.run_prompter, 'id', '?')}, "
            f"row {getattr(row, 'id', '?')}: {e}",
            exc_info=True,
        )
        return None


def _resolve_special_value(value, row, runner):
    """
    Resolve a special mapping value to actual data.

    Works in two contexts:
    1. Per-EDT eval: runner.experiment_dataset is set, runner.column = prompt output column.
    2. Base eval: runner.experiment_dataset is None but runner.column = experiment base column.
       This allows dependent evals to resolve 'output' against the experiment's base column.

    Special values:
    - 'output': Cell value from runner.column (prompt output or base column) for this row.
    - 'prompt_chain': Full prompt text with {{variables}} substituted from row cells.
    """
    if not runner or (
        not runner.experiment_dataset
        and not runner.column
        and not getattr(runner, "base_column", None)
    ):
        return None

    value_lower = value.lower()

    if value_lower == "output":
        # For base evals (no experiment_dataset), use base_column which preserves
        # the actual data column — self.column gets overwritten to the eval result
        # column by load_user_eval_metric when is_only_eval=True.
        if not runner.experiment_dataset and getattr(runner, "base_column", None):
            output_col = runner.base_column
        else:
            output_col = runner.column
        if not output_col:
            return None
        try:
            cell = Cell.objects.get(column=output_col, row=row, deleted=False)
            if cell.status == CellStatus.ERROR.value:
                raise ValueError(get_error_message("EVALUATION_NOT_FOR_ERROR_CELL"))
            return cell.value
        except Cell.DoesNotExist:
            return None

    elif value_lower == "prompt_chain":
        try:
            if not runner.experiment_dataset:
                # Fallback: resolve from RunPrompter if available
                # (base eval on run_prompt column)
                if getattr(runner, "run_prompter", None):
                    return _resolve_prompt_chain_from_run_prompter(runner, row)
                logger.warning(
                    "prompt_chain: runner.experiment_dataset is None "
                    "and no run_prompter"
                )
                return None
            epc = getattr(runner.experiment_dataset, "prompt_config", None)
            if epc:
                messages = epc.get_messages()
            else:
                # Check if this is an agent experiment
                eac = getattr(runner.experiment_dataset, "agent_config", None)
                if not eac:
                    logger.warning(
                        f"prompt_chain: no prompt_config or agent_config on EDT "
                        f"{runner.experiment_dataset.id}"
                    )
                    return None
                messages = _get_agent_node_messages(eac, runner.column)

            if not messages:
                logger.warning(
                    f"prompt_chain: could not resolve messages for EDT "
                    f"{runner.experiment_dataset.id}"
                )
                return None

            # Normalize: prompt_config_snapshot can be a wrapper dict
            # e.g. {'messages': [...], 'placeholders': [], 'configuration': {...}}
            if isinstance(messages, dict):
                messages = messages.get("messages", [messages])

            # Use populate_placeholders to resolve {{column_name}} variables,
            # same mechanism used during actual prompt execution.
            from model_hub.views.run_prompt import populate_placeholders

            col_id = runner.column.id if runner.column else None
            # Extract template_format from experiment prompt config if available
            tf = None
            if epc and hasattr(epc, "configuration"):
                tf = (epc.configuration or {}).get("template_format")
            populated_messages = populate_placeholders(
                messages,
                runner.dataset.id,
                row.id,
                col_id,
                model_name="",
                template_format=tf,
            )

            return _format_messages_to_prompt_chain(populated_messages)
        except Exception as e:
            logger.error(
                f"Error resolving prompt_chain for EDT "
                f"{getattr(runner.experiment_dataset, 'id', '?')}, "
                f"row {getattr(row, 'id', '?')}: {e}",
                exc_info=True,
            )
            return None

    return None


def process_mapping(
    mappings,
    row,
    replace_column_id=None,
    column_id=None,
    run_prompt_column=False,
    runner=None,
    eval_template_name=None,
):
    required_field = []
    mapping = []

    # Collect all column IDs that need to be fetched (extracting base column IDs from JSON paths)
    # Skip special mapping values — they resolve via runner context, not column lookup
    column_ids_to_fetch = set()
    for key, value in mappings.items():
        if key == "call_type":
            continue
        if isinstance(value, list):
            for item in value:
                if _is_special_mapping_value(item):
                    continue
                data = item if str(item) != str(replace_column_id) else column_id
                if data and not _is_knowledge_base_uuid(data):
                    base_col_id, _ = _extract_column_id_and_path(str(data))
                    column_ids_to_fetch.add(str(base_col_id))
        else:
            if _is_special_mapping_value(value):
                continue
            data = value if str(value) != str(replace_column_id) else column_id
            if data and not _is_knowledge_base_uuid(data):
                base_col_id, _ = _extract_column_id_and_path(str(data))
                column_ids_to_fetch.add(str(base_col_id))

    # Batch fetch all cells in a single query
    cells_by_column = {}
    if column_ids_to_fetch:
        cells = Cell.objects.filter(
            column__id__in=column_ids_to_fetch, row=row
        ).select_related("column")
        cells_by_column = {str(cell.column_id): cell for cell in cells}

    for key, value in mappings.items():
        if key == "call_type":
            required_field.append(key)
            mapping.append(value)
            continue
        if isinstance(value, list):
            data_list = []
            # Process each item in the list
            for item in value:
                # Check for special mapping values (output, prompt_chain)
                if _is_special_mapping_value(item):
                    resolved = _resolve_special_value(item, row, runner)
                    data_list.append(resolved)
                    continue

                data = item if str(item) != str(replace_column_id) else column_id
                try:
                    if data:
                        # Check if it's a KnowledgeBaseFile UUID
                        if _is_knowledge_base_uuid(data):
                            # Pass through UUID unchanged
                            data_list.append(str(data))
                        else:
                            # Treat as column ID and fetch cell value
                            # Extract column ID and JSON path
                            base_col_id, json_path = _extract_column_id_and_path(
                                str(data)
                            )
                            cell = cells_by_column.get(str(base_col_id))
                            if cell:
                                if cell.status == CellStatus.ERROR.value:
                                    raise ValueError(
                                        get_error_message(
                                            "EVALUATION_NOT_FOR_ERROR_CELL"
                                        )
                                    )
                                # Apply JSON path resolution if needed
                                cell_value = _get_cell_value_with_json_path(
                                    cell, json_path
                                )
                                data_list.append(cell_value)
                            else:
                                data_list.append(None)
                    else:
                        data_list.append(None)
                except Exception as e:
                    if str(e) == get_error_message("EVALUATION_NOT_FOR_ERROR_CELL"):
                        raise ValueError(  # noqa: B904
                            get_error_message("EVALUATION_NOT_FOR_ERROR_CELL")
                        )
                    data_list.append(None)
            mapping.append(data_list)
            required_field.append(key)
        else:
            # Check for special mapping values (output, prompt_chain)
            if _is_special_mapping_value(value):
                resolved = _resolve_special_value(value, row, runner)
                mapping.append(resolved)
                required_field.append(key)
                continue

            data = value if str(value) != str(replace_column_id) else column_id

            # For prompt_instruction_adherence: resolve "prompt" key to RunPrompter messages dict
            if (
                eval_template_name == "prompt_instruction_adherence"
                and key == "prompt"
                and data
                and not _is_knowledge_base_uuid(data)
            ):
                try:
                    base_col_id, _ = _extract_column_id_and_path(str(data))
                    prompt_column = Column.objects.get(id=base_col_id)
                    run_prompter = RunPrompter.objects.get(id=prompt_column.source_id)

                    system_messages = [
                        msg
                        for msg in run_prompter.messages
                        if msg.get("role") == "system"
                    ]
                    user_messages = [
                        msg
                        for msg in run_prompter.messages
                        if msg.get("role") == "user"
                    ]

                    prompt_dict = {
                        "system_prompt": system_messages,
                        "user_prompt": user_messages,
                    }
                    mapping.append(prompt_dict)
                    required_field.append(key)
                except (Column.DoesNotExist, RunPrompter.DoesNotExist) as e:
                    logger.error(
                        "failed_to_resolve_prompt_instruction_adherence_prompt",
                        column_id=str(data),
                        error=str(e),
                    )
                continue  # Skip normal cell resolution for this key

            try:
                if data:
                    # Check if it's a KnowledgeBaseFile UUID
                    if _is_knowledge_base_uuid(data):
                        # Pass through UUID unchanged
                        mapping.append(str(data))
                        required_field.append(key)
                    else:
                        # Treat as column ID and fetch cell value
                        # Extract column ID and JSON path
                        base_col_id, json_path = _extract_column_id_and_path(str(data))
                        cell = cells_by_column.get(str(base_col_id))
                        if cell:
                            if cell.status == CellStatus.ERROR.value:
                                raise ValueError(
                                    get_error_message("EVALUATION_NOT_FOR_ERROR_CELL")
                                )
                            # Apply JSON path resolution if needed
                            cell_value = _get_cell_value_with_json_path(cell, json_path)
                            mapping.append(cell_value)
                            required_field.append(key)
            except Exception as e:
                if str(e) == get_error_message("EVALUATION_NOT_FOR_ERROR_CELL"):
                    raise ValueError(
                        get_error_message("EVALUATION_NOT_FOR_ERROR_CELL")
                    ) from e
                logger.error(f"{e} e*****")

            if run_prompt_column:
                if key == "output" and not mappings.get("input"):
                    # Skip if value is a KnowledgeBaseFile UUID
                    if not _is_knowledge_base_uuid(value):
                        output_column = Column.objects.get(id=value)
                        prompt_column = RunPrompter.objects.get(
                            id=output_column.source_id
                        )
                        # Get the user prompt from messages
                        prompt = "\n".join(
                            [
                                runner._replace_dynamic_ids(
                                    content["text"], row
                                )  # Updated to handle new content structure
                                for message in prompt_column.messages
                                if message["role"] == "user"
                                for content in message["content"]
                                if content["type"] == "text"
                            ]
                        )
                        mapping.insert(0, prompt)
                        required_field.insert(0, "input")
    return required_field, mapping


class EvaluationRunner:
    def __init__(
        self,
        user_eval_metric_id,
        experiment_dataset=None,
        column=None,
        optimize=None,
        is_only_eval=False,
        format_output=False,
        cancel_event=None,
        futureagi_eval=False,
        protect=False,
        protect_flash=False,
        source=None,
        source_id=None,
        source_configs=None,
        sdk_uuid=None,
        organization_id=None,
        workspace_id=None,
        version_number=None,
    ):
        self.user_eval_metric_id = user_eval_metric_id
        self.column = column
        self.user_eval_metric = None
        self.experiment_dataset = experiment_dataset
        self.run_prompter = None  # Set for base eval on run_prompt columns
        self.base_column = None  # Base column for "output" resolution in base evals
        self.eval_template = None
        self.eval_class = None
        self.optimize = optimize
        self.replace_column_id = None
        self.is_only_eval = is_only_eval
        self.criteria = None
        self.dataset = None
        self.input_cols = None
        self.table_name = None
        self.input_types = None
        self.num_tables = None
        self.cancel_event = cancel_event
        self.futureagi_eval = futureagi_eval
        self.dataset_feedback_groups = {}
        self.df = None
        self.protect = protect
        self.protect_flash = protect_flash
        self.format_output_flag = format_output
        self.source = source
        self.source_id = str(source_id) if source_id else None
        self.source_configs = {} if source_configs is None else source_configs
        self.organization_id = organization_id
        self.workspace_id = workspace_id
        self.version_number = version_number
        self._resolved_version = None
        if not format_output:
            self._initialize_eval_metric()

    def get_few_shot_examples(self, mapping, required_field=None):
        """
        Get few-shot examples from existing feedback for an eval template using RAG

        Args:
            eval_template_id: ID of the EvalTemplate

        Returns:
            List of processed few-shot examples
        """
        # get_fewshots = RAG()
        embedding_manager = EmbeddingManager()

        all_examples = []
        try:
            if not self.organization_id:
                logger.warning(
                    "No organization_id available for filtering in get_few_shot_examples - returning empty examples"
                )
                # print(f"[FEEDBACK RAG] Skipped — no organization_id", flush=True)
                return all_examples

            # print(f"[FEEDBACK RAG] Querying eval_id={self.eval_template.id} org={self.organization_id} input_cols={required_field} inputs_preview={[str(v)[:60] for v in mapping]}", flush=True)
            start_time = datetime.now()
            examples = embedding_manager.retrieve_avg_rag_based_examples(
                eval_id=self.eval_template.id,
                inputs=mapping,
                input_cols=required_field,
                organization_id=self.organization_id,
                workspace_id=None,  # feedback is stored without workspace_id in all write paths
            )
            # print(f"[FEEDBACK RAG] Retrieved {len(examples)} examples", flush=True)
            end_time = datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()
            logger.info(
                f"retrieve_avg_rag_based_examples query took {elapsed_time:.2f} seconds to execute"
            )
            start_time = datetime.now()

            # Process examples into few-shot format
            dataset_few_shots = embedding_manager.process_examples(
                examples,
                inputs=required_field,
                feedback_col_name="feedback_comment",
                corrected_label_col_name="feedback_value",
            )
            end_time = datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()
            logger.info(
                f"process_examples query took {elapsed_time:.2f} seconds to execute"
            )
            all_examples.extend(dataset_few_shots)

        except Exception as e:
            logger.info(f"Error processing dataset {str(e)}")

        # get_fewshots.close()
        embedding_manager.close()
        return all_examples

    def _initialize_eval_metric(self):
        """Initialize and set status of user eval metric"""
        self.user_eval_metric = UserEvalMetric.objects.get(id=self.user_eval_metric_id)
        self.dataset = self.user_eval_metric.dataset

        if not self.organization_id and self.dataset:
            self.organization_id = self.dataset.organization.id
            self.workspace_id = (
                self.dataset.workspace.id if self.dataset.workspace else None
            )

        self.user_eval_metric.status = StatusType.RUNNING.value
        self.user_eval_metric.save(update_fields=["status"])

    def _get_column_config(self, dataset):
        """Get column configuration based on eval type"""
        logger.info(
            " ----- INSIDE EvaluationRunner : function _get_column_config -----"
        )
        output_type = infer_eval_result_column_data_type(self.eval_template)

        source = SourceChoices.EVALUATION.value
        source_id = self.user_eval_metric_id
        name = self.user_eval_metric.name

        if self.experiment_dataset:
            source = SourceChoices.EXPERIMENT_EVALUATION.value
            source_id = f"{self.experiment_dataset.id}-{self.column.id}-sourceid-{self.user_eval_metric_id}"
            name = f"{self.user_eval_metric.name}-{self.column.name}"
        elif self.optimize:
            source = SourceChoices.OPTIMISATION_EVALUATION.value
            source_id = f"{self.optimize.id}-sourceid-{self.user_eval_metric_id}"
            name = f"{self.user_eval_metric.name}-{self.column.name}"

        return {
            "name": name,
            "data_type": output_type,
            "source": source,
            "source_id": source_id,
            "dataset": dataset,
        }

    def _create_or_update_column(self, dataset, column_config, new_column=False):
        """Create new column or update existing one"""

        if self.is_only_eval and self.replace_column_id and not new_column:
            Cell.objects.filter(
                column__id=self.replace_column_id, deleted=False
            ).update(value=None, status=CellStatus.RUNNING.value, value_infos=None)
            return Column.objects.get(id=self.replace_column_id)

        # Use select_for_update to prevent race conditions when multiple async tasks
        # try to create the same column simultaneously
        with transaction.atomic():
            # Lock the dataset to serialize column creation
            dataset_obj = Dataset.no_workspace_objects.select_for_update().get(
                id=dataset.id
            )

            # Now try to get or create the column
            # This is safe because we have the dataset locked
            try:
                column, created = Column.objects.get_or_create(**column_config)
            except Exception:
                # Another task created it between our check and create
                # Retry to get the existing column
                column = Column.objects.get(
                    dataset=column_config["dataset"],
                    source=column_config["source"],
                    source_id=column_config["source_id"],
                    deleted=False,
                )
                created = False

            if created:
                # FIX: Set status to RUNNING for experiment eval columns.
                # Column model defaults to COMPLETED, but eval columns that haven't
                # processed yet should be RUNNING to prevent premature experiment completion.
                if self.experiment_dataset:
                    column.status = StatusType.RUNNING.value
                    column.save(update_fields=["status"])
                else:
                    column_order = dataset_obj.column_order
                    column_order.append(str(column.id))
                    dataset_obj.column_order = column_order
                    dataset_obj.save(update_fields=["column_order"])

            # Always (re)link the column to the EDT M2M for experiments.
            # M2M.add is idempotent, so safe on existing rows. This heals any
            # column that was created previously without the M2M link
            # (e.g. earlier reason-column code paths) — the grid serializer
            # filters by exp_dataset.columns, so unlinked columns never
            # render even if they exist in the DB.
            if self.experiment_dataset:
                self.experiment_dataset.columns.add(column)

        return column

    def _create_reason_column(self, dataset, reason_column_name, parent_column=None):
        """Create a reason column for the evaluation results.

        The reason column's source_id must key off its *eval* column id
        (canonical pattern `{eval_col.id}-sourceid-{metric_id}`), not the
        output/source column id. For datasets `self.replace_column_id`
        already resolves to the eval column id via the is_only_eval branch
        of load_user_eval_metric. For experiments it's the output column
        id, so either the caller passes `parent_column` explicitly
        (empty_or_create_evals_column has the just-created eval column in
        scope) or we look it up by the experiment eval column's
        deterministic source_id.
        """
        source = SourceChoices.EVALUATION_REASON.value
        source_id = f"{self.replace_column_id}-sourceid-{self.user_eval_metric_id}"
        if self.experiment_dataset:
            reason_column_name = (
                f"{self.user_eval_metric.name}-{self.column.name}-reason"
            )
            parent_col_id = None
            if parent_column is not None:
                parent_col_id = parent_column.id
            else:
                eval_col_source_id = f"{self.experiment_dataset.id}-{self.column.id}-sourceid-{self.user_eval_metric_id}"
                parent_col_id = (
                    Column.objects.filter(
                        source=SourceChoices.EXPERIMENT_EVALUATION.value,
                        source_id=eval_col_source_id,
                        deleted=False,
                    )
                    .values_list("id", flat=True)
                    .first()
                )
            if parent_col_id is not None:
                source_id = f"{parent_col_id}-sourceid-{self.user_eval_metric_id}"
        elif self.optimize:
            source = SourceChoices.OPTIMISATION_EVALUATION.value
            source_id = f"{self.optimize.id}-sourceid-{self.user_eval_metric_id}"
        column_config = {
            "name": reason_column_name,
            "data_type": "text",
            "source": source,
            "source_id": source_id,
            "dataset": dataset,
        }
        return self._create_or_update_column(dataset, column_config, new_column=True)

    def _get_input_type(self, input):
        """Determine input types for a dictionary of inputs."""
        input_type = {}
        for key, value in input.items():
            input_type[key] = detect_input_type(value)
        return input_type

    def _process_eval_result(self, row, mappings):
        """Process evaluation for a single row"""
        config = self.update_config_list_values(
            self.user_eval_metric.config.get("config"), row
        )
        status = CellStatus.PASS.value
        api_call_log_row = None

        try:
            # Extract base column IDs from mappings (remove JSON paths like uuid.field)
            # Skip special mapping values like "output" and "prompt_chain"
            base_column_ids = []
            for v in mappings.values():
                if v and not _is_special_mapping_value(v):
                    base_col_id, _ = _extract_column_id_and_path(str(v))
                    if base_col_id:
                        base_column_ids.append(base_col_id)

            cols = Column.objects.filter(id__in=base_column_ids).values(
                "id", "data_type"
            )
            col_map = {str(col["id"]): col["data_type"] for col in cols}
            api_call_log_row = self._handle_api_call(row, mappings, config)
            (
                eval_result,
                required_field_error,
                mapping_error,
                config_error,
                eval_instance,
            ) = self._run_evaluation(row, mappings, config)
            # Format response first so we can access the formatted data
            response = self._format_response(eval_result)
            # Reason column is always-on for experiments (matches dataset
            # behavior in develop_dataset.py:7102-7128). For datasets we
            # still respect the legacy config.reason_column flag.
            wants_reason = (
                bool(self.experiment_dataset)
                or self.user_eval_metric.config.get("reason_column")
            )
            if wants_reason and not self.optimize:
                reason_column_name = f"{self.user_eval_metric.name}-reason"
                reason_column = self._create_reason_column(
                    self.dataset, reason_column_name
                )
                self._create_reason_cell(
                    self.dataset,
                    reason_column,
                    row,
                    response,
                    response.get("reason"),
                    CellStatus.PASS.value,
                )
            value = self.format_output(response, row)

            config_dict = json.loads(api_call_log_row.config)
            config_dict.update(
                {
                    "output": {
                        "output": value,
                        "reason": (
                            response["reason"] if "reason" in response.keys() else None
                        ),
                    }
                }
            )
            input_types = {}
            for key, mapping_value in mappings.items():
                # Extract base column ID to look up data type
                base_col_id, _ = (
                    _extract_column_id_and_path(str(mapping_value))
                    if mapping_value
                    else (None, None)
                )
                data_type = col_map.get(str(base_col_id)) if base_col_id else None
                input_types[key] = (
                    data_type if data_type in ["image", "images", "audio"] else "text"
                )
            config_dict.update({"input_data_types": input_types})
            # logger.info(f' ----- INSIDE EvaluationRunner : function _process_eval_result | config_dict : {config_dict} -----')
            api_call_log_row.config = json.dumps(config_dict)
            api_call_log_row.save()

            self._handle_api_call_status(api_call_log_row, CellStatus.PASS.value)

            # Post-eval cost-based usage emit
            try:
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

                billing_config = BillingConfig.get()
                eval_cost = getattr(eval_instance, "cost", {})
                llm_cost = eval_cost.get("total_cost", 0)
                per_run_fee = billing_config.get_eval_per_run_fee()
                actual_cost = llm_cost + per_run_fee

                # Also compute fallback cost for comparison logging
                _fallback_cost = 0
                try:
                    from agentic_eval.core_evals.fi_utils.token_count_helper import (
                        calculate_total_cost,
                    )

                    _token_usage = getattr(eval_instance, "token_usage", {})
                    _model = self.user_eval_metric.model or "unknown"
                    _fallback = calculate_total_cost(_model, _token_usage)
                    _fallback_cost = _fallback.get("total_cost", 0)
                except Exception:
                    pass

                logger.info(
                    "eval_cost_breakdown",
                    eval_id=str(self.user_eval_metric.id),
                    model=self.user_eval_metric.model,
                    llm_cost=llm_cost,
                    per_run_fee=per_run_fee,
                    actual_cost=actual_cost,
                    fallback_calculated_cost=_fallback_cost,
                    token_usage=getattr(eval_instance, "token_usage", {}),
                )

                credits = billing_config.calculate_ai_credits(actual_cost)
                emit_org_id = str(
                    self.organization_id
                    or (
                        self.user_eval_metric.organization.id
                        if self.user_eval_metric
                        else ""
                    )
                )

                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None

                _is_code_eval = getattr(self.eval_template, "eval_type", "") == "code"
                eval_event_type = (
                    BillingEventType.CODE_EVALUATOR.value
                    if _is_code_eval
                    else _get_api_call_type(self.user_eval_metric.model)
                )
                emit(
                    UsageEvent(
                        org_id=emit_org_id,
                        event_type=eval_event_type,
                        amount=credits,
                        properties={
                            "source": self.source,
                            "source_id": str(
                                self.source_id
                                or (
                                    str(self.user_eval_metric.template.id)
                                    if self.user_eval_metric
                                    else ""
                                )
                            ),
                            "raw_cost_usd": str(actual_cost),
                        },
                    )
                )
            except Exception:
                pass

            # Trigger error localization when enabled either on the dataset binding
            # (UserEvalMetric) or directly on the eval template.
            should_run_error_localizer = bool(
                self.user_eval_metric.error_localizer
                or getattr(self.user_eval_metric.template, "error_localizer_enabled", False)
            )
            if should_run_error_localizer:
                from model_hub.tasks.user_evaluation import (
                    trigger_error_localization_for_column,
                )

                cell = Cell.objects.filter(
                    column__id=self.replace_column_id, row=row, deleted=False
                ).first()

                trigger_error_localization_for_column(
                    eval_template=self.user_eval_metric.template,
                    config=config_error,
                    required_field=required_field_error,
                    mapping=mapping_error,
                    eval_result=value,
                    response=response,
                    cell=cell,
                    log_id=str(api_call_log_row.log_id),
                )

        except Exception as e:
            logger.exception(f"Error in evaluation of row: {str(e)}")
            traceback.print_exc()

            # Use the centralized error handling function
            error_message = get_specific_error_message(e)

            response, status, value = self._handle_error(error_message)
            self._handle_api_call_status(api_call_log_row, CellStatus.ERROR.value)

            # Create reason column and cell with error status. Always-on for
            # experiments; respect legacy flag for datasets.
            wants_reason = (
                bool(self.experiment_dataset)
                or self.user_eval_metric.config.get("reason_column")
            )
            if wants_reason and not self.optimize:
                reason_column_name = f"{self.user_eval_metric.name}-reason"
                reason_column = self._create_reason_column(
                    self.dataset, reason_column_name
                )
                self._create_reason_cell(
                    self.dataset,
                    reason_column,
                    row,
                    {"reason": "No reasoning available. Please rerun the evaluation."},
                    "No reasoning available. Please rerun the evaluation.",
                    CellStatus.ERROR.value,
                )

        return response, status, value

    def _handle_api_call(
        self,
        row,
        mappings,
        config=None,
        eval_template=None,
        org=None,
        preview=False,
        req_map=None,
    ):
        if req_map is None:
            req_map = {}
        """Handle API call logging and validation"""
        reference_id = (
            str(self.user_eval_metric_id)
            if self.user_eval_metric_id
            else eval_template.id
        )

        model = (
            self.user_eval_metric.model
            if self.user_eval_metric
            else ModelChoices.TURING_LARGE.value
        )
        api_call_type = _get_api_call_type(model)

        is_futureagi_eval = self.futureagi_eval

        if not config.get("preview", False):
            input_token_count = self._get_input_token_count(row, mappings, config)
        else:
            input_token_count = self._get_input_token_count(
                row, mappings, config={"mapping": mappings}
            )

        api_call_config = {
            "reference_id": reference_id,
            "is_futureagi_eval": is_futureagi_eval,
            "input_tokens": int(input_token_count),
        }
        if self.source_configs:
            api_call_config.update(self.source_configs)
        # else:
        api_call_config.update(config)
        if preview:
            required_field = req_map.get("required_field")
            mapping = req_map.get("mapping")
            api_call_config.update(
                {
                    "mappings": self.map_fields(
                        required_field, mapping, eval_template, config
                    )
                }
            )
        else:
            required_field, mapping = self._prepare_mapping_data(row, mappings)
            api_call_config.update(
                {
                    "mappings": self.map_fields(
                        required_field=required_field, mapping=mapping, config=config
                    )
                }
            )

        api_call_log_row = log_and_deduct_cost_for_api_request(
            org if org else self.user_eval_metric.organization,
            api_call_type,
            config=api_call_config,
            source=self.source,
            source_id=(
                self.source_id
                if self.source_id
                else (
                    str(self.user_eval_metric.template.id)
                    if self.user_eval_metric
                    else None
                )
            ),
            workspace=row.dataset.workspace,
        )

        if not api_call_log_row:
            raise ValueError("API call not allowed : Error validating the api call.")

        if api_call_log_row.status != APICallStatusChoices.PROCESSING.value:
            # Use the dedicated function to get the appropriate error message for this status
            error_message = get_error_for_api_status(api_call_log_row.status)
            raise ValueError(error_message)

        return api_call_log_row

    def _create_cell(self, dataset, column, row, response, value, status):
        """Create or update cell with evaluation results"""
        # Guard: don't write cell if experiment was cancelled while this
        # thread was running in the ThreadPoolExecutor.
        experiment_id = self.source_configs.get("experiment_id")
        if experiment_id:
            from model_hub.services.experiment_utils import is_experiment_cancelled

            if is_experiment_cancelled(uuid.UUID(str(experiment_id))):
                return

        # Guard: user issued a Stop on this eval via StopUserEvalView; the
        # eval's cells were reset to "User stopped evaluation" — don't let
        # a late worker finish overwrite that state.
        from model_hub.services.experiment_utils import is_user_eval_stopped

        if is_user_eval_stopped(self.user_eval_metric_id):
            return

        cell_data = {
            "dataset": dataset,
            "column": column,
            "row": row,
            "value_infos": json.dumps(response),
            "value": value,
            "status": status,
        }

        if self.is_only_eval and self.replace_column_id:
            cell = Cell.objects.filter(
                column__id=self.replace_column_id, row=row, deleted=False
            ).first()
            if cell:
                try:
                    for key, val in cell_data.items():
                        setattr(cell, key, val)
                    cell.status = status
                    cell.save()
                    return
                except Exception as e:
                    logger.error(f"{e}")

        try:
            # Create or update cell
            Cell.objects.update_or_create(
                dataset=dataset,
                column=column,
                row=row,
                defaults={
                    "value_infos": cell_data["value_infos"],
                    "value": cell_data["value"],
                    "status": cell_data["status"],
                },
            )
        except Exception as e:
            logger.exception(f"Error updating or creating cell: {str(e)}")

    def _get_required_fields_and_mappings(
        self, user_eval_metric=None, mapping=None, config=None, required_field=None
    ):
        """
        Get required fields and their mappings from user evaluation metric configuration.

        Returns:
            tuple: (required_fields, field_mappings) where:
                - required_fields (list): List of required field IDs
                - field_mappings (dict): Dictionary mapping field names to their IDs
        """

        if user_eval_metric:
            self.user_eval_metric = user_eval_metric
            self.eval_template = user_eval_metric.template

        if not self.user_eval_metric or not self.eval_template:
            return [], {}

        run_prompt_column = self.eval_template.config.get("run_prompt_column", False)
        mappings = self.user_eval_metric.config.get("mapping")

        col_ids = list(mappings.values())
        if not mapping:
            mapping = col_ids  #  just to get required fields
        final_mapping = []
        final_required_field = []

        for key, data in enumerate(mapping):
            if data:
                final_mapping.append(data)
                final_required_field.append(col_ids[key])
            if run_prompt_column:
                break
        if self.eval_template.config.get("eval_type_id") == "DeterministicEvaluator":
            return final_required_field, final_mapping

        return final_required_field, final_mapping

    def map_fields(
        self, required_field, mapping, eval_template=None, config=None, bypass=False
    ):
        if eval_template:
            self.eval_template = eval_template

            if not self.organization_id and eval_template.organization:
                self.organization_id = eval_template.organization.id
                self.workspace_id = (
                    eval_template.workspace.id if eval_template.workspace else None
                )

        if self.eval_template and self.futureagi_eval:
            if bypass:
                final_required_field = []
                final_mapping = []
                for key, data in enumerate(mapping):
                    if data:
                        final_mapping.append(data)
                        final_required_field.append(key)
            else:
                (
                    final_required_field,
                    final_mapping,
                ) = self._get_required_fields_and_mappings(
                    user_eval_metric=self.user_eval_metric,
                    mapping=mapping,
                    config=config,
                    required_field=required_field,
                )

            few_shot_examples = self.get_few_shot_examples(
                final_mapping, final_required_field
            )

            shot_count = (
                len(few_shot_examples)
                if isinstance(few_shot_examples, list)
                else (1 if few_shot_examples else 0)
            )
            # print(f"[FEEDBACK INJECT] map_fields injecting fewshots : {few_shot_examples} for AgentEvaluator/CustomPromptEvaluator eval",flush=True)
            # print(f"[FEEDBACK INJECT] map_fields injecting {shot_count} few-shot examples for eval_template={self.eval_template.id if self.eval_template else None}", flush=True)
            required_field.append("few_shots")
            mapping.append(few_shot_examples)

        if (
            self.eval_template
            and self.futureagi_eval
            and "criteria" not in required_field
        ):
            required_field.append("criteria")
            mapping.append(
                self.eval_template.criteria if not self.criteria else self.criteria
            )

        if self.futureagi_eval:
            required_field.append("eval_name")

            mapping.append(self.eval_template.name)

            required_field.append("required_keys")
            if bypass:
                mapping.append(config.get("required_keys"))
            else:
                mapping.append(self.eval_template.config.get("required_keys"))

            # Pass param_modalities for validation
            if "param_modalities" in self.eval_template.config:
                required_field.append("param_modalities")
                mapping.append(self.eval_template.config.get("param_modalities"))

            # Pass parameter descriptions so deterministic evaluator can provide
            # explicit variable-to-key context to the model.
            if "config_params_desc" in self.eval_template.config:
                required_field.append("config_params_desc")
                if bypass:
                    mapping.append(config.get("config_params_desc", {}))
                else:
                    mapping.append(self.eval_template.config.get("config_params_desc"))

        if self.eval_template.config.get("eval_type_id") in (
            "CustomPromptEvaluator",
            "AgentEvaluator",
        ):
            from model_hub.models.choices import OwnerChoices

            # Fetch few-shot feedback examples for these eval types
            # (not covered by the futureagi_eval block above)
            if not self.futureagi_eval and "few_shots" not in required_field:
                print(
                    f"[FEEDBACK DEBUG] get_few_shot_examples called with mapping={[str(v)[:80] for v in mapping]} required_field={required_field} org_id={self.organization_id} eval_template={self.eval_template.id if self.eval_template else None}",
                    flush=True,
                )
                few_shot_examples = self.get_few_shot_examples(mapping, required_field)
                shot_count = (
                    len(few_shot_examples)
                    if isinstance(few_shot_examples, list)
                    else (1 if few_shot_examples else 0)
                )
                # print(f"[FEEDBACK INJECT] map_fields injecting fewshots : {few_shot_examples} for AgentEvaluator/CustomPromptEvaluator eval",flush=True)
                # print(f"[FEEDBACK INJECT] map_fields injecting {shot_count} few-shot examples for AgentEvaluator/CustomPromptEvaluator eval_template={self.eval_template.id if self.eval_template else None}", flush=True)
                required_field.append("few_shots")
                mapping.append(few_shot_examples)

            template_required_keys = (
                self.eval_template.config.get("required_keys") or []
            )
            required_field.append("required_keys")
            mapping.append(template_required_keys)

            # Forward optional_keys so the evaluator knows which keys are
            # allowed to be missing at run time.
            #
            # Policy:
            #   - System evals: honor the explicit `optional_keys` list from
            #     the YAML (enforces truly-required keys).
            #   - User evals: treat ALL required_keys as optional. Users can
            #     use Jinja2 templating (`{% if input %}...{% endif %}`) in
            #     their rule_prompt to handle nulls however they want — we
            #     shouldn't block the run when a field is missing.
            is_system_eval = (
                getattr(self.eval_template, "owner", None) == OwnerChoices.SYSTEM.value
            )
            if is_system_eval:
                declared_optional = self.eval_template.config.get("optional_keys")
                if declared_optional is not None:
                    required_field.append("optional_keys")
                    mapping.append(declared_optional)
            else:
                # User eval → everything is optional.
                required_field.append("optional_keys")
                mapping.append(list(template_required_keys))
        elif self.user_eval_metric_id == "CustomPromptEvaluator":
            required_field.append("required_keys")
            mapping.append(config.get("required_keys"))

        return dict(zip(required_field, mapping, strict=False))

    def load_user_eval_metric(self):
        """Load UserEvalMetric and EvalTemplate based on ID."""
        try:
            self.criteria = None
            self.user_eval_metric = UserEvalMetric.objects.get(
                id=self.user_eval_metric_id
            )
            # Only set dataset if not already explicitly set by caller
            # (e.g., for base evaluations where experiment.dataset differs from user_eval_metric.dataset)
            if self.dataset is None:
                self.dataset = self.user_eval_metric.dataset
            self.eval_template = self.user_eval_metric.template

            if not self.organization_id and self.dataset:
                self.organization_id = self.dataset.organization.id
                self.workspace_id = (
                    self.dataset.workspace.id if self.dataset.workspace else None
                )

            self.futureagi_eval = (
                True
                if self.eval_template.config.get("eval_type_id") in FUTUREAGI_EVAL_TYPES
                else False
            )
            logger.info(
                f" ----- INSIDE EvaluationRunner : function load_user_eval_metric | futureagi_eval : {self.futureagi_eval} -----"
            )
            if self.experiment_dataset:
                self.replace_column_id = self.column.id if self.column else None

            if self.optimize:
                self.replace_column_id = (
                    self.optimize.column.id if self.optimize.column else None
                )
            if self.is_only_eval:
                # find the column with same source and source_id as the user eval metric
                self.replace_column_id = (
                    Column.objects.filter(
                        source=SourceChoices.EVALUATION.value,
                        source_id=str(self.user_eval_metric_id),
                        deleted=False,
                    )
                    .order_by("created_at")
                    .values_list("id", flat=True)
                )
                if self.replace_column_id:
                    self.replace_column_id = self.replace_column_id[0]
                    self.column = Column.objects.get(id=self.replace_column_id)
                # if  self.user_eval_metric.replace_column_id:
                #     self.replace_column_id = self.user_eval_metric.replace_column_id
        except ObjectDoesNotExist:
            raise ValueError(  # noqa: B904
                "Invalid UserEvalMetric ID or EvalTemplate does not exist."
            )

        # Fetch the eval class from the eval_type_id in the config
        eval_type_id = self.eval_template.config.get("eval_type_id")
        from evaluations.engine.registry import get_eval_class

        self.eval_class = get_eval_class(eval_type_id)

    def update_config_list_values(self, config, row=None):
        if config:
            config = config.copy()
            for key, value in config.items():
                # Check if the value is a comma-separated string
                if (
                    isinstance(value, str)
                    and "," in value
                    and key not in ["rule_prompt", "criteria"]
                ):
                    # Split the string by commas and update the value as a list
                    config[key] = value.split(",")
                # Check if the key is 'comparator' and if its value matches a known function
                elif key == "comparator" and isinstance(value, str):
                    comparator_class = globals().get(value)
                    if comparator_class is None:
                        raise ValueError(f"Comparator '{value}' not found.")
                    config[key] = comparator_class()  # Instantiate comparator
                # Handle both string and list values for dynamic ID replacement
                if isinstance(value, str):
                    if key != "rule_prompt":
                        config[key] = self._replace_dynamic_ids(value, row)
                elif isinstance(value, list):
                    # Process each item in the list
                    config[key] = [
                        (
                            self._replace_dynamic_ids(item, row)
                            if isinstance(item, str)
                            else item
                        )
                        for item in value
                    ]

            return config
        return {}

    def _replace_dynamic_ids(self, value, row):
        """Helper method to replace dynamic IDs in a string value.

        Supports:
        - {{column_uuid}} - Gets full cell value by column UUID
        - {{column_uuid.json.path}} - Gets nested JSON value by UUID
        - {{column_name}} - Gets full cell value by column name
        - {{column_name.json.path}} - Gets nested JSON value by name (e.g., {{input.year}})
        """
        if not isinstance(value, str):
            return value

        if re.search(r"\{{.*?\}}", value):
            matches = re.findall(r"\{{(.*?)\}}", value)
            for match in matches:
                logger.debug(f"Processing dynamic ID match: {match}")
                try:
                    # Extract column reference and optional JSON path
                    column_ref, json_path = _extract_column_id_and_path(match)

                    # Handle placeholder replacement for output column
                    if str(column_ref) == str(self.replace_column_id):
                        column_ref = str(self.column.id)

                    if row:
                        try:
                            # Try to find column by UUID first, then by name
                            column = _resolve_column_reference(
                                column_ref, self.dataset.id
                            )
                            if not column:
                                raise ValueError(
                                    f"Column '{column_ref}' not found in dataset"
                                )

                            cell = Cell.objects.get(column=column, row=row)
                            if cell.status == CellStatus.ERROR.value:
                                raise ValueError(
                                    get_error_message("EVALUATION_NOT_FOR_ERROR_CELL")
                                )
                            # Apply JSON path resolution if needed
                            cell_value = _get_cell_value_with_json_path(cell, json_path)
                            value = value.replace(
                                f"{{{{{match}}}}}",
                                str(cell_value) if cell_value is not None else "",
                            )
                        except Cell.DoesNotExist:
                            raise ValueError(  # noqa: B904
                                f"Cell with column='{column_ref}' and row={row} not found."
                            )
                        except Exception as e:
                            if str(e) == get_error_message(
                                "EVALUATION_NOT_FOR_ERROR_CELL"
                            ):
                                raise ValueError(  # noqa: B904
                                    get_error_message("EVALUATION_NOT_FOR_ERROR_CELL")
                                )
                            raise ValueError(  # noqa: B904
                                f"Error resolving column '{column_ref}': {str(e)}"
                            )
                    else:
                        value = value.replace(f"{{{{{match}}}}}", match)
                except ValueError as e:
                    logger.error(f"Error replacing dynamic ID: {str(e)}")
                    raise e
        return value

    def format_output(self, result_data, row=None, eval_template=None):
        if not self.eval_template:
            self.eval_template = eval_template

            if (
                eval_template
                and not self.organization_id
                and eval_template.organization
            ):
                self.organization_id = eval_template.organization.id
                self.workspace_id = (
                    eval_template.workspace.id if eval_template.workspace else None
                )

        # Non-dataset callers: delegate to the extracted pure function
        if row is None:
            from evaluations.engine.formatting import format_eval_value

            return format_eval_value(result_data, self.eval_template)
        output_type = result_data.get("output")
        # If choice_scores exist, force choices processing
        if (
            self.eval_template
            and self.eval_template.choice_scores
            and output_type not in ("Pass/Fail",)
        ):
            output_type = "choices"
        value = None

        # Handle output type specific processing
        if output_type == "Pass/Fail":
            data = result_data.get("data")
            # Function evals return data as dict (input kwargs), use failure flag
            if isinstance(data, dict):
                value = "Passed" if not result_data.get("failure") else "Failed"
            elif (
                self.eval_template.config.get("eval_type_id")
                == "DeterministicEvaluator"
            ):
                if not self.eval_template.multi_choice:
                    data = data if data else []
                    value = data[0] if data else None
                else:
                    value = data
            else:
                value = "Passed" if not result_data.get("failure") else "Failed"
        elif output_type == "score":
            metrics = result_data.get("metrics", [])
            if not metrics:
                value = None
            else:
                metrics = metrics[:1]
                if len(metrics) == 1:
                    value = metrics[0].get("value")
                elif row:
                    # Create new columns for each metric
                    dataset = self.user_eval_metric.dataset
                    for metric in metrics[1:]:
                        metric_column_config = {
                            "name": f"{self.user_eval_metric.name}-{metric['id']}",
                            "data_type": "float",  # Assuming numeric values, adjust if needed
                            "source": (
                                self.column.source
                                if hasattr(self, "column")
                                else SourceChoices.EVALUATION.value
                            ),
                            "source_id": (
                                self.column.source_id
                                if hasattr(self, "column")
                                else self.user_eval_metric_id
                            ),
                            "dataset": dataset,
                        }
                        metric_column = self._create_or_update_column(
                            dataset, metric_column_config, new_column=True
                        )

                        # Create cell for this metric using _create_tags_cell
                        self._create_tags_cell(
                            dataset=dataset,
                            column=metric_column,
                            row=row,
                            response=result_data,
                            tag_values=metric.get("value"),
                            status=CellStatus.PASS.value,
                        )

                    # Return the complete metrics dictionary for the main column
                    value = metrics[0].get("value") if metrics else None
                else:
                    value = metrics[0].get("value") if metrics else None

        elif output_type == "numeric":
            # Handle numeric output type (e.g., clip_score, fid_score)
            metrics = result_data.get("metrics", [])
            if metrics:
                value = metrics[0].get("value")
            else:
                value = None
        elif output_type == "reason":
            value = result_data.get("reason")
        elif output_type == "choices":
            choice_result = result_data.get("data")
            # Extract choice from nested {"result": "choice"} objects
            if isinstance(choice_result, dict):
                choice_result = (
                    choice_result.get("result")
                    or choice_result.get("choice")
                    or next(iter(choice_result.values()), choice_result)
                )
            # Map choice string to numeric score via choice_scores
            from model_hub.utils.scoring import apply_choice_scores

            if (
                self.eval_template
                and self.eval_template.choice_scores
                and isinstance(choice_result, str)
            ):
                mapped = apply_choice_scores(
                    choice_result, self.eval_template.choice_scores
                )
                value = {
                    "score": mapped if mapped is not None else 0.0,
                    "choice": choice_result,
                }
            elif (
                self.eval_template
                and self.eval_template.choice_scores
                and isinstance(choice_result, list)
                and choice_result
            ):
                first = str(choice_result[0])
                mapped = apply_choice_scores(first, self.eval_template.choice_scores)
                value = {
                    "score": mapped if mapped is not None else 0.0,
                    "choices": choice_result,
                }
            else:
                value = choice_result

        gc.collect()

        return value

    def _run_evaluation(self, row, mappings, config):
        """Run one evaluation for a single row with input validation."""
        # Build ordered inputs from mapping keys and row values.
        required_field, mapping = self._prepare_mapping_data(row, mappings)
        config_copy = config.copy()
        eval_instance = self._create_eval_instance(
            config=config, model=self.user_eval_metric.model
        )

        config_error = self._prepare_eval_config(config_copy)
        required_field_error = required_field
        mapping_error = mapping

        def _is_empty_value(value):
            """Return True when value is effectively empty."""
            if value is None:
                return True
            if isinstance(value, str) and not value.strip():
                return True
            if isinstance(value, list) and all(not item for item in value):
                return True
            return False

        def _is_mapped(mapping_config):
            """Return True when a key is mapped to any column."""
            if isinstance(mapping_config, list):
                return any(mapping_config)
            return bool(mapping_config)

        def _has_valid_mapping(mapping_config, dataset_id):
            """Return True when mapping resolves to a valid column or KB UUID."""
            items = (
                mapping_config if isinstance(mapping_config, list) else [mapping_config]
            )
            for item in items:
                if not item:
                    continue
                if _is_knowledge_base_uuid(item):
                    return True
                base_col_id, _ = _extract_column_id_and_path(str(item))
                if _resolve_column_reference(base_col_id, dataset_id):
                    return True
            return False

        # Use eval template config with row mappings.
        required_keys = []
        optional_keys = []
        if getattr(self.eval_template, "config", None):
            required_keys = self.eval_template.config.get("required_keys", [])
            optional_keys = self.eval_template.config.get("optional_keys", [])

        # Validate mapped required/optional keys only.
        keys_to_check = list(set(required_keys) | set(optional_keys))
        if keys_to_check:
            for key in keys_to_check:
                # Mapping config indicates whether the user mapped this key.
                mapping_config = (
                    mappings.get(key) if isinstance(mappings, dict) else None
                )
                # Skip unmapped keys.
                if not _is_mapped(mapping_config):
                    continue

                # Raise if mapped key has no row value.
                if key not in required_field:
                    if not _has_valid_mapping(mapping_config, row.dataset_id):
                        raise ValueError(
                            f"Invalid mapping for '{key}'. Please check your input mapping."
                        )
                    raise ValueError(
                        f"No input received for '{key}'. Please check your input."
                    )

                # Map back from key to row value via the ordered lists.
                value = mapping[required_field.index(key)]
                if _is_empty_value(value):
                    raise ValueError(
                        f"No input received for '{key}'. Please check your input."
                    )

        # Validate param modalities for function evals (deterministic evals
        # validate inside their own _validate_param_modalities).
        param_modalities = (
            self.eval_template.config.get("param_modalities", {})
            if getattr(self.eval_template, "config", None)
            else {}
        )
        if param_modalities and not self.futureagi_eval:
            for key in required_keys:
                if key not in param_modalities or key not in required_field:
                    continue
                value = mapping[required_field.index(key)]
                if value is None:
                    continue
                detected = detect_input_type(value)
                if not detected:
                    continue
                column_type = next(iter(detected.values()), None)
                if column_type is None:
                    continue
                supported = [
                    m.lower() if isinstance(m, str) else str(m).lower()
                    for m in param_modalities[key]
                ]
                col_lower = (
                    column_type.lower()
                    if isinstance(column_type, str)
                    else str(column_type).lower()
                )
                if col_lower not in supported:
                    allowed = ", ".join(str(m).title() for m in param_modalities[key])
                    received = (
                        column_type.title()
                        if isinstance(column_type, str)
                        else str(column_type).title()
                    )
                    raise ValueError(
                        f"Input type mismatch for parameter '{key}': "
                        f"Expected {allowed}, but received {received}. "
                        f"Please check your evaluation mapping configuration and ensure "
                        f"the correct input type is mapped to '{key}'."
                    )

        _mapped = self.map_fields(
            required_field=required_field, mapping=mapping, config=config
        )

        # Inject ground truth config if enabled on the template
        if getattr(self.eval_template, "config", None):
            gt_config_in_template = self.eval_template.config.get("ground_truth")
            if gt_config_in_template and gt_config_in_template.get("enabled"):
                from model_hub.utils.ground_truth_retrieval import (
                    load_ground_truth_config,
                )

                gt_config = load_ground_truth_config(self.eval_template)
                if gt_config:
                    try:
                        from model_hub.models.evals_metric import EvalGroundTruth

                        gt_obj = EvalGroundTruth.objects.filter(
                            id=gt_config["ground_truth_id"], deleted=False
                        ).first()
                        if gt_obj:
                            gt_config["embedding_status"] = gt_obj.embedding_status
                    except Exception:
                        pass
                    _mapped["ground_truth_config"] = gt_config

        # For code evals, inject static user-defined params stored in the
        # UserEvalMetric config so they reach evaluate() as **kwargs.
        if getattr(self.eval_template, "eval_type", "") == "code":
            user_metric_params = {}
            if self.user_eval_metric:
                user_metric_params = self.user_eval_metric.config.get("params", {})
            elif isinstance(config, dict):
                user_metric_params = config.get("params", {})
            if isinstance(user_metric_params, dict):
                _mapped.update(user_metric_params)

            # Preprocess inputs for code evals that need external data (e.g. CLIP embeddings)
            from evaluations.engine.preprocessing import preprocess_inputs

            _mapped = preprocess_inputs(self.eval_template.name, _mapped)

        # Inject row_context when full_row data injection is enabled.
        # data_injection lives in the user's eval metric config (run_config)
        # or the eval template config — check both.
        _di_raw = {}
        if self.user_eval_metric and self.user_eval_metric.config:
            _uem_cfg = self.user_eval_metric.config
            _di_raw = (
                _uem_cfg.get("run_config", {}).get("data_injection", {})
                or _uem_cfg.get("data_injection", {})
            )
        if not _di_raw and self.eval_template:
            _di_raw = self.eval_template.config.get("data_injection", {})
        _di = _di_normalize(_di_raw)

        if _di["full_row"] and "row_context" not in _mapped:
            try:
                row_dict = {}
                cells = Cell.objects.filter(
                    row=row, deleted=False
                ).select_related("column")
                for cell in cells:
                    col_name = cell.column.name if cell.column else None
                    if not col_name:
                        continue
                    val = cell.value
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    row_dict[col_name] = val
                if row_dict:
                    _mapped["row_context"] = row_dict
            except Exception as e:
                logger.warning("eval_runner_row_context_build_failed", error=str(e))

        return (
            eval_instance.run(**_mapped),
            required_field_error,
            mapping_error,
            config_error,
            eval_instance,
        )

    def _prepare_mapping_data(self, row, mappings):
        """Prepare mapping data for evaluation"""
        required_field = []
        mapping = []
        required_field, mapping = process_mapping(
            mappings,
            row,
            self.replace_column_id,
            self.column.id,
            self.eval_template.config.get("run_prompt_column", False),
            runner=self,
            eval_template_name=self.eval_template.name,
        )

        return required_field, mapping

    def _get_all_column_ids_being_used(self, user_eval_metric=None, config=None):
        if user_eval_metric:
            self.user_eval_metric = user_eval_metric

        # here we first retrieve all column ids being used in this eval
        # we retrieve this via getting the column ids inside config.mapping
        # and the column ids inside config.config.input

        # get all column ids from the mapping entry
        if not config:
            config = self.user_eval_metric.config
        mapping_column_ids_initial = list(config.get("mapping", {}).values())
        mapping_column_ids = [
            col_id
            for col_id in mapping_column_ids_initial
            if (
                isinstance(col_id, int)
                or (isinstance(col_id, str) and (self._is_uuid(col_id)))
            )
        ]

        # get all column ids from the config.input entry which is used in
        # deterministic evals
        deterministic_column_ids = config.get("config", {}).get("input", [])
        deterministic_column_ids = [
            column_id.strip() for column_id in deterministic_column_ids if column_id
        ]
        deterministic_column_ids = [
            column_id.replace("{{", "").replace("}}", "")
            for column_id in deterministic_column_ids
        ]
        # Extract base column reference (strip JSON path like "input.year" -> "input")
        deterministic_column_ids = [
            _extract_column_id_and_path(col_id)[0]
            for col_id in deterministic_column_ids
        ]
        deterministic_column_ids = list(set(deterministic_column_ids))

        # combine them
        all_column_ids = deterministic_column_ids + mapping_column_ids
        all_column_ids = list(set(all_column_ids))

        if self.experiment_dataset:
            # remove self.replace_column_id from all_column_ids
            all_column_ids = [
                column_id
                for column_id in all_column_ids
                if column_id != str(self.replace_column_id)
            ]
            # and add self.replace_column_id to the end of the list
            all_column_ids.append(str(self.column.id))

        elif self.optimize:
            # remove self.replace_column_id from all_column_ids
            all_column_ids = [
                column_id
                for column_id in all_column_ids
                if column_id != str(self.replace_column_id)
            ]
            # and add self.replace_column_id to the end of the list
            all_column_ids.append(str(self.column.id))

        return [id for id in all_column_ids if id]

    @staticmethod
    def _is_uuid(value):
        import uuid

        try:
            uuid.UUID(str(value))
            return True
        except ValueError:
            return False

    def _get_input_token_count(self, row, mappings, config=None):
        # here we first retrieve all column ids being used in this eval
        # we retrieve this via getting the column ids inside config.mapping
        # and the column ids inside config.config.input

        # once we get the column ids, we retrieve the cell values for each of these column ids
        # and calculate the token count for each of these cell values
        # we also calculate the token count for the rule prompt in deterministic evals

        """Prepare mapping data for evaluation"""
        cell_values_strings = []
        cell_values_image_urls = []
        column_ids = self._get_all_column_ids_being_used(config=config)

        # Batch fetch all cells in a single query with select_related
        if column_ids:
            cells = Cell.objects.filter(
                column__id__in=column_ids, row=row
            ).select_related("column")
            cells_by_column = {str(cell.column_id): cell for cell in cells}

            for column_id in column_ids:
                cell = cells_by_column.get(str(column_id))
                if cell:
                    if cell.status == CellStatus.ERROR.value:
                        continue
                    if cell.column.data_type == DataTypeChoices.IMAGE.value:
                        cell_values_image_urls.append(cell.value)
                    else:
                        cell_values_strings.append(cell.value if cell.value else "")
                else:
                    logger.error(
                        f"unable to retrieve cell value for column id : {column_id}"
                    )
                    cell_values_strings.append("")

        input_words_string = " ".join(cell_values_strings)

        # calculate token count for the input rule prompt in deterministic evals
        try:
            if not config:
                config = self.user_eval_metric.config
            rule_prompt = config.get("config", {}).get("rule_prompt", "")
            input_words_string += " " + rule_prompt
        except Exception:
            logger.error(f"unable to retrieve rule prompt for column id : {column_id}")

        input_token_count = count_tiktoken_tokens(
            input_words_string, cell_values_image_urls
        )
        return input_token_count

    def _resolve_version(self):
        """Resolve the eval template version to use. Sets self._resolved_version."""
        if self._resolved_version is not None or not self.eval_template:
            return

        try:
            from model_hub.models.evals_metric import EvalTemplateVersion

            organization = None
            if self.organization_id:
                organization = Organization.objects.filter(
                    id=self.organization_id
                ).first()
            elif self.user_eval_metric and self.user_eval_metric.organization:
                organization = self.user_eval_metric.organization
            elif self.eval_template and self.eval_template.organization:
                organization = self.eval_template.organization

            if self.version_number is not None:
                # Look up specific version
                self._resolved_version = (
                    EvalTemplateVersion.all_objects.filter(
                        eval_template=self.eval_template,
                        version_number=self.version_number,
                        deleted=False,
                    )
                    .filter(
                        models.Q(organization__isnull=True)
                        | models.Q(organization=organization)
                    )
                    .first()
                )
            else:
                # Use default version
                self._resolved_version = EvalTemplateVersion.objects.get_default(
                    self.eval_template, organization
                )

            # Increment usage count
            if self._resolved_version:
                EvalTemplateVersion.all_objects.filter(
                    id=self._resolved_version.id
                ).update(usage_count=models.F("usage_count") + 1)
        except Exception:
            # Backward compatibility — don't break if versions don't exist yet
            logger.debug("Version resolution skipped — no versions found")
            self._resolved_version = None

    def _apply_version_overrides(self, config):
        """Apply prompt overrides from the resolved version to the config."""
        if not self._resolved_version:
            return config

        from model_hub.utils.prompt_migration import prompt_messages_to_flat_config

        flat = prompt_messages_to_flat_config(
            self._resolved_version.prompt_messages or []
        )

        # Override prompt fields if version has them
        if flat.get("system_prompt") is not None:
            config["system_prompt"] = flat["system_prompt"]
        if flat.get("rule_prompt") is not None:
            config["rule_prompt"] = flat["rule_prompt"]
        if flat.get("criteria") is not None and self.criteria is None:
            criteria_text = flat["criteria"]
            # Convert named variables ({{input}}, {{output}}) back to
            # {{variable_N}} format expected by the deterministic evaluator.
            required_keys = config.get("required_keys", [])
            for i, key in enumerate(required_keys):
                criteria_text = criteria_text.replace(
                    f"{{{{{key}}}}}", f"{{{{variable_{i + 1}}}}}"
                )
            self.criteria = criteria_text

        # Override model if version specifies one
        if self._resolved_version.model:
            config["model"] = self._resolved_version.model

        return config

    def _create_eval_instance(
        self,
        config,
        eval_class=None,
        model=ModelChoices.TURING_LARGE.value,
        kb_id=None,
        runtime_config=None,
    ):
        """Create evaluation instance based on configuration.

        Delegates to evaluations.engine.instance.create_eval_instance() for the
        core logic. Preserves self.eval_class and self.criteria mutations that
        the dataset path depends on.
        """
        from evaluations.engine.instance import create_eval_instance
        from evaluations.engine.registry import get_eval_class

        if eval_class:
            self.eval_class = eval_class
        elif not self.eval_class:
            eval_type_id = self.eval_template.config.get("eval_type_id", "")
            self.eval_class = get_eval_class(eval_type_id)

        if runtime_config is None and self.user_eval_metric:
            runtime_config = self.user_eval_metric.config

        instance, criteria = create_eval_instance(
            eval_class=self.eval_class,
            eval_template=self.eval_template,
            config=config,
            model=model,
            kb_id=kb_id,
            runtime_config=runtime_config,
            organization_id=str(self.organization_id) if self.organization_id else None,
            workspace_id=str(self.workspace_id) if self.workspace_id else None,
            version_number=self.version_number,
            is_futureagi=self.futureagi_eval,
        )

        # Preserve side effects that dataset path reads via self
        if criteria:
            self.criteria = criteria

        return instance

    def _prepare_eval_config(self, config, model=ModelChoices.TURING_LARGE.value):
        """Prepare evaluation configuration"""
        eval_type_id = self.eval_template.config.get("eval_type_id")

        if eval_type_id == "CustomCodeEval":
            # Code evals only need the code string — strip everything else.
            # Do NOT fall back to template.criteria: after an instructions
            # update via the API, criteria holds the LLM-prompt text, not
            # Python code, which would produce a silent "skip" result.
            config = {
                "code": self.eval_template.config.get("code") or config.get("code", ""),
            }
            return config

        if eval_type_id == "AgentEvaluator":
            # Agent eval — uses Falcon AI AgentLoop for multi-turn reasoning
            config["rule_prompt"] = self.eval_template.config.get("rule_prompt")
            config["model"] = model or self.eval_template.config.get("model")
            raw_output = self.eval_template.config.get("output")
            if self.eval_template.choice_scores and raw_output != "Pass/Fail":
                config["output_type"] = "choices"
            else:
                config["output_type"] = raw_output
            config["choices"] = self.eval_template.choices or (
                list(self.eval_template.choice_scores.keys())
                if self.eval_template.choice_scores
                else []
            )
            config["choice_scores"] = self.eval_template.choice_scores
            # pass_threshold and reverse_output control how the LLM's verdict
            # maps to a pass/fail decision. pass_threshold is stored on the
            # template model; reverse_output lives in the template config.
            config["pass_threshold"] = (
                self.eval_template.pass_threshold
                if self.eval_template.pass_threshold is not None
                else 0.5
            )
            config["reverse_output"] = bool(
                self.eval_template.config.get("reverse_output", False)
            )
            config["check_internet"] = self.eval_template.config.get(
                "check_internet", False
            )
            config["knowledge_base_id"] = self.eval_template.config.get(
                "knowledge_base_id"
            )
            config["agent_mode"] = self.eval_template.config.get("agent_mode", "agent")
            config["tools"] = self.eval_template.config.get("tools", {})
            config["knowledge_bases"] = self.eval_template.config.get(
                "knowledge_bases", []
            )
            # data_injection: prefer user's eval metric config (run_config),
            # fall back to the base template config. Normalize to canonical
            # snake_case flags so downstream code never has to re-handle aliases.
            _uem_di = {}
            if self.user_eval_metric and self.user_eval_metric.config:
                _uem_di = (
                    self.user_eval_metric.config.get("run_config", {}).get("data_injection", {})
                    or self.user_eval_metric.config.get("data_injection", {})
                )
            config["data_injection"] = _di_normalize(
                _uem_di or self.eval_template.config.get("data_injection", {})
            )
            config["summary"] = self.eval_template.config.get(
                "summary", {"type": "concise"}
            )
            # Pass org/workspace context for tool resolution
            config["organization_id"] = (
                str(self.eval_template.organization.id)
                if self.eval_template.organization
                else str(self.organization_id) if self.organization_id else None
            )
            config["workspace_id"] = (
                str(self.eval_template.workspace.id)
                if getattr(self.eval_template, "workspace", None)
                else str(self.workspace_id) if self.workspace_id else None
            )

        elif eval_type_id == "CustomPromptEvaluator":
            config["provider"] = self.eval_template.config.get("provider")
            config["rule_prompt"] = self.eval_template.config.get("rule_prompt")
            config["system_prompt"] = self.eval_template.config.get("system_prompt")
            # If choice_scores are defined, force choices mode
            raw_output = self.eval_template.config.get("output")
            if self.eval_template.choice_scores and raw_output != "Pass/Fail":
                config["output_type"] = "choices"
            else:
                config["output_type"] = raw_output
            # Multi-message and few-shot support
            if self.eval_template.config.get("messages"):
                config["messages"] = self.eval_template.config.get("messages")
            if self.eval_template.config.get("few_shot_examples"):
                config["few_shot_examples"] = self.eval_template.config.get(
                    "few_shot_examples"
                )

            # Resolve model — prefer runtime model over stored config
            raw_model = model or self.eval_template.config.get("model")
            futureagi_models = {
                ModelChoices.TURING_LARGE.value,
                ModelChoices.TURING_SMALL.value,
                ModelChoices.TURING_FLASH.value,
            }
            config["model"] = raw_model
            if raw_model in futureagi_models:
                config["api_key"] = None
                config["provider"] = "turing"
            else:
                # External models: use litellm with org API key
                config["api_key"] = self._get_api_key(
                    config["model"],
                    organization_id=self.eval_template.organization.id,
                    workspace_id=getattr(self.eval_template, "workspace_id", None),
                )

            # Pass agent config flags to evaluator
            config["check_internet"] = self.eval_template.config.get(
                "check_internet", False
            )
            config["multi_choice"] = self.eval_template.config.get("multi_choice")
            # Derive choices from choice_scores if not set on template
            config["choices"] = self.eval_template.choices or (
                list(self.eval_template.choice_scores.keys())
                if self.eval_template.choice_scores
                else []
            )
            config["choice_scores"] = self.eval_template.choice_scores
        elif self.user_eval_metric_id == "CustomPromptEvaluator":
            config["system_prompt"] = config.get("system_prompt")
            config["output_type"] = config.get("output")
            config["api_key"] = self._get_api_key(
                config["model"],
                organization_id=config.get("organization_id"),
                workspace_id=config.get("workspace_id"),
            )

        if self.futureagi_eval:
            config = self._prepare_futureagi_config(config=config, model=model)

        return config

    def _get_api_key(self, model, organization_id, workspace_id=None):
        """Get API key for the model"""
        model_manager = LiteLLMModelManager(model, exclude_providers="custom")
        api_key = model_manager.get_api_key(
            organization_id=organization_id, workspace_id=workspace_id
        )

        if not api_key:
            raise ValueError(
                f"No API key found for organization {self.user_eval_metric.organization.id}"
            )

        return api_key

    def _prepare_futureagi_config(self, config, model=ModelChoices.TURING_LARGE.value):
        """Prepare configuration for FutureAGI evaluation"""
        config["api_key"] = None
        if self.user_eval_metric:
            config["knowledge_base_id"] = (
                str(self.user_eval_metric.kb_id)
                if self.user_eval_metric.kb_id
                else None
            )
            #  config['knowledge_base_id'] ='232be065-132e-48b1-b207-c52e518dd196'
        if self.eval_template.owner == OwnerChoices.USER.value:
            model = self.eval_template.model if self.eval_template else model
        llm_model, provider = self._get_futureagi_model_config(model=model)
        config["model"] = llm_model
        config["provider"] = provider

        if (
            self.eval_template
            and self.eval_template.config.get("eval_type_id")
            == "DeterministicEvaluator"
        ):
            if "rule_prompt" not in config:
                config["choices"] = self.eval_template.choices
                config["rule_prompt"] = self.eval_template.criteria
                config["multi_choice"] = self.eval_template.multi_choice
                config["custom_eval"] = self.eval_template.config.get(
                    "custom_eval", False
                )

            config["model_type"] = model

            # Pass param_modalities and required_keys for validation
            if "param_modalities" in self.eval_template.config:
                config["param_modalities"] = self.eval_template.config[
                    "param_modalities"
                ]
            if "required_keys" in self.eval_template.config:
                config["required_keys"] = self.eval_template.config["required_keys"]

        if config.get("criteria"):
            self.criteria = config.pop("criteria")
            return config

        return config

    def _format_response(self, eval_result):
        """Format evaluation result response"""
        from evaluations.engine.formatting import extract_raw_result

        response = extract_raw_result(eval_result, self.eval_template)
        response["name"] = self.user_eval_metric.name
        response["model"] = ""  # Dataset path doesn't track model used
        return response

    def _handle_error(self, error):
        """Handle evaluation errors"""
        if "matching query does not exist" not in str(error):
            response = {"reason": str(error)}
        else:
            response = {"reason": "Value does not exist."}

        return response, CellStatus.ERROR.value, CellStatus.ERROR.value

    def _handle_api_call_status(self, api_call_log_row, value):
        """Handle API call errors"""
        if value == CellStatus.ERROR.value and api_call_log_row:
            try:
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                api_call_log_row.save(update_fields=["status"])

                refund_config = {"evaluation_id": str(self.user_eval_metric_id)}
                refund_cost_for_api_call(api_call_log_row, config=refund_config)
            except Exception as e:
                logger.error(f"Error refunding cost for api call: {str(e)}")
        elif value == CellStatus.PASS.value and api_call_log_row:
            try:
                api_call_log_row.status = APICallStatusChoices.SUCCESS.value
                api_call_log_row.save(update_fields=["status"])
            except Exception as e:
                logger.error(f"Error updating success api call status: {str(e)}")

    def _create_reason_cell(self, dataset, column, row, response, reason, status):
        """Create or update cell for the reason with evaluation results"""
        # Mirror the _create_cell guard: if the user stopped this eval,
        # let the "User stopped evaluation" marker persist untouched.
        from model_hub.services.experiment_utils import is_user_eval_stopped

        if is_user_eval_stopped(self.user_eval_metric_id):
            return
        cell_data = {
            "dataset": dataset,
            "column": column,
            "row": row,
            "value_infos": json.dumps(response),
            "value": reason,
            "status": status,
        }

        # For evaluation only mode, update existing cell if it exists
        if self.is_only_eval:
            updated = Cell.objects.filter(column=column, row=row).update(
                value=reason,
                value_infos=json.dumps(response),
                status=status,
            )
            if updated:
                return

        # Create new cell
        Cell.objects.update_or_create(
            dataset=dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": cell_data["value_infos"],
                "value": cell_data["value"],
                "status": cell_data["status"],
            },
        )

    def _create_tags_cell(self, dataset, column, row, response, tag_values, status):
        """Create or update cell for tags with evaluation results"""

        cell_data = {
            "dataset": dataset,
            "column": column,
            "row": row,
            "value_infos": json.dumps(response),
            "value": tag_values,
            "status": status,
        }

        # For evaluation only mode, update existing cell if it exists
        if self.is_only_eval:
            updated = Cell.objects.filter(column=column, row=row).update(
                value=tag_values,
                value_infos=json.dumps(response),
                status=status,
            )
            if updated:
                return
        # Create new cell
        Cell.objects.update_or_create(
            dataset=dataset,
            column=column,
            row=row,
            defaults={
                "value_infos": cell_data["value_infos"],
                "value": cell_data["value"],
                "status": cell_data["status"],
            },
        )

    def _create_tags_column(self, dataset):
        """Create column for tags"""
        column_config = {
            "name": self._get_tags_column_name(),
            "data_type": "array",
            "source": self._get_tags_source(),
            "source_id": self._get_tags_source_id(),
            "dataset": dataset,
        }

        return self._create_or_update_column(dataset, column_config, new_column=True)

    def _get_tags_column_name(self):
        """Get name for tags column"""
        base_name = f"{self.user_eval_metric.name}-tags"
        if self.optimize:
            return f"{self.user_eval_metric.name}-{self.column.name}-tags"
        if self.experiment_dataset:
            return f"{self.user_eval_metric.name}-{self.column.name}-tags"
        return base_name

    def _get_tags_source(self):
        """Get source for tags column"""
        if self.optimize:
            return SourceChoices.OPTIMISATION_EVALUATION_TAGS.value
        if self.experiment_dataset:
            return SourceChoices.EXPERIMENT_EVALUATION_TAGS.value
        return SourceChoices.EVALUATION_TAGS.value

    def _get_tags_source_id(self):
        """Get source ID for tags column"""
        if self.optimize:
            return f"{self.optimize.id}-sourceid-{self.user_eval_metric_id}"
        if self.experiment_dataset:
            return f"{self.experiment_dataset.id}-sourceid-{self.user_eval_metric_id}"
        return str(self.user_eval_metric_id)

    def update_cell(self, row_ids=None):
        """Main method to update cells with evaluation results

        Args:
            row_ids: Optional list of row IDs to process. If None, processes all rows.
        """
        # Use self.dataset which can be set by caller (e.g., for base evaluations
        # where experiment.dataset differs from user_eval_metric.dataset)
        # Falls back to user_eval_metric.dataset if not set
        dataset_to_use = self.dataset if self.dataset else self.user_eval_metric.dataset
        dataset = Dataset.objects.select_related("organization", "workspace").get(
            id=dataset_to_use.id
        )
        column_config = self._get_column_config(dataset)
        # When processing a specific batch (row_ids provided), pass new_column=True to prevent
        # resetting ALL cells in the column. The cell reset in _create_or_update_column is
        # intended for re-running a full evaluation, not for batch processing where we want
        # to preserve previously processed rows.
        column = self._create_or_update_column(
            dataset, column_config, new_column=bool(row_ids)
        )

        # Filter rows based on row_ids if provided
        # Use select_related for dataset to avoid N+1 queries when accessing row.dataset
        rows_query = Row.objects.filter(
            dataset_id=dataset_to_use.id, deleted=False
        ).select_related("dataset")
        if row_ids:
            rows_query = rows_query.filter(id__in=row_ids)
        rows = rows_query.order_by("order")

        cell_data = {
            "value_infos": json.dumps({}),
            "value": "",
            "status": CellStatus.RUNNING.value,
        }

        bulk_update_or_create_cells(
            rows.values_list("id", flat=True), column.id, dataset.id, cell_data
        )
        mappings = self.user_eval_metric.config.get("mapping")

        # Increase max_workers for more parallelism
        success = 0
        fail = 0

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_and_create_cell = wrap_for_thread(self._process_and_create_cell)

        with ThreadPoolExecutor(max_workers=5) as executor:
            # Map futures to rows so we can update cell status on failure
            future_to_row = {}
            for row in rows:
                if not self.cancel_event:
                    future = executor.submit(
                        wrapped_process_and_create_cell, row, mappings, dataset, column
                    )
                    future_to_row[future] = row
                else:
                    logger.info(
                        f"Evaluation for {self.user_eval_metric.id} was cancelled."
                    )
                    break

            # If the user pressed Stop on this eval, don't overwrite the
            # stopped marker with generic "Please rerun" error cells.
            try:
                from tfc.utils.distributed_state import evaluation_tracker

                _cancel_requested = evaluation_tracker.should_cancel(
                    self.user_eval_metric.id
                )
            except Exception:
                _cancel_requested = False

            # Process results as they complete
            for future in as_completed(future_to_row):
                try:
                    future.result()
                    success += 1
                except Exception as e:
                    logger.exception(f"Error processing row: {str(e)}")
                    fail += 1
                    if _cancel_requested:
                        # User stopped this eval — leave the "Evaluation
                        # stopped by user" cells (set by mark_eval_cells_stopped)
                        # in place instead of overwriting them.
                        continue
                    # CRITICAL FIX: Update cell to ERROR status when processing fails.
                    # Without this, cells stay in RUNNING status forever, blocking
                    # experiment completion.
                    row = future_to_row[future]
                    try:
                        error_msg = str(e)[:500]
                        error_value = {
                            "error": error_msg,
                            "debug_info": (
                                f"Evaluation failed for row {row.id}. "
                                "Check: 1) Input column mappings are correct and have values, "
                                "2) Eval template configuration is valid, "
                                "3) API keys for external services are configured. "
                                f"Error: {error_msg}"
                            ),
                        }
                        Cell.objects.filter(
                            column=column, row=row, dataset=dataset, deleted=False
                        ).update(
                            status=CellStatus.ERROR.value,
                            value=json.dumps(error_value),
                        )
                        # Also update reason cell to ERROR. Always-on for
                        # experiments; respect legacy flag for datasets.
                        if (
                            bool(self.experiment_dataset)
                            or self.user_eval_metric.config.get("reason_column")
                        ) and not self.optimize:
                            reason_column_name = f"{self.user_eval_metric.name}-reason"
                            reason_column = self._create_reason_column(
                                dataset, reason_column_name
                            )
                            self._create_reason_cell(
                                dataset,
                                reason_column,
                                row,
                                {
                                    "reason": "No reasoning available. Please rerun the evaluation."
                                },
                                "No reasoning available. Please rerun the evaluation.",
                                CellStatus.ERROR.value,
                            )
                    except Exception as cell_error:
                        logger.error(f"Failed to update cell to ERROR: {cell_error}")

        # Only track and update column status if processing all rows
        # For batch processing, status will be checked after each batch
        if not row_ids:
            source = (
                MixpanelSources.OPTIMIZE.value
                if self.optimize
                else (
                    MixpanelSources.EXPERIMENT.value
                    if self.experiment_dataset
                    else MixpanelSources.DATASET.value
                )
            )
            exp = None
            if self.experiment_dataset:
                exp = self.experiment_dataset.experiment
            properties = get_mixpanel_properties(
                org=self.user_eval_metric.organization,
                eval=self.user_eval_metric,
                dataset=self.user_eval_metric.dataset,
                experiment=exp,
                source=source,
                count=success,
                failed=fail,
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_COMPLETED.value, properties)

            column.status = StatusType.COMPLETED.value
            column.save(update_fields=["status"])
        else:
            # For batch processing, check if all cells are complete after processing this batch
            self._check_and_update_eval_status(column.id)

    def _process_and_create_cell(self, row, mappings, dataset, column):
        """Process a single row and create its cell"""
        try:
            close_old_connections()
            response, status, value = self._process_eval_result(row, mappings)
            self._create_cell(dataset, column, row, response, value, status)
        except Exception as e:
            logger.error(f"Error processing row {row.id}: {str(e)}")
            raise e
        finally:
            close_old_connections()

    def run_evaluation_for_row(self, row_id):
        """Run evaluation for a specific row."""
        try:
            close_old_connections()
            # Load the user eval metric and related data
            self.load_user_eval_metric()

            # Use self.dataset which can be set by caller (e.g., for base evaluations)
            dataset_to_use = (
                self.dataset if self.dataset else self.user_eval_metric.dataset
            )

            # Fetch the specific row
            row = Row.objects.get(
                id=row_id, dataset_id=dataset_to_use.id, deleted=False
            )

            # Get the column configuration
            dataset = Dataset.objects.get(id=dataset_to_use.id)
            column = self.column

            # Get the mappings from the user eval metric config
            mappings = self.user_eval_metric.config.get("mapping")

            # Process the evaluation result for the specific row
            response, status, value = self._process_eval_result(row, mappings)
            self._create_cell(dataset, column, row, response, value, status)

        except ObjectDoesNotExist:
            raise ValueError("Row or UserEvalMetric not found.")  # noqa: B904
        except Exception as e:
            raise e
        finally:
            close_old_connections()

    def _check_and_update_eval_status(self, column_id):
        """Check if all cells are complete (pass/error) and update column/user_eval_metric status"""
        from django.db.models import Count, Q

        try:
            # Use self.dataset which can be set by caller (e.g., for base evaluations)
            dataset_to_use = (
                self.dataset if self.dataset else self.user_eval_metric.dataset
            )
            dataset = Dataset.objects.get(id=dataset_to_use.id)
            column = Column.objects.get(id=column_id)

            # Single aggregation query to get all counts at once
            counts = Cell.objects.filter(
                column=column, dataset=dataset, deleted=False
            ).aggregate(
                total=Count("id"),
                passed=Count("id", filter=Q(status=CellStatus.PASS.value)),
                error=Count("id", filter=Q(status=CellStatus.ERROR.value)),
            )

            total_cells = counts["total"]
            if total_cells == 0:
                return

            # Count cells that are complete (pass or error)
            completed_cells = counts["passed"] + counts["error"]

            # If all cells are complete, update column and user_eval_metric status
            if completed_cells == total_cells:
                column.status = StatusType.COMPLETED.value
                column.save(update_fields=["status"])

                # FIX: Trigger experiment status cascade when eval column completes.
                # This ensures experiment_dataset and experiment status are updated
                # after all eval columns finish processing.
                if self.experiment_dataset:
                    from model_hub.views.experiment_runner import (
                        check_and_update_experiment_dataset_status,
                    )

                    try:
                        check_and_update_experiment_dataset_status(
                            self.experiment_dataset.id
                        )
                    except Exception as cascade_error:
                        logger.exception(
                            f"Error cascading experiment status after eval complete: {cascade_error}"
                        )

                if not self.cancel_event:
                    self.user_eval_metric.status = StatusType.COMPLETED.value
                    self.user_eval_metric.save(update_fields=["status"])
                    logger.info(
                        f"Eval Metric Status Updated {self.user_eval_metric.id} ----------"
                    )

                    # Track event
                    source = (
                        MixpanelSources.OPTIMIZE.value
                        if self.optimize
                        else (
                            MixpanelSources.EXPERIMENT.value
                            if self.experiment_dataset
                            else MixpanelSources.DATASET.value
                        )
                    )
                    exp = None
                    if self.experiment_dataset:
                        exp = self.experiment_dataset.experiment

                    # Use pre-computed counts from aggregation
                    success = counts["passed"]
                    fail = counts["error"]

                    properties = get_mixpanel_properties(
                        org=self.user_eval_metric.organization,
                        eval=self.user_eval_metric,
                        dataset=self.user_eval_metric.dataset,
                        experiment=exp,
                        source=source,
                        count=success,
                        failed=fail,
                    )
                    track_mixpanel_event(
                        MixpanelEvents.EVAL_RUN_COMPLETED.value, properties
                    )
        except Exception as e:
            logger.exception(f"Error checking eval status: {str(e)}")

    def run_prompt(self, row_ids=None):
        """Run the evaluation based on mapped parameters and save the output

        Args:
            row_ids: Optional list of row IDs to process. If None, processes all rows.
        """
        logger.info(" ----- INSIDE EvaluationRunner : function run_prompt -----")
        try:
            self.load_user_eval_metric()

            from sdk.utils.helpers import _get_api_call_type
            try:
                from ee.usage.services.metering import check_usage
            except ImportError:
                check_usage = None

            org_id = str(
                self.organization_id
                or getattr(self.user_eval_metric, "organization_id", "")
                or (
                    self.user_eval_metric.organization.id
                    if self.user_eval_metric
                    else ""
                )
            )
            api_call_type = _get_api_call_type(
                getattr(self.user_eval_metric, "model", None)
                or ModelChoices.TURING_LARGE.value
            )
            usage_check = check_usage(org_id, api_call_type)
            if not usage_check.allowed:
                self.user_eval_metric.status = StatusType.FAILED.value
                self.user_eval_metric.save(update_fields=["status"])
                raise ValueError(usage_check.reason or "Usage limit exceeded")

            self.update_cell(row_ids=row_ids)
            logger.info(
                f"run_prompt [update_cell done] self.cancel_event: {self.cancel_event} ----------"
            )
            if not self.cancel_event:
                self.user_eval_metric.status = StatusType.COMPLETED.value
                self.user_eval_metric.save(update_fields=["status"])
                logger.info(
                    f"Eval Metric Status Updated {self.user_eval_metric.id} ----------"
                )
        except Exception as e:
            # traceback.print_exc()
            logger.exception("Exception in run_prompt")
            self.user_eval_metric.status = StatusType.ERROR.value
            self.user_eval_metric.save(update_fields=["status"])
            # Flip any still-running cells (+ their paired reason cells) to
            # ERROR so the UI doesn't leave a loading skeleton behind when
            # the runner crashes before per-row error handlers get a chance.
            from model_hub.utils.eval_cell_status import mark_eval_cells_stopped

            mark_eval_cells_stopped(
                self.user_eval_metric, reason=str(e)[:500] or "Evaluation failed"
            )
            logger.info(
                f"USER EVAL METRIC ERROR UPDATED self.user_eval_metric {self.user_eval_metric.id} ----------"
            )
            raise e
        finally:
            try:
                # Close all database connections properly
                from django.db import connections

                for conn in connections.all():
                    conn.close()
            except Exception as e:
                logger.error(f"Error closing database connections: {e}")

    def _get_futureagi_model_config(
        self, eval_template=None, model=ModelChoices.TURING_LARGE.value
    ) -> tuple[str, str]:
        """
        Get model and provider configuration for FutureAGI evals.

        Returns:
            tuple[str, str]: A tuple containing (model, provider)
        """
        futureagi_model_configs = {
            ModelChoices.TURING_LARGE.value: ModelConfigs.TURING_LARGE,
            ModelChoices.TURING_SMALL.value: ModelConfigs.TURING_SMALL,
            ModelChoices.TURING_FLASH.value: ModelConfigs.TURING_FLASH,
            ModelChoices.PROTECT.value: ModelConfigs.PROTECT,
            ModelChoices.PROTECT_FLASH.value: ModelConfigs.PROTECT_FLASH,
        }

        cfg = futureagi_model_configs.get(model, ModelConfigs.TURING_LARGE)

        return cfg.model_name, cfg.provider


class CustomEvalTemplateCreateView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            serializer = CustomEvalTemplateCreateSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.validated_data
                if (
                    EvalTemplate.objects.filter(
                        name=validated_data.get("name"),
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        deleted=False,
                    ).exists()
                    or EvalTemplate.no_workspace_objects.filter(
                        name=request.data.get("name"),
                        owner=OwnerChoices.SYSTEM.value,
                        deleted=False,
                    ).exists()
                ):
                    return self._gm.bad_request(get_error_message("EVAL_NAME_EXISTS"))

                validated_data = prepare_user_eval_config(validated_data, bypass=False)
                logger.debug(f"Prepared eval config: {validated_data}")
                eval_template = EvalTemplate.objects.create(
                    name=validated_data.get("name"),
                    organization=organization,
                    owner=OwnerChoices.USER.value,
                    eval_tags=validated_data.get("eval_tags"),
                    config=(
                        validated_data.get("configuration")
                        if validated_data.get("configuration", None)
                        else validated_data.get("config")
                    ),
                    choices=validated_data.get("choices"),
                    description=validated_data.get("description"),
                    criteria=validated_data.get("criteria"),
                    multi_choice=validated_data.get("multi_choice"),
                    proxy_agi=validated_data.get("config", {}).get("proxy_agi", True),
                    visible_ui=validated_data.get("config", {}).get("visible_ui", True),
                    model=validated_data.get("config", {}).get("model", "turing_small"),
                )

                # Create v0 version for the new template
                try:
                    from model_hub.models.evals_metric import EvalTemplateVersion
                    from model_hub.utils.prompt_migration import (
                        config_to_prompt_messages,
                    )

                    template_config = eval_template.config or {}
                    prompt_messages = validated_data.get("prompt_messages")
                    if not prompt_messages:
                        prompt_messages = config_to_prompt_messages(
                            template_config,
                            eval_template.criteria,
                            template_config.get("eval_type_id"),
                        )
                    EvalTemplateVersion.objects.create_version(
                        eval_template=eval_template,
                        prompt_messages=prompt_messages,
                        config_snapshot=template_config,
                        criteria=eval_template.criteria,
                        model=eval_template.model,
                        user=request.user,
                        organization=organization,
                        workspace=getattr(eval_template, "workspace", None),
                    )
                except Exception as e:
                    logger.warning(f"Failed to create v0 for custom eval: {e}")
            else:
                return self._gm.bad_request(parse_serialized_errors(serializer))

            return self._gm.success_response({"eval_template_id": eval_template.id})
        except Exception as e:
            logger.exception(f"Error in creation of custom eval template: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_EVAL_TEMPLATE")
            )


class EvalTemplateCreateView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            serializer = EvalTemplateSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.validated_data

                EvalTemplate.objects.create(
                    name=validated_data.get("name"),
                    organization=organization,
                    owner=validated_data.get("owner"),
                    eval_tags=validated_data.get("eval_tags"),
                    config=validated_data.get("config"),
                )

                return self._gm.success_response("success")
            return self._gm.bad_request(parse_serialized_errors(serializer))
        except Exception as e:
            logger.exception(f"Error in creation of eval template: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_CREATE_EVAL_TEMPLATE")
            )


class EvalUserTemplateCreateView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            serializer = EvalUserTemplateSerializer(data=request.data)
            if serializer.is_valid():
                validated_data = serializer.validated_data

                UserEvalMetric.objects.create(
                    name=validated_data.get("name"),
                    organization=organization,
                    dataset_id=validated_data.get("dataset_id"),
                    template_id=validated_data.get("template_id"),
                    config=validated_data.get("config"),
                    user=request.user,
                    model=validated_data.get("model", ModelChoices.TURING_LARGE.value),
                )

                return self._gm.success_response("success")
            return self._gm.bad_request(parse_serialized_errors(serializer))
        except Exception as e:
            logger.exception(f"Error in creation of user eval template: {str(e)}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_CREATE_USER_EVAL_TEMP")
            )


# Define the process function for running prompts


class DatasetEvalStatsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _get_metrics_using_columns_batch(self, organization_id, column_ids):
        """
        Batch version of get_metrics_using_column to avoid N+1 queries.
        Fetches all UserEvalMetrics that use any of the specified column_ids.
        """

        def check_value_in_dict(d: dict, search_values: set) -> bool:
            """Check if any value from search_values exists in dictionary values."""
            for value in d.values():
                if isinstance(value, str):
                    for search_value in search_values:
                        if search_value in value or f"{{{{{search_value}}}}}" in value:
                            return True
                elif isinstance(value, dict):
                    if check_value_in_dict(value, search_values):
                        return True
            return False

        # Batch fetch all columns to get their datasets
        columns = Column.objects.filter(
            id__in=column_ids, dataset__organization_id=organization_id
        ).select_related("dataset")
        dataset_ids = set(col.dataset_id for col in columns)

        # If we found datasets, filter metrics by those datasets
        if dataset_ids:
            metrics = UserEvalMetric.objects.filter(
                organization_id=organization_id,
                deleted=False,
                show_in_sidebar=True,
                dataset_id__in=dataset_ids,
            )
        else:
            metrics = UserEvalMetric.objects.filter(
                organization_id=organization_id, deleted=False, show_in_sidebar=True
            )

        # Create a set of column_ids for faster lookup
        column_id_set = set(str(cid) for cid in column_ids)

        # Filter metrics in Python - check if any column_id is used
        return [
            metric
            for metric in metrics
            if (
                metric.config.get("mapping")
                and check_value_in_dict(metric.config["mapping"], column_id_set)
            )
            or (
                metric.config.get("config")
                and check_value_in_dict(metric.config["config"], column_id_set)
            )
        ]

    def get(self, request, dataset_id):
        try:
            # Get all evaluation columns for this dataset
            _org = get_request_organization(request)
            organization_id = _org.id if _org else None
            column_ids = request.query_params.get("column_ids", None)
            if column_ids is not None:
                column_ids = column_ids.split(",")

            user_eval_metric_ids = []

            if column_ids is not None and len(column_ids) > 0:
                # Use batch method instead of loop to avoid N+1 queries
                metrics = self._get_metrics_using_columns_batch(
                    organization_id=organization_id, column_ids=column_ids
                )
                serializer = UserEvalMetricSerializer(metrics, many=True)
                user_eval_metric_ids = [metric.get("id") for metric in serializer.data]
                user_eval_metric_ids = list(set(user_eval_metric_ids))

                if len(user_eval_metric_ids) == 0:
                    ans = []
                    return self._gm.success_response(ans)

            if len(user_eval_metric_ids) > 0:
                template_ids = UserEvalMetric.objects.filter(
                    id__in=user_eval_metric_ids, deleted=False
                ).values_list("template_id", flat=True)
            else:
                template_ids = UserEvalMetric.objects.filter(
                    dataset_id=dataset_id, deleted=False
                ).values_list("template_id", flat=True)

            templates = EvalTemplate.no_workspace_objects.filter(
                id__in=template_ids, deleted=False
            )

            final_data = []

            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(
                    executor.map(
                        lambda template: get_eval_stats(
                            template, dataset_id, user_eval_metric_ids
                        ),
                        templates,
                    )
                )
                final_data.extend(results)

            if request.headers.get("X-Api-Key") is not None:
                properties = get_mixpanel_properties(
                    user=request.user, dataset_id=dataset_id
                )
                track_mixpanel_event(MixpanelEvents.SDK_DATASET_EVAL.value, properties)

            return self._gm.success_response(final_data)

        except Exception as e:
            logger.exception(f"Error in fetching evaluation stats: {str(e)}")
            traceback.print_exc()
            return self._gm.bad_request(get_error_message("FAILED_TO_GET_OF_DATASET"))
