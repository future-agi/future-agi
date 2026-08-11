import base64
from concurrent.futures import ThreadPoolExecutor
import mimetypes

from jinja2 import Template

from ee.agenthub.rag_eval_agent.rag_evaluation_agent import DataProcessor
from ee.agenthub.rag_rank_eval_agent.rag_rank_evaluation_agent_v2 import (
    RagRankEval,
)
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)

EVAL_TEXT_THREAD_WORKER_COUNT = 5

from agentic_eval.core.utils.message_generator import prompt_message_generator
from tfc.utils.storage import (
    get_file_from_s3,
)
from ee.agenthub.text_eval_agent.prompts import (
    evaluation_instructions_generator_prompt,
    generic_text_template_cot_response_audio_prompt,
    generic_text_template_cot_response_prompt,
    generic_text_template_plan_prompt,
    planning_instructions_generator_prompt,
)
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import (
    eval_instruction_process_data_format,
    expand_eval_instructions,
    get_criteria_judegement_score,
    get_criteria_judegement_score_ragrank,
    get_summary_judgement_ragrank_v2,
    normalize_val,
)
from agentic_eval.core.utils.types import MetricTypes


class EvalTextLLM:
    def __init__(
        self,
        model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
        max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
        provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
        llm=None,
        check_internet=False,
        fewshots=None,
        is_audio=False,
        knowledge_base_id=None,
    ):
        if fewshots is None:
            fewshots = []
        if llm:
            self.llm = llm
        else:
            self.llm = LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )
        # Add audio model
        self.audio_provider = ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider
        self.audio_model = ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name
        self.fewshots = fewshots
        self.check_internet = False
        self.is_audio = is_audio
        self.knowledge_base_id = knowledge_base_id
        if self.check_internet:
            self.online_llm = LLM(
                model_name=ModelConfigs.INTERNET_SEARCH.model_name,
                temperature=ModelConfigs.INTERNET_SEARCH.temperature,
                max_tokens=ModelConfigs.INTERNET_SEARCH.max_tokens,
                provider=ModelConfigs.INTERNET_SEARCH.provider,
            )

    def expand_eval_instruction(self, eval_instructions):
        return expand_eval_instructions(self.llm, eval_instructions)

    def format_conversation(self, conversation):
        # Identify the last 'user' message as input
        input_msg = next(
            (msg["content"] for msg in reversed(conversation) if msg["role"] == "user"),
            None,
        )
        # Identify the last 'assistant' message as output
        output_msg = next(
            (
                msg["content"]
                for msg in reversed(conversation)
                if msg["role"] == "assistant"
            ),
            None,
        )

        # Extract all messages except the last 'user' and 'assistant' as past_conversation
        past_conversation = []
        user_found = assistant_found = False

        for msg in reversed(conversation):
            if msg["role"] == "user" and not user_found:
                user_found = True
                continue
            elif msg["role"] == "assistant" and not assistant_found:
                assistant_found = True
                continue
            past_conversation.append(msg)

        past_conversation.reverse()  # Reverse to maintain the original order

        # Format past_conversation
        formatted_past_conversation = [
            f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
            for msg in past_conversation
        ]

        # Extract context if available
        context = next(
            (msg.get("context") for msg in conversation if "context" in msg),
            None,
        )

        return {
            "past_conversation": "\n".join(formatted_past_conversation),
            "input": input_msg,
            "output": output_msg,
            "context": context,
        }

    def solve_template_problem_with_planning(
        self, eval_instruction, chat_history, fewshots
    ):
        # Check if input is audio
        if self.is_audio:
            return self._handle_audio_evaluation(
                eval_instruction, chat_history, fewshots
            )

        # Original text evaluation logic
        planning_instructions = Template(planning_instructions_generator_prompt).render(
            eval_instruction=eval_instruction
        )

        messages = prompt_message_generator(planning_instructions)
        planning_instructions = self.llm._get_completion_content(messages=messages)

        self.generate_template_plan(
            eval_instruction, chat_history, planning_instructions
        )

        solution = self.generate_cot_response(
            eval_instruction, chat_history, planning_instructions, fewshots
        )

        return solution

    def generate_cot_response(
        self, eval_instruction, chat_history, planning_instructions, fewshots
    ):
        # Define the parameters
        evaluation_instructions = Template(
            evaluation_instructions_generator_prompt
        ).render(
            eval_instruction=eval_instruction,
            planning_instructions=planning_instructions,
        )
        messages = prompt_message_generator(evaluation_instructions)
        evaluation_instructions = self.llm._get_completion_content(messages=messages)
        params = {
            "eval_instruction": eval_instruction,
            "output": chat_history.get("output"),
            "evaluation_instructions": evaluation_instructions,
        }

        # Add optional parameters if they exist
        if chat_history.get("input"):
            params["input"] = chat_history.get("input")

        if chat_history.get("context"):
            params["context"] = chat_history.get("context")

        if fewshots:
            if all(item["type"] == "text" for item in fewshots):
                # Combine all "text" values into a single string
                fewshot_text = "".join(item["text"] for item in fewshots)
            else:
                fewshot_text = []
            params["fewshots"] = fewshot_text

        template = Template(generic_text_template_cot_response_prompt)
        # Jinja2 handles all variable substitution - no need for .format()
        prompt = template.render(**params)
        if self.knowledge_base_id:
            try:
                extra_knowledge = []
                urls = get_file_from_s3(str(self.knowledge_base_id))
                if urls:
                    extra_knowledge.append(
                        {
                            "type": "text",
                            "text": f"Additional documents supplied as Knowledge Base by the user: ",
                        }
                    )
                for url in urls:
                    # Get the file format from the URL
                    file_format = (
                        mimetypes.guess_type(url)[0] or "application/octet-stream"
                    )
                    google_file = {
                        "type": "file",
                        "file": {"file_id": url, "format": file_format},
                    }
                    extra_knowledge.append(google_file)

            except Exception as e:
                logger.warning(
                    f"Failed to load knowledge base files from S3, attempting KB indexer fallback: {str(e)}"
                )
                from model_hub.utils.kb_indexer import KBIndexer

                indexer = KBIndexer()
                extra_knowledge = []
                text_inputs = []
                # Get chunk_text for each variable in params
                for key, value in params.items():
                    if key not in ["eval_instruction", "evaluation_instructions"]:
                        if isinstance(value, str):
                            # Only process string values
                            text_inputs.append(value)
                try:
                    metadata = indexer.get_data_subset_kb_id(
                        text_inputs, self.knowledge_base_id
                    )
                    if metadata:
                        # Handle both single metadata item and list of metadata items
                        if isinstance(metadata, list):
                            chunk_texts = [
                                item.get("chunk_text", "")
                                for item in metadata
                                if item.get("chunk_text")
                            ]
                            chunk_text = "\n".join(chunk_texts)
                        else:
                            chunk_text = metadata.get("chunk_text", "")
                        if chunk_text:
                            extra_knowledge.append(
                                f"Additional context or background information related to the above Elements for Review with similar context: {chunk_text}"
                            )
                except Exception as e:
                    logger.exception(
                        f"Error getting knowledge for the above input: {str(e)}"
                    )

            if extra_knowledge:
                prompt = prompt + "\n\n" + "\n".join(extra_knowledge)

        messages = prompt_message_generator(prompt)

        if self.check_internet:
            return self.online_llm._get_completion_content(messages=messages)
        else:
            return self.llm._get_completion_content(messages=messages)

    def generate_template_plan(
        self, eval_instruction, chat_history, planning_instructions
    ):
        # Define the parameters
        params = {
            "eval_instruction": eval_instruction,
            "output": chat_history.get("output"),
            "planning_instructions": planning_instructions,
        }

        # Add optional parameters if they exist
        if chat_history.get("input"):
            params["input"] = chat_history.get("input")

        if chat_history.get("context"):
            params["context"] = chat_history.get("context")

        # Use Jinja2 to render the template
        template = Template(generic_text_template_plan_prompt)
        # Jinja2 handles all variable substitution - no need for .format()
        prompt = template.render(**params)

        messages = prompt_message_generator(prompt)

        return self.llm._get_completion_content(messages=messages)

    def _handle_audio_evaluation(self, eval_instruction, chat_history, fewshots):
        input_audio_data = chat_history["input_audio_data"]
        output_audio_data = chat_history["output_audio_data"]
        input_content = chat_history["input_content"]
        output_content = chat_history["output_content"]

        if input_audio_data and not input_audio_data.startswith("data:audio/"):
            # If it's bytes, encode it
            if isinstance(input_audio_data, bytes):
                input_audio_data = f"data:audio/mp3;base64,{base64.b64encode(input_audio_data).decode('utf-8')}"
            # If it's a base64 string without data URL prefix
            elif isinstance(input_audio_data, str) and not input_audio_data.startswith(
                "data:"
            ):
                input_audio_data = f"data:audio/mp3;base64,{input_audio_data}"

        if output_audio_data and not output_audio_data.startswith("data:audio/"):
            # If it's bytes, encode it
            if isinstance(output_audio_data, bytes):
                output_audio_data = f"data:audio/mp3;base64,{base64.b64encode(output_audio_data).decode('utf-8')}"
            # If it's a base64 string without data URL prefix
            elif isinstance(
                output_audio_data, str
            ) and not output_audio_data.startswith("data:"):
                output_audio_data = f"data:audio/mp3;base64,{output_audio_data}"

        # Generate planning instructions
        self.llm.provider = "aws_bedrock_anthropic"
        planning_instructions = Template(planning_instructions_generator_prompt).render(
            eval_instruction=eval_instruction
        )
        messages = prompt_message_generator(planning_instructions)
        planning_instructions = self.llm._get_completion_content(messages=messages)

        # Format content for Gemini model with planning instructions
        content_obj = [{"type": "text", "text": planning_instructions}]
        if input_audio_data:
            content_obj.append({"type": "text", "text": "below is the input audio"})
            content_obj.append(
                {"type": "image_url", "image_url": {"url": input_content}}
            )
        elif input_content:
            content_obj.append({"type": "text", "text": input_content})

        if output_audio_data:
            content_obj.append({"type": "text", "text": "below is the output audio"})
            content_obj.append(
                {"type": "image_url", "image_url": {"url": output_content}}
            )
        elif output_content:
            content_obj.append({"type": "text", "text": output_content})
        content = content_obj

        # Get evaluation from Gemini
        evaluation_instructions = Template(
            evaluation_instructions_generator_prompt
        ).render(
            eval_instruction=eval_instruction,
            planning_instructions=planning_instructions,
        )
        planning_instructions_messages = prompt_message_generator(
            evaluation_instructions
        )
        planning_instructions = self.llm._get_completion_content(
            messages=planning_instructions_messages
        )

        self.llm.provider = self.audio_provider
        self.llm.model_name = self.audio_model

        content.append(
            {
                "type": "text",
                "text": Template(
                    generic_text_template_cot_response_audio_prompt
                ).render(
                    eval_instruction=eval_instruction,
                    evaluation_instructions=planning_instructions,
                ),
            }
        )
        messages = [{"role": "user", "content": content}]
        response = self.llm._get_completion_content(
            messages=messages, model=self.audio_model
        )

        try:
            result = extract_dict_from_string(response)
            return {
                "score": float(result.get("score", 0.0)),
                "explanation": result.get("explanation", response),
            }
        except Exception as e:
            logger.error(f"Error parsing audio evaluation response: {e}")
            return {
                "score": 0.0,
                "explanation": "Failed to parse audio evaluation response",
            }

    def _get_score_conversation_parallel(
        self, expanded_eval_instructions, chat_history, fewshots
    ):
        if not self.is_audio:
            chat_history = self.format_conversation(chat_history)

            expanded_eval_instructions = eval_instruction_process_data_format(
                expanded_eval_instructions
            )

        def process_instruction(eval_instruction):
            def get_judgment():
                if type(eval_instruction) == list:
                    eval_instruction_to_use = eval_instruction[0]
                else:
                    eval_instruction_to_use = eval_instruction

                judgment = self.solve_template_problem_with_planning(
                    eval_instruction_to_use, chat_history, fewshots
                )

                if isinstance(judgment, dict):
                    return judgment
                else:
                    return extract_dict_from_string(judgment)

            # First attempt
            judgment = get_judgment()
            # Retry up to 2 more times if not a valid dict
            attempts = 0
            max_attempts = 2
            while not isinstance(judgment, dict) and attempts < max_attempts:
                try:
                    judgment = get_judgment()
                except ValueError:
                    attempts += 1

            # Get score and judgment, retry once if missing
            calculate = {
                "score": judgment.get("score"),
                "judgment": judgment.get("explanation"),
            }

            return calculate

        with ThreadPoolExecutor(max_workers=EVAL_TEXT_THREAD_WORKER_COUNT) as executor:
            results = list(
                executor.map(process_instruction, expanded_eval_instructions)
            )

        total_score, judgments = get_criteria_judegement_score(
            expanded_eval_instructions, results
        )

        num_inst = len(expanded_eval_instructions)
        if type(expanded_eval_instructions[0]) == list:
            sum((-2) * r[1] for r in expanded_eval_instructions)
            sum((2) * r[1] for r in expanded_eval_instructions)
        else:
            (-2) * num_inst
            (2) * num_inst

        total_score = normalize_val((0, 10), (0, 1), total_score)
        summary_judgement = judgments[0].get("judgment")

        return {
            "score": total_score,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }

    def get_score_chat(self, criteria_breakdown, chat_history):
        return self._get_score_conversation_parallel(
            criteria_breakdown, chat_history, self.fewshots
        )

    def format_data_for_rag(self, data):
        formatted_data = []
        # Loop through the data and extract question-answer pairs with context
        for i in range(0, len(data), 2):
            if (data[i]["role"] == "user" and data[i + 1]["role"] == "assistant") or (
                data[i]["role"] == "assistant" and data[i + 1]["role"] == "user"
            ):
                if data[i]["role"] == "user":
                    question = data[i]["content"]
                    context = data[i].get("context")
                    answer = data[i + 1]["content"]
                else:
                    question = data[i + 1]["content"]
                    context = data[i + 1].get("context")
                    answer = data[i]["content"]

                data_item = {
                    "question": question,
                    "answer": answer,
                    "context": context,
                }
                formatted_data.append(data_item)
        return formatted_data

    def format_data_for_rag_rank(self, data):
        formatted_data = []
        # Loop through the data and extract question-answer pairs with context
        for i in range(0, len(data), 2):
            if (data[i]["role"] == "user" and data[i + 1]["role"] == "assistant") or (
                data[i]["role"] == "assistant" and data[i + 1]["role"] == "user"
            ):
                if data[i]["role"] == "user":
                    question = data[i]["content"]
                    context = data[i].get("original_context")
                    answer = data[i + 1]["content"]
                else:
                    question = data[i + 1]["content"]
                    context = data[i + 1].get("original_context")
                    answer = data[i]["content"]

                data_item = {
                    "question": question,
                    "answer": answer,
                    "context": context,
                }
                formatted_data.append(data_item)
        return formatted_data

    def get_score_rag(self, criteria_breakdown, chat_history):
        dataset = self.format_data_for_rag(chat_history)
        criteria_breakdown = eval_instruction_process_data_format(criteria_breakdown)

        data_processor = DataProcessor(
            dataset, criteria_breakdown, self.fewshots, llm=self.llm
        )
        results = data_processor.process_data()

        total_score, judgments = get_criteria_judegement_score(
            criteria_breakdown, results
        )

        num_inst = len(criteria_breakdown) * len(dataset)
        if type(criteria_breakdown[0]) == list:
            sum(
                (-2) * instruction_pair[1]
                for data_item in dataset
                for instruction_pair in criteria_breakdown
            )
            sum(
                (2) * instruction_pair[1]
                for data_item in dataset
                for instruction_pair in criteria_breakdown
            )
        else:
            (-2) * num_inst
            (2) * num_inst

        total_score = normalize_val((0, 10), (0, 1), total_score)

        summary_judgement = judgments[0].get("judgment")

        return {
            "score": total_score,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }

    def get_score_rag_rank(self, criteria_breakdown, chat_history):
        dataset = self.format_data_for_rag_rank(chat_history)
        criteria_breakdown = eval_instruction_process_data_format(criteria_breakdown)

        data_processor = RagRankEval(dataset, criteria_breakdown, llm=self.llm)
        results = data_processor.process_data()

        total_score, judgments = get_criteria_judegement_score_ragrank(results)

        summary_judgement = get_summary_judgement_ragrank_v2(
            self.llm,
            judgments[0].get("judgment"),
            question=results[0]["question"],
            context=results[0]["context"],
            subqueries=results[0]["subqueries"],
        )

        return {
            "score": total_score,
            "judgments": results,
            "summary_judgement": summary_judgement,
        }

    def score_chat_history(
        self,
        criteria_breakdown,
        chat_history,
        model_type,
        is_rag,
        eval_rag_output=None,
        eval_rag_context=None,
        eval_rag_context_ranking=None,
    ):
        scores = None
        if not is_rag:
            if model_type == MetricTypes.STEPWISE_MODEL_INFERENCE:
                scores = self.get_score_chat(criteria_breakdown, chat_history)
            else:
                scores = self.get_score_chat(criteria_breakdown, chat_history)
        else:
            if not eval_rag_context_ranking:
                scores = self.get_score_rag(criteria_breakdown, chat_history)
            else:
                scores = self.get_score_rag_rank(criteria_breakdown, chat_history)
        return scores
