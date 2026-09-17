
from pydantic import Field, RootModel

from ee.agenthub.workflow_agent.prompts import EVENT_GENERATOR_PROMPT
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.functions import parse_llm_str_response


class DynamicEventFeatures(RootModel):
    root: dict[str, str] = Field(..., description="Dynamic event features")

    def __iter__(self):
        return iter(self.root.items())


class EventGeneratorAgent:
    def __init__(self):
        self.llm = LLM(
            temperature=0.5,
            max_tokens=8196
        )
        self.prompt = EVENT_GENERATOR_PROMPT

    def extract_features(self, conversations: list[str]) -> dict[str, str]:
        formatted_conversations = "##\n\n##".join(conversations)
        response = self.llm._get_completion_response(self.prompt.format(conversations=formatted_conversations))
        features = parse_llm_str_response(response.content[0].text, DynamicEventFeatures)

        return features

    def generate_events(self, conversations: list[str]) -> list[dict[str, str]]:
        features = self.extract_features(conversations)
        events = []
        for feature, description in features.items():
            event = {
                "key": feature,
                "event_type": feature,
                "description": description
            }
            events.append(event)

        return events


def main():
    # Example usage
    conversations = [
        "User: Hi, I'm having trouble with my computer.",
        "Agent: Hello! I'm sorry to hear that. Can you describe the problem you're experiencing?",
        "User: My laptop keeps freezing and I can't open any applications.",
        "Agent: I see. Let's try a few troubleshooting steps. First, have you tried restarting your computer?",
        "User: Yes, I've already tried that but it didn't help.",
        "Agent: Okay, let's try a few more things. Can you tell me when this problem started occurring?"
    ]

    generator = EventGeneratorAgent()
    events = generator.generate_events(conversations)

    print("Generated Events:")
    for event in events:
        print(f"Event Type: {event['event_type']}")
        print(f"Description: {event['description']}")


if __name__ == "__main__":
    main()
