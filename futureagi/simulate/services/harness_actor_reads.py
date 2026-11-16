"""An environment's cast as the editor reads it: each actor typed, with what the server decides about it, and what one
actor sees and never sees."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_fields import (
    ActorFields,
    RecordDefaults,
    knowledge_of,
    record_defaults,
    typed_fields,
)
from simulate.services.harness_actor_labels import (
    TRIGGER_FIELDS,
    CapField,
    FallbackView,
    TriggerOption,
    cap_field,
    channel_label,
    editable_trigger,
    fallback_of,
    side_of,
    timeout_label,
    trigger_kinds,
    trigger_of,
    trigger_options,
    when_label,
)
from simulate.services.harness_actor_merge import created_defaults
from simulate.services.harness_actor_options import (
    FieldOptions,
    LimitField,
    LoudnessView,
    UploadView,
    field_options,
    limit_fields,
    loudness_view,
    upload_view,
)
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    ActorLimits,
    ActorRecord,
    ActorRules,
    KnowledgeSources,
    RecordSource,
    UnresolvedPoint,
    WorldAction,
    actor_models,
    actor_rules,
    agent_modality,
    cast_records,
    is_the_caller,
    knowledge_sources,
    mapping_of,
    reachable_channels,
    scenarios_using,
    stored_limits,
    text_of,
    texts_of,
    typed_limits,
    unresolved_points,
    world_actions,
    world_stores,
)
from simulate.services.harness_actor_validation import (
    ACTOR_RECORD_MAX_CHARS,
    in_use_problem,
    joins_fixed_problem,
    removal_problems,
    required_fields,
)
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import suite_documents

# The way of joining that never speaks in the conversation.
WORLD_ONLY = "Acts through the world only: never speaks in the conversation"


class LockedField(TypedDict):
    field: str
    reason: str


class Permission(TypedDict):
    action: str
    in_world: bool


class ActorRow(ActorFields):
    """One actor as the editor reads it: its record typed, and what the server decides about it."""

    facts: list[str]
    files: list[str]
    records: list[RecordSource]
    scenarios: int
    removable: bool
    removal_blocked_by: list[str]
    locked_fields: list[LockedField]
    reaches: list[str]
    fallback: FallbackView
    permissions: list[Permission]
    editable_trigger: bool
    when_label: str
    timeout_label: str | None


class ChannelOption(TypedDict):
    """One way an actor can join here; `channel` null is acting through the world only."""

    channel: str | None
    description: str
    loudness: bool
    trigger_kinds: list[str]
    required_fields: list[str]
    # The actor's own cap that counts when it joins this way.
    cap_field: CapField
    # The way of joining in a word or two; null for acting through the world only.
    label: str | None
    # `trigger_kinds` with their labels.
    trigger_options: list[TriggerOption]


class SideDefault(TypedDict):
    # The way a new actor on this side joins unless the person picks another; null acts in the world only.
    channel: str | None


class SideDefaults(TypedDict):
    caller: SideDefault
    world: SideDefault


class Cast(TypedDict):
    actors: list[ActorRow]
    source: Literal["draft", "saved"]
    revision: int
    record_schemas: dict[str, dict[str, Any]]
    actions: list[WorldAction]
    channels: dict[str, str]
    loudness: LoudnessView
    reserved_ids: list[str]
    knowledge_sources: KnowledgeSources
    # The environment's actor limits; a key set to null is no limit, a missing key the channel's default.
    limits: ActorLimits
    # The models an actor may be played on; the first is the default.
    actor_models: list[str]
    # Policy points the builder could not turn into an actor's permissions or knowledge, for the person to decide.
    unresolved: list[UnresolvedPoint]
    has_caller: bool
    record_max_chars: int
    channel_options: list[ChannelOption]
    # The id rule in the sealed schema's own words; empty when it does not describe one.
    id_hint: str
    limit_fields: list[LimitField]
    side_defaults: SideDefaults
    field_options: FieldOptions
    upload: UploadView
    # The trigger keys each kind reads, so a form shows the inputs of the kind picked.
    trigger_fields: dict[str, list[str]]
    # A new actor as a read shows one, with what a create fills in for every field left as it is.
    new_actor: ActorRow


def _actor_row(
    actor: ActorRecord,
    *,
    cast: list[ActorRecord],
    names: dict[str, str],
    using: list[str],
    action_names: set[str],
    rules: ActorRules,
    defaults: RecordDefaults,
) -> ActorRow:
    actor_id = str(actor.get("id") or "")
    name = names.get(actor_id, actor_id)
    channel = text_of(actor.get("channel")) or None
    permitted = texts_of(actor.get("permitted_actions"))
    blocked = ([in_use_problem(name, using)] if using else []) + removal_problems(
        [one for one in cast if one.get("id") != actor_id], actor_id, rules, cast
    )
    services = {one.split(".")[0] for one in permitted}
    facts, files, records = knowledge_of(actor)
    trigger = trigger_of(actor)
    return {
        **typed_fields(actor, defaults),
        "facts": facts,
        "files": files,
        "records": records,
        "scenarios": len(using),
        "removable": not blocked,
        "removal_blocked_by": blocked,
        # A scenario's trigger and level were written for the way the actor joins.
        "locked_fields": [
            {"field": "channel", "reason": joins_fixed_problem(name, using)}
        ]
        if using
        else [],
        # Another actor whose identity in a service is what one of this actor's actions addresses.
        "reaches": [
            str(other.get("id") or "")
            for other in cast
            if other.get("id") != actor_id
            and services & set(mapping_of(other.get("identities")))
        ],
        "fallback": fallback_of(actor, names, default=defaults["fallback"]),
        "permissions": [
            {"action": one, "in_world": one in action_names} for one in permitted
        ],
        "editable_trigger": editable_trigger(
            trigger, channel, trigger_kinds(rules, channel)
        ),
        "when_label": when_label(trigger, channel),
        "timeout_label": timeout_label(
            actor, names, default_fallback=defaults["fallback"]
        ),
    }


def _channel_options(
    rules: ActorRules, channels: dict[str, str]
) -> list[ChannelOption]:
    loud = (rules.get("loudness") or {}).get("channels") or []
    ways: list[tuple[str | None, str]] = [*channels.items(), (None, WORLD_ONLY)]
    return [
        {
            "channel": channel,
            "description": about,
            "loudness": channel in loud,
            "trigger_kinds": trigger_kinds(rules, channel),
            "required_fields": required_fields(rules, channel),
            "cap_field": cap_field(channel),
            "label": channel_label(channel),
            "trigger_options": trigger_options(trigger_kinds(rules, channel)),
        }
        for channel, about in ways
    ]


def cast_view(
    environment_files: dict[str, bytes],
    suite_files: dict[str, bytes],
    draft_revision: int | None,
    stores: object = None,
) -> Cast:
    """The cast as the editor reads it; `draft_revision` is the open draft's, none when it reads the saved version."""
    rules = actor_rules(environment_files)
    defaults = record_defaults(rules)
    documents = suite_documents(suite_files)
    actors = cast_records(environment_files)
    names = {
        str(one.get("id") or ""): str(one.get("name") or one.get("id") or "")
        for one in actors
    }
    actions = world_actions(environment_files, rules)
    action_names = {one["action"] for one in actions}
    channels = reachable_channels(rules, agent_modality(environment_files))
    return {
        "actors": [
            _actor_row(
                one,
                cast=actors,
                names=names,
                using=scenarios_using(documents, str(one.get("id") or "")),
                action_names=action_names,
                rules=rules,
                defaults=defaults,
            )
            # The caller's side first, the cast's order kept within each side.
            for one in sorted(actors, key=lambda one: side_of(one) != "caller")
        ],
        "source": "draft" if draft_revision is not None else "saved",
        "revision": draft_revision or 0,
        "record_schemas": rules["records"],
        "actions": actions,
        "channels": channels,
        "loudness": loudness_view(rules),
        "reserved_ids": list(rules.get("reserved_ids") or []),
        "knowledge_sources": knowledge_sources(environment_files, rules, stores),
        "limits": typed_limits(stored_limits(environment_files)),
        "actor_models": actor_models(rules),
        "unresolved": unresolved_points(environment_files),
        "has_caller": any(is_the_caller(one) for one in actors),
        "record_max_chars": ACTOR_RECORD_MAX_CHARS,
        "channel_options": _channel_options(rules, channels),
        "id_hint": text_of(
            (
                (rules["records"][ACTORS_FILE].get("properties") or {}).get("id") or {}
            ).get("description")
        ),
        "limit_fields": limit_fields(rules, agent_modality(environment_files)),
        # Someone on the caller's side speaks on the first way the agent can be reached; the world's acts in it.
        "side_defaults": {
            "caller": {"channel": next(iter(channels), None)},
            "world": {"channel": None},
        },
        "field_options": field_options(rules),
        "upload": upload_view(),
        "trigger_fields": {kind: list(keys) for kind, keys in TRIGGER_FIELDS.items()},
        # Read like any actor, as a create would write it: nameless, in no scenario.
        "new_actor": _actor_row(
            cast(ActorRecord, {**created_defaults(rules), "id": "", "name": ""}),
            cast=actors,
            names=names,
            using=[],
            action_names=action_names,
            rules=rules,
            defaults=defaults,
        ),
    }


def read_actors(environment: HostedHarnessJob) -> Cast:
    """The cast the next run would use (the draft's, else the saved version's) and what an actor can be given.

    Raises `actors_unreadable` (409) when an actor file is not valid JSON."""
    sides = drafts.edit_sides(environment)
    draft = drafts.fresh_draft(environment, "environment")
    return cast_view(
        sides["environment"],
        sides["suite"],
        draft.revision if draft is not None else None,
        world_stores(environment),
    )
