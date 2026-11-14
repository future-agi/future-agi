import json
import uuid

try:
    from ee.prompts.data_generation_prompts import (
        reaction_prompt,
        system_message_expert,
        system_message_human,
        user_reaction_content_prompt,
    )
except ImportError:
    reaction_prompt = ""
    system_message_expert = ""
    system_message_human = ""
    user_reaction_content_prompt = ""
from ...core.llm.llm import LLM


class ChatSimulator:
    def __init__(self, llm_client, role, system_message, model_name):
        self.llm_client = llm_client
        self.role = role
        self.model_name = model_name
        self.system_message = system_message
        self.chat_history = []

    def get_response(self, prompt, parent_message_id):
        message_id = str(uuid.uuid4())
        self.chat_history.append(
            {
                "message_id": message_id,
                "parent_message_id": parent_message_id,
                "role": "user",
                "content": prompt,
                "action_taken": None,
            }
        )
        response = self.llm_client._get_completion_content(
            messages=[
                {"role": "system", "content": self.system_message},
                *self.chat_history,
            ]
        )
        response_message_id = str(uuid.uuid4())
        self.chat_history.append(
            {
                "message_id": response_message_id,
                "parent_message_id": message_id,
                "role": "assistant",
                "content": response,
                "action_taken": None,
            }
        )
        return response, response_message_id

    def print_chat_history(self):
        print(f"{self.role.capitalize()} Chat History:")
        for message in self.chat_history:
            print(f"{message['role']}: {message['content']}")


def simulate_chat(human_simulator, expert_simulator, question):
    topic_resolved = False
    human_message_id = str(uuid.uuid4())
    expert_message_id = str(uuid.uuid4())

    human_simulator.chat_history.append(
        {
            "message_id": human_message_id,
            "parent_message_id": None,
            "role": "user",
            "content": f"Can you help me with this question? {question}",
            "action_taken": None,
        }
    )

    while not topic_resolved:
        expert_response, expert_message_id = expert_simulator.get_response(
            human_simulator.chat_history[-1]["content"], human_message_id
        )
        print(f"Expert: {expert_response}")
        # User reaction to the expert's response
        user_reaction = get_user_reaction(
            human_simulator.llm_client,
            human_simulator.model_name,
            expert_response,
        )
        if user_reaction != "None":
            human_simulator.chat_history[-1]["action_taken"] = user_reaction
        human_response, human_message_id = human_simulator.get_response(
            expert_response, expert_message_id
        )
        print(f"Human: {human_response}")

        if (
            "topic resolved" in human_response.lower()
            or "topic resolved" in expert_response.lower()
        ):
            topic_resolved = True

    human_simulator.print_chat_history()
    print()
    expert_simulator.print_chat_history()


def save_chat_history_to_json(human_simulator, expert_simulator, filename):
    chat_history = {
        "human": human_simulator.chat_history,
        "expert": expert_simulator.chat_history,
    }

    with open(filename, "w") as file:
        json.dump(chat_history, file, indent=4)


def get_user_reaction(llm_client, model_name, expert_response):
    user_reaction = llm_client._get_completion_content(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": user_reaction_content_prompt,
            },
            {
                "role": "user",
                "content": reaction_prompt.format(expert_response=expert_response),
            },
        ],
    )
    return user_reaction.strip()
