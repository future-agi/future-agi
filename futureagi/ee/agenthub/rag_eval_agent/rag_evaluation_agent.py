import logging
from concurrent.futures import ThreadPoolExecutor

from agentic_eval.core.utils.json_utils import extract_dict_from_string
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.message_generator import prompt_message_generator

from ee.agenthub.rag_eval_agent.prompts import (
    rag_template_cot_response_prompt,
    rag_template_plan_prompt,
)
from ee.agenthub.text_eval_agent.prompts import (
    evaluation_instructions_generator_prompt,
    planning_instructions_generator_prompt,
)
from agentic_eval.core.llm.llm import LLM

logging.basicConfig(level=logging.INFO)

RAG_EVAL_THREAD_WORKER_COUNT = 2


class DataProcessor:
    def __init__(self, dataset, criteria_breakdown, fewshots, llm=None):
        self.dataset = dataset
        self.criteria_breakdown = criteria_breakdown
        self.fewshots = fewshots

        if llm:
            self.llm = llm
        else:
            _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
            self.llm = LLM(
                model_name=_cfg.model_name,
                temperature=_cfg.temperature,
                max_tokens=_cfg.max_tokens,
                provider=_cfg.provider,
            )
        self.counter = 0

    def solve_template_problem_with_planning(
        self, eval_instruction, question, answer, context, fewshots
    ):
        planning_instructions = planning_instructions_generator_prompt.format(
            eval_instruction=eval_instruction
        )
        messages = prompt_message_generator(planning_instructions)
        planning_instructions = self.llm._get_completion_content(messages=messages)
        self.generate_template_plan(
            eval_instruction, question, answer, context, planning_instructions
        )
        solution = self.generate_cot_response(
            eval_instruction, question, answer, context, planning_instructions, fewshots
        )

        return solution

    def generate_cot_response(
        self,
        eval_instruction,
        question,
        answer,
        context,
        planning_instructions,
        fewshots,
    ):
        evaluation_instructions = evaluation_instructions_generator_prompt.format(
            eval_instruction=eval_instruction,
            planning_instructions=planning_instructions,
        )
        messages = prompt_message_generator(evaluation_instructions)
        evaluation_instructions = self.llm._get_completion_content(messages=messages)
        fewshot_text = ""
        if fewshots:
            if all(item["type"] == "text" for item in fewshots):
                # Combine all "text" values into a single string
                fewshot_text = "".join(item["text"] for item in fewshots)

        prompt = rag_template_cot_response_prompt.format(
            eval_instruction=eval_instruction,
            question=question,
            answer=answer,
            context=context,
            fewshots=fewshot_text,
            evaluation_instructions=evaluation_instructions,
        )
        messages = prompt_message_generator(prompt)

        return self.llm._get_completion_content(messages=messages)

    def generate_template_plan(
        self, eval_instruction, question, answer, context, planning_instructions
    ):
        prompt = rag_template_plan_prompt.format(
            eval_instruction=eval_instruction,
            question=question,
            answer=answer,
            context=context,
            planning_instructions=planning_instructions,
        )
        messages = prompt_message_generator(prompt)

        return self.llm._get_completion_content(messages=messages)

    def process_instruction(self, data_item, instruction_pair):
        question = data_item["question"]
        answer = data_item["answer"]
        context = data_item["context"]
        fewshots = self.fewshots

        instruction = instruction_pair
        try:
            logging.info(f"Evaluation Instruction {self.counter}: {instruction}")

            evaluation_judgement = self.solve_template_problem_with_planning(
                instruction, question, answer, context, fewshots
            )
            judgment = extract_dict_from_string(evaluation_judgement)

            score = {
                "score": judgment.get("score"),
                "judgment": judgment.get("explanation"),
            }

            logging.info(f"Question: {question}")
            logging.info(f"Answer: {answer}")
            logging.info(f"Evaluation Judgement: {score.get('judgment')}")
            logging.info(f"Score: {score.get('score')}")
            self.counter += 1
            return {
                "instruction_id": self.counter,
                "result": {
                    "question": question,
                    "answer": answer,
                    "context": context,
                    "score": score.get("score"),
                    "judgment": score.get("judgment"),
                    "instruction": instruction,
                },
            }

        except Exception as e:
            self.counter += 1
            logging.error(f"Error while processing question {question}: {e}")
            return {
                "instruction_id": self.counter,
                "result": {
                    "question": question,
                    "answer": answer,
                    "context": context,
                    "score": 0,
                    "judgment": "Error",
                    "instruction": instruction,
                },
            }

    def process_data(self):
        results_final = []

        if type(self.criteria_breakdown[0]) == list:
            task_list = [
                (data_item, instruction_pair[0])
                for data_item in self.dataset
                for instruction_pair in self.criteria_breakdown
            ]
        else:
            task_list = [
                (data_item, instruction_pair)
                for data_item in self.dataset
                for instruction_pair in self.criteria_breakdown
            ]

        with ThreadPoolExecutor(max_workers=RAG_EVAL_THREAD_WORKER_COUNT) as executor:
            results = list(
                executor.map(lambda args: self.process_instruction(*args), task_list)
            )

        # Combining results
        for result in results:
            if result:
                results_final.append(result["result"])
        return results_final
