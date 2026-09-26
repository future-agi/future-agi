import json
import os

from openai import OpenAI


class MetadataGenerator:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.global_metadata = {}

    def format_conversation(self, conversation: str) -> str:
        # Assuming the conversation is a string with "User:" and "Model:" prefixes
        return conversation

    def generate_metadata(self, conversation: str) -> dict[str, str]:
        prompt = f"""
        Analyze the following conversation and generate relevant metadata tags as key-value pairs.
        These tags should capture key aspects of the conversation such as topics discussed,
        tone, complexity, length, and any other relevant characteristics.

        Current metadata properties:
        {json.dumps(self.global_metadata, indent=2)}

        Conversation:
        {self.format_conversation(conversation)}

        Return a JSON object where each key is a metadata property and each value is the corresponding tag.
        Do not create duplicate properties that already exist in the current metadata.
        For new properties, ensure they are distinct from existing ones.

        For example:
        {{
            "language": "python",
            "skill_level": "beginner",
            "tone": "polite",
            "content_type": "code_explanation"
        }}
        """

        completion = self.client.chat.completions.create(
            model="anthropic/claude-3.5-sonnet:beta",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        new_metadata = json.loads(completion.choices[0].message.content)
        return new_metadata

    def process_dataset(self, dataset: list[str]) -> list[dict[str, dict[str, str]]]:
        result = []
        for conversation in dataset:
            metadata = self.generate_metadata(conversation)
            self.update_global_metadata(metadata)
            result.append({"conversation": conversation, "metadata": metadata})
        return result

    def update_global_metadata(self, new_metadata: dict[str, str]):
        for key, value in new_metadata.items():
            if key not in self.global_metadata:
                self.global_metadata[key] = [value]
            elif value not in self.global_metadata[key]:
                self.global_metadata[key].append(value)

    def get_global_metadata(self) -> dict[str, list[str]]:
        return self.global_metadata
