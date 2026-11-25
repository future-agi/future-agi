"""Reviewed decision-model catalog; importing it never loads EE or makes requests.

Snapshot: 2026-11-06, OpenRouter /models?endpoints=/api/alpha/decisions.
Question types: openrouter.ai/docs/guides/community/multimodal-decisions.
Native contracts: docs.typesafe.ai/models, developers.openai.com/api/docs/guides/
decisions, docs.perplexity.ai/docs/decisions/quickstart. Native IDs and router IDs
are deliberately different. A chat model with the same name stays a chat model.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from evaluations.types import DecisionModelGroup


@dataclass(frozen=True)
class DecisionModel:
    id: str
    group: str
    label: str
    author: str
    connection: str
    upstream_model: str
    accepted_models: tuple[str, ...]
    version: str = "versioned"
    question_types: tuple[str, ...] = ("noul", "choice", "score")
    min_choices: int = 1
    max_choices: int = 255
    max_state_bytes: int = 120000
    notice: str = "Text and JSON inputs only."
    unavailable_reason: str | None = None

    @property
    def gateway_model(self) -> str:
        return f"{self.connection}/{self.upstream_model}"

    def accepts_model(self, actual: object) -> bool:
        if not isinstance(actual, str):
            return False
        if actual in self.accepted_models:
            return True
        if self.version != "latest":
            return False
        # Choosing latest opts into newer versions of this family, not other
        # models (Decider must not become Flash; d1 must not become Ultra).
        patterns = {
            "~typesafe/jev-latest": r"typesafe/jev-\d+\.\d+(?:\.\d+)?(?:-\d{8})?",
            "~perplexity/pplx-decider-latest": r"perplexity/pplx-decider-v\d+(?:\.\d+)*(?:-\d{8})?",
            "~perplexity/pplx-decider-flash-latest": r"perplexity/pplx-decider-flash-v\d+(?:\.\d+)*(?:-\d{8})?",
            "~liquid/d1-latest": r"liquid/d1(?:\.\d+)*(?:-\d{8})?",
            "~liquid/d1-ultra-latest": r"liquid/d1-ultra(?:\.\d+)*(?:-\d{8})?",
        }
        candidate = (
            actual if self.connection == "openrouter" else f"{self.connection}/{actual}"
        )
        pattern = patterns.get(self.group)
        return bool(pattern and re.fullmatch(pattern, candidate))


# Route aliases are labelled as moving, never advertised as immutable pins.
# The two Clef text endpoints silently truncate upstream; use a conservative
# UTF-8 byte budget and refuse larger input rather than silently losing evidence.
_ROUTER_MODELS = (
    ("typesafe/jev-1.14", "Jev 1.14", "TypeSafe", "20261014"),
    ("typesafe/jev-1.13", "Jev 1.13", "TypeSafe", "20260917"),
    ("~typesafe/jev-latest", "Jev latest", "TypeSafe", None),
    ("openai/gpt-6-luna-decisions", "GPT-6 Luna Decisions", "OpenAI", "20261006"),
    ("perplexity/pplx-decider-v1.2", "Decider 1.2", "Perplexity", "20261016"),
    (
        "perplexity/pplx-decider-flash-v1.2",
        "Decider Flash 1.2",
        "Perplexity",
        "20261016",
    ),
    ("~perplexity/pplx-decider-latest", "Decider latest", "Perplexity", None),
    (
        "~perplexity/pplx-decider-flash-latest",
        "Decider Flash latest",
        "Perplexity",
        None,
    ),
    ("microsoft/microsoft-decision-1", "Microsoft Decision 1", "Microsoft", "20261009"),
    ("cloudflare/clef", "Clef", "Cloudflare", None),
    ("cloudflare/clef-flash", "Clef Flash", "Cloudflare", None),
    ("cloudflare/clef-omni", "Clef Omni", "Cloudflare", "20261009"),
    ("liquid/d1.1", "d1.1", "Liquid AI", "20261027"),
    ("liquid/d1", "d1", "Liquid AI", "20260930"),
    ("liquid/d1-ultra", "d1-ultra", "Liquid AI", "20261015"),
    ("~liquid/d1-latest", "d1 latest", "Liquid AI", None),
    ("~liquid/d1-ultra-latest", "d1-ultra latest", "Liquid AI", None),
    ("baseten/joey-1", "Joey 1", "Baseten", "20261029"),
    ("baseten/elephant-1", "Elephant 1", "Baseten", "20261021"),
    ("baseten/mammoth-1", "Mammoth 1", "Baseten", "20261021"),
    ("nace-ai/drex-max", "Drex Max", "Nace.AI", "20261016"),
    ("nace-ai/drex-v2.0", "Drex 2.0", "Nace.AI", "20261009"),
    ("nace-ai/drex-v1.5", "Drex 1.5", "Nace.AI", "20261009"),
    ("respan/span-02", "Span-02", "Respan", "20261015"),
    ("respan/span-01", "Span-01", "Respan", "20260925"),
    ("respan/span-01-lite", "Span-01 Lite", "Respan", "20260925"),
    ("respan/span-01-lite:free", "Span-01 Lite (free)", "Respan", None),
    ("upstage/solar-decide", "Solar Decide", "Upstage", "20260928"),
    ("upstage/solar-decide-flash", "Solar Decide Flash", "Upstage", "20261008"),
    ("inception/mercury-decide", "Mercury Decide", "Inception", "20260930"),
    ("morph/reflex", "Reflex", "Morph", "20261103"),
    ("scaledown/classify-1", "Classify-1", "ScaleDown", "20260928"),
    (
        "togethercomputer/tev1-4b-experimental",
        "Tev1 4B Experimental",
        "Together",
        "20260923",
    ),
    ("jaredpalmer/kev-4b", "Kev 4B", "Jared Palmer", "20260924"),
    ("parallel/decisions-fast:free", "Decisions Fast (free)", "Parallel", "20261106"),
)

_NATIVE = {
    "typesafe/jev-1.14": ("jev-1.14.0", "typesafe", "jev-1.14.0"),
    "typesafe/jev-1.13": ("jev-1.13.0", "typesafe", "jev-1.13.0"),
    "~typesafe/jev-latest": ("jev-latest", "typesafe", "jev-latest"),
    "openai/gpt-6-luna-decisions": (
        "decision/openai/gpt-6-luna",
        "openai",
        "gpt-6-luna",
    ),
    **{
        group: (f"decision/perplexity/{upstream}", "perplexity", upstream)
        for group, upstream in (
            ("perplexity/pplx-decider-v1.2", "pplx-decider-v1.2"),
            ("perplexity/pplx-decider-flash-v1.2", "pplx-decider-flash-v1.2"),
            ("~perplexity/pplx-decider-latest", "pplx-decider-latest"),
            ("~perplexity/pplx-decider-flash-latest", "pplx-decider-flash-latest"),
        )
    },
}


_ALIAS_TARGETS = {
    "~typesafe/jev-latest": ("typesafe/jev-1.14",),
    "~perplexity/pplx-decider-latest": ("perplexity/pplx-decider-v1.2",),
    "~perplexity/pplx-decider-flash-latest": ("perplexity/pplx-decider-flash-v1.2",),
    "~liquid/d1-latest": ("liquid/d1.1",),
    "~liquid/d1-ultra-latest": ("liquid/d1-ultra",),
}


def _catalog():
    rows = []
    for group, label, author, date in _ROUTER_MODELS:
        moving = group.startswith("~")
        accepted = (group, f"{group.partition(':')[0]}-{date}") if date else (group,)
        if group == "respan/span-01-lite:free":
            accepted = (group, "respan/span-01-lite-20260925")
        options = {}
        if group == "inception/mercury-decide":
            # Live decision calls and the public model lookup returned 404 on
            # 2026-11-08. Keep saved IDs readable, but never offer a broken run.
            options["unavailable_reason"] = (
                "No active OpenRouter endpoint. Choose another decision model."
            )
        if group.startswith("respan/span-01"):
            # Native Span-01 is a binary behavior scorer. Do not advertise
            # router-generated multi-class semantics as native model support.
            options["question_types"] = ("noul",)
        if group.startswith("togethercomputer/"):
            options.update(min_choices=2, max_choices=24)
        if group == "jaredpalmer/kev-4b":
            options.update(max_state_bytes=24000)
        if group.startswith("baseten/"):
            options.update(max_state_bytes=48000)
        if group in ("cloudflare/clef", "cloudflare/clef-flash"):
            options.update(
                max_state_bytes=2000,
                notice="Text/JSON only; maximum 2,000 UTF-8 bytes to avoid upstream text truncation.",
            )
        if group.endswith(":free") or group == "respan/span-01-lite":
            options["notice"] = (
                "Provider inference is free; FutureAGI evaluation charges still apply. Text/JSON only."
            )
        if group.startswith("parallel/"):
            options["notice"] = (
                "Researches the live web. Inputs may be used in web queries. "
                "Provider inference is free; FutureAGI evaluation charges still apply. Text/JSON only."
            )
        if moving:
            accepted = tuple(
                value
                for candidate, _, _, stamp in _ROUTER_MODELS
                if candidate in _ALIAS_TARGETS[group]
                for value in (
                    (candidate, f"{candidate}-{stamp}") if stamp else (candidate,)
                )
            )
        rows.append(
            DecisionModel(
                id=f"decision/openrouter/{group}",
                group=group,
                label=label,
                author=author,
                connection="openrouter",
                upstream_model=group,
                accepted_models=accepted,
                version="latest" if moving else "versioned",
                **options,
            )
        )
        if group in _NATIVE:
            identifier, connection, upstream = _NATIVE[group]
            native_accepted = (upstream,)
            if moving:
                # Some native APIs report the requested moving alias instead
                # of a resolved checkpoint (verified Perplexity, 2026-11-08).
                native_accepted = (upstream,) + tuple(
                    values[2]
                    for key, values in _NATIVE.items()
                    if key in _ALIAS_TARGETS[group]
                )
            rows.append(
                DecisionModel(
                    id=identifier,
                    group=group,
                    label=label,
                    author=author,
                    connection=connection,
                    upstream_model=upstream,
                    accepted_models=native_accepted,
                    version="latest" if moving else "versioned",
                    **options,
                )
            )
    return {row.id: row for row in rows}


DECISION_MODELS = _catalog()
CONNECTION_LABELS = {
    "typesafe": "TypeSafe",
    "openai": "OpenAI",
    "perplexity": "Perplexity",
    "openrouter": "OpenRouter",
}


def get_decision_model(model: object) -> DecisionModel | None:
    return DECISION_MODELS.get(model) if isinstance(model, str) else None


def is_decision_model(model: object) -> bool:
    return get_decision_model(model) is not None


def uses_jev_billing(model: object) -> bool:
    """Jev keeps its flat-fee policy even when reached through OpenRouter."""
    spec = get_decision_model(model)
    return spec is not None and spec.author == "TypeSafe"


def has_decision_prefix(model: object) -> bool:
    return isinstance(model, str) and model.lower().startswith(("decision/", "jev-"))


def model_groups(
    configured_providers: Collection[str] = (),
) -> list[DecisionModelGroup]:
    """One picker row per model, with explicit (never automatic) connections."""
    groups: dict[str, DecisionModelGroup] = {}
    for model in DECISION_MODELS.values():
        group = groups.setdefault(
            model.group,
            {
                "model_name": model.group,
                "label": model.label,
                "author": model.author,
                "version": model.version,
                "question_types": list(model.question_types),
                "min_choices": model.min_choices,
                "max_choices": model.max_choices,
                "max_state_bytes": model.max_state_bytes,
                "notice": model.notice,
                "unavailable_reason": model.unavailable_reason,
                "connections": [],
                "type": "decision",
            },
        )
        group["connections"].append(
            {
                "model_name": model.id,
                "provider": model.connection,
                "label": CONNECTION_LABELS[model.connection],
                "is_available": model.connection in configured_providers,
            }
        )
    for group in groups.values():
        group["connections"].sort(
            key=lambda c: (c["provider"] == "openrouter", c["label"])
        )
    return list(groups.values())
