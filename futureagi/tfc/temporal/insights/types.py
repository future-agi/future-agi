from dataclasses import dataclass, field


@dataclass
class InsightRunInput:
    run_id: str


@dataclass
class InsightBatchInput:
    run_id: str
    unit_pks: list[int] = field(default_factory=list)


@dataclass
class InsightFailInput:
    run_id: str
    message: str
