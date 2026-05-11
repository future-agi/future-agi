"""
Temporal activities for Dataset Optimization.

Design notes:
- Single activity model: setup -> run_optimization (long-running) -> finalize
- Per-trial persistence via callback (store_single_trial)
- Resume from latest DatasetOptimizationTrial.metadata["optimizer_state"]
- No checkpoint table - state lives in DatasetOptimizationTrial.metadata
- No locks - workflows are guaranteed not to conflict
"""

from __future__ import annotations

from typing import Any, Dict

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from temporalio import activity

from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.utils.dataset_optimization import (
    finalize_optimization_run,
    get_dataset_optimization_steps,
    store_single_trial,
    update_dataset_optimization_step,
)
from tfc.temporal.common.heartbeat import Heartbeater

logger = structlog.get_logger(__name__)


def _safe_close_db():
    try:
        close_old_connections()
    except Exception:
        pass


def _compute_total_trials(optimizer_algorithm: str, config: Dict[str, Any]) -> int:
    """
    Calculate total trials based on optimizer type and configuration.

    Each optimizer has different loop structures:
    - random_search: num_variations trials (simple iteration)
    - metaprompt: num_rounds trials (iterative refinement)
    - bayesian: n_trials trials (Optuna-driven search)
    - protegi: Expansion-based with beam search (multiple evals per round)
    - promptwizard: Mutation + refinement phases (multiple evals per iteration)
    - gepa: max_metric_calls (evolutionary optimization)
    """
    optimizer_algorithm = optimizer_algorithm.lower()

    if optimizer_algorithm == "random_search":
        return int(config.get("num_variations", 3))

    elif optimizer_algorithm == "metaprompt":
        return int(config.get("num_rounds", 5))

    elif optimizer_algorithm == "bayesian":
        return int(config.get("n_trials", 10))

    elif optimizer_algorithm == "protegi":
        num_rounds = int(config.get("num_rounds", 3))
        beam_size = int(config.get("beam_size", 3))
        num_gradients = int(config.get("num_gradients", 4))
        prompts_per_gradient = int(config.get("prompts_per_gradient", 1))

        total = 0
        current_beam = 1
        for round_num in range(num_rounds):
            expanded = current_beam * num_gradients * prompts_per_gradient
            candidate_pool_size = current_beam + expanded
            total += candidate_pool_size
            current_beam = min(beam_size, candidate_pool_size)

        return total

    elif optimizer_algorithm == "promptwizard":
        refine_iterations = int(config.get("refine_iterations", 2))
        mutate_rounds = int(config.get("mutate_rounds", 3))
        beam_size = int(config.get("beam_size", 1))
        thinking_styles = 2

        total = 0
        for _ in range(refine_iterations):
            mutated_count = mutate_rounds * thinking_styles
            total += 1 + mutated_count
            refined_count = beam_size
            total += 1 + refined_count

        return total

    elif optimizer_algorithm == "gepa":
        return int(config.get("max_metric_calls", 150))

    else:
        return 0


def _calc_best_from_trials(run: OptimizeDataset):
    """Calculate best trial from OptimizeDataset object."""
    trials = (
        DatasetOptimizationTrial.objects.filter(optimization_run=run)
        .order_by("-average_score")
        .values("trial_number", "average_score", "prompt")
    )
    if not trials:
        return None, None, None
    best = trials[0]
    return (
        best["trial_number"],
        best["average_score"],
        best["prompt"],
    )


def _calc_best_from_trials_by_id(run_id: str):
    """Calculate best trial from run_id (optimized for read-only, no object needed)."""
    trials = (
        DatasetOptimizationTrial.objects.filter(optimization_run_id=run_id)
        .order_by("-average_score")
        .values("trial_number", "average_score", "prompt")
    )
    if not trials:
        return None, None, None
    best = trials[0]
    return (
        best["trial_number"],
        best["average_score"],
        best["prompt"],
    )


@activity.defn(name="dataset_optimization_setup_activity")
async def setup_run_activity(input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mark run as RUNNING, compute totals.
    Simplified without locks - workflows are guaranteed not to conflict.
    """
    _safe_close_db()
    hb = Heartbeater(("setup_run",))
    async with hb:
        run_id: str = input["run_id"]

        def _sync():
            close_old_connections()

            # Get the run
            try:
                run = OptimizeDataset.objects.get(id=run_id)
            except OptimizeDataset.DoesNotExist:
                raise ValueError(f"Run {run_id} does not exist")

            # Update status if not already running
            if run.status != OptimizeDataset.StatusType.RUNNING:
                run.mark_as_running()
                logger.info("Run status updated to RUNNING", run_id=run_id)

            # Update step 1 to running
            steps = get_dataset_optimization_steps(run_id)
            update_dataset_optimization_step(
                steps, 1, status=DatasetOptimizationStep.Status.RUNNING
            )

            optimizer_algorithm = run.optimizer_algorithm
            configuration = run.optimizer_config or {}

            total_trials = _compute_total_trials(optimizer_algorithm, configuration)

            # Check for existing trials (resume case)
            latest_trial = (
                DatasetOptimizationTrial.objects.filter(optimization_run_id=run_id)
                .order_by("-trial_number")
                .first()
            )

            current_trial_number = latest_trial.trial_number if latest_trial else -1

            # Get best trial
            _, best_score, best_prompt = _calc_best_from_trials_by_id(run_id)

            # Extract optimizer state from latest trial metadata
            optimizer_state = None
            if latest_trial and latest_trial.metadata:
                optimizer_state = latest_trial.metadata.get("optimizer_state")

            # Mark step 1 as completed
            update_dataset_optimization_step(
                steps, 1, status=DatasetOptimizationStep.Status.COMPLETED
            )

            logger.info(
                "Setup completed successfully",
                run_id=run_id,
                total_trials=total_trials,
                current_trial=current_trial_number,
                has_resume_state=optimizer_state is not None,
            )

            return {
                "run_id": run_id,
                "total_trials": total_trials,
                "current_trial_number": current_trial_number,
                "optimizer_state": optimizer_state,
                "best_prompt": best_prompt,
                "best_score": best_score,
            }

        return await sync_to_async(_sync, thread_sensitive=False)()


@activity.defn(name="dataset_optimization_run_activity")
async def run_optimization_activity(input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run entire optimization in one activity. Resume from latest DatasetOptimizationTrial if exists.
    Uses callback to persist each trial immediately after completion.
    """
    _safe_close_db()
    hb = Heartbeater(("optimization",))
    async with hb:
        run_id = input["run_id"]

        def _sync():
            close_old_connections()

            try:
                run = OptimizeDataset.objects.select_related(
                    "column__dataset", "optimizer_model"
                ).get(id=run_id)
            except OptimizeDataset.DoesNotExist:
                raise ValueError(f"Run {run_id} does not exist")

            steps = get_dataset_optimization_steps(run_id)

            # Update step 2 (baseline) to running
            update_dataset_optimization_step(
                steps, 2, status=DatasetOptimizationStep.Status.RUNNING
            )

            # Check for existing trials (resume case)
            latest_trial = (
                DatasetOptimizationTrial.objects.filter(optimization_run=run)
                .order_by("-trial_number")
                .first()
            )

            # Determine resume state
            resume_state = None
            skip_baseline = False
            if latest_trial and latest_trial.metadata:
                optimizer_state = latest_trial.metadata.get("optimizer_state")
                if optimizer_state:
                    resume_state = {"optimizer_state": optimizer_state}
                skip_baseline = True

            # Calculate remaining trials
            completed = latest_trial.trial_number if latest_trial else -1
            total_trials = _compute_total_trials(
                run.optimizer_algorithm, run.optimizer_config or {}
            )
            remaining = max(0, total_trials - max(0, completed))

            if remaining <= 0:
                _, best_score, best_prompt = _calc_best_from_trials(run)
                return {
                    "trials_run": 0,
                    "best_score": best_score,
                    "best_prompt": best_prompt,
                }

            # Get the column and dataset for optimization context
            column = run.column
            if not column:
                raise ValueError(f"Run {run_id} has no column associated")

            dataset = column.dataset
            if not dataset:
                raise ValueError(f"Column {column.id} has no dataset")

            # Get the initial prompt based on column source type
            from model_hub.models.develop_dataset import Cell, Column, Row

            initial_prompt = ""
            prompt_template = None  # For PromptEvalConfig lookup

            logger.info(
                "Extracting initial prompt",
                column_id=str(column.id),
                column_name=column.name,
                column_source=column.source,
                column_source_id=str(column.source_id) if column.source_id else None,
            )

            if column.source_id:
                # Check source type to determine which model to query
                if column.source == "run_prompt":
                    # Source is RunPrompter - extract messages template
                    from model_hub.models.run_prompt import RunPrompter

                    try:
                        run_prompter = RunPrompter.objects.get(id=column.source_id)
                        logger.info(
                            "Found RunPrompter",
                            run_prompter_id=str(run_prompter.id),
                            run_prompter_name=run_prompter.name,
                        )

                        # Extract prompt from messages
                        # Messages format: [{'role': 'user', 'content': [{'text': '{{column_id}}', 'type': 'text'}]}]
                        if run_prompter.messages:
                            prompt_parts = []
                            for msg in run_prompter.messages:
                                content = msg.get("content", [])
                                if isinstance(content, list):
                                    # New format: content is list of parts
                                    for part in content:
                                        if (
                                            isinstance(part, dict)
                                            and part.get("type") == "text"
                                        ):
                                            prompt_parts.append(part.get("text", ""))
                                elif isinstance(content, str):
                                    # Old format: content is string
                                    prompt_parts.append(content)

                            initial_prompt = "".join(prompt_parts).strip()

                            # Convert column ID references to column name references
                            # e.g., {{b3732734-de83-457e-a53c-c0594f76734a}} -> {{prompt}}
                            import re

                            uuid_pattern = r"\{\{([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}\}"
                            matches = re.findall(uuid_pattern, initial_prompt)
                            for col_id in matches:
                                try:
                                    ref_col = Column.objects.get(id=col_id)
                                    initial_prompt = initial_prompt.replace(
                                        f"{{{{{col_id}}}}}", f"{{{{{ref_col.name}}}}}"
                                    )
                                except Column.DoesNotExist:
                                    pass

                            logger.info(
                                "Extracted initial prompt from RunPrompter",
                                prompt_len=len(initial_prompt),
                                prompt_preview=initial_prompt[:200],
                            )

                    except RunPrompter.DoesNotExist:
                        logger.warning(
                            f"RunPrompter {column.source_id} not found for column {column.id}"
                        )

                else:
                    # Default: try PromptTemplate
                    from model_hub.models.run_prompt import (
                        PromptTemplate,
                        PromptVersion,
                    )

                    try:
                        prompt_template = PromptTemplate.objects.get(
                            id=column.source_id
                        )
                        logger.info(
                            "Found PromptTemplate",
                            template_id=str(prompt_template.id),
                            template_name=prompt_template.name,
                        )

                        # Get the default version's prompt
                        default_version = PromptVersion.objects.filter(
                            original_template=prompt_template, is_default=True
                        ).first()

                        if default_version:
                            logger.info(
                                "Found default version",
                                version_id=str(default_version.id),
                                has_config=bool(default_version.prompt_config_snapshot),
                            )

                            if default_version.prompt_config_snapshot:
                                # Extract prompt from first message config
                                configs = default_version.prompt_config_snapshot
                                if configs and len(configs) > 0:
                                    messages = configs[0].get("messages", [])
                                    # Concatenate all message contents
                                    initial_prompt = "\n".join(
                                        msg.get("content", "") for msg in messages
                                    )
                                    logger.info(
                                        "Extracted initial prompt from PromptVersion",
                                        prompt_len=len(initial_prompt),
                                        num_messages=len(messages),
                                    )
                        else:
                            logger.warning(
                                "No default version found for prompt template"
                            )
                    except PromptTemplate.DoesNotExist:
                        logger.warning(
                            f"PromptTemplate {column.source_id} not found for column {column.id}"
                        )

            # Fallback: if initial_prompt is still empty, try to get from first cell in this column
            if not initial_prompt:
                first_row = Row.objects.filter(dataset=dataset).first()
                if first_row:
                    first_cell = Cell.objects.filter(
                        row=first_row, column=column
                    ).first()
                    if first_cell and first_cell.value:
                        initial_prompt = str(first_cell.value)
                        logger.info(
                            "Extracted initial prompt from first cell (fallback)",
                            prompt_len=len(initial_prompt),
                            prompt_preview=initial_prompt[:100],
                        )

            if not initial_prompt:
                logger.warning(
                    "Could not extract initial prompt - optimization will not work correctly",
                    column_id=str(column.id),
                )

            # Get evaluations - prefer user_eval_template_ids from the run
            # We need the full UserEvalMetric objects to get the mapping config
            user_eval_metrics = []

            if run.user_eval_template_ids.exists():
                user_eval_metrics = list(
                    run.user_eval_template_ids.select_related("template").all()
                )
                logger.info(
                    "Got UserEvalMetrics from run.user_eval_template_ids",
                    num_metrics=len(user_eval_metrics),
                    metric_ids=[str(m.id) for m in user_eval_metrics],
                )

            if not user_eval_metrics:
                logger.warning(
                    "No eval metrics found - optimization will use default 0.5 scores",
                    run_id=str(run.id),
                )

            # Import the optimizer agent
            try:
                from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.fix_your_agent.fix_your_agent", exc_info=True)
                return None

            # Get organization and workspace for API keys
            organization = dataset.organization
            workspace = dataset.workspace

            # Create callback that saves trial AND updates progress
            def on_trial_complete(
                trial_data: dict,
                trial_number: int,
                stepper_state: dict,
                is_baseline: bool,
            ):
                store_single_trial(
                    optimization_run=run,
                    trial_data=trial_data,
                    trial_number=trial_number,
                    stepper_state=stepper_state,
                    is_baseline=is_baseline,
                    user_eval_metrics=user_eval_metrics,
                )

                # Update progress step
                if is_baseline:
                    description = "Baseline evaluation completed."
                    update_dataset_optimization_step(
                        steps, 2, status=DatasetOptimizationStep.Status.COMPLETED
                    )
                    update_dataset_optimization_step(
                        steps, 3, status=DatasetOptimizationStep.Status.RUNNING
                    )
                else:
                    description = f"Trial {trial_number} completed."
                    update_dataset_optimization_step(steps, 3, description=description)

            # Normalize Unicode characters (non-breaking spaces, zero-width chars, etc.)
            # that come from rich text editors / web UI copy-paste
            try:
                from ee.agent_opt.utils.template_variables import (
                    build_template_variable_instruction,
                    extract_template_variables,
                    normalize_prompt_text,
                )
            except ImportError:
                import re
                import unicodedata

                def normalize_prompt_text(text: str) -> str:
                    text = unicodedata.normalize("NFKC", text)
                    return re.sub(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]", "", text)

                def extract_template_variables(prompt: str) -> set[str]:
                    return set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", prompt))

                def build_template_variable_instruction(
                    template_variables: set[str],
                ) -> str:
                    if not template_variables:
                        return ""
                    vars_list = ", ".join(
                        f"{{{{{var}}}}}" for var in sorted(template_variables)
                    )
                    return (
                        "\n\nCRITICAL CONSTRAINT - Template Variable Preservation:\n"
                        "Preserve these template variables exactly, including "
                        f"double curly braces: {vars_list}"
                    )

            initial_prompt = normalize_prompt_text(initial_prompt)

            # Prepare the dataset execution data for optimization
            # This follows the pattern from agent_prompt_optimiser but adapted for dataset optimization
            execution_data = _prepare_dataset_execution_data(
                column=column,
                dataset=dataset,
                user_eval_metrics=user_eval_metrics,
                initial_prompt=initial_prompt,
            )

            agent = FixYourAgent()

            # Get execution model (model used to run prompts and generate outputs)
            execution_model_name = run.model.model_name if run.model else "gpt-4o"

            logger.info(
                "Starting optimization with DirectEvaluator",
                initial_prompt_len=len(initial_prompt),
                initial_prompt_preview=(
                    initial_prompt[:200] if initial_prompt else "(empty)"
                ),
                num_eval_metrics=len(user_eval_metrics),
                eval_metric_ids=[str(m.id) for m in user_eval_metrics],
                execution_model=execution_model_name,
                optimizer_type=run.optimizer_algorithm,
            )

            # Inject template variable preservation instructions into optimizer config

            template_vars = extract_template_variables(initial_prompt)
            optimizer_config = dict(run.optimizer_config or {})
            if template_vars:
                existing_desc = optimizer_config.get("task_description", "")
                optimizer_config["task_description"] = (
                    existing_desc + build_template_variable_instruction(template_vars)
                )

            # Run optimization with direct evaluation (single input/output, no conversation simulation)
            result = agent.optimize_from_execution(
                execution_data=execution_data,
                optimizer_type=run.optimizer_algorithm,
                optimization_model=(
                    run.optimizer_model.model_name if run.optimizer_model else "gpt-4o"
                ),
                optimizer_config=optimizer_config,
                use_dual_llm_sim=False,  # Not using dual LLM for dataset optimization
                agent_optimiser_run_steps=steps,
                organization=organization,
                workspace=workspace,
                resume_state=resume_state,
                max_new_trials=remaining,
                skip_baseline=skip_baseline,
                on_trial_callback=on_trial_complete,
                use_temporal_evaluation=False,  # Simpler for dataset optimization
                use_direct_evaluation=True,  # Use direct evaluation for datasets (no conversation simulation)
                execution_model=execution_model_name,  # Model to run prompts and generate outputs
            )

            # Mark step 3 as completed
            update_dataset_optimization_step(
                steps, 3, status=DatasetOptimizationStep.Status.COMPLETED
            )

            # Get final best results
            _, best_score, best_prompt = _calc_best_from_trials(run)

            return {
                "trials_run": len(result.history),
                "best_score": best_score or result.final_score,
                "best_prompt": best_prompt or result.best_prompt,
            }

        return await sync_to_async(_sync, thread_sensitive=False)()


def _prepare_dataset_execution_data(column, dataset, user_eval_metrics, initial_prompt):
    """
    Prepare execution data for the optimizer from dataset and column.
    This adapts the dataset structure to the format expected by FixYourAgent.optimize_from_execution.
    """
    from model_hub.models.develop_dataset import Cell, Column, Row

    # Get rows and cells for the dataset
    rows = Row.objects.filter(dataset=dataset).order_by("order")

    # Build a mapping of column UUID -> column name for converting mappings
    column_id_to_name = {
        str(col.id): col.name for col in Column.objects.filter(dataset=dataset)
    }

    # Build eval configs from UserEvalMetric objects
    # Each UserEvalMetric has:
    #   - template: FK to EvalTemplate
    #   - config: dict with 'mapping' key containing {param: column_uuid_or_value}
    eval_configs = []
    for user_eval_metric in user_eval_metrics:
        eval_template = user_eval_metric.template
        if not eval_template:
            logger.warning(f"UserEvalMetric {user_eval_metric.id} has no template")
            continue

        template_config = eval_template.config or {}
        metric_config = user_eval_metric.config or {}

        # Get the user-defined mapping from UserEvalMetric.config['mapping']
        # This maps eval parameters to column UUIDs or special values
        user_mapping = metric_config.get("mapping", {})

        converted_mapping = {}
        optimization_column_id = str(column.id) if column else None
        for param_key, param_value in user_mapping.items():
            if param_value in column_id_to_name:
                column_name = column_id_to_name[param_value]
                if (
                    param_key in ("output", "response")
                    or param_value == optimization_column_id
                ):
                    converted_mapping[param_key] = "output"
                    logger.info(
                        f"Mapping '{param_key}' to LLM output (column '{column_name}' is the optimization target)"
                    )
                else:
                    converted_mapping[param_key] = column_name
            else:
                converted_mapping[param_key] = param_value

        logger.info(
            "Built eval mapping",
            eval_template_name=eval_template.name,
            user_mapping=user_mapping,
            converted_mapping=converted_mapping,
        )

        eval_configs.append(
            {
                "eval_template_id": str(eval_template.id),
                "eval_template_name": eval_template.name,
                "description": eval_template.description or "",
                "criteria": eval_template.criteria or "",
                "template_config": template_config,  # For EvalTemplate.config
                "config": metric_config.get(
                    "config", {}
                ),  # For SimulateEvalConfig.config
                "mapping": converted_mapping,  # Converted mapping with column names
                "model": user_eval_metric.model or eval_template.model,  # Eval model
                "eval_type_id": template_config.get("eval_type_id"),
                "output_type": template_config.get("output"),
                "required_keys": template_config.get("required_keys"),
            }
        )

    # Warn if initial_prompt is empty - this will cause optimization to fail
    if not initial_prompt:
        logger.warning(
            "initial_prompt is empty - optimization will not work correctly",
            column_id=str(column.id) if column else None,
        )

    # Validate that template placeholders match available columns
    import re

    if initial_prompt:
        # Find all placeholders in template (both {{var}} and {var} formats)
        double_brace_placeholders = set(re.findall(r"\{\{(\w+)\}\}", initial_prompt))
        single_brace_placeholders = set(re.findall(r"\{(\w+)\}", initial_prompt))
        all_placeholders = double_brace_placeholders | single_brace_placeholders

        # Get all column names from dataset
        from model_hub.models.develop_dataset import Column

        available_columns = set(
            Column.objects.filter(dataset=dataset).values_list("name", flat=True)
        )

        # Check for missing columns
        missing_columns = all_placeholders - available_columns
        if missing_columns:
            logger.error(
                "Template placeholders don't match dataset columns!",
                template_placeholders=list(all_placeholders),
                available_columns=list(available_columns),
                missing_columns=list(missing_columns),
                initial_prompt_preview=initial_prompt[:200],
            )
        else:
            logger.info(
                "Template placeholders validated",
                placeholders=list(all_placeholders),
                available_columns=list(available_columns),
            )

    # Build call_executions equivalent for dataset optimization
    # Structure must match what optimize_from_execution() expects
    call_executions = []
    for row in rows[:50]:  # Limit to 50 rows for optimization
        # Get cells for this row
        cells = Cell.objects.filter(row=row).select_related("column")

        # Build input/output data from cells
        cell_data = {}
        for cell in cells:
            cell_data[cell.column.name] = cell.value

        # Build structure that matches optimize_from_execution expectations
        call_executions.append(
            {
                "call_execution_id": str(row.id),
                "input": cell_data,  # Dataset row data for DirectEvaluator
                "initial_agent_prompt": initial_prompt,
                "evaluations": eval_configs,
                # These fields are expected by optimize_from_execution but not used for dataset optimization:
                "transcripts": [],  # No conversation transcripts for single I/O
                "scenario_data": {
                    "row_data": cell_data,  # Pass cell data as row_data for compatibility
                },
            }
        )

    logger.info(
        "Prepared dataset execution data",
        num_rows=len(call_executions),
        initial_prompt_len=len(initial_prompt),
        num_eval_configs=len(eval_configs),
    )

    return {
        "agent_definition_prompt": {
            "description": initial_prompt,
            "inbound": True,
        },
        "call_executions": call_executions,
    }


@activity.defn(name="dataset_optimization_finalize_activity")
async def finalize_run_activity(input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mark run as completed/failed/canceled.
    Simplified without locks - workflows are guaranteed not to conflict.
    """
    _safe_close_db()
    status = input.get("status", "completed")
    error = input.get("error")

    run_id = input["run_id"]

    def _sync():
        close_old_connections()

        # Calculate best results first
        _, best_score, best_prompt = _calc_best_from_trials_by_id(run_id)

        # Get the run
        try:
            run = OptimizeDataset.objects.get(id=run_id)
        except OptimizeDataset.DoesNotExist:
            logger.error("Run does not exist in finalize", run_id=run_id)
            raise ValueError(f"Run {run_id} does not exist")

        steps = get_dataset_optimization_steps(run_id)

        # Update step 4 to running
        update_dataset_optimization_step(
            steps, 4, status=DatasetOptimizationStep.Status.RUNNING
        )

        # Update status based on input
        if status == "completed":
            if run.status != OptimizeDataset.StatusType.COMPLETED:
                finalize_optimization_run(run)
                logger.info("Run marked as completed", run_id=run_id)
                update_dataset_optimization_step(
                    steps, 4, status=DatasetOptimizationStep.Status.COMPLETED
                )
        elif status == "cancelled":
            if run.status not in (
                OptimizeDataset.StatusType.CANCELLED,
                OptimizeDataset.StatusType.FAILED,
            ):
                run.mark_as_cancelled()
                logger.info("Run marked as cancelled", run_id=run_id)
                update_dataset_optimization_step(
                    steps, 4, status=DatasetOptimizationStep.Status.FAILED
                )
        else:  # Failed status
            # Don't overwrite cancelled status (set by stop endpoint)
            if run.status not in (
                OptimizeDataset.StatusType.FAILED,
                OptimizeDataset.StatusType.CANCELLED,
            ):
                error_message = str(error) if error else "Unknown error"
                run.mark_as_failed(error_message=error_message)
                logger.info(
                    "Run marked as failed",
                    run_id=run_id,
                    error_message=error_message,
                )
                update_dataset_optimization_step(
                    steps, 4, status=DatasetOptimizationStep.Status.FAILED
                )

        final_status = run.status

        logger.info(
            "Finalize completed",
            run_id=run_id,
            final_status=final_status,
            best_score=best_score,
        )

        return {
            "run_id": run_id,
            "status": final_status,
            "error": error,
            "best_score": best_score,
            "best_prompt": best_prompt,
        }

    return await sync_to_async(_sync, thread_sensitive=False)()


ALL_ACTIVITIES = [
    setup_run_activity,
    run_optimization_activity,
    finalize_run_activity,
]
