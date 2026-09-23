"""Platform evals a hosted harness run selects for itself.

The guest returns eval names only; the mapping tables below are the sole mappings this path can
produce, so a model cannot bind a variable to a source that resolves empty. The harness's own
deterministic checkpoints do not come through here.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Q

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import RunTest, SimulateEvalConfig

logger = structlog.get_logger(__name__)

# Stable id so a retried provision cannot double the configs.
_SELECTED_EVAL_NAMESPACE = uuid.UUID("2b0f2f19-2c65-4b1e-9c9a-2f1a3b4c5d6e")

# Each selected eval is one judge call per call in the suite.
MOST_SELECTED_EVALS = 8


# M1 fix: a module-level sentinel populated on success only, rather than
# `@lru_cache(maxsize=1)`. The first attempt at this fix called
# `offerable_eval_names.cache_clear()` from inside the `except` block, right
# before `return frozenset()` — but `lru_cache`'s wrapper writes the *current*
# call's return value into its cache only *after* the wrapped function body
# returns, so clearing from inside the body cannot stop that write: the wrong
# `frozenset()` still got cached immediately after, one line later, outside
# this function's control. Proven by
# `test_an_unreadable_catalog_is_retried_not_memoised`, which failed against
# that version. A plain module global, written to only on the success branch,
# has no such moment where the caller loses control of the write.
_offerable_eval_names_cache: frozenset[str] | None = None


def offerable_eval_names() -> frozenset[str]:
    """The built-in eval names the catalog lists.

    Being a key in ``evaluations/catalog/system_evals.yaml`` is the only thing
    that makes a built-in eval offerable (catalog contract P3). Nothing stored
    marks a template as listed and no model field is added for it, so the key
    list is read from the file once per process and cached here; the rule never
    opens a YAML per request and never reads the legacy definitions at all
    (catalog contract F1). The path comes from the seeder rather than a second
    copy of it, so the two can never point at different files.

    Every dict entry counts, tagged or not: P3 is about the key list, while the
    seeder's ``_load_catalog_tags`` keeps only tagged entries because it is
    copying tags. A key with no tags is listed and then fails the tag gate.

    Only a *successful* read is cached (M1): a read failure returns an
    uncached `frozenset()` so the very next call retries the file instead of
    replaying one transient failure for the life of the worker.
    """
    global _offerable_eval_names_cache
    if _offerable_eval_names_cache is not None:
        return _offerable_eval_names_cache

    from model_hub.management.commands.seed_system_evals import CATALOG_YAML

    try:
        catalog = yaml.safe_load(CATALOG_YAML.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        # Mirrors the seeder's own tolerance. An unreadable catalog offers
        # nothing, which is visible in the logs and in an empty picker, rather
        # than taking a launch down. Deliberately not stored in
        # `_offerable_eval_names_cache`: caching this would memoise the
        # failure for the life of the process and every later provision would
        # 400 forever over one transient read failure (M1).
        #
        # `ValueError` alongside `OSError` (L5, round 3): `.read_text()` on a
        # truncated or mis-encoded catalog raises `UnicodeDecodeError`, a
        # `ValueError` subclass, not an `OSError` — the original tuple let a
        # bad-encoding catalog escape this handler and surface as a 500 on
        # `GET evaluations/available/` and `POST evaluations/` instead of the
        # documented empty offer.
        logger.exception("harness_eval_catalog_unreadable", path=str(CATALOG_YAML))
        return frozenset()
    if not isinstance(catalog, dict) or not catalog:
        # L6: "only a successful read is cached" is wider than it sounds. A
        # zero-byte or all-comments file parses to `{}` via the `or {}`
        # above — that is a *successful* read of nothing, and caching it
        # would reproduce M1's exact failure mode (every eval-picking
        # provision 400ing forever) through the success branch this fix
        # otherwise closed. A catalog that parses to a list or a scalar
        # keeps that value past `or {}` (which only substitutes for falsy
        # values, and a non-empty list is truthy) and would raise an
        # unhandled `AttributeError` from `.items()` below — a 500, not the
        # documented empty offer. Neither case is cached, so the next call
        # retries the file instead of being stuck with this one.
        logger.warning(
            "harness_eval_catalog_empty_or_invalid",
            path=str(CATALOG_YAML),
            catalog_type=type(catalog).__name__,
        )
        return frozenset()
    _offerable_eval_names_cache = frozenset(
        str(name) for name, entry in catalog.items() if isinstance(entry, dict)
    )
    return _offerable_eval_names_cache


# The agent kinds the contract names. Equal by definition to
# harness_environment.AGENT_TYPE_VOICE / AGENT_TYPE_CHAT; they are restated
# here rather than imported so this module keeps no dependency on the read
# model above it. A test asserts the two pairs are equal.
AGENT_KIND_VOICE = "voice"
AGENT_KIND_CHAT = "chat"

# A template is relevant to a kind of agent when it carries one of these tags
# (catalog contract P4). Matched case-insensitively on trimmed strings, the way
# the product's own eval list matches them. `Agents` is in both sets on
# purpose; the two lists differ by three evals today and that is accepted
# (design D3).
_RELEVANT_TAGS_VOICE = ("Conversation", "Voice", "Audio", "Agents")
_RELEVANT_TAGS_CHAT = ("Conversation", "Chatbot behaviors", "Agents")
_RELEVANT_TAGS = {
    AGENT_KIND_VOICE: frozenset(tag.lower() for tag in _RELEVANT_TAGS_VOICE),
    AGENT_KIND_CHAT: frozenset(tag.lower() for tag in _RELEVANT_TAGS_CHAT),
}

# A bound row stores the name in a 255-character column
# (SimulateEvalConfig.name) while a template name may be 2000, so a longer name
# is never offered (frontend contract P12).
_MOST_NAME_CHARACTERS = 255


# Which stored piece of a call fills each required key, per agent kind
# (design §4). These are the only keys that can be filled; an eval asking for
# anything else is not offered rather than offered and then refused.
_SOURCE_BY_KEY_VOICE = {
    # The combined recording; a per-channel mapping resolves empty on
    # combined-only providers.
    "conversation": "voice_recording",
    # Audio-analysing evals ask for the recording itself, not a transcript of it.
    "input_audio": "voice_recording",
    "audio": "voice_recording",
    # Changed from today, where voice `output` was the recording: a
    # single-output eval judges what was said, and the judge reads text. No
    # offered eval asks for `output` on voice, so no stored mapping migrates.
    "output": "transcript",
    "text": "transcript",
    "agent_prompt": "agent_prompt",
    "system_prompt": "agent_prompt",
    # A simulated call has no retrieval context, so the agent's own
    # instructions are what it can be judged against (design D8). This is what
    # brings `conversation_hallucination` into the offer.
    "context": "agent_prompt",
    "input": "scenario_columns.situation.value",
}
_SOURCE_BY_KEY_TEXT = {
    # A chat run has no recording, so the conversation is its transcript text.
    "conversation": "transcript",
    "output": "transcript",
    "text": "transcript",
    "agent_prompt": "agent_prompt",
    "system_prompt": "agent_prompt",
    "context": "agent_prompt",
    "input": "scenario_columns.situation.value",
    # `input_audio` and `audio` are deliberately absent: a chat run leaves no
    # recording behind, so an eval asking for one is not offered to it.
}

# The only text a picker shows for a source (frontend contract P1, F1). The
# frontend never computes this, so every source in the tables above must have
# an entry here.
_LABEL_BY_SOURCE = {
    "voice_recording": "Call recording",
    "transcript": "Transcript",
    "agent_prompt": "Agent instructions",
    "scenario_columns.situation.value": "Scenario situation",
}


def _sources_for(modality: str) -> dict[str, str]:
    return _SOURCE_BY_KEY_VOICE if modality == "voice" else _SOURCE_BY_KEY_TEXT


def _required_keys(template: EvalTemplate) -> list[str]:
    config = template.config or {}
    keys = config.get("required_keys")
    return [str(key) for key in keys] if isinstance(keys, list) else []


def _visible_templates(organization, workspace):
    """Templates this organization may select: its own plus system-owned, never another tenant's.

    Drafts are excluded here rather than at the offer gate, so no path can bind
    one: `visible_ui` false is what the create/edit form sets while an eval is
    still being written (design §3.3). Verified against the legacy YAMLs: none
    of the 21/22 offered names is `visible_ui: false`, so this removes nothing
    from the pinned lists.

    No explicit ``deleted=False`` here (L3): `no_workspace_objects` is
    `NoWorkspaceFilterManager`, whose `get_queryset` already filters
    `deleted=False` (`tfc/utils/base_model.py`), so a second copy of that
    filter on the queryset was redundant and, worse, made
    `test_a_deleted_template_is_never_offered` pass even with the copy
    deleted — it was never the line doing the work. Soft-delete exclusion is
    the manager's job; this function excludes only what the manager does not
    already exclude for it (workspace/organization scope, drafts).
    """
    visible_scope = Q(organization=organization) | Q(organization__isnull=True)
    workspace_scope = Q(workspace=workspace) | Q(workspace__isnull=True)
    return EvalTemplate.no_workspace_objects.filter(
        visible_scope, workspace_scope, visible_ui=True
    )


def resolve_eval_mapping(
    template: EvalTemplate, modality: str
) -> dict[str, str] | None:
    """The mapping for one template, or None to refuse it when a required key has no source."""
    sources = _sources_for(modality)
    keys = _required_keys(template)
    if not keys:
        return None
    mapping: dict[str, str] = {}
    for key in keys:
        source = sources.get(key)
        if source is None:
            return None
        mapping[key] = source
    return mapping


def agent_kind(modality: str) -> str:
    """The agent kind behind a modality.

    The code has said `voice` / `text` since the first harness run and the P9
    refusal string prints that word; the contract's entry says `voice` / `chat`.
    This is the one place the two vocabularies meet.
    """
    return AGENT_KIND_VOICE if modality == "voice" else AGENT_KIND_CHAT


def _has_relevant_tag(template: EvalTemplate, modality: str) -> bool:
    """Whether the stored tags say this eval is meant for this kind of agent."""
    wanted = _RELEVANT_TAGS[agent_kind(modality)]
    return any(
        str(tag or "").strip().lower() in wanted for tag in (template.eval_tags or [])
    )


def _is_listed_or_owned(
    template: EvalTemplate, organization: Any, offerable: frozenset[str]
) -> bool:
    """A built-in is offerable when the catalog lists it; a custom one when it is ours.

    Built-in means ``owner == "system"`` (design N13). For those the catalog's
    key list is the only switch (catalog P3, F2) — the 97 legacy templates it
    does not list are never offered, whatever their tags. For a custom eval the
    gate is tenancy: this organisation's own row, never another tenant's and
    never an ownerless one (catalog P6).

    ``organization`` must be an ``Organization`` instance or ``None`` (L8): a
    caller that hands this a bare id instead has no ``.id`` attribute to read,
    so ``getattr(organization, "id", None)`` used to answer ``None`` and every
    custom eval silently vanished from the offer with no error anywhere. A raw
    id is now a loud ``TypeError`` at the call site that made the mistake,
    rather than a quiet, wrong answer here.

    The type check runs before the system-owner branch (L8, round 2): a
    caller that passes a bare id against a *system* template used to return
    silently (the branch above never reads ``organization`` at all), so the
    guarantee only ever fired for the less common case of a caller reaching
    the custom branch with no custom evals in play. Checking first makes the
    guarantee unconditional, for one extra line.
    """
    if organization is not None and not hasattr(organization, "id"):
        raise TypeError(
            "_is_listed_or_owned expects an Organization instance or None, "
            f"got {type(organization)!r}"
        )
    if str(template.owner or "") == OwnerChoices.SYSTEM.value:
        return str(template.name or "") in offerable
    organization_id = getattr(organization, "id", None)
    return (
        organization_id is not None
        and template.organization_id is not None
        and str(template.organization_id) == str(organization_id)
    )


def _pick_winner(templates: list[EvalTemplate]) -> EvalTemplate | None:
    """Of several templates that would resolve to the same name, the one that wins.

    `EvalTemplate.name` carries no uniqueness constraint
    (`model_hub/models/evals_metric.py`), and the product lets a tenant
    create a custom eval that reuses a catalog eval's name — the duplicate
    endpoint's own collision check is scoped to the organisation's own rows
    (`model_hub/views/separate_evals.py`). Before this module made ownership
    a route into the offer, that collision was harmless because a custom
    eval was never offered at all; now every downstream lookup keys on the
    name, so two templates sharing one name must resolve to exactly the same
    row everywhere, or a pick made in the picker can bind a different
    template than the one the sandbox was shown (M1). The tenant's own row
    wins over a same-named system row — it is the more specific match, and a
    tenant that created a same-named row on purpose almost certainly means
    to use it (owner decision, 2026-09-24). This is the *one* place that
    decision is made: `offered_templates`, `add_selected_eval` (through
    `_resolve_named_template`) and `create_selected_eval_configs` all call
    through here, so they cannot disagree about which row a shared name
    means.

    L1: when two candidates are equally specific — both a tenant's own
    templates, or in principle both system rows — the newest `created_at`
    wins, rather than silently depending on the candidate list's own order
    (which, since round 2's L5 removed `.order_by("name")` from
    `_visible_templates`, is whatever `BaseModel.Meta.ordering` gives it).
    """
    winner: EvalTemplate | None = None
    for template in templates:
        if winner is None:
            winner = template
            continue
        winner_is_system = str(winner.owner or "") == OwnerChoices.SYSTEM.value
        template_is_system = str(template.owner or "") == OwnerChoices.SYSTEM.value
        if winner_is_system and not template_is_system:
            winner = template
        elif winner_is_system == template_is_system and (
            template.created_at > winner.created_at
        ):
            winner = template
    return winner


def _resolve_named_template(
    organization, workspace, name: str, offerable: frozenset[str]
) -> EvalTemplate | None:
    """The one template ``name`` resolves to for this tenant, or ``None``.

    Applies the same listed-or-owned gate `offered_templates` applies before
    grouping its candidates by name, then hands every visible, eligible
    candidate to `_pick_winner` (M1). Used by `add_selected_eval` so that
    adding a name by hand binds the same row `offered_templates` listed it
    under.
    """
    candidates = [
        template
        for template in _visible_templates(organization, workspace).filter(name=name)
        if _is_listed_or_owned(template, organization, offerable)
    ]
    return _pick_winner(candidates)


def offered_templates(
    organization, workspace, modality: str
) -> list[tuple[EvalTemplate, dict[str, str]]]:
    """Every eval this environment may be graded by, with its resolved inputs.

    The gates, in the order the code actually runs them (L4: `lld-2-offer-rule.puml`
    draws the name-length gate fourth of six, before "asks for at least one
    input" and the mapping gate — not last, as an earlier draft of this
    docstring claimed; design §3 does not draw it as a numbered gate at all,
    it appears only through catalog P5's cross-reference. Here it runs
    first, since it is the cheapest check and needs no query; the point of
    this note is that the diagram's drawn order and this function's actual
    order genuinely differ, not what that difference is measured against):
    the name fits the 255-character column a bound row stores it in; listed
    in the catalog (built-in) or this organisation's own (custom); carries a
    relevant tag for the agent kind; is not a draft and not deleted (both in
    the queryset); and every input it asks for can be filled for this kind.

    A name shared by more than one visible template — a tenant's own custom
    eval reusing a catalog eval's name, which nothing prevents (M1) — is
    listed exactly once, carrying the one row `_pick_winner` resolves it to.
    Templates are grouped by name before the tag and mapping gates run, so
    those gates are evaluated only for the winning row, never for a row that
    lost the resolution.

    Sorted by name, which is what `available` promises (frontend contract
    P6) — guaranteed here rather than left to the database's collation (L5),
    so the order is a property of this function.
    """
    offerable = offerable_eval_names()
    candidates_by_name: dict[str, list[EvalTemplate]] = {}
    for template in _visible_templates(organization, workspace):
        name = str(template.name or "")
        if len(name) > _MOST_NAME_CHARACTERS:
            continue
        if not _is_listed_or_owned(template, organization, offerable):
            continue
        candidates_by_name.setdefault(name, []).append(template)

    pairs: list[tuple[EvalTemplate, dict[str, str]]] = []
    for name, candidates in candidates_by_name.items():
        template = _pick_winner(candidates)
        if template is None:
            continue
        if not _has_relevant_tag(template, modality):
            continue
        mapping = resolve_eval_mapping(template, modality)
        if mapping is None:
            logger.warning(
                "harness_eval_not_offerable",
                template=name,
                required_keys=_required_keys(template),
                modality=modality,
            )
            continue
        pairs.append((template, mapping))
    # Sorted here rather than trusting the query's own order (L5): a DB's
    # collation and this function's Python sort only happen to agree today
    # because none of the pinned names has punctuation collation treats
    # specially, and grouping by name above no longer even asks for a
    # particular query order. Sorting again here, in Python, makes the order
    # a property of this function rather than an accident — proven
    # independent of the query's order by
    # `test_offered_templates_sorts_even_when_the_queryset_does_not`.
    pairs.sort(key=lambda pair: pair[0].name)
    return pairs


def offered_names(organization, workspace, modality: str) -> set[str]:
    """Just the names offered for one kind, for gating a name someone sends back."""
    return {
        str(template.name or "")
        for template, _mapping in offered_templates(organization, workspace, modality)
    }


def briefed_eval_names(organization, workspace) -> set[str]:
    """Every name the launch briefing carried: the union of both kinds.

    The sandbox picks from that briefing, so a pick is "unknown" (400
    `eval_selection_unknown`, harness contract P7) only when it was not in the
    union. A pick the briefing did carry but which this run cannot fill is
    dropped with a warning instead (P8).
    """
    return offered_names(organization, workspace, "voice") | offered_names(
        organization, workspace, "text"
    )


# The flat cost of one eval run, whatever the eval (design D10). Nothing is
# free: every run costs this, and a judged eval — `eval_type` `llm` or
# `agent` — additionally charges the judge's tokens on top of it;
# `charges_judge_tokens` on the entry says which is which.
EVAL_RUN_CREDITS = 0.5


def eval_entry(
    template: EvalTemplate, mapping: dict[str, str], modality: str
) -> dict[str, Any]:
    """One eval in the one list format the harness, the picker and the detail share.

    `required_keys` is the template's stored order; `inputs` is sorted by key.
    The two are deliberately not aligned — pair them by `key`, never by
    position (frontend contract P1). `credits_per_run` is always
    `EVAL_RUN_CREDITS`: nothing is free. `charges_judge_tokens` is derived,
    not stored: a code eval is judged by code the platform runs and asks no
    model; an `llm` or `agent` eval sends the transcript to a judge and is
    billed for its tokens on top of the flat run cost (design D10).

    `label` falls back to the source name for a mapping stored before this
    change: the detail builds entries from what is bound, and a row bound under
    the old voice table can carry a source this table no longer produces.
    """
    return {
        "name": str(template.name or ""),
        "description": str(template.description or "")[:500],
        "source": (
            "system"
            if str(template.owner or "") == OwnerChoices.SYSTEM.value
            else "custom"
        ),
        "tags": [str(tag) for tag in (template.eval_tags or [])],
        "required_keys": _required_keys(template),
        "agent_type": agent_kind(modality),
        "modality": modality,
        "credits_per_run": EVAL_RUN_CREDITS,
        "charges_judge_tokens": str(template.eval_type or "") in ("llm", "agent"),
        "inputs": [
            {
                "key": key,
                "source": mapping[key],
                "label": _LABEL_BY_SOURCE.get(mapping[key], mapping[key]),
            }
            for key in sorted(mapping)
        ],
    }


def offered_evals(organization, workspace, modality: str) -> list[dict[str, Any]]:
    """The catalogue for one agent kind, as entries, sorted by name."""
    return [
        eval_entry(template, mapping, modality)
        for template, mapping in offered_templates(organization, workspace, modality)
    ]


# The fields the sandbox reads, and nothing else. `name`, `description`,
# `required_keys` and `modality` are the unchanged four its validator and its
# authoring prompt read; `source`, `tags`, `credits_per_run` and
# `charges_judge_tokens` are new and informational (harness contract P2, F2,
# F3).
_BRIEFING_FIELDS = (
    "name",
    "description",
    "required_keys",
    "modality",
    "source",
    "tags",
    "credits_per_run",
    "charges_judge_tokens",
)


def _briefing_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One entry as the sandbox sees it: no agent kind, no resolved inputs.

    The sandbox never resolves inputs and never sends a mapping; the platform
    owns it (harness contract F1).
    """
    return {field: entry[field] for field in _BRIEFING_FIELDS}


def briefing_evals(
    organization, workspace, modality: str = ""
) -> list[dict[str, Any]]:
    """What goes into the job's briefing under ``metadata.available_evals``.

    Before a contract exists the agent kind is unknown, so the union of both
    lists travels and a name both lists offer is marked ``modality: "any"``,
    which the sandbox's validator accepts for either kind. Offering each name
    twice, once per modality, is what made a guest reject its own correct
    choices on run a5ffa58d, so a name appears exactly once.
    """
    if modality in ("voice", "text"):
        return [
            _briefing_entry(entry)
            for entry in offered_evals(organization, workspace, modality)
        ]
    by_name: dict[str, dict[str, Any]] = {}
    for kind_modality in ("voice", "text"):
        for entry in offered_evals(organization, workspace, kind_modality):
            name = str(entry["name"])
            if name in by_name:
                by_name[name] = {**by_name[name], "modality": "any"}
                continue
            by_name[name] = _briefing_entry(entry)
    return [by_name[name] for name in sorted(by_name)]


class UnknownEvalSelection(Exception):
    """A chosen name is not a template this organization can see."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(", ".join(names))


class EvalSelectionRefused(Exception):
    """One name this run cannot bind, carrying the reason to report.

    Separate from :class:`UnknownEvalSelection`, which the guest raises over a
    whole batch it authored. A person adding a single eval needs to know which
    of the ways it was refused, since two of them are fixable by choosing a
    different eval and one is not.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"{name}: {reason}")


class EvalSelectionFull(EvalSelectionRefused):
    """The run already binds as many evals as it is allowed to."""


# Agent templates name no model and the default cannot read audio. Overridable per deployment.
FALLBACK_EVAL_MODEL = "turing_large"


def bind_eval_config(
    run_test: RunTest, template: EvalTemplate, mapping: dict[str, str]
) -> SimulateEvalConfig:
    """Bind one template to one run, reviving a binding that was removed before.

    The id is derived from the run and the template, so a name chosen again
    after being removed lands on the row that already exists. That row is soft
    deleted and therefore invisible to the default manager, which would create
    rather than find it and collide on the primary key.
    """
    config_id = uuid.uuid5(_SELECTED_EVAL_NAMESPACE, f"{run_test.id}:{template.id}")
    existing = SimulateEvalConfig.all_objects.filter(id=config_id).first()
    if existing is None:
        return SimulateEvalConfig.objects.create(
            id=config_id,
            eval_template=template,
            name=str(template.name),
            config=template.config or {},
            mapping=mapping,
            run_test=run_test,
            filters={},
            model=template.model
            or getattr(settings, "HARNESS_EVAL_MODEL", FALLBACK_EVAL_MODEL),
        )
    if existing.deleted:
        existing.deleted = False
        existing.deleted_at = None
        # A revived binding restates its mapping rather than trusting the one
        # the first binding stored: the run's modality decides the sources, and
        # a contract authored since then can have changed it.
        existing.mapping = mapping
        existing.save(update_fields=["deleted", "deleted_at", "mapping", "updated_at"])
    return existing


def _tenant_known_names(organization, workspace, names: set[str]) -> set[str]:
    """Of ``names``, the ones this tenant could ever have recognised — in any state.

    Used only to classify a name the *current* offer no longer carries (M3):
    was it ever something real for this tenant — a catalog key, or one of its
    own custom templates, whatever its current draft/deleted/tag state — or
    was it never anything this tenant could have been offered at all? The
    dispatched launch briefing (`metadata.available_evals`) is never
    persisted back onto the job: `HostedHarnessGateway.launch` saves
    `job.payload` (`hosted_harness_gateway.py`, the `job.save(update_fields=
    ["payload", ...])` call) *before* `prepare_dispatch_payload` adds
    `available_evals` to a local `dispatch_payload` that is sent to the
    sandbox and then discarded, so what was actually sent cannot be read back
    here. This is the fallback the briefing not being retrievable forces: a
    name that is not a catalog key and never had a template of this tenant's
    own is genuinely unknown (harness P7, a 400); a name that is a catalog
    key or was once one of this tenant's own templates — but has since been
    drafted, untagged or deleted — is stale, not unknown, and is dropped with
    a warning instead of failing the whole provision.

    A system-owned template that is not a catalog key was never real (the 97
    legacy templates the catalog does not list), so only custom (non-system)
    template rows are searched here; the catalog-key half of "known" is
    `offerable_eval_names()` itself, checked separately below.

    Uses ``EvalTemplate.all_objects`` rather than the ``no_workspace_objects``
    manager every other function in this module uses (L1): this lookup must
    still see a soft-deleted row (a name is "known" even if the row backing
    it was deleted since launch), and there is no manager that both skips
    the soft-delete filter and skips workspace-context filtering. That makes
    this the one query in the module sensitive to ``get_current_workspace()``:
    ``AllObjectsManager.get_queryset`` (`tfc/utils/base_model.py`) narrows to
    ``filter(workspace=current_workspace)`` under a non-default workspace
    context, which would hide a tenant's own template stored with
    ``workspace=NULL`` and turn a stale pick back into a 400 for the whole
    provision. Inert today — the provisioning endpoint authenticates through
    ``HarnessAttemptAuthentication``, which sets no workspace context, and
    ``WorkspaceContextMiddleware.process_request`` clears any that is set —
    but this function must never be called from anything that runs under one.
    """
    if not names:
        return set()
    known = set(names) & offerable_eval_names()
    visible_scope = Q(organization=organization) | Q(organization__isnull=True)
    workspace_scope = Q(workspace=workspace) | Q(workspace__isnull=True)
    known |= set(
        EvalTemplate.all_objects.filter(visible_scope, workspace_scope, name__in=names)
        .exclude(owner=OwnerChoices.SYSTEM.value)
        # L2: an organisation-less non-system row is not "this tenant's
        # own" — `_is_listed_or_owned` only ever admits a custom template
        # when `template.organization_id is not None`, and catalog P6 says
        # "never an ownerless one". Without this exclusion a stray
        # ownerless custom template would make its name "known" — and so
        # merely stale rather than genuinely unknown — for every tenant,
        # disagreeing with what `_is_listed_or_owned` calls "ours".
        .exclude(organization__isnull=True)
        .values_list("name", flat=True)
    )
    return known


def create_selected_eval_configs(
    run_test: RunTest, chosen: list[str], modality: str
) -> list[SimulateEvalConfig]:
    """Bind the guest's chosen eval names to this run.

    Raises only on a name this tenant never could have been offered (harness
    P7). A name that was offered at launch but has since gone stale — a
    template drafted, untagged or deleted between the briefing and this
    provision — is dropped with a warning instead (M3), because the offer the
    provision gate checks against is necessarily recomputed, not the literal
    briefing that was sent (see `_tenant_known_names`).
    """
    wanted = [str(name).strip() for name in (chosen or []) if str(name).strip()]
    if not wanted:
        return []
    seen: set[str] = set()
    ordered = [name for name in wanted if not (name in seen or seen.add(name))]

    # The briefing gates this path too, so the docstring above holds. The gate
    # is the union of both kinds, because that is exactly what the sandbox was
    # shown: a name it did not receive is unknown and fails the provision
    # (harness contract P7), while a name it did receive but this run cannot
    # fill, or that is tagged for the other kind, is dropped with a warning
    # below (P8, Q2).
    briefed = briefed_eval_names(run_test.organization, run_test.workspace)
    offerable = offerable_eval_names()
    candidates_by_name: dict[str, list[EvalTemplate]] = {}
    for template in _visible_templates(
        run_test.organization, run_test.workspace
    ).filter(name__in=ordered):
        name = str(template.name or "")
        if name not in briefed:
            continue
        if not _is_listed_or_owned(template, run_test.organization, offerable):
            continue
        candidates_by_name.setdefault(name, []).append(template)
    # `_pick_winner` (M1) resolves a name shared by more than one visible
    # template to the same row `offered_templates` and `add_selected_eval`
    # resolve it to. A name-keyed dict comprehension over the raw queryset
    # would instead keep whichever row the queryset's default `-created_at`
    # ordering happened to return last — disagreeing with the add endpoint
    # about which template a shared name binds, and binding a different
    # template through provisioning than the one a person clicked in the
    # picker.
    found = {
        name: _pick_winner(candidates)
        for name, candidates in candidates_by_name.items()
    }
    # A name the *current* offer does not carry is not necessarily one that
    # was never offered (M3): it may have been offered at launch and become
    # stale since — drafted, untagged or deleted before this provision ran.
    # Since the sent briefing cannot be read back (see `_tenant_known_names`),
    # staleness is judged against whether this tenant could ever have
    # recognised the name at all. Only a name neither the catalog nor this
    # tenant's own templates ever knew fails the whole provision.
    not_currently_offered = [name for name in ordered if name not in found]
    stale = _tenant_known_names(
        run_test.organization, run_test.workspace, set(not_currently_offered)
    )
    missing = [name for name in not_currently_offered if name not in stale]
    if missing:
        raise UnknownEvalSelection(missing)
    for name in not_currently_offered:
        if name in stale:
            logger.warning(
                "harness_eval_selection_stale",
                run_test_id=str(run_test.id),
                template=name,
            )

    # A pick the union briefing carried but which is tagged for the other kind
    # — e.g. `toxicity` (chat-only tags) on a voice run — or one this run
    # cannot fill — e.g. `audio_quality` on a text run — is dropped with a
    # warning and never bound (Q2; harness contract P8). Both drops happen
    # here, before the `[:MOST_SELECTED_EVALS]` slice: a dropped pick must
    # never take one of the eight binding places, so only names that clear
    # both gates become survivors and compete for the cap, and
    # `harness_eval_selection_capped` below counts and names only those
    # survivors, never a pick already dropped for its kind, its mapping or
    # its staleness.
    survivors: list[tuple[str, EvalTemplate, dict[str, str]]] = []
    for name in ordered:
        template = found.get(name)
        if template is None:
            # Dropped above, as stale; `harness_eval_selection_stale` already
            # logged it and `missing` (raised above) would have caught it
            # otherwise, so there is nothing further to do for this name.
            continue
        if not _has_relevant_tag(template, modality):
            logger.warning(
                "harness_eval_selection_wrong_kind",
                run_test_id=str(run_test.id),
                template=name,
                modality=modality,
            )
            continue
        mapping = resolve_eval_mapping(template, modality)
        if mapping is None:
            logger.warning(
                "harness_eval_selection_unmappable",
                run_test_id=str(run_test.id),
                template=name,
                required_keys=_required_keys(template),
                modality=modality,
            )
            continue
        survivors.append((name, template, mapping))

    kept = survivors[:MOST_SELECTED_EVALS]
    dropped = survivors[MOST_SELECTED_EVALS:]
    if dropped:
        logger.warning(
            "harness_eval_selection_capped",
            run_test_id=str(run_test.id),
            kept=MOST_SELECTED_EVALS,
            dropped=[name for name, _template, _mapping in dropped],
        )
    return [
        bind_eval_config(run_test, template, mapping)
        for _name, template, mapping in kept
    ]


def selected_eval_configs(
    run_test: RunTest, *, mapping_only: bool = False
) -> list[SimulateEvalConfig]:
    """The evals bound to this run right now, oldest binding first.

    ``mapping_only`` narrows it to the rows a person or the sandbox chose. A
    row with an empty mapping is one ingestion made for one of the harness's
    own result columns: it is bound to the run, but it is not a selected eval,
    it is not listed in the detail, and it does not count toward the cap of 8
    (frontend contract P16).
    """
    configs = list(
        SimulateEvalConfig.objects.filter(run_test=run_test, deleted=False)
        .select_related("eval_template")
        .order_by("created_at")
    )
    return [config for config in configs if config.mapping] if mapping_only else configs


def _bound_eval_names(run_test: RunTest) -> set[str]:
    """Every name already bound to this run, under both names a row can carry.

    A row ingestion creates for one of the harness's own result columns is
    named after that result column, which is not always the template's name —
    the runner can name the template explicitly through `platform_template`.
    Matching on only one of the two would offer such an eval a second time
    under a second id (frontend contract P6a).
    """
    names: set[str] = set()
    for config in selected_eval_configs(run_test):
        names.add(str(config.name or ""))
        names.add(str(getattr(config.eval_template, "name", "") or ""))
    names.discard("")
    return names


def addable_evals(run_test: RunTest, modality: str) -> list[dict[str, Any]]:
    """The catalogue a person may still add to this run.

    The same offer the sandbox was given, minus **every** eval already bound —
    including the empty-mapping rows ingestion creates — so an eval the harness
    already reports natively is never offered a second time under a second id,
    and no row the picker shows can be refused as a duplicate (frontend
    contract P2, P6, P6a).
    """
    bound = _bound_eval_names(run_test)
    return [
        entry
        for entry in offered_evals(run_test.organization, run_test.workspace, modality)
        if str(entry["name"]) not in bound
    ]


def add_selected_eval(
    run_test: RunTest, name: str, modality: str
) -> SimulateEvalConfig:
    """Bind one more eval to a run, refusing with the reason it cannot be bound.

    The gates are the offer rule's own: the name fits the 255-character column
    a bound row stores it in, listed in the catalog or owned by this
    organisation, tagged for this kind of agent, not a draft, and a source for
    every key the template requires. Adding by hand may not reach an eval the
    authored selection could not.

    The name-length gate is this function's own (L1) rather than borrowed from
    `HarnessEnvironmentAddEvaluationSerializer.name`'s `max_length=255`: that
    serializer happens to cap the HTTP body at the same number today, but this
    function is called directly by nothing that guarantees that, and a
    >255-character custom eval that cleared every other gate would otherwise
    bind here and then fail at insert into `SimulateEvalConfig.name`.

    A name is resolved to exactly one template through `_resolve_named_template`
    (M1) — the same helper, calling the same `_pick_winner`, that
    `create_selected_eval_configs` resolves a shared name through. A plain
    `.filter(name=wanted).first()` here would instead depend on the
    queryset's default ordering and could bind a different row than the one
    `offered_templates` listed the name under, when a tenant's own custom
    eval shares a name with a catalog eval.

    The 255-character gate raises its own reason (L2, round 3) rather than
    falling through to the generic "not an eval this environment can be
    graded by" refusal below: that sentence also covers "another tenant's"
    and "wrong tag", and a direct service caller — the only caller this gate
    exists for (L1, round 1) — cannot tell a length problem from an
    eligibility one without a distinct reason.
    """
    wanted = str(name or "").strip()
    if wanted and len(wanted) > _MOST_NAME_CHARACTERS:
        raise EvalSelectionRefused(wanted, "name is longer than 255 characters")
    template = (
        _resolve_named_template(
            run_test.organization, run_test.workspace, wanted, offerable_eval_names()
        )
        if wanted
        else None
    )
    # `_resolve_named_template` already applies the listed-or-owned gate, so
    # there is nothing left to re-check here but the tag.
    if template is None or not _has_relevant_tag(template, modality):
        raise EvalSelectionRefused(
            wanted, "not an eval this environment can be graded by"
        )
    mapping = resolve_eval_mapping(template, modality)
    if mapping is None:
        needed = ", ".join(_required_keys(template)) or "inputs"
        raise EvalSelectionRefused(
            wanted, f"needs {needed}, which a {modality} run does not produce"
        )
    # The cap is read and then written, so two clicks arriving together would
    # both see room and both bind. The run row is the thing they contend for,
    # so it is the thing to hold.
    with transaction.atomic():
        type(run_test).objects.select_for_update(of=("self",)).filter(pk=run_test.pk).first()
        current = selected_eval_configs(run_test)
        for config in current:
            if wanted in {
                str(config.name or ""),
                str(getattr(config.eval_template, "name", "") or ""),
            }:
                # Idempotent rather than an error: a second click on one row
                # should leave the environment listing the eval once, not
                # report a failure. The template name is matched too, for the
                # same reason `available` subtracts on both (P6a).
                return config
        if len([config for config in current if config.mapping]) >= MOST_SELECTED_EVALS:
            raise EvalSelectionFull(
                wanted,
                f"an environment runs at most {MOST_SELECTED_EVALS} evals; "
                "remove one before adding another",
            )
        return bind_eval_config(run_test, template, mapping)


def runnable_eval_config_ids(run_test_id) -> list[str]:
    """Config ids the evaluator can run: a harness result column carries an empty mapping."""
    return [
        str(config_id)
        for config_id, mapping in SimulateEvalConfig.objects.filter(
            run_test_id=run_test_id, deleted=False
        ).values_list("id", "mapping")
        if mapping
    ]
