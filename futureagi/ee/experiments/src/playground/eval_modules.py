import logging

import dspy


class ScorerMidwayChat(dspy.Signature):
    """given a judging criteria, a query (with the associated chat history) and a response, tell how well the response is to the query  with reasoning."""

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
        judgment = self.scorer(
            judging_criteria=judging_criteria, query=query, response=response
        )
        return judgment


class ScorerConcludedChat(dspy.Signature):
    """given a judging criteria and a chat history with a user and a model, evaluate how well the model is throughout the chat in the judging criteria. Give your answers with reasoning."""

    judging_criteria = dspy.InputField(desc="the judging criteria")
    chat_history = dspy.InputField(desc="the chat history of the user with the model")
    judgment = dspy.OutputField(
        desc="judgment of how good the model was throughout the chat with respect to the judging criteria."
    )


class ScorerConcludedChatModule(dspy.Module):
    def __init__(self):
        self.scorer = dspy.ChainOfThought(ScorerConcludedChat)

    def forward(self, judging_criteria, chat_history):
        judgment = self.scorer(
            judging_criteria=judging_criteria, chat_history=chat_history
        )
        return judgment


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
        logging.info(f"Evaluation Judgement: {prediction.evaluation_judgement}")
        return prediction.evaluation_judgement


class CheckEvaluationCriteria(dspy.Signature):
    """Check how the the answer fares given the context and the question, and the evaluation instruction"""

    context = dspy.InputField(desc="may contain relevant facts")
    question = dspy.InputField(desc="question asked by the user")
    evaluation_instruction = dspy.InputField(
        desc="Evaluation on the answer based on the context and the question"
    )
    answer = dspy.InputField(desc="answer to the question asked by the user")
    evaluation_judgement = dspy.OutputField(
        desc="describe if the context has the knowledge required to answer the question"
    )
