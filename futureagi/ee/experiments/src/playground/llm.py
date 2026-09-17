import json

# load_dotenv()
import os

from dsp import LM
from openai import OpenAI


class QualitativeEvaluator:
    def __init__(
        self,
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model_name="anthropic/claude-3-opus",
    ):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.model_name = model_name

    def get_qualitative_eval_parameter_prompt(self, criteria):
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": f"""You are evaluating a person who answers queries of a customer using some context. You will be given the exact criteria based on which you need to evaluate the person. You will expand the criteria into 5 non-overlapping sub-criterias, which can be used to evaluate the person. The sub-criterias will have the context, the question and the answer of the person. You will return the response in a RFC8259 compliant JSON response with the keys as the serial numbers starting from 1 and the values as the sub-criterias. You will return nothing but the JSON response.
                Criteria: {criteria}""",
                },
            ],
            response_format={"type": "json_object"},
        )
        return completion.choices[0].message.content

    def summarize_eval_report(self, evaluation_report):
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": f"""You will be given a report which judges if the required knowledge is present in a particular context or not. You need to read the evaluation report and summarize into "very good", "good", "bad" and very bad. You will return the response in a RFC8259 compliant JSON response with a single key called "summary" and the value as the summary of the evaluation report. You will return nothing but the JSON response.
                    evaluation_report: {evaluation_report}""",
                },
            ],
            response_format={"type": "json_object"},
        )
        return completion.choices[0].message.content

    def get_score(self, evaluation_report):
        summary_report = self.summarize_eval_report(evaluation_report)
        summary = json.loads(summary_report)["summary"]
        if summary.lower() == "very good":
            return 2
        if summary.lower() == "good":
            return 1
        if summary.lower() == "bad":
            return -1
        if summary.lower() == "very bad":
            return -2


class OpenRouterLLM(LM):
    def __init__(self, model_name: str = "anthropic/claude-3-opus", **kwargs):
        self.model_name = model_name
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.kwargs = kwargs
        self.max_tokens = kwargs.get("max_tokens", 8196)
        self.temperature = kwargs.get("temperature", 0.5)
        # print(f"Max tokens: {self.max_tokens}")
        self.history = []
        self.provider = "openrouter"

    def basic_request(self, prompt: str, **kwargs):
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        response = completion.choices[0].message.content
        self.history.append(
            {
                "prompt": prompt,
                "response": response,
                "kwargs": kwargs,
            }
        )
        return response

    def __call__(self, prompt, only_completed=True, return_sorted=False, **kwargs):
        response = self.basic_request(prompt, **kwargs)
        return [response]
