"""OpenAI Decisions wire adaptation, not a chat-completions approximation.

Contract: developers.openai.com/api/docs/guides/decisions (2026-11-06).
Only the envelope changes; probabilities and scores are never synthesized.
"""

import json

from ee.decisions.errors import DecisionResponseError
from evaluations.types import (
    DecisionQuestions,
    DecisionRequest,
    JSONValue,
    OpenAIDecisionQuestion,
    OpenAIDecisionRequest,
)


def request_body(payload: DecisionRequest, gateway_model: str) -> OpenAIDecisionRequest:
    questions: list[OpenAIDecisionQuestion] = []
    for name, source in payload["questions"].items():
        instructions = source["instructions"]
        if not isinstance(instructions, str):
            instructions = "\n\n".join(instructions)
        kind = source["type"]
        question = {
            "name": name,
            "type": "predicate" if kind == "noul" else kind,
            "instructions": instructions,
        }
        criteria = source.get("criteria")
        if kind == "noul" and criteria:
            question["instructions"] += "\n\nDecision criteria:\n" + json.dumps(
                criteria, ensure_ascii=False
            )
        elif kind == "choice":
            question["choices"] = [
                {
                    "value": label,
                    **({"description": description} if description else {}),
                }
                for label, description in criteria.items()
            ]
        elif kind == "score":
            # Stable ordinal labels prevent a provider's label-length limit
            # from truncating the authored rubric, which lives in description.
            question["levels"] = [
                {"label": str(index), "description": level}
                for index, level in enumerate(criteria)
            ]
        questions.append(question)
    state = payload["state"]
    return {
        "model": gateway_model,
        "input": state
        if isinstance(state, str)
        else json.dumps(state, ensure_ascii=False, allow_nan=False),
        "questions": questions,
    }


def normalize_response(
    response: dict[str, JSONValue], questions: DecisionQuestions
) -> dict[str, JSONValue]:
    """Validate identifiers and preserve every numeric field for shared validation."""

    def require(condition):
        if not condition:
            raise DecisionResponseError("DECISION_RESPONSE_INVALID")

    require(isinstance(response, dict))
    answers = response.get("answers")
    require(isinstance(answers, list) and len(answers) == len(questions))
    normalized = {}
    for answer in answers:
        require(isinstance(answer, dict))
        name = answer.get("name")
        require(isinstance(name, str) and name in questions and name not in normalized)
        source = questions[name]
        kind = answer.get("type")
        if kind == "refusal":
            raise DecisionResponseError("DECISION_REFUSED")
        expected = "predicate" if source["type"] == "noul" else source["type"]
        require(kind == expected)
        if kind == "predicate":
            normalized[name] = {"type": "noul", "noul": answer.get("probability")}
            continue
        rows = answer.get("probabilities")
        require(isinstance(rows, list))
        probabilities = {}
        for row in rows:
            require(isinstance(row, dict))
            value = row.get("value")
            if kind == "score":
                require(type(value) is int and 0 <= value < len(source["criteria"]))
                require(row.get("label") == str(value))
                value = str(value)
            else:
                require(isinstance(value, str))
            require(value not in probabilities)
            probabilities[value] = row.get("probability")
        typed = {
            "type": kind,
            "probabilities": probabilities,
            "confidence": answer.get("confidence"),
        }
        if kind == "choice":
            typed["choice"] = answer.get("choice")
        else:
            typed.update(
                score=answer.get("score"),
                legend={str(i): level for i, level in enumerate(source["criteria"])},
            )
        normalized[name] = typed
    return {**response, "answers": normalized}
