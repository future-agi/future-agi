import json
import time

import structlog

from ee import _ee_stub

try:
    from ee.agenthub.rag_rank_eval_agent.rag_rank_evaluation_agent_v2 import (
        RagRankEval,
    )
    from ee.agenthub.text_eval_agent.eval_text_llm import EvalTextLLM
except ImportError:
    RagRankEval = _ee_stub("RagRankEval")
    EvalTextLLM = _ee_stub("EvalTextLLM")
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.functions import (
    get_criteria_judegement_score_ragrank,
    get_summary_judgement_ragrank_v2,
)
from agentic_eval.core_evals.fi_evals.eval_type import FutureAgiEvalTypeId
from agentic_eval.core_evals.fi_utils.evals_result import EvalResult
from agentic_eval.core_evals.fi_utils.fi_model import Model

logger = structlog.get_logger(__name__)

# from ...core.utils.types import MetricTypes


class RankingEvaluator(LLM):
    """Evaluates and ranks contexts based on given criteria."""

    def __init__(
        self,
        model: str = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        api_key: str = "api_key",
        **kwargs,
    ):
        # Resolve model config with fallback to CLAUDE_4_5_SONNET_BEDROCK_ARN
        requested_config = ModelConfigs.get_config(model)
        if requested_config is None:
            if model is not None:
                logger.warning(
                    "Model not found in ModelConfigs, using CLAUDE_4_5_SONNET_BEDROCK_ARN fallback",
                    model=model,
                )
            self.model_config = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        else:
            self.model_config = requested_config

        super().__init__(
            model_name=self.model_config.model_name,
            provider=self.model_config.provider,
            temperature=self.model_config.temperature,
            max_tokens=self.model_config.max_tokens,
            api_key=api_key,
        )
        self.kwargs = kwargs
    @property
    def name(self):
        return FutureAgiEvalTypeId.RANKING_EVAL.value

    @property
    def display_name(self):
        return "Context Ranking Evaluation"

    @property
    def default_model(self):
        return Model.GPT4.value

    @property
    def required_args(self):
        return ["input", "context", "criteria"]

    
    def _strip_outer_quotes(self,text: str) -> str:
        # Define all characters you'd consider as "quotes" for outer removal
        possible_quotes = ['"', "'", '""', '""']

        # Keep peeling off the outer quotes if they match
        while len(text) >= 2 and (text[0] in possible_quotes) and (text[-1] in possible_quotes):
            text = text[1:-1].strip()

        return text

    def _format_chat_history(self, **kwargs) -> list[dict]:
        """Format input and context into chat history format."""
        try:
            # Handle different context formats
            try:
                kwargs["context"] = json.loads(kwargs["context"])
            except:
                pass
            if isinstance(kwargs["context"], str):
                context_str = kwargs["context"].strip()

                # Remove outer quotes (including curly quotes) if present
                context_str = self._strip_outer_quotes(context_str)


                # Normalize any remaining single quotes or curly quotes to double quotes
                context_str = context_str.replace("'", '"')
                context_str = context_str.replace('"', '"').replace('"', '"')


                try:
                    # Try parsing as JSON first
                    parsed_context = json.loads(context_str)
                    if isinstance(parsed_context, list):
                        contexts = [str(c).strip() for c in parsed_context]
                    elif isinstance(parsed_context, dict):
                        contexts = [str(v).strip() for v in parsed_context.values()]
                    else:
                        raise ValueError("Context must be either a list or dictionary format string")
                except json.JSONDecodeError:
                    # If JSON parsing fails, try manual parsing for common formats
                    if context_str.startswith("[") and context_str.endswith("]"):
                        # Handle list-like string with various quote combinations
                        content = context_str[1:-1].strip()
                        if content:
                            # Split by comma and handle both quoted and unquoted values
                            contexts = []
                            current_item = ""
                            in_quotes = False
                            quote_char = None
                            bracket_count = 0

                            for char in content:
                                if char in ['"', "'"]:
                                    if not in_quotes:
                                        in_quotes = True
                                        quote_char = char
                                    elif char == quote_char:
                                        in_quotes = False
                                        quote_char = None
                                    else:
                                        current_item += char
                                elif char == ',' and not in_quotes and bracket_count == 0:
                                    contexts.append(current_item.strip().strip('"\'\\'))
                                    current_item = ""
                                else:
                                    current_item += char

                            if current_item:
                                contexts.append(current_item.strip().strip('"\'\\'))

                            # Clean up the contexts
                            contexts = [c.strip() for c in contexts if c.strip()]
                    elif context_str.startswith("{") and context_str.endswith("}"):
                        # Handle dict-like string with various quote combinations
                        content = context_str[1:-1].strip()
                        if content:
                            # Split by comma and extract values (ignoring keys)
                            pairs = content.split(",")
                            contexts = []
                            for pair in pairs:
                                if ":" in pair:
                                    value = pair.split(":", 1)[1].strip().strip('"\'\\')
                                    contexts.append(value)
                    else:
                        raise ValueError("Invalid context format. Must be either a list format string like \"['context1', 'context2']\" or a dictionary format string like \"{\'context1\': 'text1', \'context2\': 'text2'}\"")
            else:
                # Handle direct list/dict input
                if isinstance(kwargs["context"], list):
                    contexts = [str(c).strip() for c in kwargs["context"]]
                elif isinstance(kwargs["context"], dict):
                    contexts = [str(v).strip() for v in kwargs["context"].values()]
                else:
                    raise ValueError("Context must be either a list, dictionary, or their string representations")

            # Remove empty strings and ensure we have at least two contexts
            contexts = [c for c in contexts if c.strip()]
            if len(contexts) < 2:
                raise ValueError("At least two contexts are required for ranking evaluation")

            chat_history = [
                {
                    "role": "user",
                    "content": kwargs["input"],
                    "context": contexts,
                    "original_context": contexts
                },
                {
                    "role": "assistant",
                    "content": kwargs.get("output")
                }
            ]
            return chat_history
        except KeyError:
            raise ValueError("Missing required 'context' in kwargs")

    def _evaluate(self, **kwargs) -> EvalResult:
        """
        Run the LLM evaluator.
        """
        start_time = time.time()
        # Validate that correct args were passed
        self.validate_args(**kwargs)
        
        chat_history = self._format_chat_history(**kwargs)
        fewshots=kwargs.get("few_shots")

        agent_task1 = EvalTextLLM(llm=self,check_internet=self.kwargs.get("check_internet",False),fewshots=fewshots)
        

        dataset = agent_task1.format_data_for_rag_rank(chat_history)
        
        if len(dataset[0]["context"]) == 1:
            raise ValueError("At least two contexts are required for ranking evaluation. Only one context was provided. Kindly ensure that the context is properly formatted. For CSV inputs : 'context1'; 'context2' ; ... . For Json inputs, context should be a list of strings.")

        # criteria_breakdown = eval_instruction_process_data_format(criteria_breakdown)


        agent_task2 = RagRankEval(dataset,"criteria_breakdown",llm=agent_task1.llm)
        results = agent_task2.process_data()

        
        total_score, judgments = get_criteria_judegement_score_ragrank(results)
        summary_judgement = get_summary_judgement_ragrank_v2(self, judgments[0].get("judgment"), question=results[0]["question"], context=results[0]["context"], subqueries = results[0]["subqueries"] )

        formatted_summary = []
        if summary_judgement:
            # Extract lines that contain "Document" and scores
            for line in summary_judgement.split('\n'):
                if 'Document' in line and 'Final score:' in line:
                    # Extract document number and score
                    doc_num = line.split('Document')[1].split('(')[0].strip()
                    score = line.split('Final score:')[1].strip(')')
                    formatted_summary.append(f"Doc {doc_num}: {score}")

        formatted_summary = '\n'.join(formatted_summary) if formatted_summary else "No rankings available"
       

        if judgments[0]["judgment"] == "Error":
            # raise ValueError("The context may not be formatted correctly.")

            summary_judgement = "A valid judgment could not be produced. The context may be too irrelevant or improperly formatted. It must be either a list-format string like ['context1', 'context2'] or a dictionary-format string like {'context1': 'text1', 'context2': 'text2'}. Also, ensure that the input can be successfully parsed as JSON—JSON conversion might be failing of context."
            total_score = 0.0


        score_results = {
            "score": total_score,
            "summary_judgement": summary_judgement,
        }


        end_time = time.time()
        eval_runtime_ms = int((end_time - start_time) * 1000)
        metadata = json.dumps({
                "usage": {
                    "completion_tokens": self.token_usage["completion_tokens"],
                    "prompt_tokens": self.token_usage["prompt_tokens"],
                    "total_tokens": self.token_usage["total_tokens"],
                },
                "cost": {
                    "total_cost": self.cost["total_cost"],
                    "prompt_cost": self.cost["prompt_cost"],
                    "completion_cost": self.cost["completion_cost"],
                },
                "response_time": eval_runtime_ms,
            })


        llm_eval_result: EvalResult = {
            "name": self.name,
            "display_name": self.display_name,
            "data": kwargs,
            "failure": False,
            # reason=formatted_reason,
            "reason": score_results.get("summary_judgement") or "",
            "metadata": metadata,
            "runtime": eval_runtime_ms,
            "model": self.model_name,
            "metrics": [{
                "id": self.__class__.__name__,
                "value": score_results.get('score', 0.0)
            }],
            "datapoint_field_annotations": None,
        }
        return {k: v for k, v in llm_eval_result.items() if v is not None}