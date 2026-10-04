"""Validate saved and effective template fields before any gateway request."""

import copy
import hashlib
import json

from ee.jev.models import MAPPING_REVISION, JevMapping, JevQuestion


class JevMappingError(ValueError):
    def __init__(self, code, field, detail, details=None):
        self.code, self.field, self.detail = code, field, detail
        self.details = details or {field: [code]}
        super().__init__(f"{code}: {detail}")


def _reject(code, field, detail="This configuration is not supported by Jev."):
    raise JevMappingError(code, field, detail)


def _text(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def validate_jev_mapping(template_like, *, output, choice_scores, multi_choice, config):
    """C1..C17, C20: inspect effective fields, never infer compatibility by model."""
    eval_type = config.get("eval_type_id", "CustomPromptEvaluator")
    if eval_type not in (
        "CustomPromptEvaluator",
        "AgentEvaluator",
        "JevEvaluator",
    ) or config.get("function_eval"):
        _reject("JEV_EVAL_TYPE_UNSUPPORTED", "eval_type_id")
    if multi_choice or config.get("multi_choice"):
        _reject("JEV_MULTI_CHOICE_UNSUPPORTED", "multi_choice")
    if config.get("few_shot_examples") or config.get("ground_truth_few_shot"):
        _reject("JEV_FEW_SHOT_UNSUPPORTED", "few_shot_examples")
    if (
        config.get("agent_mode", "agent" if eval_type == "AgentEvaluator" else "quick")
        != "quick"
    ):
        _reject("JEV_AGENT_MODE_UNSUPPORTED", "agent_mode")
    incompatible = {}
    for keys, code, field in [
        (("tools",), "JEV_TOOLS_UNSUPPORTED", "tools"),
        (
            ("knowledge_base_id", "knowledge_bases"),
            "JEV_KNOWLEDGE_BASE_UNSUPPORTED",
            "knowledge_bases",
        ),
        (("check_internet",), "JEV_INTERNET_UNSUPPORTED", "check_internet"),
    ]:
        if any(config.get(key) for key in keys):
            incompatible[field] = [code]
    if incompatible:
        field = next(iter(incompatible))
        raise JevMappingError(
            incompatible[field][0],
            field,
            "Jev cannot use tools, retrieval, or internet access.",
            incompatible,
        )
    injection = config.get("data_injection") or {}
    if not isinstance(injection, dict) or any(
        (
            value is not True
            if key in ("variables_only", "variablesOnly")
            else bool(value)
        )
        for key, value in injection.items()
    ):
        _reject("JEV_DATA_INJECTION_UNSUPPORTED", "data_injection")
    modalities = config.get("input_data_types") or {}
    if not isinstance(modalities, dict) or any(
        str(v).lower() not in ("text", "json", "string", "object", "array")
        for v in modalities.values()
    ):
        _reject("JEV_INPUT_UNSUPPORTED", "input_data_types")

    raw = config.get("jev_mapping")
    raw = {} if raw is None else copy.deepcopy(raw)
    allowed = {
        "revision",
        "question_type",
        "pass",
        "choice",
        "score",
        "include_messages",
        "content_hash",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) - allowed
        or raw.get("revision", MAPPING_REVISION) != MAPPING_REVISION
    ):
        _reject("JEV_MAPPING_INVALID", "jev_mapping")
    if not isinstance(raw.get("include_messages", False), bool):
        _reject("JEV_MAPPING_INVALID", "jev_mapping.include_messages")
    if any(
        message.get("role") != "system" and message.get("content")
        for message in config.get("messages") or []
    ) and not raw.get("include_messages"):
        _reject("JEV_MESSAGES_REQUIRE_CONFIRMATION", "jev_mapping.include_messages")
    question_type = {"Pass/Fail": "noul", "choices": "choice", "score": "score"}.get(
        output
    )
    if choice_scores and output != "Pass/Fail":
        question_type = "choice"
    if not question_type or raw.get("question_type", question_type) != question_type:
        _reject("JEV_MAPPING_INVALID", "jev_mapping.question_type")
    for name, keys in [
        ("pass", {"criteria_true", "criteria_false", "threshold_source"}),
        ("choice", {"labels", "descriptions"}),
        ("score", {"levels"}),
    ]:
        section = raw.get(name)
        if section is not None and (
            not isinstance(section, dict) or set(section) - keys
        ):
            _reject("JEV_MAPPING_INVALID", f"jev_mapping.{name}")
    pass_config = raw.get("pass") or {}
    if pass_config.get("threshold_source", "effective") != "effective":
        _reject("JEV_MAPPING_INVALID", "jev_mapping.pass.threshold_source")
    for key in ("criteria_true", "criteria_false"):
        value = pass_config.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 2000):
            _reject("JEV_MAPPING_INVALID", f"jev_mapping.pass.{key}")
    choice = raw.get("choice") or {}
    score = raw.get("score") or {}
    if question_type == "choice":
        labels = list(choice_scores) if isinstance(choice_scores, dict) else []
        if (
            not 1 <= len(labels) <= 255
            or any(not _text(label, 200) for label in labels)
            or len({label.strip().lower() for label in labels}) != len(labels)
        ):
            _reject("JEV_CHOICE_LABELS_INVALID", "choice_scores")
        if "labels" in choice and choice["labels"] != labels:
            _reject("JEV_CHOICE_LABELS_INVALID", "jev_mapping.choice.labels")
        descriptions = choice.get("descriptions", {})
        if (
            not isinstance(descriptions, dict)
            or set(descriptions) - set(labels)
            or any(
                value is not None and (not isinstance(value, str) or len(value) > 2000)
                for value in descriptions.values()
            )
        ):
            _reject("JEV_MAPPING_INVALID", "jev_mapping.choice.descriptions")
        choice = {"labels": labels, "descriptions": descriptions}
    if question_type == "score":
        if "levels" not in score:
            _reject(
                "JEV_SCORE_LEVELS_REQUIRED",
                "jev_mapping.score.levels",
                "Jev score requires explicit ordered rubric levels.",
            )
        levels = score["levels"]
        if (
            not isinstance(levels, list)
            or not 2 <= len(levels) <= 10
            or any(not _text(level, 2000) for level in levels)
            or len(set(levels)) != len(levels)
        ):
            _reject(
                "JEV_SCORE_LEVELS_INVALID",
                "jev_mapping.score.levels",
                "Jev score requires 2 to 10 unique non-empty rubric levels.",
            )
    return JevMapping(
        question_type, pass_config, choice, score, raw.get("include_messages", False)
    )


def build_question(mapping, rendered_instructions):
    parts = (
        rendered_instructions
        if isinstance(rendered_instructions, list)
        else [rendered_instructions]
    )
    if not parts or any(
        not isinstance(part, str) or not part.strip() for part in parts
    ):
        _reject(
            "JEV_MAPPING_INVALID", "rule_prompt", "Jev instructions must contain text."
        )
    if mapping.question_type == "noul":
        criteria = {
            key: mapping.pass_config[f"criteria_{key}"]
            for key in ("true", "false")
            if mapping.pass_config.get(f"criteria_{key}") is not None
        } or None
    elif mapping.question_type == "choice":
        criteria = {
            label: mapping.choice.get("descriptions", {}).get(label)
            for label in mapping.choice["labels"]
        }
    else:
        criteria = mapping.score["levels"]
    return (
        "q1",
        JevQuestion(mapping.question_type, rendered_instructions, criteria).to_dict(),
    )


def build_state(rendered_vars):
    def value(item):
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
                if isinstance(parsed, (dict, list)):
                    return parsed
            except ValueError:
                pass
        if not isinstance(item, (str, dict, list)):
            _reject("JEV_INPUT_UNSUPPORTED", "state")
        return item

    state = {key: value(item) for key, item in rendered_vars.items()}
    return next(iter(state.values())) if len(state) == 1 else state


def content_hash(mapping):
    canonical = json.dumps(
        mapping.canonical_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def mapping_revision(mapping):
    return f"{MAPPING_REVISION}:{content_hash(mapping)[:12]}"


def check_input_size(payload):
    from django.conf import settings

    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        state = payload.get("state", "")
        state_text = (
            state
            if isinstance(state, str)
            else json.dumps(state, ensure_ascii=False, allow_nan=False)
        )
    except (ValueError, TypeError):
        _reject("JEV_INPUT_UNSUPPORTED", "state")
    if len(body.encode()) > getattr(settings, "JEV_MAX_INPUT_BYTES", 256 * 1024) or len(
        state_text
    ) / 4 > getattr(settings, "JEV_MAX_STATE_TOKENS", 30000):
        _reject(
            "JEV_INPUT_TOO_LARGE",
            "state",
            "Jev input exceeds the configured size limit.",
        )
