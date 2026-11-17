import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import structlog
from django.db import close_old_connections
from django.db.models import Case, When

# Activity-aware stub: invocations raise a Temporal non-retryable
# ApplicationError (these tasks run inside Temporal optimizer activities).
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import (
        PromptOptimizer,
        get_updated_chat,
    )
    from ee.agenthub.text_eval_agent.eval_text_llm import EvalTextLLM
except ImportError:
    PromptOptimizer = _ee_stub("PromptOptimizer")
    get_updated_chat = _ee_stub("get_updated_chat")
    EvalTextLLM = _ee_stub("EvalTextLLM")
from model_hub.models.conversations import Node
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.serializers.conversation import NodeSerializer
from model_hub.serializers.metric import MetricSerializer, MetricSerializerWithAIModel
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.tasks.agent import create_criteria, create_criteria_text_prompt
from model_hub.utils.clickhouse import update_eval_record
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity
from tfc.utils.clickhouse import ClickHouseClientSingleton

logger = structlog.get_logger(__name__)

PROMPT_OPTIMIZER_EVAL_TEMPLATE_WORKERS = 6
PROMPT_OPTIMIZER_PROCESS_SCORE_WORKERS = 6


@temporal_activity(time_limit=3600 * 3, queue="default")
def evaluate_top_k_prompts_cel(data_to_process, optim_obj_id=None):
    # Wrap function with OTel context propagation for thread safety
    wrapped_process_gen_text = wrap_for_thread(process_gen_text)

    future_list = []
    with ThreadPoolExecutor(
        max_workers=PROMPT_OPTIMIZER_EVAL_TEMPLATE_WORKERS
    ) as executor:
        for inference in data_to_process:
            inference["optim_obj_id"] = inference["optim_id"]
            metrics_list = [
                MetricSerializerWithAIModel(metric).data
                for metric in Metric.objects.filter(
                    id__in=[str(inference["metric"]["id"])]
                )
            ]
            top_k_prompts_list = inference["k_prompts"]
            if top_k_prompts_list and len(top_k_prompts_list) > 0:
                top_k_prompts_list.insert(0, "")
                future = executor.submit(
                    wrapped_process_gen_text,
                    inference,
                    metrics_list,
                    top_k_prompts_list,
                    optim_obj_id,
                )
                future_list.append(future)

    for future in as_completed(future_list):
        try:
            # Optionally get the result of the task
            future.result()
        except Exception as e:
            logger.exception(f"Task generated an exception: {e}")


@temporal_activity(time_limit=3600 * 3, queue="default")
def gather_data_for_optimization():
    client = ClickHouseClientSingleton()
    data_to_process = []
    optimization_objects = OptimizeDataset.objects.filter(
        status=OptimizeDataset.StatusType.COMPLETED.value,
        optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
    ).all()
    for optimization_obj in optimization_objects:
        metrics = optimization_obj.metrics.all()
        start_date = optimization_obj.start_date.date()
        end_date = optimization_obj.end_date.date()
        for metric in metrics:
            if len(metric.criteria_breakdown) == 0:
                create_criteria(metric)
            datasets = metric.datasets
            if datasets and len(datasets) == 0:
                continue

            dataset_filter_query = []
            params = {
                "model_id": str(metric.model.id),
                "start_date": str(start_date),
                "end_date": str(end_date),
                "status_key": f"optimized_{optimization_obj.id}_{metric.id}_status",
            }
            for index, dataset in enumerate(datasets):
                params[f"environment_{index}"] = OptimizeDataset.EnvTypes.events_number(
                    dataset["environment"]
                )
                params[f"version_{index}"] = str(dataset["model_version"])
                dataset_filter_query.append(
                    f"""
                    ( Environment = %(environment_{index})s
                    AND ModelVersion = %(version_{index})s
                    AND EventDate>=%(start_date)s
                    AND EventDate<=%(end_date)s)
                """
                )

            dataset_filter_query = " OR ".join(dataset_filter_query)

            dataset_filter_query = f""" AND ( {dataset_filter_query} )"""

            if metric.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
                filter_query = f"""
                    SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue, original_uuid
                    FROM events
                    WHERE AIModel = %(model_id)s
                    {dataset_filter_query}
                    AND (
                        NOT has(EvalResults.Key, %(status_key)s)
                        OR (
                            has(EvalResults.Key, %(status_key)s)
                            AND arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(status_key)s)) = 'not_processed'
                        )
                    )
                    AND has(Features.Key, 'node_id')
                    AND deleted=0
                    ORDER BY EventDateTime ASC
                    LIMIT 10

                """

                # print(filter_query)

                clickhouse_data = client.execute_read(filter_query, params)

                node_ids = [d[0] for d in clickhouse_data]
                uuids = [d[1] for d in clickhouse_data]
                order = Case(
                    *[When(id=id, then=pos) for pos, id in enumerate(node_ids)]
                )
                parent_nodes = Node.objects.filter(id__in=node_ids).order_by(order)
                for parent_node, _uuid in zip(parent_nodes, uuids, strict=False):
                    child_nodes = parent_node.child_nodes.all()
                    if not child_nodes:
                        continue
                    child_node = child_nodes[len(child_nodes) - 1]
                    past_nodes = [child_node]
                    trav_node = parent_node
                    while trav_node is not None:
                        past_nodes.insert(0, trav_node)
                        trav_node = trav_node.parent_node

                    data_to_process.append(
                        {
                            "_uuid": _uuid,
                            "k_prompts": optimization_obj.optimized_k_prompts,
                            "optim_id": optimization_obj.id,
                            "model_type": metric.model.model_type,
                            "conversation_id": parent_node.conversation.id,
                            "metric": MetricSerializer(metric).data,
                            "conversation": [
                                NodeSerializer(n).data for n in past_nodes
                            ],
                        }
                    )

                    res = {
                        f"optimized_{optimization_obj.id}_{metric.id}_status": "processing",
                    }

                    update_eval_record(
                        "events",
                        "original_uuid",
                        _uuid,
                        res,
                        columns=[
                            "UUID",
                            "original_uuid",
                            "EventDate",
                            "EventDateTime",
                            "EventName",
                            "EventType",
                            "AIModel",
                            "OrgID",
                            "PredictionID",
                            "ModelVersion",
                            "BatchID",
                            "Environment",
                            "Properties.Key",
                            "Properties.Value",
                            "Properties.DataType",
                            "Features.Key",
                            "Features.Value",
                            "Features.DataType",
                            "ActualLabel.Key",
                            "ActualLabel.Value",
                            "ActualLabel.DataType",
                            "PredictionLabel.Key",
                            "PredictionLabel.Value",
                            "PredictionLabel.DataType",
                            "EvalResults.Key",
                            "EvalResults.Value",
                            "EvalResults.DataType",
                            "ShapValues.Key",
                            "ShapValues.Value",
                            "ShapValues.DataType",
                            "Tags.Key",
                            "Tags.Value",
                            "Tags.DataType",
                            "Embedding",
                        ],
                    )

    if len(data_to_process) > 0:
        evaluate_top_k_prompts_cel.apply_async(
            args=(data_to_process, optimization_obj.id)
        )


def process_chat_history(conversation):
    """Return data in LLM format"""
    chat_history = []
    for conv in conversation:
        flattened_context = ""
        if conv["message"]["content"]["rag_info"].get("context"):
            flattened_context = [
                sentence
                for sublist in conv["message"]["content"]["rag_info"]["context"]
                for sentence in sublist
            ]
        chat_entry = {
            "role": conv["message"]["author"]["role"],
            "content": " ".join(conv["message"]["content"]["parts"]),
        }
        rag_info = conv["message"]["content"]["rag_info"]
        if rag_info.get("variables"):
            chat_entry["variables"] = rag_info["variables"]
        if rag_info.get("prompt_template"):
            chat_entry["prompt_template"] = rag_info["prompt_template"]
        if rag_info.get("context"):
            chat_entry["context"] = " ".join(flattened_context)
        chat_history.append(chat_entry)
    return chat_history


def evaluate_prompt(agent_task, criteria_breakdown, new_assistant_answer):
    return agent_task.score_chat_history(
        criteria_breakdown=criteria_breakdown,
        chat_history=new_assistant_answer,
        is_rag=None,
        model_type="test",
        eval_rag_context=None,
        eval_rag_output=None,
    )


def process_metric_and_prompts(inference, metric, top_k_prompt, prompt_index):
    close_old_connections()
    inference_copy = copy.deepcopy(inference)  # Avoid modifying the original inference
    inference_copy["metric"] = metric
    inference_copy["model_type"] = metric["model"]["model_type"]
    inference_copy["metric"]["eval_prompt_template_index"] = prompt_index

    eval_instructions = inference_copy["metric"]["text_prompt"]
    optim_obj = OptimizeDataset.objects.filter(id=inference_copy["optim_obj_id"]).get()
    if len(inference_copy["metric"]["criteria_breakdown"]) > 0:
        criteria_breakdown = inference_copy["metric"]["criteria_breakdown"]
    else:
        # One key: the criteria call runs on the run's route, with the client
        # the runner's calls use (_run_client): Future AGI credits held and
        # settled per call; never key-less, never platform-paid.
        organization, workspace = _run_billing_identity(optim_obj)
        credit_ledger.bind_tenancy(organization, workspace)
        criteria_breakdown = create_criteria_text_prompt(
            eval_instructions, llm=_run_client(organization, workspace)
        )

    chat_history = process_chat_history(inference_copy["conversation"])
    is_rag = (
        False
        if optim_obj.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE
        else True
    )
    # is_rag=True
    new_assistant_answer = get_updated_chat(chat_history, top_k_prompt, is_rag)
    agent_task = EvalTextLLM()
    score_results = agent_task.score_chat_history(
        criteria_breakdown=criteria_breakdown,
        chat_history=new_assistant_answer,
        is_rag=is_rag,
        model_type="test",
        eval_rag_context=None,
        eval_rag_output=None,
    )
    close_old_connections()
    if prompt_index == 0:
        prompt_index = "original"
        return {
            f"optimized_{inference_copy['optim_obj_id']}_{inference_copy['metric']['id']}_{prompt_index}": score_results[
                "score"
            ]
        }
    else:
        return {
            f"optimized_{inference_copy['optim_obj_id']}_{inference_copy['metric']['id']}_{prompt_index - 1}_score": score_results[
                "score"
            ]
        }


def process_all_metrics_and_prompts(inference, metrics_list, top_k_prompts_list):
    results = []
    # Wrap function with OTel context propagation for thread safety
    wrapped_process_metric_and_prompts = wrap_for_thread(process_metric_and_prompts)

    with ThreadPoolExecutor(
        max_workers=PROMPT_OPTIMIZER_PROCESS_SCORE_WORKERS
    ) as executor:
        future_to_metric_prompt = {
            executor.submit(
                wrapped_process_metric_and_prompts, inference, metric, k_prompt, idx
            ): (
                metric,
                k_prompt,
                idx,
            )
            for metric in metrics_list
            for idx, k_prompt in enumerate(top_k_prompts_list)
        }
    for future in future_to_metric_prompt:
        try:
            result = future.result()
            if result:
                results.append(result)
        except Exception as exc:
            logger.exception(f"Generated an exception: {exc}")
    return results


def process_gen_text(inference, metrics_list, top_k_prompts_list, optim_obj_id):
    try:
        res_ini = {}
        results_dict = process_all_metrics_and_prompts(
            inference, metrics_list, top_k_prompts_list
        )

        for optimized_metric_score in results_dict:
            res_ini.update(optimized_metric_score)

        for metric in metrics_list:
            res = {
                f"optimized_{optim_obj_id}_{str(metric['id'])}_status": "processed",
            }
            for optimized_metric_key, optimized_metric_score in res_ini.items():
                if str(metric["id"]) in optimized_metric_key:
                    res.update({optimized_metric_key: optimized_metric_score})

            metric_type = metric["metric_type"]

            if metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
                update_eval_record(
                    "events",
                    "original_uuid",
                    inference["_uuid"],
                    res,
                    columns=[
                        "UUID",
                        "original_uuid",
                        "EventDate",
                        "EventDateTime",
                        "EventName",
                        "EventType",
                        "AIModel",
                        "OrgID",
                        "PredictionID",
                        "ModelVersion",
                        "BatchID",
                        "Environment",
                        "Properties.Key",
                        "Properties.Value",
                        "Properties.DataType",
                        "Features.Key",
                        "Features.Value",
                        "Features.DataType",
                        "ActualLabel.Key",
                        "ActualLabel.Value",
                        "ActualLabel.DataType",
                        "PredictionLabel.Key",
                        "PredictionLabel.Value",
                        "PredictionLabel.DataType",
                        "EvalResults.Key",
                        "EvalResults.Value",
                        "EvalResults.DataType",
                        "ShapValues.Key",
                        "ShapValues.Value",
                        "ShapValues.DataType",
                        "Tags.Key",
                        "Tags.Value",
                        "Tags.DataType",
                        "Embedding",
                    ],
                )
    except Exception as e:
        logger.exception(f"{e} error in process_gen_text:")


@temporal_activity(time_limit=3600 * 24, queue="default")
def get_topk_prompts(
    model_name,
    environment,
    version,
    metrics,
    optim_obj_id,
    start_date,
    end_date,
):
    """Run a Prompt Template optimization: read its traced rows and its
    metrics (_runner_inputs), then optimize (runner_topk_optimizer).

    First, a retry of a run whose model calls had started (the run carries
    the marker) ends with no read, hold or call, whatever the run's status
    (_stop_a_retry_after_model_calls); a run that had completed keeps its
    results and the retry ends as a success. Any error while the rows and
    metrics are read ends the run with no retry and a fixed reason
    (_fail_run_without_retry); only a transient database error is left to
    retry. The raw error goes to the server log.
    """
    optimize_dataset = OptimizeDataset.objects.filter(id=optim_obj_id).get()
    if _stop_a_retry_after_model_calls(optimize_dataset):
        return
    try:
        runner_data, eval_prompts = _runner_inputs(
            model_name, environment, version, metrics, start_date, end_date
        )
    except Exception as exc:
        _fail_run_without_retry(optimize_dataset, None, exc)
        raise
    runner_topk_optimizer(runner_data, eval_prompts, optim_obj_id)


def _runner_inputs(
    model_name,
    environment,
    version,
    metrics,
    start_date,
    end_date,
):
    """The runner's rows, from the run's traced conversations (ClickHouse,
    then their nodes), and its metrics' instructions."""
    client = ClickHouseClientSingleton()
    dataset_filter_query = []
    dataset_filter_query.append(
        """
                    ( Environment = %(environment)s
                    AND ModelVersion = %(version)s
                    AND EventDate>=%(start_date)s
                    AND EventDate<=%(end_date)s)

                """
    )

    dataset_filter_query = " OR ".join(dataset_filter_query)

    dataset_filter_query = f""" AND ( {dataset_filter_query} )"""

    filter_query = f"""
                    SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue,
                    Tags.Key AS TagKeys,
                    Tags.Value AS TagValues
                    FROM events
                    WHERE AIModel = %(model_id)s
                    {dataset_filter_query}
                    AND has(Features.Key, 'node_id')
                    AND deleted=0
                    ORDER BY EventDateTime ASC

                """
    # A stored environment is compared as its number, as the result views
    # do; anything else is NULL and matches no row.
    params = {
        "model_id": str(model_name),
        "environment": OptimizeDataset.EnvTypes.events_number(environment),
        "version": str(version),
        "start_date": str(start_date),
        "end_date": str(end_date),
    }
    data_to_process = []
    clickhouse_data = client.execute_read(filter_query, params)

    node_ids = [d[0] for d in clickhouse_data]
    # clickhouse_data will now return tuples (FeatureValue, TagKeys, TagValues)
    tag_data = {d[0]: {"keys": d[1], "values": d[2]} for d in clickhouse_data}
    parent_nodes = Node.objects.filter(id__in=node_ids)
    for parent_node in parent_nodes:
        child_nodes = parent_node.child_nodes.all()
        if not child_nodes:
            continue
        child_node = child_nodes[len(child_nodes) - 1]
        past_nodes = [child_node]
        trav_node = parent_node
        while trav_node is not None:
            past_nodes.insert(0, trav_node)
            trav_node = trav_node.parent_node

        # Get the TagKeys and TagValues corresponding to the FeatureValue (node_id)
        tag_info = tag_data.get(str(parent_node.id), {"keys": [], "values": []})
        tags = dict(
            zip(tag_info["keys"], tag_info["values"], strict=False)
        )  # Combine keys and values into a dictionary

        data_to_process.append(
            {
                "model_type": "Generative LLM",
                "conversation_id": parent_node.conversation.id,
                "conversation": [NodeSerializer(n).data for n in past_nodes],
                "tags": tags,  # Store all tags (keys and values)
            }
        )

    user_chats = []
    model_chats = []
    context_chats = []
    prompt_templates = []
    variables = []
    metadata = []
    for data in data_to_process:
        chat_history = []
        for conv in data["conversation"]:
            flattened_context = ""
            if conv["message"]["content"]["rag_info"].get(
                "context"
            ):  # format = [ ["",""], ["",""] ,["",""] ]
                flattened_context = [
                    sentence
                    for sublist in conv["message"]["content"]["rag_info"]["context"]
                    for sentence in sublist
                ]
            chat_entry = {
                "role": conv["message"]["author"]["role"],
                "content": " ".join(conv["message"]["content"]["parts"]),
            }
            # Add 'variables' and 'prompt_template' if they exist in rag_info
            rag_info = conv["message"]["content"]["rag_info"]
            if rag_info.get("variables"):
                chat_entry["variables"] = rag_info["variables"]
            if rag_info.get("prompt_template"):
                chat_entry["prompt_template"] = rag_info["prompt_template"]
            if rag_info.get("context"):
                chat_entry["context"] = " ".join(flattened_context)

            chat_entry["metadata"] = data["tags"]
            # Append the entry to chat_history
            chat_history.append(chat_entry)

        last_conv = chat_history[-2:]
        for items in last_conv:
            if items.get("role") == "user":
                user_chats.append(items.get("content"))
                prompt_templates.append(items.get("prompt_template"))
                variables.append(items.get("variables"))
                context_chats.append(items.get("context"))
                metadata.append(items.get("metadata"))
            else:
                model_chats.append(items.get("content"))

    # clean prompt templates
    prompt_templates = list(prompt_templates)

    runner_data = {
        "prompt_template": prompt_templates,
        "context": context_chats,
        "user_chat": user_chats,
        "model_chat": model_chats,
        "variables": variables,
        "metadata": metadata,
    }

    metrics_list = Metric.objects.filter(id__in=metrics)
    eval_prompts = [metrics_obj.text_prompt for metrics_obj in metrics_list]

    return runner_data, eval_prompts


def _run_billing_identity(optimize_dataset):
    """The organization and workspace a run's model calls are made for: the
    run's own, else its AI model's (create_criteria takes a metric's from its
    AI model the same way)."""
    if optimize_dataset.organization_id is not None:
        return optimize_dataset.organization, optimize_dataset.workspace
    ai_model = optimize_dataset.model
    if ai_model is not None and ai_model.organization_id is not None:
        return ai_model.organization, ai_model.workspace
    return None, None


CRITERIA_UNAVAILABLE_MESSAGE = (
    "The criteria for this run could not be built from its metrics."
)
# What a run that failed after its model calls had started shows as the
# reason it ended, a retry stopped by its marker included. The error itself
# goes to the server log.
STOPPED_AFTER_MODEL_CALLS = (
    "The optimization stopped because of an error after it had started "
    "calling the model, so it was not retried. Run it again to start over."
)
# What a run that could not start, or had nothing to run on, shows as the
# reason it ended. The error itself goes to the server log.
COULD_NOT_START_MESSAGE = (
    "The optimization could not start because of an error. Run it again."
)
NO_ROWS_MESSAGE = (
    "The optimization found no traced rows for its model, version, environment "
    "and dates, so it did not run."
)
# What a run with no managed route shows as the reason it ended: on a
# deployment that can turn Future AGI credits on (Cloud, or a licensed
# install), and on a self-hosted one that cannot, where no Billing page would
# help.
NO_CREDITS_ROUTE_MESSAGE = (
    "The optimization runs on Future AGI credits, which are not available to "
    "this organization for its model, so it did not run. Check Future AGI "
    "credits on the Billing page, then run it again."
)
CREDITS_ONLY_MESSAGE = (
    "The optimization currently runs on Future AGI credits only, which this "
    "deployment does not offer, so it did not run."
)


def _no_route_message():
    """The no-route reason for this deployment: CREDITS_ONLY_MESSAGE where
    Future AGI credits cannot be turned on (tfc.ee_gating.is_oss: no EE
    license and not Cloud), else NO_CREDITS_ROUTE_MESSAGE."""
    from tfc import ee_gating

    return CREDITS_ONLY_MESSAGE if ee_gating.is_oss() else NO_CREDITS_ROUTE_MESSAGE


class _RunCannotGoOn(Exception):
    """A run that cannot go on, with the fixed reason it shows."""

    code = "CouldNotStart"
    message = COULD_NOT_START_MESSAGE


class _CriteriaUnavailable(_RunCannotGoOn):
    """The criteria helper gave nothing usable: an answer that does not parse,
    an empty breakdown (a cached one included), or any other failure that is
    not a refusal."""

    code = "CriteriaUnavailable"
    message = CRITERIA_UNAVAILABLE_MESSAGE


class _NoRowsToOptimize(_RunCannotGoOn):
    """The run's model, version, environment and dates have no traced rows."""

    code = "NoRowsToOptimize"
    message = NO_ROWS_MESSAGE


class _RetriedAfterModelCalls(_RunCannotGoOn):
    """A retry of a run whose model calls had already started
    (_stop_a_retry_after_model_calls)."""

    code = "FailedAfterModelCalls"
    message = STOPPED_AFTER_MODEL_CALLS


def _run_refusal(exc):
    """The typed refusal that stopped one of a run's model calls, as the
    message and type of the error that ends the run; None for any other error.

    A managed call fails with the managed transport's ModelRouteError, whose
    message is the ledger's refusal copy and never provider text. A model the
    organization has no managed route for is ModelRouteUnavailable; the run
    then shows this deployment's no-route reason (_no_route_message), since a
    stored key is not used here (_run_client). A tenancy or ledger refusal is
    one of the ledger's own classes.
    """
    from model_hub.services.model_route import ModelRouteError, ModelRouteUnavailable

    seen = set()
    item = exc
    while item is not None and id(item) not in seen:
        seen.add(id(item))
        if isinstance(item, ModelRouteError):
            return {"message": item.message, "type": item.code}
        if isinstance(item, ModelRouteUnavailable):
            return {"message": _no_route_message(), "type": item.code}
        item = item.__cause__ or item.__context__
    if credit_ledger.is_terminal_refusal(exc):
        refusal = credit_ledger.refusal_activity_error(exc)
        return {"message": refusal["message"], "type": refusal["type"]}
    return None


class _MeteredRunClient:
    """The one client every model call of a Prompt Template run is made with,
    the criteria calls included, bound to the run's organization
    (_run_client).

    PromptOptimizer logs a failed row and goes on, so the first route or
    ledger refusal is kept here: every later call is refused at once, before
    any model call, and the runner fails the run with it. ``settled`` counts
    the calls that were or may have been sent: every one that returned (a
    managed call returns only once it is settled) and every one that failed
    with an error other than a refusal. The runner never lets a retry pay
    for them again.
    """

    def __init__(self, llm):
        self.llm = llm
        self.refusal = None
        self.settled = 0
        self._lock = threading.Lock()

    def _get_completion_content(self, *args, **kwargs):
        refusal = self.refusal
        if refusal is not None:
            raise refusal
        try:
            content = self.llm._get_completion_content(*args, **kwargs)
        except Exception as exc:
            refused = _run_refusal(exc) is not None
            with self._lock:
                if refused:
                    if self.refusal is None:
                        self.refusal = exc
                else:
                    self.settled += 1
            raise
        with self._lock:
            self.settled += 1
        return content

    def __getattr__(self, name):
        if name == "llm":
            raise AttributeError(name)
        return getattr(self.llm, name)


def _run_client(organization, workspace):
    """The one client a run makes all its model calls with (the criteria
    calls, PromptOptimizer's loop and its scoring): a managed binding for the
    run's organization on the criteria helper's own model, bound as
    platform_llm binds one, so each call is held and settled on its own
    (managed_chat_completion, CallMeter.metered). The binding's operation is
    the ledger's route operation for this optimizer (PURPOSE_LEGACY_OPTIMIZER,
    ``run_prompt``), and it is every hold's purpose: the meter takes its
    purpose from the binding, so it is fixed per client, not per call.

    A stored provider key is never used here. An LLM given a key skips the
    gateway (agentic_eval/core/llm/llm.py:726) and calls
    ``litellm.completion(**payload)`` with a payload that carries no key
    (:1134, built at :1731), so the deployment's credentials would pay,
    unmetered. Without a managed binding, ModelRouteUnavailable is raised
    before any model call: never key-less, never platform-paid.
    """
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.llm.platform_model import PLATFORM_MODELS, PlatformFeature
    from model_hub.services import model_route

    config, _temperature = PLATFORM_MODELS[PlatformFeature.CRITERIA_HELPER]
    binding = model_route.bind_managed_route(
        organization_id=str(organization.id),
        workspace_id=str(workspace.id) if workspace is not None else None,
        model_name=config.model_name,
        provider=config.provider,
        operation=credit_ledger.route_operation(credit_ledger.PURPOSE_LEGACY_OPTIMIZER),
    )
    # PromptOptimizer's own client settings: the criteria helper's model is
    # the optimizer's default model (agent_task_v2.py).
    return _MeteredRunClient(
        LLM(
            provider=config.provider,
            model_name=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            optimizer=True,
            model_route=binding,
        )
    )


def _metered_criteria_breakdown(criteria_text, client):
    """The run's criteria breakdown from the criteria helper, on the run's
    client.

    One key: the helper's model call is made with the same client as the
    run's loop (_run_client), held and settled on its own on Future AGI
    credits (CallMeter.metered in credit_ledger_port). A refusal is raised as
    it came. Any other failure, an answer that does not parse included, is
    _CriteriaUnavailable, which ends the run without a retry: a retry would
    place a second hold.
    """
    try:
        return create_criteria_text_prompt(criteria_text, llm=client)
    except Exception as exc:
        if _run_refusal(exc) is not None:
            raise
        raise _CriteriaUnavailable(CRITERIA_UNAVAILABLE_MESSAGE) from exc


def _is_transient(exc):
    """A database connection that failed or was lost (django.db
    OperationalError or InterfaceError): the one error a retry before any
    model call may cure."""
    from django.db import InterfaceError, OperationalError

    return isinstance(exc, (OperationalError, InterfaceError))


def _fail_run_without_retry(optimize_dataset, client, exc):
    """End the run with no retry, unless ``exc`` is a transient database
    error (_is_transient) raised before any model call was sent, which is
    left to the caller to retry. The reason the run shows, first match:

    - a refusal (``exc``'s, or one the run's client kept): its own text;
    - a run that cannot go on (no rows, no usable criteria, a retry after its
      model calls had started): its fixed reason;
    - any error after a model call was sent: STOPPED_AFTER_MODEL_CALLS, since
      a retry would pay again for calls already made;
    - any other error before the first call: COULD_NOT_START_MESSAGE.

    The error itself goes to the server log. The run is failed with that
    reason and a non-retryable ApplicationError is raised, as
    tfc/temporal/dataset_optimization/activities.py ends a run on a refusal.
    If marking the run failed raises too, that is logged and the
    non-retryable error is still raised: a run left showing RUNNING is a
    display fault, while a retry after a sent call would pay again.
    """
    kept = client.refusal if client is not None else None
    refusal = _run_refusal(kept if kept is not None else exc)
    sent = client.settled if client is not None else 0
    if refusal is None and isinstance(exc, _RunCannotGoOn):
        refusal = {"message": exc.message, "type": exc.code}
    if refusal is None and sent > 0:
        logger.exception(
            f"Prompt Template optimization {optimize_dataset.id} stopped after "
            f"{sent} model calls: {str(exc)}"
        )
        refusal = {
            "message": STOPPED_AFTER_MODEL_CALLS,
            "type": "FailedAfterModelCalls",
        }
    if refusal is None:
        if _is_transient(exc):
            return
        logger.exception(
            f"Prompt Template optimization {optimize_dataset.id} could not "
            f"start: {str(exc)}"
        )
        refusal = {"message": COULD_NOT_START_MESSAGE, "type": "CouldNotStart"}
    try:
        optimize_dataset.mark_as_failed(refusal["message"])
    except Exception:
        logger.exception(
            f"Prompt Template optimization {optimize_dataset.id} could not be "
            "marked failed; it is not retried"
        )
    from temporalio import exceptions as temporal_exceptions

    raise temporal_exceptions.ApplicationError(
        refusal["message"], type=refusal["type"], non_retryable=True
    ) from exc


# The one key of a Prompt Template run's optimizer_config written here: the
# run's model calls have started. Its presence alone stops a retry (every run
# is a new row and a new workflow); the workflow ids it holds are for
# information only. No migration. The serializers that show optimizer_config
# list dataset optimization runs only (a column and an optimizer_algorithm),
# never Prompt Template runs.
MODEL_CALLS_STARTED_KEY = "prompt_template_model_calls_started"
# The marker's value when no activity's ids can be read (a direct call).
NO_EXECUTION = {"workflow_id": None, "workflow_run_id": None}


def _activity_execution():
    """The Temporal workflow execution this attempt belongs to: its workflow
    id and run id, the same on every retry of one execution. None outside an
    activity (a direct call)."""
    from temporalio import activity

    if not activity.in_activity():
        return None
    info = activity.info()
    return {
        "workflow_id": info.workflow_id,
        "workflow_run_id": info.workflow_run_id,
    }


def _carries_model_calls_marker(optimize_dataset):
    """Whether the run carries the marker, whatever ids it holds."""
    config = optimize_dataset.optimizer_config
    return isinstance(config, dict) and MODEL_CALLS_STARTED_KEY in config


def _mark_model_calls_started(optimize_dataset):
    """Record on the run, as one committed write, that its first model call is
    about to be sent, so that a retry sends none
    (_stop_a_retry_after_model_calls). It is always written, inside an
    activity or not: with this execution's ids when they can be read, else
    NO_EXECUTION. A failed write raises, so the run ends before any call.

    Neither the drop-in runner nor this module opens a transaction, so the
    save commits at once. The runner writes it on the instance whose full
    saves come after, so the marker survives them: a COMPLETED run still
    carries it, which _stop_a_retry_after_model_calls relies on.
    """
    execution = _activity_execution() or dict(NO_EXECUTION)
    config = optimize_dataset.optimizer_config
    config = dict(config) if isinstance(config, dict) else {}
    config[MODEL_CALLS_STARTED_KEY] = execution
    optimize_dataset.optimizer_config = config
    optimize_dataset.save(update_fields=["optimizer_config"])


def _stop_a_retry_after_model_calls(optimize_dataset):
    """End a retry of a run whose model calls had already started: True when
    the caller is to return at once, False when the run goes on.

    A run that carries the marker is never run again, whatever ids the marker
    holds and whatever the run's status: an earlier attempt sent calls that
    may have been paid. No read, hold or model call is made, and one warning
    is logged.

    - RUNNING (its worker was lost, so no handler ran): failed with
      STOPPED_AFTER_MODEL_CALLS, and the non-retryable error is raised
      (_fail_run_without_retry).
    - COMPLETED (the worker was lost after the results were saved, before
      Temporal heard): True. The row keeps its results and is not written.
    - FAILED, or any other status: the row is not written (its reason stays)
      and the same non-retryable error is raised.

    With no marker, no model call was sent and the run goes on.
    """
    if not _carries_model_calls_marker(optimize_dataset):
        return False
    status = optimize_dataset.status
    logger.warning(
        f"Prompt Template optimization {optimize_dataset.id} was retried after "
        f"its model calls had started (status {status}); it is not run again"
    )
    if status == OptimizeDataset.StatusType.COMPLETED:
        return True
    stopped = _RetriedAfterModelCalls(STOPPED_AFTER_MODEL_CALLS)
    if status == OptimizeDataset.StatusType.RUNNING:
        # Marks the run failed and raises.
        _fail_run_without_retry(optimize_dataset, None, stopped)
    from temporalio import exceptions as temporal_exceptions

    raise temporal_exceptions.ApplicationError(
        stopped.message, type=stopped.code, non_retryable=True
    ) from stopped


def _template_to_optimize(optimize_dataset, data):
    """The prompt template a run optimizes: the one it was created with, else
    the first template its traced conversations carry."""
    if optimize_dataset.prompt:
        return optimize_dataset.prompt
    templates = data.get("prompt_template") or []
    return next((template for template in templates if template), "")


def runner_topk_optimizer(data, eval_prompts, optim_obj_id):
    """Optimize a Prompt Template run's prompt against its metrics' criteria.

    ``eval_prompts`` (the metrics' instructions) is kept for the caller; the
    criteria are built from the run's own metrics below. Every model call of
    the run, the criteria helper's and PromptOptimizer's, is made with one
    managed client on the run's organization (_run_client). A run with no
    traced rows, no organization or no managed route is refused before any
    call. Immediately before the first model call (the criteria call, else
    the loop's first call), the run is marked (_mark_model_calls_started), so
    a retry after a lost worker sends nothing. Every error ends the run with
    no retry and a fixed reason (_fail_run_without_retry), a failed save of
    the marker or of the results included; only a transient database error
    before the first call is left to retry.
    """
    optimize_dataset = OptimizeDataset.objects.filter(id=optim_obj_id).get()
    client = None
    try:
        df_temp = pd.DataFrame(data)
        if df_temp.empty:
            raise _NoRowsToOptimize(NO_ROWS_MESSAGE)
        organization, workspace = _run_billing_identity(optimize_dataset)
        credit_ledger.bind_tenancy(organization, workspace)
        client = _run_client(organization, workspace)
        criteria_stored = len(optimize_dataset.criteria_breakdown) != 0
        if criteria_stored:
            criteria_breakdown = optimize_dataset.criteria_breakdown
        else:
            metrics_list = list(optimize_dataset.metrics.all())
            input_eval_prompts = [str(metric.text_prompt) for metric in metrics_list]
            metric = "\n".join(
                [f"{i}. {criteria}" for i, criteria in enumerate(input_eval_prompts)]
            )
            # The criteria call is the run's first model call. The full save
            # below keeps the marker, and optim_obj is loaded after it.
            _mark_model_calls_started(optimize_dataset)
            criteria_breakdown = _metered_criteria_breakdown(metric, client)

            optimize_dataset.criteria_breakdown = criteria_breakdown
            optimize_dataset.save()
        if not criteria_breakdown:
            # An empty breakdown, a cached one included, leaves PromptOptimizer
            # nothing to score against (it would read a dataset record instead).
            raise _CriteriaUnavailable(CRITERIA_UNAVAILABLE_MESSAGE)
        optim_obj = OptimizeDataset.objects.filter(id=optim_obj_id).get()
        # PromptOptimizer optimizes one template string and scores each
        # candidate against the criteria it is given, with the run's client;
        # RAG runs never reach this runner.
        prompt_optim_agent = PromptOptimizer(
            prompt=_template_to_optimize(optim_obj, data),
            train_data=df_temp,
            criteria_breakdown=criteria_breakdown,
            client=client,
        )
        if criteria_stored:
            # With stored criteria the loop sends the run's first model call.
            # The marker goes on optim_obj, whose full save comes last, so a
            # completed run keeps it.
            _mark_model_calls_started(optim_obj)
        top_k_optimized_prompts = prompt_optim_agent.get_optimized_prompt()
        if client.refusal is not None:
            # A refusal the loop logged and stepped over still ends the run.
            raise client.refusal
        # Saving the results is inside the handler too: a retry after it
        # failed would pay again for every call already made.
        optimized_prompts = [
            instruction_template["instruction_template"]
            for instruction_template in top_k_optimized_prompts
        ]
        optim_obj.optimized_k_prompts = optimized_prompts
        optim_obj.status = OptimizeDataset.StatusType.COMPLETED
        optim_obj.save()
    except Exception as exc:
        _fail_run_without_retry(optimize_dataset, client, exc)
        raise


def get_template_results(optimization_id):
    client = ClickHouseClientSingleton()

    filter_query = """
                   SELECT
                    splitByString('_',evalKey)[-2] as template_num,
                    splitByString('_',evalKey)[-3] as metric_id,
                    AVG(toFloat32(evalValue)) as score
                    FROM
                        (
                            SELECT
                            DISTINCT
                            arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS node_id,
                            er.Key AS evalKey,
                            er.Value AS evalValue

                            FROM events
                            ARRAY JOIN EvalResults as er
                            WHERE has(Features.Key, 'node_id')
                            AND deleted=0
                        )
                        WHERE
                        evalKey LIKE %(optimization_pattern)s
                        GROUP BY
                        1,2
                        """

    clickhouse_data = client.execute_read(
        filter_query, {"optimization_pattern": f"%{optimization_id}%"}
    )
    templates = [d[0] for d in clickhouse_data]
    metrics = [d[1] for d in clickhouse_data]
    scores = [d[2] for d in clickhouse_data]

    results = []
    for template, metric, score in zip(templates, metrics, scores, strict=False):
        results.append(
            {
                "template": template,
                "metric": metric,
                "score": (score / 10) * 100.0,
            }
        )

    return results


def get_template_explore(optimization_id):
    client = ClickHouseClientSingleton()

    filter_query = """
                    SELECT
                        node_id,
                        metric_id,
                        groupArray(res_type) as ans_type,
                        groupArray(score) as scores
                        FROM
                            (
                                SELECT
                                node_id,
                                metric_id,
                                res_type,
                                AVG(score) as score
                                from
                                (
                                    SELECT
                                    node_id,
                                    template_num,
                                    metric_id,
                                    score,
                                    IF(toInt64(template_num)>0,'Final','Original') as res_type
                                    FROM
                                    (
                                        SELECT
                                        node_id,
                                        splitByString('_',evalKey)[-2] as template_num,
                                        splitByString('_',evalKey)[-3] as metric_id,
                                        toFloat32(evalValue) as score
                                        FROM
                                        (
                                            SELECT
                                            arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS node_id,
                                            er.Key AS evalKey,
                                            er.Value AS evalValue

                                            FROM events
                                            ARRAY JOIN EvalResults as er
                                            WHERE has(Features.Key, 'node_id')
                                            AND deleted=0
                                        )
                                    WHERE
                                    evalKey LIKE %(optimization_pattern)s
                                    )
                                )
                                GROUP BY
                                1,2,3
                            )
                            GROUP BY
                            1,2
                    """

    clickhouse_data = client.execute_read(
        filter_query, {"optimization_pattern": f"%{optimization_id}%"}
    )
    node_ids = [d[0] for d in clickhouse_data]
    metrics = [d[1] for d in clickhouse_data]
    ans_type_arr = [d[2] for d in clickhouse_data]
    scores = [d[3] for d in clickhouse_data]

    parent_nodes = Node.objects.filter(id__in=node_ids)
    child_nodes = []
    for parent_node in parent_nodes:
        child_nodes_temp = parent_node.child_nodes.all()
        if not child_nodes_temp:
            child_nodes.append(None)
            continue
        child_node = child_nodes_temp[len(child_nodes_temp) - 1]
        child_nodes.append(child_node)

    results = []
    for parent, child, metric, ans, score in zip(
        parent_nodes, child_nodes, metrics, ans_type_arr, scores, strict=False
    ):
        parent_info = NodeSerializer(parent).data
        child_info = NodeSerializer(child).data
        results.append(
            {
                "input": " ".join(parent_info["message"]["content"]["parts"]),
                "output": " ".join(child_info["message"]["content"]["parts"]),
                "metric": metric,
                "original_score": score[0] if ans[0] == "Original" else score[1],
                "final_score": score[0] if ans[0] == "Final" else score[1],
            }
        )

    return results
