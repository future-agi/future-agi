import concurrent.futures
import json
import re
import time

import pandas as pd
import structlog
from django.db import close_old_connections
from django.shortcuts import get_object_or_404
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer
except ImportError:
    PromptOptimizer = _ee_stub("PromptOptimizer")
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import (
    eval_instruction_process_data_format,
    get_qualitative_eval_parameter_prompt_v2,
)
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from tfc.telemetry import wrap_for_thread

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from analytics.utils import (
    MixpanelEvents,
    MixpanelSources,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.run_prompt import RunPrompter
from model_hub.serializers.develop_optimisation import OptimizationDetailSerializer
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.prompt_template import replace_ids_with_column_name
try:
    from ee.usage.models.usage import APICallTypeChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import count_tiktoken_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from tfc.oss_stubs.usage import count_tiktoken_tokens
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_api_request


class DevelopOptimizer:
    def __init__(self, optim_obj_id: int, avoid_cost: bool = False):
        self.optim_obj_id = optim_obj_id
        self.optimize_dataset = self._get_optimization_dataset()
        self.dataset = self.optimize_dataset.dataset
        self.messages = self.optimize_dataset.messages
        if not avoid_cost:
            self._calculate_tokens_and_deduct()
        self.column = self.optimize_dataset.column
        self.model_config = self.optimize_dataset.model_config
        if not self.model_config:
            run_prompt = get_object_or_404(RunPrompter, id=self.column.source_id)
            self.model_config = {
                "frequency_penalty": run_prompt.frequency_penalty or 0.0,
                "presence_penalty": run_prompt.presence_penalty or 0.0,
                "top_p": run_prompt.top_p or 0.0,
                "model_name": run_prompt.model,
                "temperature": float(run_prompt.temperature) or 0.0,
                "max_tokens": int(run_prompt.max_tokens) or 4000,
            }
        self.old_column = None
        self.new_column = None
        self.prompt = None

    def _calculate_tokens_and_deduct(self):
        try:
            column = Column.objects.get(id=self.optimize_dataset.column.id)
            column_id = str(column.id)
            opt_column_token_count = 0
            cells = Cell.objects.filter(column=column, deleted=False)
            cell_values_strings = []
            cell_values_image_urls = []
            for cell in cells:
                if cell.status == CellStatus.ERROR.value:
                    continue
                try:
                    if cell.column.data_type == DataTypeChoices.IMAGE.value:
                        cell_values_image_urls.append(cell.value)
                    else:
                        cell_values_strings.append(cell.value)
                except Exception:
                    logger.error(f"cell not found for column id : {column_id}")
                    cell_values_strings.append("")

            input_words_string = " ".join(cell_values_strings)
            opt_column_token_count = count_tiktoken_tokens(
                input_words_string, input_image_urls=cell_values_image_urls
            )

            # print("optimisation token_count : ", opt_column_token_count)
            reference_id = str(self.optimize_dataset.id)

            config = {
                "input_tokens": opt_column_token_count,
                "reference_id": reference_id,
                "input": column_id,
            }
            organization = column.dataset.organization
            api_call_type = APICallTypeChoices.DATASET_OPTIMIZATION.value
            log_and_deduct_cost_for_api_request(
                organization,
                api_call_type,
                config,
                source="optimisation",
                workspace=column.dataset.workspace,
            )

            # Non-chargeable tracking event — optimisation uses user's own API keys.
            # Eval runs within optimisation emit ai_credits events via run_eval_func().
            # "dataset_optimization" is intentionally not in billing.yaml.
            try:
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=api_call_type,
                        properties={
                            "source": "optimisation",
                            "source_id": str(self.optimize_dataset.id),
                        },
                    )
                )
            except Exception:
                pass  # Metering failure must not break the action

            return 0
        except Exception as e:
            logger.error(f"error in calculate token count: {e}")
            return -1

    def _update_api_call_log_row(self):
        try:
            # get the optimisation object by id
            try:
                from ee.usage.models.usage import APICallLog, APICallStatusChoices
            except ImportError:
                APICallLog = None
                from tfc.oss_stubs.usage import APICallStatusChoices
            try:
                from ee.usage.utils.usage_entries import refund_cost_for_api_call
            except ImportError:
                from tfc.oss_stubs.usage import refund_cost_for_api_call

            optimizer_row = OptimizationDataset.objects.get(id=self.optimize_dataset.id)
            optimisation_id = str(optimizer_row.id)
            # get the api call log row by reference id
            api_call_log_row = APICallLog.objects.filter(
                reference_id=optimisation_id, deleted=False
            ).first()
            if optimizer_row.status == StatusType.FAILED.value:
                # refund the cost
                # update the api call log row
                api_call_log_row.status = APICallStatusChoices.ERROR.value
                api_call_log_row.save()
                refund_config = {"reference_id": str(optimizer_row.id)}
                refund_cost_for_api_call(api_call_log_row, config=refund_config)
            else:
                # update the api call log row
                api_call_log_row.status = APICallStatusChoices.SUCCESS.value
                api_call_log_row.save()

        except Exception as e:
            logger.exception(f"error in refunding cost : {e}")

    def create_column(self):
        column_order = self.dataset.column_order
        self.old_column, created = Column.objects.get_or_create(
            name=f"{self.optimize_dataset.name}-{self.model_config.get('model_name')}-old-prompt",
            data_type="text",
            source=SourceChoices.OPTIMISATION.value,
            dataset=self.dataset,
            source_id=self.optim_obj_id,
        )
        if created:
            column_order.append(str(self.old_column.id))

        self.new_column, created = Column.objects.get_or_create(
            name=f"{self.optimize_dataset.name}-{self.model_config.get('model_name')}-new-prompt",
            data_type="text",
            source=SourceChoices.OPTIMISATION.value,
            dataset=self.dataset,
            source_id=self.optim_obj_id,
        )
        if created:
            column_order.append(str(self.new_column.id))

        self.dataset.column_order = column_order
        self.dataset.save()

    def _get_optimization_dataset(self) -> OptimizationDataset:
        return OptimizationDataset.objects.filter(id=self.optim_obj_id).get()

    def _handle_failure(self, error_message: str):
        self.optimize_dataset.status = StatusType.FAILED.value
        self.optimize_dataset.optimized_k_prompts = [error_message]
        self.optimize_dataset.save()
        raise ValueError(error_message)

    def _get_messages(self):
        if not self.messages:
            if not self.column:
                self._handle_failure("No messages found in the optimization dataset.")
            elif self.column.source != SourceChoices.RUN_PROMPT.value:
                self._handle_failure("Mentioned column is not a run prompt column.")
            else:
                run_prompt = get_object_or_404(RunPrompter, id=self.column.source_id)
                self.messages = run_prompt.messages
        if not self.model_config:
            run_prompt = get_object_or_404(RunPrompter, id=self.column.source_id)
            self.model_config = {
                "frequency_penalty": run_prompt.frequency_penalty or 0.0,
                "presence_penalty": run_prompt.presence_penalty or 0.0,
                "top_p": run_prompt.top_p or 0.0,
                "model_name": run_prompt.model,
                "temperature": float(run_prompt.temperature) or 0.0,
                "max_tokens": int(run_prompt.max_tokens) or 4000,
            }

        # Get the user prompt from messages
        self.prompt = "\n".join(
            [
                content["text"]
                for message in self.messages
                if message["role"] == "user"
                for content in message["content"]
                if content["type"] == "text"
            ]
        )

        if not self.prompt:
            self._handle_failure("No user prompt found in messages.")

        return self.messages

    def _process_variable_replacement(
        self, value: str, row: Row, role: str | None = None, prompt_template: str = ""
    ) -> tuple:
        if isinstance(value, str) and re.search(r"\{{.*?\}}", value):
            matches = re.findall(r"\{{(.*?)\}}", value)
            row_variables = {}
            if role == "user":
                prompt_template += value + "\n"

            for match in matches:
                try:
                    column_id = match.strip()
                    cell = Cell.objects.get(column__id=column_id, row=row)
                    row_variables[column_id] = str(cell.value)
                    value = value.replace(f"{{{match}}}", str(cell.value))
                except Exception as e:
                    # raise ValueError(f"Cell with column_id={match} and row={row} not found.")
                    logger.error(f"error in processing variable replacement: {e}")
                    pass
            return value, row_variables, prompt_template
        return value, {}, prompt_template

    def get_optimization_data(self) -> dict[str, list]:
        self._get_messages()
        rows = Row.objects.filter(dataset_id=self.dataset.id, deleted=False).all()

        data: dict[str, list] = {
            "user_chat": [],
            "model_chat": [],
            "system_chat": [],
            "prompt_template": [],
            "variables": [],
            "context": [],
            "metadata": [],
        }

        for row in rows:
            messages_by_role = {"user": "", "system": "", "assistant": ""}
            row_variables = {}
            prompt_template = ""

            for message in self.messages:
                value = " \n".join(
                    message["content"][i].get("text")
                    for i in range(len(message["content"]))
                    if message["content"][i].get("text")
                )
                role = message["role"]

                value, vars_, prompt = self._process_variable_replacement(
                    value, row, role, prompt_template
                )
                row_variables.update(vars_)
                prompt_template = prompt
                messages_by_role[role] += value

            data["user_chat"].append(messages_by_role.get("user"))
            data["prompt_template"].append(prompt_template)
            data["variables"].append(row_variables)
            data["model_chat"].append(messages_by_role.get("assistant"))
            data["system_chat"].append(messages_by_role.get("system"))
            data["context"].append([])
            data["metadata"].append([])

        return data

    def get_eval_template_descriptions(self) -> list[str]:
        user_eval_metrics = UserEvalMetric.objects.filter(
            id__in=self.optimize_dataset.user_eval_template_ids.all(), deleted=False
        ).select_related("template")

        return [str(user_eval.template.description) for user_eval in user_eval_metrics]

    def _create_llm_client(self, config: dict | None = None) -> LLM:
        config = config or self.model_config
        return LLM(
            model_name=config.get("model_name", ""),
            temperature=float(config.get("temperature", 0.7)),
            max_tokens=int(config.get("max_tokens", 1000)),
            provider="openai",
            config={
                "frequency_penalty": config.get("frequency_penalty", 0),
                "presence_penalty": config.get("presence_penalty", 0),
                "top_p": config.get("top_p", 0),
            },
        )

    def create_column_develop_optimizer(self):
        client = self._create_llm_client()

        old_column = self.old_column
        new_column = self.new_column

        rows = Row.objects.filter(dataset_id=self.dataset.id, deleted=False).all()
        new_template = self.optimize_dataset.optimized_k_prompts[0]

        def process_row(row):
            """Helper function to process a single row"""
            close_old_connections()
            if self.optimize_dataset.status != StatusType.FAILED.value:
                old_messages = []
                new_messages = []
                for message in self.messages:
                    value = " \n".join(
                        message["content"][i].get("text")
                        for i in range(len(message["content"]))
                        if message["content"][i].get("text")
                    )
                    value, _, _ = self._process_variable_replacement(value, row)
                    old_messages.append({"role": message["role"], "content": value})
                    if message["role"] != "user":
                        new_messages.append({"role": message["role"], "content": value})
                    else:
                        new_value, _, _ = self._process_variable_replacement(
                            new_template, row
                        )
                        new_messages.append(
                            {"role": message["role"], "content": new_value}
                        )

                old_content = client._get_completion_content(old_messages)
                new_content = client._get_completion_content(new_messages)

                Cell.objects.create(
                    dataset=self.dataset,
                    column=old_column,
                    row=row,
                    value=old_content,
                    status=CellStatus.PASS.value,
                )
            else:
                new_content = "Error"
                old_content = "Error"
                value_info = {"reason": self.optimize_dataset.optimized_k_prompts[0]}
                status = CellStatus.ERROR.value

            Cell.objects.create(
                dataset=self.dataset,
                column=new_column,
                row=row,
                value=new_content,
                value_infos=(
                    json.dumps(value_info)
                    if "value_info" in locals()
                    else json.dumps({})
                ),
                status=status if "status" in locals() else CellStatus.PASS.value,
            )

            if self.optimize_dataset.status == StatusType.FAILED.value:
                Cell.objects.create(
                    dataset=self.dataset,
                    column=old_column,
                    row=row,
                    value=old_content,
                    value_infos=(
                        json.dumps(value_info)
                        if "value_info" in locals()
                        else json.dumps({})
                    ),
                    status=status if "status" in locals() else CellStatus.PASS.value,
                )
            close_old_connections()

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_row = wrap_for_thread(process_row)

        # Process rows in parallel using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all rows for processing
            futures = [executor.submit(wrapped_process_row, row) for row in rows]

            # Wait for all tasks to complete
            concurrent.futures.wait(futures)

            # Check for any exceptions
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error processing row: {str(e)}")

        if self.optimize_dataset.status != StatusType.FAILED.value:
            self.run_evaluation(self.old_column, self.new_column)
            self.optimize_dataset.status = StatusType.COMPLETED.value
            self.optimize_dataset.save()

    def create_criteria_text_prompt(self, metric):
        llm = self._create_llm_client()
        output_eval_prompt = get_qualitative_eval_parameter_prompt_v2(
            llm,
            criteria=None,
            prompt=optimise_prompt.format(metrics=metric, prompt=self.prompt),
        )
        output_eval_prompt = extract_dict_from_string(output_eval_prompt)
        criteria_breakdown = [v for k, v in output_eval_prompt.items()]

        criteria_breakdown = eval_instruction_process_data_format(criteria_breakdown)

        return criteria_breakdown

    def _create_criteria_breakdown(self):
        input_eval_prompts = self.get_eval_template_descriptions()
        metric = "\n".join(
            [f"{i}. {criteria}" for i, criteria in enumerate(input_eval_prompts)]
        )
        return self.create_criteria_text_prompt(metric)

    def runner_topk_develop_optimizer(self):
        try:
            data = self.get_optimization_data()
            self.create_column()
            df_temp = pd.DataFrame(data)

            # # Save DataFrame to CSV
            # try:
            #     csv_filename = f"optimization_data.csv"
            #     df_temp.to_csv(csv_filename, index=False)
            #     print(f"DataFrame saved to {csv_filename}")
            # except Exception as e:
            #     print(f"Error saving DataFrame to {csv_filename}")

            # # if not self.optimize_dataset.criteria_breakdown:
            # self.optimize_dataset.criteria_breakdown = self._create_criteria_breakdown()
            # self.optimize_dataset.save()

            config = self.model_config
            time.time()

            model_manager = LiteLLMModelManager(config["model_name"])
            provider = model_manager.get_provider(
                model_name=config["model_name"], organization_id=None
            )

            prompt_optim_agent = PromptOptimizer(
                prompt=self.prompt,
                train_data=df_temp,
                model_name=config["model_name"],
                temperature=config["temperature"],
                max_tokens=config["max_tokens"],
                provider=provider,
                optimizer=self.optimize_dataset,
                config={
                    k: config.get(k, 0.0)
                    for k in ["frequency_penalty", "presence_penalty", "top_p"]
                },
            )

            with open("optimize_data.txt", "w") as f:
                f.write(f"""Prompt: {self.prompt},""")

            top_k_optimized_prompts = prompt_optim_agent.get_optimized_prompt(k=3)
            # best_template = max(top_k_optimized_prompts, key=lambda x: x['train_score'])
            # Store all prompts but keep them sorted by train_score
            optimized_prompts = [
                prompt["instruction_template"]
                for prompt in sorted(
                    top_k_optimized_prompts,
                    key=lambda x: x["train_score"],
                    reverse=True,
                )
            ]

            self.optimize_dataset.optimized_k_prompts = optimized_prompts
            self.optimize_dataset.save()
            self.create_column_develop_optimizer()

        except Exception as e:
            logger.exception(f"{e} error optimiser")
            self.optimize_dataset.status = StatusType.FAILED.value
            self.optimize_dataset.optimized_k_prompts = [str(e)]
            self.optimize_dataset.save()
            self.create_column_develop_optimizer()

    def run(self):
        self.optimize_dataset.status = StatusType.RUNNING.value
        self.optimize_dataset.save()
        self.runner_topk_develop_optimizer()
        self._update_api_call_log_row()

    def run_evaluation(self, old_column, new_column):
        """Run evaluation on result columns using multi-threading"""

        def run_column_evaluation(eval_template, column):
            """Helper function to run evaluation for a single template-column combination"""
            try:
                close_old_connections()
                runner = EvaluationRunner(
                    user_eval_metric_id=eval_template.id,
                    optimize=self.optimize_dataset,
                    column=column,
                    source="optimization",
                    source_configs={
                        "dataset_id": str(self.optimize_dataset.id),
                        "evaluation_id": str(eval_template.id),
                        "source": "optimization",
                    },
                    source_id=eval_template.id,
                )
                runner.run_prompt()
                return True
            except Exception as e:
                logger.error(
                    f"Error evaluating template {eval_template.id} for column {column.id}: {str(e)}"
                )
                return False
            finally:
                close_old_connections()

        # Create tasks for parallel execution
        tasks = []
        for eval_template in self.optimize_dataset.user_eval_template_ids.all():
            # both old and new column evaluations for each template
            tasks.append((eval_template, old_column))
            tasks.append((eval_template, new_column))

        # Wrap function with OTel context propagation for thread safety
        wrapped_run_column_evaluation = wrap_for_thread(run_column_evaluation)

        # Run evaluations in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks
            futures = [
                executor.submit(wrapped_run_column_evaluation, template, column)
                for template, column in tasks
            ]

            # Wait for all tasks to complete
            for future, (template, column) in zip(futures, tasks, strict=False):
                try:
                    success = future.result()
                    if not success:
                        logger.error(
                            f"Failed to evaluate template {template.id} for column {column.id}"
                        )
                except Exception as e:
                    logger.error(
                        f"Exception in evaluation: template {template.id}, column {column.id}: {str(e)}"
                    )

    def run_feedback_eval(self, column, user_eval_metric):
        """Run feedback evaluation for a specific user_eval_metric"""
        try:
            close_old_connections()
            rows = Row.objects.filter(dataset=user_eval_metric.dataset, deleted=False)
            properties = get_mixpanel_properties(
                org=user_eval_metric.organization,
                dataset=user_eval_metric.dataset,
                count=rows.count(),
                source=MixpanelSources.OPTIMIZE.value,
                optimize_dataset=self.optimize_dataset,
                eval=user_eval_metric,
            )
            track_mixpanel_event(MixpanelEvents.EVAL_RUN_STARTED.value, properties)
            runner = EvaluationRunner(
                user_eval_metric_id=user_eval_metric.id,
                optimize=self.optimize_dataset,
                column=column,
                source_configs={},
                source="optimization",
                source_id=user_eval_metric.template.id,
            )
            runner.run_prompt()
            return True
        except Exception as e:
            logger.error(
                f"Error running feedback evaluation for metric {user_eval_metric.id}: {str(e)}"
            )
        finally:
            close_old_connections()


class OptimizationDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OptimizationDetailSerializer
    queryset = OptimizationDataset.objects.all()

    def get_object(self):
        obj = super().get_object()
        # Replace column IDs with column names in the optimized prompts
        if obj.optimized_k_prompts:
            obj.optimized_k_prompts = [
                replace_ids_with_column_name(prompt)
                for prompt in obj.optimized_k_prompts
            ]
        return obj


optimise_prompt = """
You are an AI assistant tasked with analyzing a prompt and evaluation metrics to create one specific evaluation criterion. Your goal is to generate a single, focused criterion that will help optimize the given prompt based on the provided metrics.

First, you will be given a set of evaluation metrics:

<metrics>
{metrics}
</metrics>

Next, you will be provided with the prompt that needs to be optimized:

<prompt_to_optimize>
{prompt}
</prompt_to_optimize>

Carefully analyze both the evaluation metrics and the prompt. Create one specific, measurable criterion in the form of a question that directly evaluates how well the prompt's output meets the given metrics.

The criterion should:
- Directly address both the prompt's purpose and the evaluation metrics
- Be specific and measurable
- Focus on the most critical aspect of optimization needed

Present your result as a RFC8259 compliant JSON object with a single key and an array containing [criterion_question, 1.0]. For example:

{{
  "1": ["Your specific evaluation question here", 1.0]
}}

Return only the JSON object without any additional text or explanation."""
