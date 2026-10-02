import os

import dspy
from dsp import LM
from openai import OpenAI

# load_dotenv()


class OpenRouter(LM):
    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.kwargs = kwargs
        self.max_tokens = kwargs.get("max_tokens", 8196)
        self.temperature = kwargs.get("temperature", 0.5)
        self.history = []
        self.provider = "openrouter"

    def basic_request(self, prompt: str, **kwargs):
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        response = completion.choices[0].message.content

        self.history.append({"prompt": prompt, "response": response, "kwargs": kwargs})
        return response

    def __call__(self, prompt, **kwargs):
        return [self.basic_request(prompt, **kwargs)]


class ScorerMidwayChat(dspy.Signature):
    judging_criteria = dspy.InputField(desc="the judging criteria")
    query = dspy.InputField(
        desc="the query of the user and the associated chat history"
    )
    response = dspy.InputField(desc="the response by a model")
    judgment = dspy.OutputField(
        desc="judgment of how good the response is to the query with respect to the judging criteria."
    )


class ScorerMidwayChatModule(dspy.Module):
    def __init__(self):
        self.scorer = dspy.ChainOfThought(ScorerMidwayChat)

    def forward(self, judging_criteria, query, response):
        return self.scorer(
            judging_criteria=judging_criteria, query=query, response=response
        ).judgment


class ScorerConcludedChat(dspy.Signature):
    judging_criteria = dspy.InputField(desc="the judging criteria")
    chat_history = dspy.InputField(desc="the chat history of the user with the model")
    judgment = dspy.OutputField(
        desc="judgment of how good the model was throughout the chat with respect to the judging criteria."
    )


class ScorerConcludedChatModule(dspy.Module):
    def __init__(self):
        self.scorer = dspy.ChainOfThought(ScorerConcludedChat)

    def forward(self, judging_criteria, chat_history):
        return self.scorer(
            judging_criteria=judging_criteria, chat_history=chat_history
        ).judgment


class CheckEvaluationCriteria(dspy.Signature):
    context = dspy.InputField(desc="may contain relevant facts")
    question = dspy.InputField(desc="question asked by the user")
    evaluation_instruction = dspy.InputField(
        desc="Evaluation on the answer based on the context and the question"
    )
    answer = dspy.InputField(desc="answer to the question asked by the user")
    evaluation_judgement = dspy.OutputField(
        desc="describe if the context has the knowledge required to answer the question"
    )


class RAGCheck(dspy.Module):
    def __init__(self, reasoning_strategy="chain_of_thought", **kwargs):
        super().__init__()
        if reasoning_strategy == "chain_of_thought":
            self.evaluation_criteria_module = dspy.ChainOfThought(
                CheckEvaluationCriteria, n=kwargs.get("n", 1)
            )
        else:
            raise ValueError("Invalid reasoning strategy")

    def forward(self, context, question, answer, evaluation_instruction):
        prediction = self.evaluation_criteria_module(
            context=context,
            question=question,
            answer=answer,
            evaluation_instruction=evaluation_instruction,
        )
        return prediction.evaluation_judgement
