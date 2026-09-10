"""Model-facing reports for the bounded, per-trace investigation.

These are inference contracts, not database models or issue-clustering IDs.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Outcome = Literal["satisfied", "violated", "unknown"]


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Finding(Report):
    id: str
    kind: Literal["outcome", "process", "instruction", "safety"]
    status: Outcome
    summary: str
    evidence_ids: list[str]
    missing_facts: list[str]


class Requirement(Report):
    requirement: str
    status: Outcome
    evidence_ids: list[str]


class Amendment(Report):
    requirement: str
    decision: Literal["accepted", "rejected"]
    provenance: list[str]


class Decision(Report):
    action: Literal["investigate", "finish"]
    reason: str
    task: str
    system_prompt: str
    source_ids: list[str]
    check: str
    findings: list[Finding]
    amendments: list[Amendment]


class ProposedRequirement(Report):
    requirement: str
    evidence_ids: list[str]


class ChildReport(Report):
    assignment_challenges: list[str]
    proposed_requirements: list[ProposedRequirement]
    findings: list[Finding]
    missing_facts: list[str]


class Counterevidence(Report):
    evidence_id: str
    resolved: bool
    explanation: str


class Verification(Report):
    accepted: bool
    reason: str
    findings: list[Finding]
    requirement_checks: list[Requirement]
    counterevidence: list[Counterevidence]
