"""Provider-independent records for the Jev mapping and validated answers."""

from dataclasses import dataclass, field

JEV_SERVICE = "jev"
JEV_PROVIDER = "typesafe"
MAPPING_REVISION = "jev-map-v1"


@dataclass(frozen=True)
class JevMapping:
    question_type: str
    pass_config: dict = field(default_factory=dict)
    choice: dict = field(default_factory=dict)
    score: dict = field(default_factory=dict)
    include_messages: bool = False

    def canonical_dict(self) -> dict:
        return {
            "question_type": self.question_type,
            "pass": self.pass_config or None,
            "choice": self.choice or None,
            "score": self.score or None,
            "include_messages": self.include_messages,
        }

    def to_dict(self) -> dict:
        from ee.jev.mapping import content_hash

        return {
            "revision": MAPPING_REVISION,
            **self.canonical_dict(),
            "content_hash": content_hash(self),
        }


@dataclass(frozen=True)
class JevQuestion:
    type: str
    instructions: str | list[str]
    criteria: dict | list | None = None

    def to_dict(self) -> dict:
        result = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            result["criteria"] = self.criteria
        return result


@dataclass(frozen=True)
class NormalizedAnswer:
    question_type: str
    probability: float | None = None
    choice: str | None = None
    distribution: dict | None = None
    confidence: float | None = None
    raw_score: float | None = None
    legend: dict | None = None
    normalized_score: float | None = None
