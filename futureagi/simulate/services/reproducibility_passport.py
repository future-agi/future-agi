"""Build reproducibility passports for simulation test executions.

A passport is a deterministic, JSON-serializable summary of the inputs that
made a simulate/eval run happen: agent or prompt version, scenarios, eval
configs, and execution options. It is meant to make reruns and regression
debugging explicit without storing raw transcripts or provider responses.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Model, QuerySet

from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import TestExecution

PASSPORT_SCHEMA_VERSION = "2026-06-09"
REDACTED = "[redacted]"
SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "apiSecret",
    "api_secret",
    "auth",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
DRIFT_SEVERITY_BY_SECTION = {
    "agent": "blocker",
    "prompt": "blocker",
    "scenarios": "blocker",
    "eval_configs": "blocker",
    "run_test": "warning",
    "execution_options": "warning",
    "execution": "info",
}
DRIFT_REASON_BY_SECTION = {
    "agent": "The agent definition, version snapshot, or simulator changed.",
    "prompt": "The prompt template, prompt version, or prompt snapshot changed.",
    "scenarios": "The scenario set, dataset links, metadata, or source hashes changed.",
    "eval_configs": "The eval templates, configs, mappings, or filters changed.",
    "run_test": "Run-level settings changed.",
    "execution_options": "Execution metadata or selected ids changed.",
    "execution": "Execution status or counters changed.",
}
REPLAY_INPUT_SECTIONS = (
    "run_test",
    "agent",
    "prompt",
    "scenarios",
    "eval_configs",
    "execution_options",
)
RUNTIME_SECTIONS = ("execution",)


@dataclass(frozen=True)
class PassportDiff:
    """Section-level drift between two reproducibility passports."""

    changed_sections: list[str]
    before_hashes: dict[str, str | None]
    after_hashes: dict[str, str | None]

    @property
    def has_drift(self) -> bool:
        return bool(self.changed_sections)

    def as_dict(self) -> dict[str, object]:
        return {
            "has_drift": self.has_drift,
            "changed_sections": self.changed_sections,
            "before_hashes": self.before_hashes,
            "after_hashes": self.after_hashes,
        }


@dataclass(frozen=True)
class ReplayReadinessIssue:
    """A concrete reason a run may not be replayable as-is."""

    severity: str
    code: str
    section: str
    message: str
    remediation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "section": self.section,
            "message": self.message,
            "remediation": self.remediation,
        }


def build_test_execution_passport(
    test_execution: TestExecution,
) -> dict[str, object]:
    """Return a deterministic passport for a completed or in-flight run.

    The passport intentionally captures configuration snapshots and identifiers
    instead of live transcript payloads. That keeps the artifact useful for
    replay planning while avoiding accidental leakage of call content or secrets.
    """

    run_test = test_execution.run_test
    sections: dict[str, object] = {
        "execution": _execution_section(test_execution),
        "run_test": _run_test_section(run_test),
        "agent": _agent_section(test_execution, run_test),
        "prompt": _prompt_section(run_test),
        "scenarios": _scenario_section(run_test, test_execution),
        "eval_configs": _eval_config_section(run_test),
        "execution_options": _execution_options_section(test_execution, run_test),
    }

    section_hashes = {
        name: stable_hash(payload) for name, payload in sections.items()
    }
    input_section_hashes = {
        name: section_hashes[name]
        for name in REPLAY_INPUT_SECTIONS
        if name in section_hashes
    }
    runtime_section_hashes = {
        name: section_hashes[name]
        for name in RUNTIME_SECTIONS
        if name in section_hashes
    }
    input_fingerprint = stable_hash(
        {
            "schema_version": PASSPORT_SCHEMA_VERSION,
            "section_hashes": input_section_hashes,
        }
    )
    runtime_fingerprint = stable_hash(
        {
            "schema_version": PASSPORT_SCHEMA_VERSION,
            "section_hashes": runtime_section_hashes,
        }
    )

    return {
        "schema_version": PASSPORT_SCHEMA_VERSION,
        "passport_hash": stable_hash(
            {
                "schema_version": PASSPORT_SCHEMA_VERSION,
                "section_hashes": section_hashes,
            }
        ),
        "input_fingerprint": input_fingerprint,
        "runtime_fingerprint": runtime_fingerprint,
        "section_hashes": section_hashes,
        "input_section_hashes": input_section_hashes,
        "runtime_section_hashes": runtime_section_hashes,
        **sections,
    }


def build_replay_plan(test_execution: TestExecution) -> dict[str, object]:
    """Build a replay-oriented plan from a test execution passport.

    The plan is intentionally declarative. It does not rerun anything; it tells
    a caller which stable inputs should be used and whether the current run has
    enough pinned state to make a future replay trustworthy.
    """

    passport = build_test_execution_passport(test_execution)
    issues = _replay_readiness_issues(passport)
    replay_inputs = _replay_inputs(passport)
    replay_key = stable_hash(
        {
            "schema_version": passport["schema_version"],
            "input_fingerprint": passport["input_fingerprint"],
            "replay_inputs": replay_inputs,
        }
    )

    return {
        "replay_key": replay_key,
        "passport_hash": passport["passport_hash"],
        "input_fingerprint": passport["input_fingerprint"],
        "can_replay": not any(issue.severity == "blocker" for issue in issues),
        "issues": [issue.as_dict() for issue in issues],
        "replay_inputs": replay_inputs,
        "baseline": {
            "section_hashes": passport["section_hashes"],
            "input_section_hashes": passport["input_section_hashes"],
            "passport_hash": passport["passport_hash"],
            "input_fingerprint": passport["input_fingerprint"],
        },
    }


def diff_passports(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> PassportDiff:
    """Compare two passports using their section hashes."""

    before_hashes = _extract_section_hashes(before)
    after_hashes = _extract_section_hashes(after)
    section_names = sorted(set(before_hashes) | set(after_hashes))
    changed = [
        section
        for section in section_names
        if before_hashes.get(section) != after_hashes.get(section)
    ]
    return PassportDiff(
        changed_sections=changed,
        before_hashes={
            section: before_hashes.get(section) for section in section_names
        },
        after_hashes={section: after_hashes.get(section) for section in section_names},
    )


def explain_passport_drift(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    """Return a severity-ranked explanation of section-level drift."""

    diff = diff_passports(before, after)
    changes = [
        {
            "section": section,
            "severity": DRIFT_SEVERITY_BY_SECTION.get(section, "info"),
            "reason": DRIFT_REASON_BY_SECTION.get(
                section,
                "The section hash changed.",
            ),
            "before_hash": diff.before_hashes.get(section),
            "after_hash": diff.after_hashes.get(section),
        }
        for section in diff.changed_sections
    ]
    severity_rank = {"blocker": 0, "warning": 1, "info": 2}
    changes.sort(key=lambda item: severity_rank.get(str(item["severity"]), 3))

    return {
        "has_drift": diff.has_drift,
        "highest_severity": _highest_drift_severity(changes),
        "changed_sections": diff.changed_sections,
        "changes": changes,
    }


def explain_replay_input_drift(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    """Explain only replay-input drift, ignoring runtime execution changes."""

    before_hashes = _extract_input_section_hashes(before)
    after_hashes = _extract_input_section_hashes(after)
    section_names = sorted(set(before_hashes) | set(after_hashes))
    changed_sections = [
        section
        for section in section_names
        if before_hashes.get(section) != after_hashes.get(section)
    ]
    changes = [
        {
            "section": section,
            "severity": DRIFT_SEVERITY_BY_SECTION.get(section, "info"),
            "reason": DRIFT_REASON_BY_SECTION.get(
                section,
                "The replay input section hash changed.",
            ),
            "before_hash": before_hashes.get(section),
            "after_hash": after_hashes.get(section),
        }
        for section in changed_sections
    ]
    severity_rank = {"blocker": 0, "warning": 1, "info": 2}
    changes.sort(key=lambda item: severity_rank.get(str(item["severity"]), 3))

    return {
        "has_drift": bool(changed_sections),
        "highest_severity": _highest_drift_severity(changes),
        "changed_sections": changed_sections,
        "changes": changes,
        "before_input_fingerprint": before.get("input_fingerprint"),
        "after_input_fingerprint": after.get("input_fingerprint"),
    }


def stable_hash(value: object) -> str:
    """Hash a JSON-compatible value after stable normalization."""

    payload = json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execution_section(test_execution: TestExecution) -> dict[str, object]:
    return {
        "id": str(test_execution.id),
        "status": test_execution.status,
        "started_at": _normalize(test_execution.started_at),
        "completed_at": _normalize(test_execution.completed_at),
        "total_scenarios": test_execution.total_scenarios,
        "scenario_ids": _normalize(test_execution.scenario_ids),
        "total_calls": test_execution.total_calls,
        "completed_calls": test_execution.completed_calls,
        "failed_calls": test_execution.failed_calls,
        "error_reason": test_execution.error_reason,
    }


def _run_test_section(run_test: RunTest) -> dict[str, object]:
    return {
        "id": str(run_test.id),
        "name": run_test.name,
        "description": run_test.description,
        "source_type": run_test.source_type,
        "organization_id": _model_id(run_test, "organization_id"),
        "workspace_id": _model_id(run_test, "workspace_id"),
        "dataset_row_ids": _normalize(run_test.dataset_row_ids),
        "enable_tool_evaluation": run_test.enable_tool_evaluation,
    }


def _agent_section(
    test_execution: TestExecution,
    run_test: RunTest,
) -> dict[str, object]:
    agent_definition = test_execution.agent_definition or run_test.agent_definition
    agent_version = test_execution.agent_version or run_test.agent_version
    simulator_agent = test_execution.simulator_agent or run_test.simulator_agent

    return {
        "agent_definition": _agent_definition_payload(agent_definition),
        "agent_version": _agent_version_payload(agent_version),
        "simulator_agent": _simulator_agent_payload(simulator_agent),
    }


def _prompt_section(run_test: RunTest) -> dict[str, object] | None:
    prompt_version = run_test.prompt_version
    prompt_template = run_test.prompt_template
    if not prompt_version and not prompt_template:
        return None

    snapshot = getattr(prompt_version, "prompt_config_snapshot", None)
    return {
        "prompt_template_id": _model_id(prompt_template),
        "prompt_template_name": getattr(prompt_template, "name", None),
        "prompt_version_id": _model_id(prompt_version),
        "template_version": getattr(prompt_version, "template_version", None),
        "commit_message": getattr(prompt_version, "commit_message", None),
        "config_snapshot": _normalize(snapshot),
        "config_snapshot_hash": stable_hash(snapshot or {}),
        "variable_names": _normalize(getattr(prompt_version, "variable_names", None)),
        "placeholders": _normalize(getattr(prompt_version, "placeholders", None)),
    }


def _scenario_section(
    run_test: RunTest,
    test_execution: TestExecution,
) -> list[dict[str, object]]:
    scenarios = _selected_scenarios(run_test, test_execution)
    return [
        {
            "id": str(scenario.id),
            "name": scenario.name,
            "scenario_type": scenario.scenario_type,
            "source_type": scenario.source_type,
            "status": scenario.status,
            "dataset_id": _model_id(scenario, "dataset_id"),
            "agent_definition_id": _model_id(scenario, "agent_definition_id"),
            "prompt_template_id": _model_id(scenario, "prompt_template_id"),
            "prompt_version_id": _model_id(scenario, "prompt_version_id"),
            "simulator_agent_id": _model_id(scenario, "simulator_agent_id"),
            "metadata": _normalize(scenario.metadata),
            "source_hash": stable_hash(scenario.source or ""),
        }
        for scenario in scenarios
    ]


def _eval_config_section(run_test: RunTest) -> list[dict[str, object]]:
    eval_configs = (
        SimulateEvalConfig.objects.filter(run_test=run_test, deleted=False)
        .select_related("eval_template", "eval_group", "kb_id")
        .order_by("id")
    )
    return [_eval_config_payload(eval_config) for eval_config in eval_configs]


def _execution_options_section(
    test_execution: TestExecution,
    run_test: RunTest,
) -> dict[str, object]:
    metadata = _normalize(test_execution.execution_metadata or {})
    return {
        "execution_metadata": metadata,
        "execution_metadata_hash": stable_hash(metadata),
        "dataset_row_ids": _normalize(run_test.dataset_row_ids),
        "scenario_ids": _normalize(test_execution.scenario_ids),
        "enable_tool_evaluation": run_test.enable_tool_evaluation,
    }


def _replay_inputs(passport: Mapping[str, object]) -> dict[str, object]:
    agent = _mapping_or_empty(passport.get("agent"))
    agent_definition = _mapping_or_empty(agent.get("agent_definition"))
    agent_version = _mapping_or_empty(agent.get("agent_version"))
    simulator_agent = _mapping_or_empty(agent.get("simulator_agent"))
    prompt = _mapping_or_empty(passport.get("prompt"))
    scenarios = _sequence_or_empty(passport.get("scenarios"))
    eval_configs = _sequence_or_empty(passport.get("eval_configs"))
    run_test = _mapping_or_empty(passport.get("run_test"))

    return {
        "run_test_id": run_test.get("id"),
        "source_type": run_test.get("source_type"),
        "agent_definition_id": agent_definition.get("id"),
        "agent_version_id": agent_version.get("id"),
        "simulator_agent_id": simulator_agent.get("id"),
        "prompt_template_id": prompt.get("prompt_template_id"),
        "prompt_version_id": prompt.get("prompt_version_id"),
        "scenario_ids": [scenario.get("id") for scenario in scenarios],
        "eval_config_ids": [config.get("id") for config in eval_configs],
        "dataset_row_ids": run_test.get("dataset_row_ids", []),
        "enable_tool_evaluation": run_test.get("enable_tool_evaluation"),
    }


def _replay_readiness_issues(
    passport: Mapping[str, object],
) -> list[ReplayReadinessIssue]:
    issues: list[ReplayReadinessIssue] = []
    run_test = _mapping_or_empty(passport.get("run_test"))
    execution = _mapping_or_empty(passport.get("execution"))
    agent = _mapping_or_empty(passport.get("agent"))
    prompt = _mapping_or_empty(passport.get("prompt"))
    scenarios = _sequence_or_empty(passport.get("scenarios"))
    eval_configs = _sequence_or_empty(passport.get("eval_configs"))

    source_type = run_test.get("source_type")
    if source_type == RunTest.SourceTypes.PROMPT and not prompt.get(
        "prompt_version_id"
    ):
        issues.append(
            ReplayReadinessIssue(
                severity="blocker",
                code="missing_prompt_version",
                section="prompt",
                message="Prompt simulation does not have a pinned prompt version.",
                remediation="Attach a prompt version before treating reruns as exact.",
            )
        )

    agent_definition = _mapping_or_empty(agent.get("agent_definition"))
    agent_version = _mapping_or_empty(agent.get("agent_version"))
    if source_type == RunTest.SourceTypes.AGENT_DEFINITION and not agent_definition:
        issues.append(
            ReplayReadinessIssue(
                severity="blocker",
                code="missing_agent_definition",
                section="agent",
                message="Agent-definition simulation has no agent definition.",
                remediation="Attach the agent definition used by the original run.",
            )
        )
    elif source_type == RunTest.SourceTypes.AGENT_DEFINITION and not agent_version:
        issues.append(
            ReplayReadinessIssue(
                severity="warning",
                code="missing_agent_version_snapshot",
                section="agent",
                message="Agent-definition simulation has no pinned agent version.",
                remediation="Create or attach an agent version snapshot for reruns.",
            )
        )

    requested_scenarios = execution.get("scenario_ids") or []
    if not scenarios:
        issues.append(
            ReplayReadinessIssue(
                severity="blocker",
                code="missing_scenarios",
                section="scenarios",
                message="No scenarios are available in the passport.",
                remediation="Attach at least one scenario before replaying this run.",
            )
        )
    elif requested_scenarios and len(scenarios) != len(requested_scenarios):
        issues.append(
            ReplayReadinessIssue(
                severity="blocker",
                code="scenario_selection_mismatch",
                section="scenarios",
                message="Some scenario ids from the execution were not resolved.",
                remediation="Restore the missing scenarios or replay a new baseline.",
            )
        )

    if not eval_configs:
        issues.append(
            ReplayReadinessIssue(
                severity="warning",
                code="missing_eval_configs",
                section="eval_configs",
                message="No eval configs are attached to the run.",
                remediation="Attach eval configs before comparing eval outcomes.",
            )
        )

    return issues


def _agent_definition_payload(
    agent_definition: Model | None,
) -> dict[str, object] | None:
    if not agent_definition:
        return None

    return {
        "id": str(agent_definition.id),
        "name": getattr(agent_definition, "agent_name", None),
        "agent_type": getattr(agent_definition, "agent_type", None),
        "provider": getattr(agent_definition, "provider", None),
        "assistant_id": getattr(agent_definition, "assistant_id", None),
        "language": getattr(agent_definition, "language", None),
        "languages": _normalize(getattr(agent_definition, "languages", None)),
        "model": getattr(agent_definition, "model", None),
        "model_details": _normalize(getattr(agent_definition, "model_details", None)),
        "knowledge_base_id": _model_id(agent_definition, "knowledge_base_id"),
        "observability_provider_id": _model_id(
            agent_definition, "observability_provider_id"
        ),
    }


def _agent_version_payload(agent_version: Model | None) -> dict[str, object] | None:
    if not agent_version:
        return None

    snapshot = getattr(agent_version, "configuration_snapshot", None)
    return {
        "id": str(agent_version.id),
        "version_number": getattr(agent_version, "version_number", None),
        "version_name": getattr(agent_version, "version_name", None),
        "status": getattr(agent_version, "status", None),
        "commit_message": getattr(agent_version, "commit_message", None),
        "configuration_snapshot": _normalize(snapshot),
        "configuration_snapshot_hash": stable_hash(snapshot or {}),
    }


def _simulator_agent_payload(simulator_agent: Model | None) -> dict[str, object] | None:
    if not simulator_agent:
        return None

    return {
        "id": str(simulator_agent.id),
        "name": getattr(simulator_agent, "name", None),
        "model": getattr(simulator_agent, "model", None),
        "voice_provider": getattr(simulator_agent, "voice_provider", None),
        "voice_name": getattr(simulator_agent, "voice_name", None),
        "prompt_hash": stable_hash(getattr(simulator_agent, "prompt", "") or ""),
    }


def _eval_config_payload(eval_config: SimulateEvalConfig) -> dict[str, object]:
    eval_template = eval_config.eval_template
    eval_group = eval_config.eval_group
    return {
        "id": str(eval_config.id),
        "name": eval_config.name,
        "status": eval_config.status,
        "model": eval_config.model,
        "error_localizer": eval_config.error_localizer,
        "knowledge_base_id": _model_id(eval_config, "kb_id_id"),
        "eval_group": (
            {
                "id": str(eval_group.id),
                "name": eval_group.name,
            }
            if eval_group
            else None
        ),
        "eval_template": {
            "id": str(eval_template.id),
            "name": eval_template.name,
            "eval_id": eval_template.eval_id,
            "eval_type": eval_template.eval_type,
            "template_type": eval_template.template_type,
            "model": eval_template.model,
            "config": _normalize(eval_template.config),
            "criteria_hash": stable_hash(eval_template.criteria or ""),
            "choices": _normalize(eval_template.choices),
        },
        "config": _normalize(eval_config.config),
        "mapping": _normalize(eval_config.mapping),
        "filters": _normalize(eval_config.filters),
    }


def _selected_scenarios(
    run_test: RunTest,
    test_execution: TestExecution,
) -> list[Scenarios]:
    scenario_ids = [str(item) for item in test_execution.scenario_ids or []]
    queryset = run_test.scenarios.filter(deleted=False)
    if scenario_ids:
        queryset = queryset.filter(id__in=scenario_ids)
    return list(
        queryset.select_related(
            "dataset",
            "agent_definition",
            "prompt_template",
            "prompt_version",
            "simulator_agent",
        ).order_by("id")
    )


def _extract_section_hashes(passport: Mapping[str, object]) -> dict[str, str]:
    raw_hashes = passport.get("section_hashes")
    if not isinstance(raw_hashes, Mapping):
        return {}
    return {
        str(section): str(section_hash)
        for section, section_hash in raw_hashes.items()
        if section_hash is not None
    }


def _extract_input_section_hashes(passport: Mapping[str, object]) -> dict[str, str]:
    raw_hashes = passport.get("input_section_hashes")
    if isinstance(raw_hashes, Mapping):
        return {
            str(section): str(section_hash)
            for section, section_hash in raw_hashes.items()
            if section_hash is not None
        }

    section_hashes = _extract_section_hashes(passport)
    return {
        section: section_hashes[section]
        for section in REPLAY_INPUT_SECTIONS
        if section in section_hashes
    }


def _highest_drift_severity(changes: Sequence[Mapping[str, object]]) -> str | None:
    if not changes:
        return None

    severity_rank = {"blocker": 0, "warning": 1, "info": 2}
    return str(
        min(
            (change.get("severity", "info") for change in changes),
            key=lambda severity: severity_rank.get(str(severity), 3),
        )
    )


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence_or_empty(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _model_id(instance: object | None, attr: str = "id") -> str | None:
    if instance is None:
        return None
    value = getattr(instance, attr, None)
    if value is None:
        return None
    return str(value)


def _normalize(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_secret_key(str(key)) else _normalize(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }

    if isinstance(value, QuerySet):
        return [_normalize(item) for item in value]

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize(item) for item in value]

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, Model):
        return str(value.pk)

    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment.lower() in lowered for fragment in SECRET_KEY_FRAGMENTS)
