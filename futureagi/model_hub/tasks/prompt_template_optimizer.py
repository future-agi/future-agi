import copy
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
from model_hub.models.ai_model import AIModel
from model_hub.models.conversations import Node
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.serializers.conversation import NodeSerializer
from model_hub.serializers.metric import MetricSerializer, MetricSerializerWithAIModel
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
            for dataset in datasets:
                dataset_filter_query.append(
                    f"""
                    ( Environment = {AIModel.EnvTypes.get_env_num_types(dataset["environment"])}
                    AND ModelVersion = '{dataset["model_version"]}'
                    AND EventDate>='{start_date}'
                    AND EventDate<='{end_date}')
                """
                )

            dataset_filter_query = " OR ".join(dataset_filter_query)

            dataset_filter_query = f""" AND ( {dataset_filter_query} )"""

            if metric.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
                filter_query = f"""
                    SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue, original_uuid
                    FROM events
                    WHERE AIModel = '{metric.model.id}'
                    {dataset_filter_query}
                    AND (
                        NOT has(EvalResults.Key, 'optimized_{optimization_obj.id}_{metric.id}_status')
                        OR (
                            has(EvalResults.Key, 'optimized_{optimization_obj.id}_{metric.id}_status')
                            AND arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_obj.id}_{metric.id}_status')) = 'not_processed'
                        )
                    )
                    AND has(Features.Key, 'node_id')
                    AND deleted=0
                    ORDER BY EventDateTime ASC
                    LIMIT 10

                """

                # print(filter_query)

                clickhouse_data = client.execute_read(filter_query)

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
    criteria_breakdown = (
        inference_copy["metric"]["criteria_breakdown"]
        if len(inference_copy["metric"]["criteria_breakdown"]) > 0
        else create_criteria_text_prompt(eval_instructions)
    )

    chat_history = process_chat_history(inference_copy["conversation"])
    optim_obj = OptimizeDataset.objects.filter(id=inference_copy["optim_obj_id"]).get()
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
    client = ClickHouseClientSingleton()
    dataset_filter_query = []
    dataset_filter_query.append(
        f"""
                    ( Environment = {environment}
                    AND ModelVersion = '{version}'
                    AND EventDate>='{start_date}'
                    AND EventDate<='{end_date}')

                """
    )

    dataset_filter_query = " OR ".join(dataset_filter_query)

    dataset_filter_query = f""" AND ( {dataset_filter_query} )"""

    filter_query = f"""
                    SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue,
                    Tags.Key AS TagKeys,
                    Tags.Value AS TagValues
                    FROM events
                    WHERE AIModel = '{model_name}'
                    {dataset_filter_query}
                    AND has(Features.Key, 'node_id')
                    AND deleted=0
                    ORDER BY EventDateTime ASC

                """
    data_to_process = []
    clickhouse_data = client.execute_read(filter_query)

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

    runner_topk_optimizer(runner_data, eval_prompts, optim_obj_id)


def runner_topk_optimizer(data, eval_prompts, optim_obj_id):
    df_temp = pd.DataFrame(data)
    optimize_dataset = OptimizeDataset.objects.filter(id=optim_obj_id).get()
    if len(optimize_dataset.criteria_breakdown) != 0:
        criteria_breakdown = optimize_dataset.criteria_breakdown
    else:
        metrics_list = list(optimize_dataset.metrics.all())
        input_eval_prompts = [str(metric.text_prompt) for metric in metrics_list]
        metric = "\n".join(
            [f"{i}. {criteria}" for i, criteria in enumerate(input_eval_prompts)]
        )
        criteria_breakdown = create_criteria_text_prompt(metric)

        optimize_dataset.criteria_breakdown = criteria_breakdown
        optimize_dataset.save()
    optim_obj = OptimizeDataset.objects.filter(id=optim_obj_id).get()
    # if optim_obj.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE:
    prompt_optim_agent = PromptOptimizer(
        prompt=eval_prompts,
        train_data=df_temp,
        criteria_breakdown=criteria_breakdown,
        optimize_rag=False,
    )
    top_k_optimized_prompts = prompt_optim_agent.get_optimized_prompt()

    optimized_prompts = [
        instruction_template["instruction_template"]
        for instruction_template in top_k_optimized_prompts
    ]
    optim_obj.optimized_k_prompts = optimized_prompts
    optim_obj.status = OptimizeDataset.StatusType.COMPLETED
    optim_obj.save()


def get_template_results(optimization_id):
    client = ClickHouseClientSingleton()

    filter_query = f"""
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
                        evalKey LIKE '%{optimization_id}%'
                        GROUP BY
                        1,2
                        """

    clickhouse_data = client.execute_read(filter_query)
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

    filter_query = f"""
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
                                    evalKey LIKE '%{optimization_id}%'
                                    )
                                )
                                GROUP BY
                                1,2,3
                            )
                            GROUP BY
                            1,2
                    """

    clickhouse_data = client.execute_read(filter_query)
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
