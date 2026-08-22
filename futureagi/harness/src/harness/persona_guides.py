"""The behaviour guidance the platform already uses for a simulated caller.

A persona profile names what somebody is like: impatient and direct, cautious and skeptical. It
does not say how that should sound turn by turn, and a model handed only the label improvises
one, which is how "in a hurry" became a caller who says it every turn instead of a caller who
cuts in once and accepts the first workable answer.

The platform solved that with lookup tables mapping each value to a sentence of guidance, and
voice simulation has run on them for months. They are read from there rather than restated here,
because two copies of the same wording drift and then a caller behaves one way on the platform
and another way through the harness, for reasons nobody can see.

Read, not imported: the tables live inside a Django app this package cannot import, but they are
plain literals, so they are parsed out of the file. Absent, every lookup answers with nothing and
a persona still renders — one without guidance, never a crash.
"""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from pathlib import Path

# Where the platform's tables are mounted. Colon-separated so voice and chat guides can both be
# offered; the first file defining a table wins, so voice takes precedence when both are present.
GUIDES_ENV = "HARNESS_PERSONA_GUIDES"

WANTED = (
    "VOICE_PERSONALITY_GUIDES",
    "VOICE_COMMUNICATION_STYLE_GUIDES",
    "CHAT_PERSONALITY_GUIDES",
    "CHAT_COMMUNICATION_STYLE_GUIDES",
    "CHAT_TONE_GUIDES",
    "CHAT_VERBOSITY_GUIDES",
)


def _tables_in(path: Path) -> dict[str, dict[str, str]]:
    """Every guidance table defined in one file, by name.

    Parsed rather than executed. The file sits in an app with imports this process cannot
    satisfy, and running it to read a dictionary would fail for reasons that have nothing to do
    with the dictionary.
    """
    found: dict[str, dict[str, str]] = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return found
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        )
        for target in targets:
            name = getattr(target, "id", "")
            if name not in WANTED or node.value is None:
                continue
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                continue
            if isinstance(value, dict) and value:
                found[name] = {str(k).lower(): str(v) for k, v in value.items()}
    return found


@lru_cache(maxsize=1)
def guides() -> dict[str, dict[str, str]]:
    """Every table the platform offers this harness, merged."""
    merged: dict[str, dict[str, str]] = {}
    for raw in (os.environ.get(GUIDES_ENV) or "").split(":"):
        if not raw.strip():
            continue
        for name, table in _tables_in(Path(raw.strip())).items():
            merged.setdefault(name, table)
    return merged


def guidance_for(kind: str, value: str, *, voice: bool = True) -> str:
    """The platform's sentence for one persona value, or nothing.

    ``kind`` is ``personality``, ``communication_style``, ``tone`` or ``verbosity``. Voice tables
    are preferred for a spoken call and the chat table is the fallback, because the two describe
    the same disposition and only one of them is written for speech.
    """
    if not value.strip():
        return ""
    tables = guides()
    order = ("VOICE", "CHAT") if voice else ("CHAT", "VOICE")
    for prefix in order:
        table = tables.get(f"{prefix}_{kind.upper()}_GUIDES") or {}
        found = table.get(value.strip().lower())
        if found:
            return found
    return ""


def available() -> bool:
    """Whether any guidance was found, so a build can say so rather than silently omitting it."""
    return bool(guides())


# Where the platform's persona model is mounted, for the values it accepts.
VOCABULARY_ENV = "HARNESS_PERSONA_VOCABULARY"

# The persona fields worth constraining, and the choice class each is drawn from. Only the ones
# that change behaviour or routing: a free-text occupation harms nothing, an accent nobody
# recognises silently loses the voice it was supposed to select.
FIELDS = {
    "gender": "GenderChoices",
    "age_group": "AgeGroupChoices",
    "occupation": "ProfessionChoices",
    "location": "LocationChoices",
    "personality": "PersonalityChoices",
    "communication_style": "CommunicationStyleChoices",
    "accent": "AccentChoices",
    "languages": "LanguageChoices",
}

# Constrained because something downstream reads them. The rest are offered as vocabulary but a
# writer who needs a value outside them is not stopped: an unknown occupation costs nothing,
# an unknown accent costs the voice.
ENFORCED = ("personality", "communication_style", "accent", "languages")


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, list[str]]:
    """What the platform accepts for each persona field.

    Parsed out of the model's ``TextChoices`` classes for the same reason the guidance is read
    rather than restated: the platform is the one that has to understand these values, so it is
    the one that decides what they are. A persona written in words of its own renders fine, gets
    no behaviour guidance, and cannot be grouped with anything on the platform afterwards.
    """
    path = os.environ.get(VOCABULARY_ENV) or ""
    if not path or not Path(path).exists():
        return {}
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    by_class: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        values: list[str] = []
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            try:
                held = ast.literal_eval(item.value)
            except ValueError:
                continue
            # ``NAME = "value", "Label"`` is the choices shape; a bare string is also accepted.
            if isinstance(held, tuple) and held and isinstance(held[0], str):
                values.append(held[0])
            elif isinstance(held, str):
                values.append(held)
        if values:
            by_class[node.name] = values

    return {
        field: by_class[cls] for field, cls in FIELDS.items() if by_class.get(cls)
    }


def offered(field: str) -> list[str]:
    """The values this field accepts, or nothing if the platform's model was not readable."""
    return list(vocabulary().get(field, []))


def unrecognised(persona: dict[str, object]) -> list[str]:
    """Persona values the platform would not recognise, as sentences saying what to use instead.

    Only the fields something downstream actually reads, and only when the vocabulary was found:
    a harness that cannot see the platform's model must not start refusing personas over it.
    """
    known = vocabulary()
    if not known:
        return []
    problems: list[str] = []
    for field in ENFORCED:
        allowed = known.get(field) or []
        if not allowed:
            continue
        held = persona.get(field)
        values = held if isinstance(held, list) else ([held] if held else [])
        lowered = {str(one).strip().lower() for one in allowed}
        for one in values:
            text = str(one).strip()
            if text and text.lower() not in lowered:
                problems.append(
                    f"persona {field} {text!r} is not one the platform knows, so it will not "
                    f"reach the call. Use one of: {', '.join(allowed)}. Anything else this "
                    "person is like belongs in persona.metadata."
                )
    return problems
