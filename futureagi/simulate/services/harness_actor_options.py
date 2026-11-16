"""What the actor editor may offer and pick from, read from where the platform already holds each value set: the persona
model's choices for how an actor sounds, the sealed rules for how it is played, how loud it is and its limits' defaults,
and the runtime's own words for a fallback. Writes are checked against the same lists."""

from __future__ import annotations

from typing import Literal, TypedDict

from django.db import models

from simulate.models.persona import Persona
from simulate.services.harness_actor_labels import FALLBACK_WORDS
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    LIMIT_FIELDS,
    UPLOAD_KINDS,
    UPLOAD_MAX_BYTES,
    ActorRecord,
    ActorRules,
    real_of,
    text_of,
)
from simulate.services.harness_actor_validation import Problem, problem


class FieldOption(TypedDict):
    value: str
    label: str


class FieldOptions(TypedDict):
    """Each pickable field's values, `[{value, label}]`; an empty value is "any" for the voice fields."""

    kind: list[FieldOption]
    play: list[FieldOption]
    fallback_word: list[FieldOption]
    gender: list[FieldOption]
    age_group: list[FieldOption]
    accent: list[FieldOption]
    response_timing: list[FieldOption]


class LoudnessView(TypedDict):
    """Which channels have a loudness, the range a call draws from when none is set, and the range one may be set in."""

    channels: list[str]
    automatic: list[float]
    min: float
    max: float
    step: float
    # The value a fixed loudness starts at: the middle of the automatic range.
    default: float


class LimitField(TypedDict):
    key: str
    kind: Literal["whole", "seconds", "model"]
    # What a limit left unset takes, from the sealed rules; null for a bundle that seals none.
    default: int | float | str | None
    default_label: str | None


class UploadView(TypedDict):
    """What a person may upload as a document an actor reads."""

    formats: list[str]
    max_bytes: int
    max_label: str


# The kinds the editor offers when the sealed schema lists none; any other kind a builder writes is kept and shown.
ACTOR_KINDS = {
    "person": "A person",
    "approver": "An approver",
    "agent": "Another agent",
    "system": "A system",
}
PLAY_LABELS = {
    "mocked": "Only the actions each scenario gives them",
    "live": "Decide their own actions when reached",
    "both": "Both",
}
TIMING_LABELS = {"immediate": "Straight away", "delayed": "After a delay"}
# A fixed loudness is picked in steps of this share of the caller's voice.
LOUDNESS_STEP = 0.05
# The persona fields an actor's voice is picked from.
_VOICE_FIELDS = ("gender", "age_group", "accent")


def _properties(
    rules: ActorRules, definition: str | None = None
) -> dict[str, dict[str, object]]:
    schema = rules["records"][ACTORS_FILE]
    block = (
        ((schema.get("$defs") or {}).get(definition) or {}) if definition else schema
    )
    return block.get("properties") or {}


def _listed(
    rules: ActorRules, name: str, labels: dict[str, str], definition: str | None = None
) -> list[FieldOption]:
    """A field's values as the sealed schema lists them, each with its label; the labelled ones when it lists none."""
    listed = (_properties(rules, definition).get(name) or {}).get("enum")
    values = [str(one) for one in listed] if isinstance(listed, list) else list(labels)
    return [
        {"value": one, "label": labels.get(one, one.replace("_", " ").capitalize())}
        for one in values
    ]


def _choices(choices: type[models.TextChoices]) -> list[FieldOption]:
    return [
        {"value": str(value), "label": str(label)} for value, label in choices.choices
    ]


def field_options(rules: ActorRules) -> FieldOptions:
    return {
        "kind": _listed(rules, "kind", ACTOR_KINDS),
        "play": _listed(rules, "play", PLAY_LABELS),
        "fallback_word": [
            {"value": value, "label": label} for value, label in FALLBACK_WORDS.items()
        ],
        "gender": _choices(Persona.GenderChoices),
        "age_group": _choices(Persona.AgeGroupChoices),
        "accent": _choices(Persona.AccentChoices),
        "response_timing": _listed(rules, "kind", TIMING_LABELS, "ResponseTiming"),
    }


def loudness_view(rules: ActorRules) -> LoudnessView:
    sealed = rules.get("loudness") or {}
    automatic = [
        one
        for one in (real_of(value) for value in sealed.get("automatic") or [])
        if one is not None
    ]
    bound = _properties(rules).get("loudness_ratio") or {}
    # An optional ratio is "a number or null": its bounds sit on the number's branch.
    branches = bound.get("anyOf")
    number = next(
        (
            one
            for one in (branches if isinstance(branches, list) else [bound])
            if isinstance(one, dict) and one.get("type") != "null"
        ),
        {},
    )
    lowest = real_of(number.get("minimum"))
    above = real_of(number.get("exclusiveMinimum"))
    low = lowest if lowest is not None else (above or 0.0) + LOUDNESS_STEP
    high = real_of(number.get("maximum")) or 1.0
    middle = sum(automatic) / len(automatic) if automatic else (low + high) / 2
    return {
        "channels": [str(one) for one in sealed.get("channels") or []],
        "automatic": automatic,
        "min": round(low, 3),
        "max": high,
        "step": LOUDNESS_STEP,
        "default": min(
            high, max(low, round(round(middle / LOUDNESS_STEP) * LOUDNESS_STEP, 3))
        ),
    }


def _default_label(kind: str, value: object) -> str:
    if value is None:
        return "Default: the caller's model" if kind == "model" else "Default: no limit"
    if kind == "seconds":
        number = real_of(value) or 0.0
        return f"Default: {int(number) if number == int(number) else number} s"
    return f"Default: {value}"


def _sealed_default(entry: object, modality: str | None) -> tuple[bool, object]:
    """Whether a sealed limit default says a value, and the value, for the agent's modality: its own when the release
    sealed one for it, else the shared default."""
    if not isinstance(entry, dict):
        return False, None
    by_modality = entry.get("by_modality")
    if modality and isinstance(by_modality, dict) and modality in by_modality:
        return True, by_modality[modality]
    return "default" in entry, entry.get("default")


def limit_fields(rules: ActorRules, modality: str | None) -> list[LimitField]:
    """How each limit is typed and, when the sealed rules say, what one left unset takes for this agent's modality
    (`limit_defaults`: `{key: {default, by_modality: {modality: value}}}`); null for a bundle that seals none."""
    sealed = rules.get("limit_defaults")
    defaults = sealed if isinstance(sealed, dict) else {}
    fields: list[LimitField] = []
    for key, type_ in LIMIT_FIELDS.items():
        kind: Literal["whole", "seconds", "model"] = (
            "whole" if type_ is int else "seconds" if type_ is float else "model"
        )
        given, value = _sealed_default(defaults.get(key), modality)
        if not isinstance(value, (int, float, str, type(None))) or isinstance(
            value, bool
        ):
            given, value = False, None
        fields.append(
            {
                "key": key,
                "kind": kind,
                "default": value if given else None,
                "default_label": _default_label(kind, value) if given else None,
            }
        )
    return fields


def size_label(size: int) -> str:
    """A byte count as a person reads it: 1 MB, 512 KB."""
    if size >= 1024 * 1024 and size % (1024 * 1024) == 0:
        return f"{size // (1024 * 1024)} MB"
    return f"{size // 1024} KB"


def upload_view() -> UploadView:
    return {
        "formats": list(UPLOAD_KINDS["knowledge"]["formats"]),
        "max_bytes": UPLOAD_MAX_BYTES,
        "max_label": size_label(UPLOAD_MAX_BYTES),
    }


def option_problems(
    before: ActorRecord | None, after: ActorRecord, rules: ActorRules
) -> list[Problem]:
    """A picked value this edit sets that is not one the editor offers; a value stored before is kept as it is."""
    options = field_options(rules)
    offered: dict[str, list[FieldOption]] = {
        "kind": options["kind"],
        "gender": options["gender"],
        "age_group": options["age_group"],
        "accent": options["accent"],
    }
    old_persona = (before or {}).get("persona")
    new_persona = after.get("persona")
    picked = [
        (
            name,
            text_of(new_persona.get(name) if isinstance(new_persona, dict) else None),
            text_of(old_persona.get(name) if isinstance(old_persona, dict) else None),
        )
        for name in _VOICE_FIELDS
    ]
    picked.append(
        ("kind", text_of(after.get("kind")), text_of((before or {}).get("kind")))
    )
    return [
        problem(
            name, f"Pick one of: {', '.join(one['label'] for one in offered[name])}."
        )
        for name, value, old in picked
        if value
        and value != old
        and value not in {one["value"] for one in offered[name]}
    ]
