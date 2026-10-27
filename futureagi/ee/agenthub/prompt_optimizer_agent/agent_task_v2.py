import json
import os
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from tfc.telemetry import wrap_for_thread
from tqdm import tqdm

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)

PROMPT_OPTIMIZER_THREAD_WORKER_COUNT = 2

from agentic_eval.core_evals.fi_evals import *
from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.views.eval_runner import EvaluationRunner
try:
    from ee.usage.utils.usage_entries import APICallStatusChoices, APICallTypeChoices, log_api_call
except ImportError:
    APICallStatusChoices = None
    APICallTypeChoices = None
    log_api_call = None

from .prompts import (
    CRITERIA_CORRECTOR_PROMPT_MB,
    FOLLOW_UP_PROMPT,
    INSTRUCTION_CORRECTOR_PROMPT_MB,
)

futureagi_eval_types = [
    "OutputEvaluator",
    "ContextEvaluator",
    "RankingEvaluator",
    "ImageInstructionEvaluator",
    "PromptEvaluator",
    "ImageInputOutputEvaluator",
    "DeterministicEvaluator",
]

client = LLM(
    model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
    temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
    max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
    provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
)


def get_improvements(improvement_str):
    try:
        sub1 = "<INSTRUCTION_IMPROVEMENTS>"
        sub2 = "</INSTRUCTION_IMPROVEMENTS>"

        idx1 = improvement_str.index(sub1)
        idx2 = improvement_str.index(sub2)

        improvements = improvement_str[idx1 + len(sub1) + 1 : idx2]

        return improvements
    except Exception:
        traceback.print_exc()
        return improvement_str


def get_template(template_str):
    try:
        sub1 = "<IMPROVED_INSTRUCTION_TEMPLATE>"
        sub2 = "</IMPROVED_INSTRUCTION_TEMPLATE>"

        idx1 = template_str.index(sub1)
        idx2 = template_str.index(sub2)

        improvement_instruction_template = template_str[idx1 + len(sub1) + 1 : idx2]
        return improvement_instruction_template
    except Exception:
        traceback.print_exc()
        return template_str


def parse_template(template, variables):
    """
    Replaces placeholders in the template with their corresponding values from the variables list.

    Args:
    - template (str): The template string with placeholders.
    - variables (dict): A dictionary with variable names and their values.

    Returns:
    - str: The template with placeholders replaced by the actual values.
    """
    if variables:
        for var_name, var_value in variables.items():
            # Escape the variable name for regex
            escaped_var_name = re.escape(var_name)
            # Define all possible placeholder patterns using raw strings
            patterns = [
                r"\{\{" + escaped_var_name + r"\}\}",  # {{var_name}}
                r"\$\$" + escaped_var_name + r"\$\$",  # $$var_name$$
                r"\{" + escaped_var_name + r"\}",  # {var_name}
                r"\$" + escaped_var_name + r"\$",  # $var_name$
            ]

            # Replace each pattern with the corresponding value
            for pattern in patterns:
                template = re.sub(pattern, str(var_value), template)

    return template


def get_updated_chat(chat, instruction_template, client=None):
    if client is None:
        _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        client = LLM(
            model_name=_cfg.model_name,
            temperature=_cfg.temperature,
            max_tokens=_cfg.max_tokens,
            provider=_cfg.provider,
        )

    user_message = next((msg for msg in chat if msg.get("role") == "user"), None)

    if not user_message:
        return chat

    variables = user_message.get("variables")
    final_content = instruction_template or user_message.get("prompt_template")

    if variables and final_content:
        content = parse_template(final_content, variables)
    else:
        content = user_message.get("content")

    messages = [{"role": "user", "content": content}]
    response_content = client._get_completion_content(messages)

    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": response_content},
    ]


class TextGradientDescent:
    def step_v3(self, system_prompt, score, judgement_criteria, client=None):
        """
        Responsible to get a final improved template after running on a batch of data and  getting there score , judgement_criteria and make a correction accordingly to get a final template for that batch of data.
        """
        if client is None:
            _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
            client = LLM(
                model_name=_cfg.model_name,
                temperature=_cfg.temperature,
                max_tokens=_cfg.max_tokens,
                provider=_cfg.provider,
            )

        messages = [
            {
                "role": "user",
                "content": CRITERIA_CORRECTOR_PROMPT_MB.format(
                    system_prompt=system_prompt,
                    # score=score,
                    CRITERIA_JUDGEMENT=judgement_criteria,
                ),
            }
        ]
        suggested_improvements = client._get_completion_content(messages)
        # suggested_improvements = get_improvements(suggested_improvements)

        messages = [
            {
                "role": "user",
                "content": INSTRUCTION_CORRECTOR_PROMPT_MB.format(
                    system_prompt=system_prompt,
                    improved_instructions=suggested_improvements,
                ),
            }
        ]
        improved_instruction_template = client._get_completion_content(messages)
        # improved_instruction_template = get_template(improved_instruction_template)
        return improved_instruction_template


class PromptOptimizer:
    def __init__(
        self,
        prompt,
        train_data,
        variables=None,
        model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
        max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
        provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
        config=None,
        optimizer=None,
    ):
        self.prompt = prompt
        self.prompt_optimizer = TextGradientDescent()
        self.train_data = train_data
        self.variables = variables
        self.optimizer = optimizer
        self.user_eval_metrics = UserEvalMetric.objects.filter(
            id__in=optimizer.user_eval_template_ids.all()
        ).select_related("template")
        self.client = LLM(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
            config=config,
            optimizer=True,
        )

    def _get_evaluation_feedback(self, chat_history):
        """
        Get evaluation feedback and scores from multiple evaluation metrics

        Args:
            chat_history (list): List of chat messages

        Returns:
            tuple: (aggregated_judgement_criterias, aggregated_score)
        """
        if not self.user_eval_metrics:
            return "", 0.0

        all_judgements = []
        scores = []

        # Process each evaluation metric
        for eval_metric in self.user_eval_metrics:
            try:
                # Get the evaluation template configuration
                eval_template = eval_metric.template
                user_eval_metric_config = eval_metric.config
                eval_config = eval_template.config
                futureagi_eval = (
                    True
                    if eval_template.config.get("eval_type_id") in futureagi_eval_types
                    else False
                )
                runner = EvaluationRunner(
                    "eval_type_id",
                    format_output=True,
                    futureagi_eval=futureagi_eval,
                    source="optimization",
                    source_id=eval_metric.id,
                    source_configs=user_eval_metric_config["mapping"],
                )
                runner.eval_template = eval_template
                # Get the evaluation class and run evaluation
                eval_class_name = eval_config.get("eval_type_id")
                from evaluations.engine.registry import get_eval_class, is_registered
                if not is_registered(eval_class_name):
                    continue
                eval_class = get_eval_class(eval_class_name)

                # Setup and run evaluation
                run_params = self._setup_eval_params(
                    eval_class,
                    eval_config,
                    user_eval_metric_config,
                    eval_template,
                    chat_history,
                    futureagi_eval,
                )

                eval_instance = runner._create_eval_instance(
                    config={}, eval_class=eval_class
                )

                cols = Column.objects.filter(
                    id__in=user_eval_metric_config["mapping"].values()
                ).values("id", "data_type")
                col_map = {col["id"]: col["data_type"] for col in cols}
                input_types = {
                    key: col_map.get(str(value))
                    if col_map.get(str(value)) in ["image", "audio"]
                    else "text"
                    for key, value in user_eval_metric_config["mapping"].items()
                }
                config = {"mappings": run_params, "input_data_types": input_types}
                api_call_log_row = log_api_call(
                    organization=eval_metric.organization,
                    api_call_type=APICallTypeChoices.OPTIMISATION_EVALUATION.value,
                    source="optimization",
                    source_id=eval_metric.template.id,
                    config=config,
                    workspace=eval_metric.workspace,
                )

                if not api_call_log_row:
                    api_call_log_row.status = APICallStatusChoices.ERROR.value
                    api_call_log_row.save(update_fields=["status"])

                if api_call_log_row.status != APICallStatusChoices.PROCESSING:
                    raise Exception("API call not allowed.")

                eval_result = eval_instance.run(**run_params)

                # Process evaluation results
                output_type = eval_config.get("output", "score")
                score, reason = self._process_eval_result(eval_result, output_type)

                config_dict = json.loads(api_call_log_row.config)
                config_dict.update(
                    {
                        "input": eval_result.eval_results[0].get("data"),
                        "output": {"output": score, "reason": reason},
                    }
                )
                api_call_log_row.config = json.dumps(config_dict)
                api_call_log_row.status = APICallStatusChoices.SUCCESS.value
                api_call_log_row.save(update_fields=["config", "status"])

                # # Normalize score
                # normalized_score = self._normalize_score(score, output_type)

                # Collect judgements and scores
                judgement = {
                    "criteria": eval_template.description
                    if not hasattr(eval_template, "criteria")
                    else eval_template.criteria,
                    "judgment": reason,
                    "score": score,
                }

                all_judgements.append(judgement)
                scores.append(score)

            except Exception as e:
                logger.exception(
                    f"Error processing evaluation metric {eval_metric.id}: {str(e)}"
                )
                continue
        # Format aggregated judgement criterias
        aggregated_judgement_criterias = self._format_judgements(all_judgements)

        # Calculate aggregated score (average of all scores)
        aggregated_score = round((sum(scores) / len(scores)) * 100, 2)

        return aggregated_judgement_criterias, aggregated_score

    def _setup_eval_params(
        self,
        eval_class,
        eval_config,
        user_eval_metric_config,
        eval_template,
        chat_history,
        futureagi_eval,
    ):
        """Helper method to setup evaluation parameters"""
        run_params = {}
        required_keys = eval_config.get("required_keys", [])

        # Map common parameters from chat history
        param_mapping = {
            "input": chat_history[0]["content"],
            "output": chat_history[-1]["content"],
            "response": chat_history[-1]["content"],
            "expected_response": chat_history[-1]["content"],
            "text": chat_history[-1]["content"],
            "query": chat_history[-1]["content"],
            "context": chat_history[0].get("context", ""),
            "messages": chat_history[-1]["content"]
            if isinstance(chat_history[-1]["content"], list)
            else [chat_history[-1]["content"]],
            "reference": chat_history[0]["content"],
            "hypothesis": chat_history[-1]["content"],
        }
        i = 0
        for key in required_keys:
            if key in param_mapping:
                run_params[key] = param_mapping[key]
            else:
                if i == 0:
                    run_params[key] = param_mapping["input"]
                    i += 1
                else:
                    run_params[key] = param_mapping["output"]

        return run_params

    def _create_eval_instance_config(
        self, eval_class, user_eval_metric_config, eval_template, futureagi_eval, runner
    ):
        """Helper method to create evaluation instance"""

        if user_eval_metric_config:
            if user_eval_metric_config.get("model"):
                user_eval_metric_config["api_key"] = os.getenv("OPENAI_API_KEY")

        if futureagi_eval:
            user_eval_metric_config["api_key"] = None
            model, provider = runner._get_futureagi_model_config(eval_template)
            user_eval_metric_config["model"] = model
            user_eval_metric_config["provider"] = provider

            if eval_template.config.get("eval_type_id") == "DeterministicEvaluator":
                self._setup_deterministic_config(eval_template, user_eval_metric_config)

        return user_eval_metric_config

    def _process_eval_result(self, eval_results, output_type):
        eval_result = {
            "data": eval_results.eval_results[0].get("data"),
            "failure": eval_results.eval_results[0].get("failure"),
            "reason": eval_results.eval_results[0].get("reason"),
            "runtime": eval_results.eval_results[0].get("runtime"),
            "model": eval_results.eval_results[0].get("model"),
            "metrics": eval_results.eval_results[0].get("metrics"),
            "metadata": eval_results.eval_results[0].get("metadata"),
        }
        """Helper method to process evaluation results"""
        if output_type in ("score", "numeric"):
            metrics = eval_result.get("metrics", [])
            metrics = metrics[:1]
            score = float(metrics[0].get("value", 0.0))
            reason = eval_result.get("reason", "No reason provided")
        elif output_type == "Pass/Fail":
            score = 1.0 if not eval_result.get("failure") else 0.0
            reason = eval_result.get("reason", "No reason provided")
        elif output_type == "choices":
            score = float(eval_result.get("data", 0.0))
            reason = eval_result.get("reason", "No reason provided")
        else:
            score = 1.0 if not eval_result.get("failure") else 0.0
            reason = eval_result.get("reason", "No reason provided")

        return score, reason

    def _normalize_score(self, score, output_type):
        """Helper method to normalize scores"""
        try:
            if output_type == "Pass/Fail":
                return score  # Already normalized to 0.0 or 1.0
            elif isinstance(score, dict):
                # Handle dictionary of scores
                valid_scores = [
                    float(s)
                    for s in score.values()
                    if str(s).replace(".", "").isdigit()
                ]
                normalized = (
                    sum(valid_scores) / len(valid_scores) if valid_scores else 0.0
                )
            else:
                normalized = float(score)

            # Scale to percentage if needed
            return normalized * 100 if normalized <= 1.0 else normalized
        except (ValueError, TypeError):
            return 0.0

    def _format_judgements(self, judgements):
        """Helper method to format judgements"""
        return "\n".join(
            f"\nCRITERIA:{j['criteria']}\nJUDGEMENT:{j['judgment']}\n"
            for j in judgements
        )

    def _setup_deterministic_config(self, eval_template, config):
        """Helper method to setup deterministic evaluator config"""
        if "choices" not in config:
            config["choices"] = eval_template.choices
            config["rule_prompt"] = eval_template.criteria
            config["multi_choice"] = eval_template.multi_choice

    def process_row_data(self, row, instruction_template):
        original_chat = [
            {
                "role": "user",
                "content": row["user_chat"],
                "variables": row["variables"],
                "prompt_template": row["prompt_template"],
                "metadata": row["metadata"],
            },
            {"role": "assistant", "content": row["model_chat"]},
        ]

        chat_history = get_updated_chat(
            original_chat,
            instruction_template or row["prompt_template"],
            client=self.client,
        )

        return self._get_evaluation_feedback(chat_history)

    def _process_mini_batch(self, mini_batch, mb_super_instruction_template):
        """
        responsible to get score and judgement_criterias for each row in the minibatch
        """

        def _process_mb_row(row, mb_super_instruction_template):
            judgement_criterias, score = self.process_row_data(
                row, mb_super_instruction_template
            )

            return judgement_criterias, score

        mini_batch_rows = [row for index, row in mini_batch.iterrows()]

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_mb_row = wrap_for_thread(_process_mb_row)

        future_list = []
        with ThreadPoolExecutor(
            max_workers=PROMPT_OPTIMIZER_THREAD_WORKER_COUNT
        ) as executor:
            for mini_batch_row in mini_batch_rows:
                future = executor.submit(
                    wrapped_process_mb_row,
                    mini_batch_row,
                    mb_super_instruction_template,
                )
                future_list.append(future)

        results = []
        for future in as_completed(future_list):
            try:
                # Optionally get the result of the task
                results.append(future.result())
            except Exception as e:
                logger.exception(f"Task generated an exception: {e}")

        return results

    def get_optimized_prompt(self, k=5):
        num_mini_batch = 2
        train_ratio = 0.2
        val_ratio = 0.2
        max_iterations = 10

        mb_super_instruction_template = self.prompt
        instruction_template_list = []

        # Split data into train and validation sets
        shuffled_df = self.train_data.sample(frac=1, random_state=42).reset_index(
            drop=True
        )
        total_samples = len(shuffled_df)
        train_size = max(1, int(train_ratio * total_samples))
        val_size = max(1, int(val_ratio * total_samples))

        if total_samples == 1:
            train_chat_data = val_chat_data = shuffled_df
        else:
            train_chat_data, val_chat_data, _ = np.array_split(
                shuffled_df, [train_size, train_size + val_size]
            )
        curr_train_score = self._calculate_score(
            train_chat_data, mb_super_instruction_template
        )
        train_chat_data_mb = np.array_split(train_chat_data, num_mini_batch)
        iteration_num = 1
        prev_train_score = 0
        curr_train_score = self._calculate_score(
            train_chat_data, mb_super_instruction_template
        )

        while iteration_num <= max_iterations and len(instruction_template_list) < k:
            for mini_batch in tqdm(train_chat_data_mb):
                mb_result = self._process_mini_batch(
                    mini_batch=mini_batch,
                    mb_super_instruction_template=mb_super_instruction_template,
                )

                judgement_criterias_list = [jc for jc, _ in mb_result]
                scores = [score for _, score in mb_result]
                avg_score_mb = np.mean(scores)

                mb_super_instruction_template = self.prompt_optimizer.step_v3(
                    system_prompt=mb_super_instruction_template or self.prompt,
                    score=avg_score_mb,
                    judgement_criteria="\n".join(judgement_criterias_list),
                    client=self.client,
                )
                mb_super_instruction_template = self.client._get_completion_content(
                    [
                        {
                            "role": "user",
                            "content": FOLLOW_UP_PROMPT.format(
                                original_prompt=self.prompt,
                                suggested_prompt=mb_super_instruction_template,
                                CRITERIA_JUDGEMENT="\n".join(judgement_criterias_list),
                            ),
                        }
                    ]
                )

            prev_train_score = (
                curr_train_score
                if curr_train_score > prev_train_score
                else prev_train_score
            )
            curr_train_score = self._calculate_score(
                train_chat_data, mb_super_instruction_template
            )

            if curr_train_score > prev_train_score:
                instruction_template_list.append(
                    {
                        "iteration_num": iteration_num,
                        "train_score": curr_train_score,
                        "instruction_template": mb_super_instruction_template,
                    }
                )

            iteration_num += 1
        if not instruction_template_list:
            instruction_template_list.append(
                {
                    "train_score": curr_train_score,
                    "instruction_template": self.prompt,
                }
            )
        return sorted(
            instruction_template_list, key=lambda x: x["train_score"], reverse=True
        )[:k]

    def _log_metrics(self, train_score, val_score, super_instruction_template):
        pass

    def _calculate_score(self, data_df, mb_super_instruction_template):
        def _calculate_score_single(row, mb_super_instruction_template):
            original_chat = [
                {
                    "role": "user",
                    "content": row["user_chat"],
                    "variables": row["variables"],
                    "prompt_template": row["prompt_template"],
                    "metadata": row["metadata"],
                },
                {"role": "model", "content": row["model_chat"]},
            ]

            chat_history = get_updated_chat(
                original_chat, mb_super_instruction_template, client=self.client
            )

            _, score = self._get_evaluation_feedback(chat_history)
            return score

        data_df_rows = [row for index, row in data_df.iterrows()]

        # Wrap function with OTel context propagation for thread safety
        wrapped_calculate_score_single = wrap_for_thread(_calculate_score_single)

        future_list = []
        with ThreadPoolExecutor(
            max_workers=PROMPT_OPTIMIZER_THREAD_WORKER_COUNT
        ) as executor:
            for data_df_row in data_df_rows:
                future = executor.submit(
                    wrapped_calculate_score_single,
                    data_df_row,
                    mb_super_instruction_template,
                )
                future_list.append(future)

        score_list = []
        for future in as_completed(future_list):
            try:
                score_list.append(future.result())
            except Exception as e:
                logger.exception(f"Task generated an exception: {e}")

        return np.mean(score_list)


if __name__ == "__main__":
    pass
