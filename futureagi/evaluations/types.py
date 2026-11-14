"""Open evaluator-domain shapes shared by catalog, pricing and transports."""

from typing import Literal, NotRequired, TypeAlias, TypedDict

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class DecisionQuestion(TypedDict):
    type: Literal["noul", "choice", "score"]
    instructions: str | list[str]
    criteria: NotRequired[dict[str, str] | list[str]]


DecisionQuestions: TypeAlias = dict[str, DecisionQuestion]


class DecisionRequest(TypedDict):
    model: str
    state: JSONValue
    questions: DecisionQuestions


class OpenAIDecisionChoice(TypedDict):
    value: str
    description: NotRequired[str]


class OpenAIDecisionLevel(TypedDict):
    label: str
    description: str


class OpenAIDecisionQuestion(TypedDict):
    name: str
    type: Literal["predicate", "choice", "score"]
    instructions: str
    choices: NotRequired[list[OpenAIDecisionChoice]]
    levels: NotRequired[list[OpenAIDecisionLevel]]


class OpenAIDecisionRequest(TypedDict):
    model: str
    input: str
    questions: list[OpenAIDecisionQuestion]


class DecisionUsage(TypedDict, total=False):
    input_tokens: int
    prompt_tokens: int
    output_tokens: int
    completion_tokens: int


class DecisionCost(TypedDict):
    model: str
    prompt_cost: float
    completion_cost: float
    total_cost: float
    pricing_source: Literal[
        "jev_flat_fee", "decision_catalog_estimate", "provider_usage"
    ]


class DecisionConnection(TypedDict):
    model_name: str
    provider: str
    label: str
    is_available: bool


class DecisionModelGroup(TypedDict):
    model_name: str
    label: str
    author: str
    version: str
    question_types: list[str]
    min_choices: int
    max_choices: int
    max_state_bytes: int
    notice: str
    unavailable_reason: str | None
    connections: list[DecisionConnection]
    type: Literal["decision"]
