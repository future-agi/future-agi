import json
import os
from concurrent.futures import ThreadPoolExecutor

import dspy
from modules import (
    OpenRouter,
    RAGCheck,
    ScorerConcludedChatModule,
    ScorerMidwayChatModule,
)
from openai import OpenAI

# load_dotenv()


class AgentTask:
    def __init__(self, chat_properties, metric_properties):
        self.chat_properties = chat_properties
        self.metric_properties = metric_properties
        self.model_properties = {
            "model_name": "anthropic/claude-3.5-sonnet:beta",
            "temperature": 0.7,
            "max_tokens": 8192,
        }
        self.llm = OpenRouter(
            model_name=self.model_properties["model_name"],
            temperature=self.model_properties["temperature"],
            max_tokens=self.model_properties["max_tokens"],
        )
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        dspy.configure(lm=self.llm)
        if self.chat_properties["chat_type"] == "conversation":
            self.chat_mid_way_scorer = ScorerMidwayChatModule()
            self.chat_concluded_scorer = ScorerConcludedChatModule()
        elif self.chat_properties["chat_type"] == "rag":
            self.rag_scorer = RAGCheck()
        if "expanded_eval_instructions" not in self.metric_properties:
            self.get_qualitative_eval_parameter_prompt()

    def format_conversation(self, conversation):
        formatted_lines = []
        for message in conversation:
            role = "User" if message["role"] == "user" else "Model"
            content = message["content"]
            formatted_lines.append(f"{role}: {content}")
        return "\n".join(formatted_lines)

    def get_qualitative_eval_parameter_prompt(self):
        eval_instructions = self.metric_properties["eval_instructions"]

        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[
                {
                    "role": "user",
                    "content": f"""You will be given some instructions by a person, on how to evaluate a response with respect to a query and the chat history leading up to the query. You will take the initial instructions and convert it to 10 very specific instructions which cover all the major nuances keeping in mind the original instructions. You will then return the expanded instructions. You will return the response in a RFC8259 compliant JSON response with the serial number as keys, starting from 1. There should be nothing in the response apart from the JSON.\n\nInstructions: {eval_instructions}""",
                },
            ],
            response_format={"type": "json_object"},
        )
        self.metric_properties["expanded_eval_instructions"] = json.loads(
            completion.choices[0].message.content
        )

    def calculate_score(self, judgment):
        judgment = judgment.strip()
        completion = self.client.chat.completions.create(
            model="anthropic/claude-3-opus",
            messages=[
                {
                    "role": "user",
                    "content": f"""Given an analysis of a query, response pair, you will return the summarized version of the analysis. You will only return one of the following: 'good', 'very good', 'bad', 'very bad'. You will return the response in a RFC8259 compliant JSON response. There should be nothing in the response apart from the JSON. The JSON will have a single key 'summary' and value as the string.\n\nAnalysis: {judgment}""",
                },
            ],
            response_format={"type": "json_object"},
        )
        grade = json.loads(completion.choices[0].message.content)["summary"]
        score = {"good": 1, "very good": 2, "bad": -1, "very bad": -2}.get(
            grade.lower(), 0
        )
        return {"score": score, "judgment": judgment}

    def get_summary_judgement(self, judgements):
        chat_conversation = ""
        for judgement in judgements:
            chat_conversation += f"Criteria: {judgement['criteria']}\nEvaluation: {judgement['judgment']}\n\n"
        prompt = f"""You will be given a list of criteria and the evaluation for that criteria. You will return the summarized version of the judgements.
        {chat_conversation}"""
        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[{"role": "user", "content": prompt}],
        )
        response = completion.choices[0].message.content
        return response

    def get_score_at_unfinished_conversation(self, fraction_finished):
        chat_history = self.chat_properties["chat_history"]
        expanded_eval_instructions = self.metric_properties[
            "expanded_eval_instructions"
        ]
        clip_index = int(len(chat_history) * fraction_finished) - 1
        if chat_history[clip_index]["role"] == "assistant":
            clip_index += 1
        if clip_index >= len(chat_history):
            clip_index = len(chat_history) - 2
        response = chat_history[clip_index + 1]["content"]
        chat_history = self.format_conversation(chat_history[:clip_index])

        def process_instruction(eval_instruction):
            judgment = self.chat_mid_way_scorer(
                judging_criteria=eval_instruction,
                query=chat_history,
                response=response,
            )
            result = self.calculate_score(judgment)
            return {
                "criteria": eval_instruction,
                "judgment": result["judgment"],
                "score": result["score"],
            }

        with ThreadPoolExecutor() as executor:
            judgments = list(
                executor.map(process_instruction, expanded_eval_instructions.values())
            )

        total_score = sum(judgment["score"] for judgment in judgments)
        summary_judgement = self.get_summary_judgement(judgments)
        return {
            "score": total_score,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }

    def get_score_at_finished_conversation(self):
        chat_history = self.format_conversation(self.chat_properties["chat_history"])
        expanded_eval_instructions = self.metric_properties[
            "expanded_eval_instructions"
        ]

        def process_instruction(eval_instruction):
            judgment = self.chat_concluded_scorer(
                judging_criteria=eval_instruction, chat_history=chat_history
            )
            result = self.calculate_score(judgment)
            return {
                "criteria": eval_instruction,
                "judgment": result["judgment"],
                "score": result["score"],
            }

        with ThreadPoolExecutor() as executor:
            judgments = list(
                executor.map(process_instruction, expanded_eval_instructions.values())
            )

        total_score_chat = sum(judgment["score"] for judgment in judgments)
        summary_judgement = self.get_summary_judgement(judgments)
        return {
            "score": total_score_chat,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }

    def get_score_rag(self):
        chat_history = self.chat_properties["chat_history"]
        query = chat_history[0]["content"]  # User Input
        answer = chat_history[1]["content"]  # Assistant Response
        context = "\n".join(chat_history[1]["context"])  # Context
        expanded_evaluation_instruction = self.metric_properties[
            "expanded_eval_instructions"
        ]
        total_score_rag = 0
        judgments = []
        for eval_instruction in expanded_evaluation_instruction.values():
            judgment = self.rag_scorer(
                context=context,
                question=query,
                answer=answer,
                evaluation_instruction=eval_instruction,
            )
            result = self.calculate_score(judgment)
            total_score_rag += result["score"]
            judgments.append(
                {
                    "criteria": eval_instruction,
                    "judgment": result["judgment"],
                    "score": result["score"],
                }
            )
        summary_judgement = self.get_summary_judgement(judgments)
        return {
            "score": total_score_rag,
            "judgments": judgments,
            "summary_judgement": summary_judgement,
        }

    def score_chat_history(self, fractions=None):
        if fractions is None:
            fractions = [0.25]
        scores = {}
        if self.chat_properties["chat_type"] == "conversation":
            for fraction in fractions:
                if fraction != 1:
                    scores[str(fraction)] = self.get_score_at_unfinished_conversation(
                        fraction
                    )
                else:
                    scores[str(fraction)] = self.get_score_at_finished_conversation()
        elif self.chat_properties["chat_type"] == "rag":
            scores = self.get_score_rag()

        return scores


def evaluate_conversation(chat_properties, metric_properties, fractions):
    agent_task = AgentTask(chat_properties, metric_properties)
    return agent_task.score_chat_history(fractions=fractions)


def evaluate_rag(chat_properties, metric_properties):
    agent_task = AgentTask(chat_properties, metric_properties)
    return agent_task.score_chat_history(fractions=None)


if __name__ == "__main__":
    chat_properties_10_turns = {
        "chat_history": [
            {
                "role": "user",
                "content": "How do I implement a neural network in Python?",
            },
            {
                "role": "assistant",
                "content": "To implement a neural network in Python, you can use libraries like TensorFlow or PyTorch. Here’s a simple example using PyTorch...",
            },
            {
                "role": "user",
                "content": "Can you give me more details about the layers and activation functions?",
            },
            {
                "role": "assistant",
                "content": "Sure! A neural network typically consists of an input layer, one or more hidden layers, and an output layer. Each layer has neurons, which apply an activation function to the input data. Common activation functions include ReLU, Sigmoid, and Tanh.",
            },
            {
                "role": "user",
                "content": "What is the purpose of an activation function?",
            },
            {
                "role": "assistant",
                "content": "The activation function introduces non-linearity into the network, allowing it to learn complex patterns. Without activation functions, the network would essentially be a linear model, regardless of its depth.",
            },
            {
                "role": "user",
                "content": "Can you show me an example of a neural network with ReLU activation function?",
            },
            {
                "role": "assistant",
                "content": "Certainly! Here’s a simple example using PyTorch:\n\n```python\nimport torch\nimport torch.nn as nn\nimport torch.optim as optim\n\nclass SimpleNN(nn.Module):\n    def __init__(self):\n        super(SimpleNN, self).__init__()\n        self.fc1 = nn.Linear(10, 50)\n        self.relu = nn.ReLU()\n        self.fc2 = nn.Linear(50, 1)\n    \n    def forward(self, x):\n        x = self.fc1(x)\n        x = self.relu(x)\n        x = self.fc2(x)\n        return x\n\nmodel = SimpleNN()\ncriterion = nn.MSELoss()\noptimizer = optim.SGD(model.parameters(), lr=0.01)\n```",
            },
            {"role": "user", "content": "How do I train this neural network?"},
            {
                "role": "assistant",
                "content": "To train the neural network, you need to feed it data, compute the loss, and update the weights. Here’s how you can do it:\n\n```python\nfor epoch in range(100):\n    optimizer.zero_grad()\n    outputs = model(inputs)\n    loss = criterion(outputs, targets)\n    loss.backward()\n    optimizer.step()\n    print(f'Epoch [{epoch+1}/100], Loss: {loss.item():.4f}')\n```",
            },
            {
                "role": "user",
                "content": "What kind of data can I use to train this model?",
            },
            {
                "role": "assistant",
                "content": "You can use any numerical data that fits the input and output dimensions of your model. For example, if you’re working on a regression problem, you might use datasets like the Boston Housing dataset. For classification, you could use the MNIST dataset. The key is to preprocess your data to match the input shape expected by the model.",
            },
        ],
        "chat_type": "conversation",
    }

    metric_properties = {
        "eval_instructions": "Evaluate the response based on clarity, completeness, correctness, and relevance."
    }

    model_properties = {
        "model_name": "google/gemini-pro",
        "temperature": 0.7,
        "max_tokens": 8192,
    }

    chat_properties_1_turn = {
        "chat_history": [
            {
                "role": "user",
                "content": "How do I implement a neural network in Python?",
            },
            {
                "role": "assistant",
                "content": "To implement a neural network in Python, you can use libraries like TensorFlow or PyTorch. Here’s a simple example using PyTorch...",
            },
        ],
        "chat_type": "conversation",
    }

    rag_chat_properties_1_turn = {
        "chat_history": [
            {
                "role": "user",
                "content": "How do I implement a neural network in Python?",
            },
            {
                "role": "assistant",
                "content": "To implement a neural network in Python, you can use libraries like TensorFlow or PyTorch. Here’s a simple example using PyTorch...",
                "context": [
                    "To implement a neural network in Python, you can use libraries like TensorFlow or PyTorch. Here’s a simple example using PyTorch..."
                ],
            },
        ],
        "chat_type": "rag",
    }

    score_logs = evaluate_rag(rag_chat_properties_1_turn, metric_properties)
    score_logs = evaluate_conversation(
        chat_properties_1_turn, metric_properties, fractions=[1]
    )
    score_logs = evaluate_conversation(
        chat_properties_10_turns, metric_properties, fractions=[0.5, 1]
    )
