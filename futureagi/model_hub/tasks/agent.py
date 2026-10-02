import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import structlog
from django.db.models import Case, When

# Activity-aware stub: invocations raise a Temporal non-retryable
# ApplicationError (these tasks run inside Temporal evaluation activities).
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.prompt_template_agent.eval_prompt_template import (
        EvalPromptTemplateLLM,
    )
    from ee.agenthub.tags_generation_agent.tags_v2 import TagsV2
    from ee.agenthub.text_eval_agent.eval_text_llm import EvalTextLLM
except ImportError:
    EvalPromptTemplateLLM = _ee_stub("EvalPromptTemplateLLM")
    TagsV2 = _ee_stub("TagsV2")
    EvalTextLLM = _ee_stub("EvalTextLLM")
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import (
    eval_instruction_process_data_format,
    get_qualitative_eval_parameter_prompt_v2,
)
from agentic_eval.core.utils.model_config import ModelConfigs
from model_hub.models.ai_model import AIModel, CriteriaCache
from model_hub.models.conversations import Conversation, Node
from model_hub.models.metric import Metric, add_unique_tags
from model_hub.serializers.conversation import ConversationSerializer, NodeSerializer
from model_hub.serializers.metric import MetricSerializer
from model_hub.utils.clickhouse import update_eval_record
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity
from tfc.utils.clickhouse import ClickHouseClientSingleton

logger = structlog.get_logger(__name__)

EVAL_PROCESS_WORKER_COUNT = 10

# from ee.agenthub.prompt_template_agent.eval_prompt_template import EvalPromptTemplateLLM


def create_criteria_text_prompt(text_prompt, llm=None):
    # Check if the criteria_breakdown is already cached
    try:
        cached_criteria = CriteriaCache.objects.filter(text_prompt=text_prompt).first()
        return cached_criteria.criteria_breakdown
    except Exception as e:
        logger.error(f"{e} *****e*****")

    _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
    llm = LLM(
        model_name=_cfg.model_name,
        temperature=_cfg.temperature,
        max_tokens=_cfg.max_tokens,
        provider=_cfg.provider,
    )
    try:
        output_eval_prompt = get_qualitative_eval_parameter_prompt_v2(llm, text_prompt)
        output_eval_prompt = json.loads(output_eval_prompt)
        criteria_breakdown = [v for k, v in output_eval_prompt.items()]

        criteria_breakdown = eval_instruction_process_data_format(criteria_breakdown)

        # Cache the result
        CriteriaCache.objects.get_or_create(
            text_prompt=text_prompt, criteria_breakdown=criteria_breakdown
        )

        return criteria_breakdown
    except Exception as e:
        logger.error(f"{e} Criteria breakdown*****e*****")
        raise Exception("Criteria breakdown not making sense.")  # noqa: B904


def create_criteria(metric):
    criteria_breakdown = create_criteria_text_prompt(metric.text_prompt)

    metric.criteria_breakdown = criteria_breakdown
    metric.save()
    return criteria_breakdown


def get_serialized_conversation(id):
    conversation = Conversation.objects.get(id=id)
    serializer = ConversationSerializer(conversation)
    return serializer.data


def get_serialized_node(id):
    node = Node.objects.get(id=id)
    serializer = NodeSerializer(node)
    return serializer.data


@temporal_activity(time_limit=3600, queue="default")
def gather_data_for_eval():
    client = ClickHouseClientSingleton()
    metrics = Metric.objects.filter(model__deleted=False).all()
    data_to_process = []
    max_tasks = 100
    for metric in metrics:
        # metric_id = metric.id
        # model_id = metric.model.id
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
                AND ModelVersion = '{dataset["model_version"]}' )
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
                    NOT has(EvalResults.Key, 'metric_{metric.id}_status')
                    OR (
                        has(EvalResults.Key, 'metric_{metric.id}_status')
                        AND arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric.id}_status')) = 'not_processed'
                    )
                )
                AND has(Features.Key, 'node_id')
                AND deleted=0
                ORDER BY EventDateTime ASC
                LIMIT 10

            """

            clickhouse_data = client.execute_read(filter_query)

            node_ids = [d[0] for d in clickhouse_data]
            uuids = [d[1] for d in clickhouse_data]
            order = Case(*[When(id=id, then=pos) for pos, id in enumerate(node_ids)])
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
                        "model_type": metric.model.model_type,
                        "conversation_id": parent_node.conversation.id,
                        "metric": MetricSerializer(metric).data,
                        "conversation": [NodeSerializer(n).data for n in past_nodes],
                    }
                )

                res = {
                    f"metric_{metric.id}_status": "processing",
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

                if len(data_to_process) > max_tasks:
                    break

    eval.apply_async(args=(data_to_process,))
    # except Exception as e:
    #     # print(str(e), "error in gather_data_for_eval")
    #     data = f"gather_data_for_eval failed : {str(e)}  metric_id : {metric_id}   model_id : {model_id}"
    #     webhook = WebhookClient(SLACK_WEBHOOK_CHANNEL)
    #     webhook.send(text=data)


@temporal_activity(time_limit=3600 * 3, queue="default")
def eval(data_to_process):
    # Wrap functions with OTel context propagation for thread safety
    # This ensures trace context flows from Temporal activity into thread pool workers
    wrapped_process_gen_text = wrap_for_thread(process_gen_text)

    futures = []
    with ThreadPoolExecutor(max_workers=EVAL_PROCESS_WORKER_COUNT) as executor:
        for inference in data_to_process:
            # print(inference["model_type"])

            if inference["model_type"] == AIModel.ModelTypes.GENERATIVE_LLM:
                futures.append(executor.submit(wrapped_process_gen_text, inference))
                # process_gen_text(inference)

    # Wait for all futures to complete
    for future in as_completed(futures):
        try:
            future.result()  # This will raise an exception if the task raised one
        except Exception as e:
            logger.exception(f"An error occurred while processing a task: {str(e)}")


def process_gen_text(inference):
    agent_task = EvalTextLLM()
    tags = TagsV2()
    eval_instructions = inference["metric"]["text_prompt"]
    metric_type = inference["metric"]["metric_type"]
    evaluation_type = inference["metric"]["evaluation_type"]
    eval_rag_context = evaluation_type == Metric.EvalMetricTypes.EVAL_CONTEXT
    eval_rag_output = evaluation_type == Metric.EvalMetricTypes.EVAL_RAG_OUTPUT
    eval_prompt_template = (
        evaluation_type == Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE
    )
    eval_rag_context_ranking = (
        evaluation_type == Metric.EvalMetricTypes.EVAL_CONTEXT_RANKING
    )
    criteria_breakdown = inference["metric"]["criteria_breakdown"]
    chat_history = []
    # print("inference", inference, inference["conversation"][-2]["id"])
    _uuid = inference["_uuid"]

    for conv in inference["conversation"]:
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
            chat_entry["original_context"] = flattened_context
        # Append the entry to chat_history
        chat_history.append(chat_entry)

    if eval_prompt_template:
        agent_task = EvalPromptTemplateLLM()
        score_results = agent_task.score_template_chat(
            criteria_breakdown=criteria_breakdown, chat_history=chat_history
        )
    else:
        score_results = agent_task.score_chat_history(
            criteria_breakdown,
            chat_history,
            metric_type,
            (eval_rag_output or eval_rag_context or eval_rag_context_ranking),
            eval_rag_output,
            eval_rag_context,
            eval_rag_context_ranking,
        )

    metric_properties = {
        "id": inference["metric"]["id"],
        "eval_instructions": eval_instructions,
    }

    generated_tags = tags.generate_tags(
        metric_properties, score_results["summary_judgement"]
    )

    metric_modal = Metric.objects.get(id=inference["metric"]["id"])
    add_unique_tags(metric_modal, generated_tags)

    if metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
        res = {
            f"metric_{inference['metric']['id']}_score": score_results["score"],
            f"metric_{inference['metric']['id']}_explanation": score_results[
                "summary_judgement"
            ],
            f"metric_{inference['metric']['id']}_tags": ";".join(generated_tags),
            f"metric_{inference['metric']['id']}_status": "processed",
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
