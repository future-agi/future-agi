import json
import logging
import math
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

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


def simulate_chat(
    human_simulator,
    expert_simulator,
    question,
    max_turns: int = 10,
    timeout_s: float | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run a bounded human/expert chat simulation.

    The loop terminates when either side emits ``topic resolved``,
    when ``max_turns`` is reached, when ``timeout_s`` wall-clock
    budget is exhausted, or when both sides return empty responses
    twice in a row (transient empty is tolerated once).

    Mirrors the ``max_turns=10`` convention used by
    ``ee/agenthub/fix_your_agent/transcript_simulator.py``.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be >= 1")
    if timeout_s is not None and (
        not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be a positive finite number or None")

    deadline = time.monotonic() + timeout_s if timeout_s is not None else None

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

    empty_streak = 0
    for turn in range(max_turns):
        if deadline is not None and time.monotonic() >= deadline:
            if verbose:
                human_simulator.print_chat_history()
                print()
                expert_simulator.print_chat_history()
            return {"resolved": False, "reason": "timeout", "turns": turn}

        expert_response, expert_message_id = expert_simulator.get_response(
            human_simulator.chat_history[-1]["content"], human_message_id
        )
        if verbose:
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
        if verbose:
            print(f"Human: {human_response}")

        expert_text = (
            expert_response
            if isinstance(expert_response, str)
            else str(expert_response)
        )
        human_text = (
            human_response if isinstance(human_response, str) else str(human_response)
        )

        if not expert_text.strip() and not human_text.strip():
            empty_streak += 1
            if empty_streak >= 2:
                if verbose:
                    human_simulator.print_chat_history()
                    print()
                    expert_simulator.print_chat_history()
                return {
                    "resolved": False,
                    "reason": "empty_response",
                    "turns": turn + 1,
                }
            continue
        empty_streak = 0

        if (
            "topic resolved" in human_text.lower()
            or "topic resolved" in expert_text.lower()
        ):
            if verbose:
                human_simulator.print_chat_history()
                print()
                expert_simulator.print_chat_history()
            return {"resolved": True, "reason": "topic_resolved", "turns": turn + 1}

    logger.warning("simulate_chat capped at max_turns=%d", max_turns)
    if verbose:
        human_simulator.print_chat_history()
        print()
        expert_simulator.print_chat_history()
    return {"resolved": False, "reason": "max_turns", "turns": max_turns}


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
