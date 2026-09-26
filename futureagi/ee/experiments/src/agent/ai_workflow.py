import json
import os

from openai import OpenAI

from ...core.utils.functions import format_conversation

# load_dotenv()


class AgentTask:
    def __init__(self, chat_properties, metric):
        self.chat_properties = chat_properties
        self.metric = metric
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.parsed_metric = self.parse_metric(metric)

    def parse_metric(self, metric):
        prompt = """
        Parse the following metric into a JSON format with keys 'property', 'filter', and 'other_values'.
        For e.g.
        For the metric: "the customer is asking about python code"
        Return:
        ```

            "property": "User asking about python code",
            "filter": "python",
            "other_values": "c++", "java", "javascript", "golang"

        ```
        For the metric: Where the AI model is rude to the customer
        Return:
        ```

            "property": "AI Response Tone",
            "filter_criteria": "Rude",
            "other_filter_criterias": [
                "Polite",
                "Neutral",
            "Professional",
            "Empathetic",
            "Frustrated"
          ]

        ```
        Now do it for the Metric: {metric}
        Return only the JSON object, nothing else.
        """
        processed_prompt = prompt.format(metric=metric.strip())
        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[{"role": "user", "content": processed_prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(completion.choices[0].message.content)

    def check_condition(self, conversation):
        prompt = f"""
        Analyze the following conversation based on this metric:
        Property: {self.parsed_metric['property']}
        Filter: {self.parsed_metric['filter']}
        Conversation:
        {format_conversation(conversation)}

        Does this conversation meet the condition specified by the metric?
        Return only 'true' or 'false' as a JSON object with the key 'result'.
        """
        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(completion.choices[0].message.content)["result"] == "true"

    def score_chat_history(self, fractions=None):
        if fractions is None:
            fractions = [1]
        scores = {}
        for fraction in fractions:
            if fraction != 1:
                clip_index = int(len(self.chat_properties["chat_history"]) * fraction)
                conversation = self.chat_properties["chat_history"][:clip_index]
            else:
                conversation = self.chat_properties["chat_history"]

            condition_met = self.check_condition(conversation)
            scores[str(fraction)] = {"condition_met": condition_met}
        return scores


# Usage
def analyze_conversation(chat_properties, metric):
    agent_task = AgentTask(chat_properties, metric)
    return agent_task.score_chat_history()


# Example usage
chat_properties = {
    "chat_history": [
        {"role": "user", "content": "Can you help me with Java?"},
        {
            "role": "assistant",
            "content": "Of course! What specific aspect of Java do you need help with?",
        },
        {"role": "user", "content": "How do I create a list?"},
        {
            "role": "assistant",
            "content": "To create a list in Java, you can use square brackets []. Here's an example:...",
        },
    ],
    "chat_type": "conversation",
}

metric = "User is chatting about python with the model"

result = analyze_conversation(chat_properties, metric)
print(result)
