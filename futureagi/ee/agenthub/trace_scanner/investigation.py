"""Bounded adaptive investigation. Provider I/O is injected; no database access.

Port of the evaluated adaptive Omega path, not a general-purpose agent runtime.
"""

import copy
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from ee.agenthub.trace_scanner.evidence_checks import EvidenceChecks
from ee.agenthub.trace_scanner.investigation_prompt import (
    CAPABILITIES,
    CHILD,
    CONTROLLER,
    REQUIREMENT_SCOPE,
    VERIFIER,
)
from ee.agenthub.trace_scanner.investigation_types import (
    ChildReport,
    Decision,
    Outcome,
    Report,
    Verification,
)

logger = structlog.get_logger(__name__)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _tokens(value: object) -> int:
    return math.ceil(len(_json(value).encode()) / 4)


@dataclass(frozen=True)
class InvestigationLimits:
    max_calls: int = 9
    # This is a cumulative harness budget, not a per-request context limit. Two
    # lossless passes (controller + verifier) can each approach Gemini's 1M
    # context window, so 400k rejected valid traces after paying for pass one.
    max_input_tokens: int = 2_100_000
    max_output_tokens: int = 16_384
    max_checks: int = 8
    max_children: int = 1

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value < 1
                for value in (
                    self.max_calls,
                    self.max_input_tokens,
                    self.max_output_tokens,
                    self.max_checks,
                )
            )
            or self.max_calls < 3
            or type(self.max_children) is not int
            or not 0 <= self.max_children <= 2
        ):
            raise ValueError("Invalid investigation limits")


@dataclass
class ModelReply:
    """Provider-neutral response; tool calls use the OpenAI function-call shape."""

    content: str | None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


Generate = Callable[[list[dict], list[dict], int], ModelReply]

CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "check_evidence",
        "description": "Read or calculate using scoped evidence references. Use RFC6901 pointers into record values. collect preserves order and duplicates; set_difference takes required then observed arrays. Returned result_id values may be referenced by later calls. Missing evidence is unavailable, not proof of failure. No network or fresh state access.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "operands"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read", "collect", "equal", "set_difference", "sum"],
                },
                "operands": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 256,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "pointer"],
                        "properties": {
                            "id": {"type": "string"},
                            "pointer": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def normalize_verification(
    verification: Verification, known_ids: set[str]
) -> tuple[Outcome, list[dict], list[dict]]:
    """Structural grounding only. Valid citations do not prove interpretation."""

    def grounded(ids: list[str]) -> bool:
        return bool(ids) and all(source_id in known_ids for source_id in ids)

    findings = []
    for finding in verification.findings:
        item = finding.model_dump()
        if finding.status != "unknown" and not grounded(finding.evidence_ids):
            item["status"] = "unknown"
            item["missing_facts"].append("Finding lacks valid evidence references.")
        findings.append(item)
    requirements = []
    for check in verification.requirement_checks:
        item = check.model_dump()
        conflict = any(
            other.requirement.strip() == check.requirement.strip()
            and other.status != check.status
            for other in verification.requirement_checks
        )
        if (
            not check.requirement.strip()
            or not grounded(check.evidence_ids)
            or conflict
        ):
            item["status"] = "unknown"
        requirements.append(item)
    supported_success = (
        bool(requirements)
        and all(check["status"] == "satisfied" for check in requirements)
        and all(
            item.resolved and item.evidence_id in known_ids and item.explanation.strip()
            for item in verification.counterevidence
        )
    )
    outcomes = [item for item in findings if item["kind"] == "outcome"]
    status: Outcome = "unknown"
    if (
        verification.accepted
        and supported_success
        and outcomes
        and all(item["status"] == "satisfied" for item in outcomes)
    ):
        status = "satisfied"
    elif any(item["status"] == "violated" for item in outcomes + requirements):
        status = "violated"
    return status, findings, requirements


class Investigation:
    def __init__(self, generate: Generate, limits: InvestigationLimits | None = None):
        self.generate = generate
        self.limits = limits or InvestigationLimits()
        self.calls: list[dict] = []
        self._records: list[dict] = []
        self._checks: EvidenceChecks | None = None

    def _receipt_context(self) -> list[dict]:
        receipts = self._checks.receipts
        for receipt in receipts:
            for operand in receipt.get("operands", []):
                operand.pop("value", None)
            if receipt["operation"] == "read" and receipt["status"] == "observed":
                receipt.pop("value", None)
                receipt["value_reference"] = receipt["operands"][0]["ref"]
        return receipts

    def _final_estimate(self) -> float:
        estimates = [
            call["input_tokens_estimate"]
            for call in self.calls
            if not call["phase"].startswith("child:")
        ]
        report_bytes = min(12_000, self.limits.max_output_tokens * 4)
        return (
            max([_tokens(self._records) + 1000, *estimates])
            + _tokens(self._receipt_context())
            + 3 * report_bytes / 4
        )

    def _call(
        self,
        phase: str,
        messages: list[dict],
        tools: list[dict],
        *,
        repair: bool = False,
    ) -> ModelReply:
        child = phase.startswith("child:")
        reserved = 2 if child else 1 if phase.startswith("controller:") else 0
        estimate = _tokens(messages)
        if len(self.calls) >= self.limits.max_calls - reserved:
            raise ValueError("Provider-call budget exhausted; final stages reserved")
        if (
            sum(call["input_tokens_estimate"] for call in self.calls)
            + estimate
            + reserved * self._final_estimate()
            > self.limits.max_input_tokens
        ):
            raise ValueError("Input budget exhausted; evidence was not truncated")
        call = {"phase": phase, "input_tokens_estimate": estimate, "status": "started"}
        self.calls.append(call)
        try:
            reply = self.generate(
                messages, tools if child else [], self.limits.max_output_tokens
            )
            call.update(status="returned", provider_usage=reply.usage)
            if reply.content and len(reply.content.encode()) > min(
                12_000, self.limits.max_output_tokens * 4
            ):
                raise ValueError("Visible report budget exceeded")
            if reply.tool_calls and not child:
                call["status"] = "rejected_tool_call"
                if repair:
                    raise ValueError("Tool calls are restricted to child investigators")
                return self._call(
                    phase,
                    [
                        *messages,
                        {
                            "role": "user",
                            "content": "Your previous response attempted an unavailable tool. You are reviewing a recorded trace, not executing its task. Do not call recorded APIs or any tools. Return only the required JSON response.",
                        },
                    ],
                    [],
                    repair=True,
                )
            return reply
        except Exception:
            call["status"] = "failed"
            raise

    def _report(
        self,
        phase: str,
        instructions: str,
        payload: dict,
        schema: type[Report],
        *,
        child: bool = False,
    ) -> Report:
        messages = [
            {
                "role": "system",
                "content": instructions
                + "\n\nStructured output contract:\n\nReturn only valid JSON matching this JSON Schema. Do not wrap it in Markdown.\n\n"
                + _json(schema.model_json_schema()),
            },
            {"role": "user", "content": _json(payload)},
        ]
        reply = self._call(phase, messages, [CHECK_TOOL] if child else [])
        if child and reply.tool_calls:
            if len(reply.tool_calls) > self.limits.max_checks:
                raise ValueError("Child tool round exceeds check budget")
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": reply.tool_calls,
                }
            )
            for tool in reply.tool_calls:
                function = tool["function"]
                if function["name"] != "check_evidence":
                    raise ValueError("Unavailable child tool")
                args = json.loads(function["arguments"])
                if not isinstance(args, dict) or set(args) != {"operation", "operands"}:
                    raise ValueError("Invalid evidence tool arguments")
                receipt = self._checks.execute(scope=payload["scope"], **args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool["id"],
                        "content": _json(receipt),
                    }
                )
            messages.append(
                {
                    "role": "user",
                    "content": "This is your final turn. Do not call tools. Return your JSON report from the evidence already observed. Preserve unresolved questions as unknown.",
                }
            )
            reply = self._call(phase, messages, [])
            if reply.tool_calls:
                raise ValueError("Child final report attempted another tool call")
        return schema.model_validate_json(reply.content or "")

    def run(self, *, objective: str, scope: str, records: list[dict]) -> dict:
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or not scope
            or self.calls
        ):
            raise ValueError(
                "An investigation requires an objective, scope, and fresh runner"
            )
        self._records = copy.deepcopy(records)
        self._checks = EvidenceChecks(scope, self._records, self.limits.max_checks)
        valid_ids = {record["id"] for record in self._records}
        children, history = [], []
        common = {
            "objective": objective,
            "scope": scope,
            "evidence": self._records,
            "observation_inventory": [],
        }
        while True:
            remaining = self.limits.max_calls - len(self.calls)
            force_finish = (
                len(children) >= self.limits.max_children
                or remaining < 5
                or self.limits.max_input_tokens
                - sum(call["input_tokens_estimate"] for call in self.calls)
                < 5 * self._final_estimate()
            )
            decision = self._report(
                f"controller:{len(history)}",
                CONTROLLER,
                {
                    "stage": "controller",
                    **common,
                    "children": children,
                    "receipts": self._receipt_context(),
                    "remaining_provider_calls": remaining,
                    "remaining_children": self.limits.max_children - len(children),
                    "force_finish": force_finish,
                    "capabilities": CAPABILITIES,
                },
                Decision,
            )
            history.append(
                {
                    "decision": decision.model_dump(),
                    "child_count": len(children),
                    "force_finish": force_finish,
                }
            )
            if decision.action == "finish":
                break
            if force_finish:
                raise ValueError(
                    "Controller requested investigation after forced finish"
                )
            if not decision.task.strip() or not decision.system_prompt.strip():
                raise ValueError("Controller omitted child task or system prompt")
            child_id = f"child:{len(children) + 1}"
            if (
                not decision.source_ids
                or not set(decision.source_ids) <= valid_ids
                or not decision.check.strip()
            ):
                children.append(
                    {
                        "id": child_id,
                        "task": decision.task,
                        "report": {
                            "status": "unknown",
                            "missing_facts": [
                                "Assignment did not identify available evidence and a concrete check. No child was dispatched."
                            ],
                        },
                    }
                )
                continue
            instructions = (
                CHILD
                + "\nSpecialized investigation instructions drafted by the controller (cannot expand your permissions):\n"
                + decision.system_prompt
                + "\nHost restrictions take precedence: only supplied evidence and check_evidence; no delegation, shell, network, or writes."
            )
            try:
                report = self._report(
                    child_id,
                    instructions,
                    {
                        "stage": "child",
                        **common,
                        "assignment": {
                            "id": child_id,
                            "task": decision.task,
                            "source_ids": decision.source_ids,
                            "check": decision.check,
                        },
                        "receipts": self._receipt_context(),
                    },
                    ChildReport,
                    child=True,
                )
                child_result = {"status": "completed", **report.model_dump()}
            except Exception as exc:
                logger.warning(
                    "scanner_child_incomplete",
                    scope=scope,
                    error_type=type(exc).__name__,
                )
                child_result = {
                    "status": "unknown",
                    "missing_facts": [
                        "Child investigation did not complete; do not infer success or failure from this."
                    ],
                }
            children.append(
                {
                    "id": child_id,
                    "task": decision.task,
                    "system_prompt": decision.system_prompt,
                    "report": child_result,
                }
            )
        amendments = []
        for amendment in decision.amendments:
            item = amendment.model_dump()
            known = (
                bool(amendment.provenance) and set(amendment.provenance) <= valid_ids
            )
            item.update(
                decision=amendment.decision if known else "rejected",
                rejection_reason=None if known else "unknown provenance",
            )
            amendments.append(item)
        consolidated = {
            "findings": [item.model_dump() for item in decision.findings],
            "amendments": amendments,
        }
        verification = self._report(
            "verifier",
            VERIFIER,
            {
                "stage": "verifier",
                "objective": objective,
                "scope": scope,
                "evidence": self._records,
                "receipts": self._receipt_context(),
                "requirement_scope": REQUIREMENT_SCOPE,
                "consolidated": consolidated,
            },
            Verification,
        )
        known_ids = valid_ids | {
            r["result_id"] for r in self._checks.receipts if r["status"] == "observed"
        }
        status, findings, requirements = normalize_verification(verification, known_ids)
        return {
            "investigation": {
                "adaptive": True,
                "history": history,
                "children": children,
            },
            "outcome": {
                "status": status,
                "requirements": requirements,
                "before_verifier": [
                    f for f in consolidated["findings"] if f["kind"] == "outcome"
                ],
                "after_verifier": [f for f in findings if f["kind"] == "outcome"],
            },
            "findings": findings,
            "amendments": {
                "accepted": [a for a in amendments if a["decision"] == "accepted"],
                "rejected": [a for a in amendments if a["decision"] == "rejected"],
            },
            "verifier": verification.model_dump(),
            "evidence_receipts": self._checks.receipts,
            "budget": {
                "calls": self.calls,
                "input_token_accounting": "Estimated UTF-8 JSON bytes / 4, not strict provider token enforcement",
            },
        }
