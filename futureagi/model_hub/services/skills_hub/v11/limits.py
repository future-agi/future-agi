"""One effective Limits contract for options, previews and admission (§E.1).

SKILLS_TEST_LIMITS may lower the integer ceilings below and may configure a
positive decimal-string cost ceiling (default USD25). Repetitions are profile
semantics, not a deployment override. Invalid settings fail closed.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import Limits

MAX_SUITE_CASES = 20
MAX_CASE_PROMPT_BYTES = 16 * 1024
MAX_CASE_FIXTURE_BYTES = 48 * 1024
MAX_SUITE_BYTES = 1024 * 1024
MAX_CHECKS_PER_CASE = 10
MAX_CHECK_TEXT = 2000
MAX_SCHEMA_BYTES = 16 * 1024
MAX_SCHEMA_DEPTH = 20
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_OUTBOUND_FINDINGS = 1000
MAX_ASSIST_INPUT_BYTES = 64 * 1024
MAX_ASSIST_OUTPUT_BYTES = 64 * 1024
MAX_ASSIST_FILES = 3
MAX_ASSIST_OUTPUT_TOKENS = 8192
ASSIST_DEADLINE_SECONDS = 60
GITHUB_PREVIEW_TTL_SECONDS = 900
DEFAULT_RETENTION_DAYS = 30
HARNESS_VERSION = "skills-text-v1.1"
ADAPTER_VERSION = "skills-tools-v1.1"
EVALUATOR_VERSION = "skills-checks-v1.1"

_COMMON_PROFILE = {
    "max_task_turns_per_attempt": 6,
    "max_tool_calls_per_attempt": 12,
    "task_phase_seconds": 90,
    "judge_phase_seconds": 30,
    "provider_call_seconds": 45,
    "max_judge_output_tokens": 2048,
    "max_delivered_bytes_per_attempt": 192 * 1024,
}
DEFAULT_LIMITS: Limits = {
    "profiles": {
        "quick": {
            **_COMMON_PROFILE,
            "max_cases": 3,
            "max_variants": 2,
            "repetitions": 1,
            "max_attempts": 6,
            "deadline_seconds": 300,
            "max_task_output_tokens": 1536,
            "max_total_tokens": 150_000,
        },
        "repeated": {
            **_COMMON_PROFILE,
            "max_cases": 20,
            "max_variants": 3,
            "repetitions": 3,
            "max_attempts": 180,
            "deadline_seconds": 1800,
            "max_task_output_tokens": 2048,
            "max_total_tokens": 1_000_000,
        },
    },
    "max_competitors": 5,
    "max_active_attempts_per_run": 2,
    "max_active_provider_calls_per_organization": 4,
    "max_active_model_jobs_per_organization": 2,
    "max_queue_wait_seconds": 60,
    "max_estimated_cost_usd": "25.00000000",
}


def effective_limits() -> Limits:
    """Return a fresh JSON-compatible object containing the actual ceilings."""
    result = deepcopy(DEFAULT_LIMITS)
    overrides = getattr(settings, "SKILLS_TEST_LIMITS", {})
    if not isinstance(overrides, dict):
        raise ImproperlyConfigured("SKILLS_TEST_LIMITS must be an object.")

    def apply(target: dict, updates: dict) -> None:
        for name, value in updates.items():
            if name not in target:
                raise ImproperlyConfigured("Unknown Skills limit.")
            if isinstance(target[name], dict):
                if not isinstance(value, dict):
                    raise ImproperlyConfigured("Skills profile limits must be objects.")
                apply(target[name], value)
            elif name == "max_estimated_cost_usd":
                try:
                    amount = Decimal(value) if isinstance(value, str) else None
                except InvalidOperation as exc:
                    raise ImproperlyConfigured("Invalid Skills cost ceiling.") from exc
                if (
                    amount is None
                    or not amount.is_finite()
                    or not 0 < amount < Decimal("10000000000")
                    or amount.as_tuple().exponent < -8
                ):
                    raise ImproperlyConfigured(
                        "Skills cost ceiling must be a positive decimal string (18,8)."
                    )
                target[name] = format(amount, ".8f")
            elif (
                type(value) is not int
                or value < 1
                or value > target[name]
                or (name == "repetitions" and value != target[name])
            ):
                raise ImproperlyConfigured(
                    "Skills limits may only lower positive ceilings; repetitions are fixed."
                )
            else:
                target[name] = value

    apply(result, overrides)
    return result


def retention_days() -> int:
    value = getattr(settings, "SKILLS_PAYLOAD_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
    if type(value) is not int or not 7 <= value <= 90:
        raise ImproperlyConfigured("Skills payload retention must be 7–90 days.")
    return value


def validate_competitors(*, skill_id: object, competitors: list[dict]) -> None:
    """The target must not leak into the without-target arm via competitors."""
    identities = [(str(item["skill_id"]), item["version"]) for item in competitors]
    if any(identity[0] == str(skill_id) for identity in identities):
        raise SkillsHubError(
            "invalid_test_config",
            "The target skill cannot be selected as a competitor.",
            status=400,
        )
    if len({identity[0] for identity in identities}) != len(identities):
        raise SkillsHubError(
            "invalid_test_config", "Select each competitor skill only once.", status=400
        )


def validate_run_limits(configuration: dict, *, skill_id: object | None = None) -> None:
    limits = effective_limits()
    profile = limits["profiles"][configuration["profile"]]
    baselines = configuration["baselines"]
    if (
        len(set(baselines)) != len(baselines)
        or not 1 <= len(baselines) < profile["max_variants"]
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Choose the allowed number of distinct baselines.",
            status=400,
        )
    if configuration["repetitions"] != profile["repetitions"]:
        raise SkillsHubError(
            "invalid_test_config",
            "Repetitions must match the selected profile.",
            status=400,
        )
    selection = configuration["cases"]
    count = len(
        selection["case_ids"] if selection["kind"] == "saved" else selection["cases"]
    )
    if (
        not 1 <= count <= profile["max_cases"]
        or count * (1 + len(baselines)) * profile["repetitions"]
        > profile["max_attempts"]
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "The case selection exceeds this profile's limits.",
            status=400,
        )
    if len(configuration["competitors"]) > limits["max_competitors"]:
        raise SkillsHubError("invalid_test_config", "Too many competitors.", status=400)
    if skill_id is not None:
        validate_competitors(
            skill_id=skill_id, competitors=configuration["competitors"]
        )
    for name, maximum in (
        ("model", profile["max_task_output_tokens"]),
        ("judge", profile["max_judge_output_tokens"]),
    ):
        if configuration[name] and configuration[name]["max_output_tokens"] > maximum:
            raise SkillsHubError(
                "invalid_test_config",
                "Output allowance exceeds the selected profile.",
                status=400,
            )
    budget = configuration["limits"]
    if not 0 < budget["max_total_tokens"] <= profile[
        "max_total_tokens"
    ] or not 0 < Decimal(budget["max_estimated_cost_usd"]) <= Decimal(
        limits["max_estimated_cost_usd"]
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Choose a positive spend and token stop within deployment limits.",
            status=400,
        )
