"""An environment's actors: the cast as the editor reads it, the edits it sends, and what one actor sees."""

from __future__ import annotations

import json

from rest_framework import serializers

from model_hub.serializers.contracts import JsonObjectRequestField
from simulate.services.harness_actor_rules import LIMIT_FIELDS
from simulate.services.harness_actor_validation import ACTOR_RECORD_MAX_CHARS

# The trigger kinds a stored record may hold; a kind the editor cannot make reads as kept (`editable_trigger`).
TRIGGER_KINDS = (
    "always",
    "after_seconds",
    "on_tool",
    "contacted",
    "probability",
    "mid_journey",
    "on_keyword",
    "conditional",
)


class HarnessActorRecordField(JsonObjectRequestField):
    """One actor record, checked on write against its environment's sealed rules (`record_schemas`)."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        if size > ACTOR_RECORD_MAX_CHARS:
            raise serializers.ValidationError(
                f"Keep an actor under {ACTOR_RECORD_MAX_CHARS} characters."
            )
        return value


class HarnessTriggerWhereValueField(serializers.JSONField):
    """One field value a `conditional` trigger waits for: a string, number, true, false or null."""

    class Meta:
        swagger_schema_fields = {"type": "string", "x-nullable": True}

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if isinstance(value, (dict, list)):
            raise serializers.ValidationError(
                "A field's value is a string, number, true, false or null."
            )
        return value


class HarnessActorTriggerSerializer(serializers.Serializer):
    """When an actor comes in; only the keys its kind uses are present, none defaulted."""

    kind = serializers.ChoiceField(choices=TRIGGER_KINDS, required=False)
    seconds = serializers.FloatField(required=False, min_value=0)
    tool = serializers.CharField(required=False, allow_blank=True, max_length=200)
    on_call = serializers.IntegerField(required=False, min_value=1)
    p = serializers.FloatField(required=False, min_value=0, max_value=1)
    turn = serializers.IntegerField(required=False, min_value=0)
    words = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=50
    )
    # `conditional`: the record collection watched, and the field values that bring the actor in.
    collection = serializers.CharField(required=False, allow_blank=True, max_length=200)
    where = serializers.DictField(child=HarnessTriggerWhereValueField(), required=False)


class HarnessActorPersonaSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True)
    gender = serializers.CharField(required=False, allow_blank=True)
    age_group = serializers.CharField(required=False, allow_blank=True)
    accent = serializers.CharField(required=False, allow_blank=True)
    occupation = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(required=False, allow_blank=True)
    personality = serializers.CharField(required=False, allow_blank=True)
    communication_style = serializers.CharField(required=False, allow_blank=True)
    initial_message = serializers.CharField(required=False, allow_blank=True)
    languages = serializers.ListField(child=serializers.CharField(), required=False)
    multilingual = serializers.BooleanField(required=False)


class HarnessActorConstraintsSerializer(serializers.Serializer):
    may_agree = serializers.ListField(child=serializers.CharField(allow_blank=True))
    private = serializers.ListField(child=serializers.CharField(allow_blank=True))
    escalate_when = serializers.ListField(child=serializers.CharField(allow_blank=True))
    stop_when = serializers.ListField(child=serializers.CharField(allow_blank=True))


class HarnessActorResponseTimingSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("immediate", "delayed"))
    # How long a delayed actor waits before it answers; an immediate one ignores it.
    seconds = serializers.FloatField(required=False, min_value=0)


class HarnessActorBehaviourSerializer(serializers.Serializer):
    style = serializers.CharField(allow_blank=True)
    response_timing = HarnessActorResponseTimingSerializer()


class HarnessActorActivationSerializer(serializers.Serializer):
    trigger = HarnessActorTriggerSerializer()
    timeout_seconds = serializers.FloatField()
    fallback = serializers.CharField()
    # A world action's own arguments, shaped by that action; absent when the fallback takes none.
    fallback_arguments = JsonObjectRequestField(required=False)


class HarnessActorProvenanceSerializer(serializers.Serializer):
    origin = serializers.CharField(allow_blank=True)
    by = serializers.CharField(allow_null=True)
    # Who set each field: `editor`, or the builder that wrote it.
    fields = serializers.DictField(child=serializers.CharField())


class HarnessActorLockedFieldSerializer(serializers.Serializer):
    field = serializers.CharField()
    reason = serializers.CharField()


class HarnessActorFallbackSerializer(serializers.Serializer):
    """What happens when the actor does not answer: nothing, a refusal, one of its own actions, or another actor."""

    kind = serializers.ChoiceField(choices=("silent", "decline", "action", "actor"))
    value = serializers.CharField()
    label = serializers.CharField()


class HarnessActorPermissionSerializer(serializers.Serializer):
    action = serializers.CharField()
    # Whether this world still has the action.
    in_world = serializers.BooleanField()


class HarnessActorRecordSourceSerializer(serializers.Serializer):
    store = serializers.CharField(allow_blank=True)
    collection = serializers.CharField()


class HarnessActorKnowledgeEntrySerializer(serializers.Serializer):
    """One thing an actor is given to read, as stored: written facts, files, or a record collection with a filter."""

    kind = serializers.CharField(required=False)
    text = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    paths = serializers.ListField(child=serializers.CharField(), required=False)
    store = serializers.CharField(required=False, allow_blank=True)
    collection = serializers.CharField(required=False, allow_blank=True)
    where = serializers.DictField(child=HarnessTriggerWhereValueField(), required=False)


class HarnessActorSerializer(serializers.Serializer):
    """One actor of the cast, typed, with what the server decides about it. A field a later release adds is kept in
    storage and not shown here until it is typed."""

    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(allow_blank=True)
    kind = serializers.CharField()
    goal = serializers.CharField(allow_blank=True, trim_whitespace=False)
    play = serializers.CharField()
    prompt = serializers.CharField(allow_blank=True, trim_whitespace=False)
    surface = serializers.CharField(allow_blank=True)
    # Null: the environment's actor model.
    model = serializers.CharField(allow_null=True)
    side = serializers.ChoiceField(choices=("caller", "world"))
    # Null: it acts through the world only, never speaking in the conversation.
    channel = serializers.CharField(allow_null=True)
    parent = serializers.CharField(allow_null=True)
    on_behalf_of = serializers.CharField(allow_null=True)
    # The actor's identity in each world service, which another actor's actions address.
    identities = serializers.DictField(child=serializers.CharField(allow_blank=True))
    persona = HarnessActorPersonaSerializer(allow_null=True)
    loudness_ratio = serializers.FloatField(allow_null=True)
    constraints = HarnessActorConstraintsSerializer()
    # The written facts, in order; a fact may span lines.
    facts = serializers.ListField(child=serializers.CharField(trim_whitespace=False))
    files = serializers.ListField(child=serializers.CharField())
    records = HarnessActorRecordSourceSerializer(many=True)
    # Every stored knowledge entry, in order; `facts`, `files` and `records` read the same entries by kind.
    knowledge = HarnessActorKnowledgeEntrySerializer(many=True)
    behaviour = HarnessActorBehaviourSerializer()
    permitted_actions = serializers.ListField(child=serializers.CharField())
    activation = HarnessActorActivationSerializer()
    # Null: no limit of its own.
    max_turns = serializers.IntegerField(allow_null=True)
    max_lines = serializers.IntegerField(allow_null=True)
    scenarios = serializers.IntegerField(help_text="Scenarios that include the actor.")
    provenance = HarnessActorProvenanceSerializer(allow_null=True)
    removable = serializers.BooleanField()
    removal_blocked_by = serializers.ListField(child=serializers.CharField())
    locked_fields = HarnessActorLockedFieldSerializer(many=True)
    # Ids of the actors this actor's permitted actions reach.
    reaches = serializers.ListField(child=serializers.CharField())
    fallback = HarnessActorFallbackSerializer()
    permissions = HarnessActorPermissionSerializer(many=True)
    # False when the stored trigger is one the editor cannot make again (a chance, a later call of a tool).
    editable_trigger = serializers.BooleanField()
    when_label = serializers.CharField()
    # Null when it never waits.
    timeout_label = serializers.CharField(allow_null=True)


class HarnessActorLimitsSerializer(serializers.Serializer):
    """The environment's actor limits: each a positive number, or null for no limit; a key left out takes the
    channel's default."""

    max_active = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    caller_takes_back_after_turns = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    actor_seconds_per_call = serializers.FloatField(
        required=False, allow_null=True, min_value=0
    )
    max_messages = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    live_turns_per_actor = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    live_turns_per_trial = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    # One of `actor_models`.
    model = serializers.CharField(required=False, allow_null=True, max_length=200)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(LIMIT_FIELDS))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["is not an actor limit"] for key in unknown}
                )
        return super().to_internal_value(data)


class HarnessActorLimitFieldSerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.ChoiceField(choices=("whole", "seconds", "model"))


class HarnessActorChannelOptionSerializer(serializers.Serializer):
    """One way an actor can join here; `channel` null is acting through the world only."""

    channel = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    loudness = serializers.BooleanField()
    trigger_kinds = serializers.ListField(child=serializers.CharField())
    required_fields = serializers.ListField(child=serializers.CharField())


class HarnessActorChangesSerializer(serializers.Serializer):
    """A field-level patch to one actor: only the fields sent change; every other field stays as stored."""

    name = serializers.CharField(required=False, allow_blank=True, max_length=500)
    role = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    # Whose side the actor is on, picked when it is added.
    side = serializers.ChoiceField(choices=("caller", "world"), required=False)
    # Null or empty: the actor acts through the world only.
    channel = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=100
    )
    gender = serializers.CharField(required=False, allow_blank=True, max_length=100)
    age_group = serializers.CharField(required=False, allow_blank=True, max_length=100)
    accent = serializers.CharField(required=False, allow_blank=True, max_length=100)
    # Kept only on a channel with a loudness; null is automatic.
    loudness_ratio = serializers.FloatField(required=False, allow_null=True)
    kind = serializers.CharField(required=False, max_length=100)
    parent = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=64
    )
    # Long text is bounded by the record as a whole (`record_max_chars`) and the sealed rules.
    goal = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    may_agree = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, max_length=200
    )
    private = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, max_length=200
    )
    escalate_when = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, max_length=200
    )
    stop_when = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, max_length=200
    )
    # The written facts in order; one that is still there keeps its entry.
    facts = serializers.ListField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
        required=False,
        max_length=500,
    )
    files = serializers.ListField(
        child=serializers.CharField(max_length=500), required=False, max_length=200
    )
    # A collection still picked keeps its stored entry.
    records = HarnessActorRecordSourceSerializer(
        many=True, required=False, max_length=200
    )
    style = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False
    )
    response_timing = HarnessActorResponseTimingSerializer(required=False)
    permitted_actions = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=200
    )
    play = serializers.CharField(required=False, max_length=100)
    trigger = HarnessActorTriggerSerializer(required=False)
    timeout_seconds = serializers.FloatField(required=False, min_value=0)
    fallback = serializers.CharField(required=False, max_length=200)
    # The fallback action's own arguments; null or empty clears them. A new fallback sent without them drops them.
    fallback_arguments = JsonObjectRequestField(required=False, allow_null=True)
    # Null clears: the environment's actor model.
    model = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=200
    )
    # Null clears: no limit of its own.
    max_turns = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    max_lines = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class HarnessActorWriteSerializer(serializers.Serializer):
    # A whole record, written as sent; or `changes`, a patch merged into the stored record. Exactly one of them.
    actor = HarnessActorRecordField(required=False)
    changes = HarnessActorChangesSerializer(required=False)
    # On a create only; left out, the server makes one from the name, unique and not reserved.
    id = serializers.CharField(required=False, max_length=64)
    # The draft revision the actors were read at; an edit made elsewhere since is refused.
    expected_revision = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )
    # Actions this edit gives the actor that only one other actor holds, confirmed as shared by both.
    share_actions = serializers.ListField(
        child=serializers.CharField(max_length=200),
        required=False,
        max_length=100,
        help_text=(
            "Actions the edit gives that only one other actor holds today, confirmed as shared; without it such an "
            "edit is refused with permission_conflict (409) and each conflict."
        ),
    )

    def validate(self, attrs):
        if ("actor" in attrs) == ("changes" in attrs):
            raise serializers.ValidationError(
                "Send the actor as a whole record (actor) or as changes, one of them."
            )
        return attrs


class HarnessActorLimitsWriteSerializer(serializers.Serializer):
    # The whole limits block, replacing the stored one.
    limits = HarnessActorLimitsSerializer(
        help_text=(
            "The whole limits block, replacing the stored one. Each limit is a positive number or null for no limit; "
            "model is one of actor_models. A limit left out takes the channel's default."
        )
    )
    expected_revision = serializers.IntegerField(required=False, min_value=0)


class HarnessActorDeleteQuerySerializer(serializers.Serializer):
    # The draft revision the actors were read at; an edit made elsewhere since is refused.
    expected_revision = serializers.IntegerField(required=False, min_value=0)


class HarnessActorValidateSerializer(serializers.Serializer):
    """One edit to check without writing it: a create, a replace or a removal, as its write would send it."""

    op = serializers.ChoiceField(choices=("create", "replace", "delete"))
    # The actor a replace or removal is about; a create has none.
    actor_id = serializers.CharField(required=False, max_length=64)
    # The patch a create or replace would send; a removal sends none.
    changes = HarnessActorChangesSerializer(required=False)
    # On a create only; left out, the id the server would make from the name is checked.
    id = serializers.CharField(required=False, max_length=64)
    # Actions already confirmed as shared; every other conflicting action is listed.
    share_actions = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=100
    )

    def validate(self, attrs):
        op = attrs["op"]
        if (op == "create") == ("actor_id" in attrs):
            raise serializers.ValidationError(
                {"actor_id": "A replace or removal names its actor; a create does not."}
            )
        if (op == "delete") == ("changes" in attrs):
            raise serializers.ValidationError(
                {
                    "changes": "A create or replace sends its changes; a removal does not."
                }
            )
        if op != "create" and "id" in attrs:
            raise serializers.ValidationError({"id": "Only a create is given an id."})
        return attrs


class HarnessActorProblemSerializer(serializers.Serializer):
    # The editor's field the problem is about; null when it is about the record as a whole.
    field = serializers.CharField(allow_null=True)
    message = serializers.CharField()


class HarnessActorConflictSerializer(serializers.Serializer):
    """An action the edit gives that only one other actor holds today."""

    actor = serializers.CharField()
    action = serializers.CharField()
    holder = serializers.CharField()
    holder_name = serializers.CharField()


class HarnessActorValidationSerializer(serializers.Serializer):
    """What a write would say about an edit, nothing written."""

    problems = HarnessActorProblemSerializer(many=True)
    # Unconfirmed by `share_actions`; a write is refused with permission_conflict while any is left.
    conflicts = HarnessActorConflictSerializer(many=True)
    # The id a create would get; null on a replace or removal.
    suggested_id = serializers.CharField(allow_null=True)
    # Characters over `record_max_chars`; 0 when within it.
    over_by = serializers.IntegerField()


class HarnessWorldActionSerializer(serializers.Serializer):
    """One action the world lets an actor take, named as the world names it, with the tool behind it."""

    action = serializers.CharField()
    tool = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)


class HarnessActorKnowledgeSourcesSerializer(serializers.Serializer):
    files = serializers.ListField(child=serializers.CharField())
    records = HarnessActorRecordSourceSerializer(many=True)


class HarnessActorUnresolvedSerializer(serializers.Serializer):
    policy = serializers.CharField()
    why = serializers.CharField(required=False, allow_blank=True)
    actor = serializers.CharField(required=False, allow_blank=True)


class HarnessActorUnresolvedAcceptSerializer(serializers.Serializer):
    # The policy point accepted as written: it leaves the list waiting for a decision.
    policy = serializers.CharField(max_length=500)
    expected_revision = serializers.IntegerField(required=False, min_value=0)


class HarnessActorSideDefaultSerializer(serializers.Serializer):
    # How a new actor on this side joins unless the person picks another; null acts in the world only.
    channel = serializers.CharField(allow_null=True)


class HarnessActorSideDefaultsSerializer(serializers.Serializer):
    caller = HarnessActorSideDefaultSerializer()
    world = HarnessActorSideDefaultSerializer()


class HarnessActorListSerializer(serializers.Serializer):
    actors = HarnessActorSerializer(many=True)
    # The API field is named source.
    source = serializers.ChoiceField(choices=("draft", "saved"))  # type: ignore[assignment]
    revision = serializers.IntegerField()
    # Each actor file's record schema, from the rules of the release that sealed the environment.
    record_schemas = JsonObjectRequestField()
    # What an actor can be given here: the world's actions, and the call channels an actor speaks on.
    actions = HarnessWorldActionSerializer(many=True)
    channels = serializers.DictField(child=serializers.CharField(allow_blank=True))
    # Which channels have a loudness, and the range a call draws from when none is set.
    loudness = JsonObjectRequestField()
    # Ids no actor may take.
    reserved_ids = serializers.ListField(child=serializers.CharField())
    # What an actor may be given to read here: the environment's own files and its world's record collections.
    knowledge_sources = HarnessActorKnowledgeSourcesSerializer()
    # The environment's actor limits: null is no limit, a missing key the channel's default.
    limits = HarnessActorLimitsSerializer(required=False)
    # The models an actor may be played on; the first is the default.
    actor_models = serializers.ListField(child=serializers.CharField(), required=False)
    # Policy points the builder could not turn into an actor's permissions or knowledge, waiting for the person.
    unresolved = HarnessActorUnresolvedSerializer(many=True, required=False)
    has_caller = serializers.BooleanField()
    # The server's bound on one actor record, in characters of compact JSON.
    record_max_chars = serializers.IntegerField()
    # Each way an actor can join, with the triggers it can be given and the fields it needs.
    channel_options = HarnessActorChannelOptionSerializer(many=True)
    # The id rule in words; empty when the sealed rules do not describe one.
    id_hint = serializers.CharField(allow_blank=True)
    # How each limit is typed.
    limit_fields = HarnessActorLimitFieldSerializer(many=True)
    side_defaults = HarnessActorSideDefaultsSerializer()


class HarnessEnvironmentFileUploadSerializer(serializers.Serializer):
    # An empty file is the service's to refuse, with the other problems of the same upload.
    file = serializers.FileField(allow_empty_file=True)
    kind = serializers.ChoiceField(
        choices=("knowledge",),
        help_text="What the file is for: `knowledge`, a document an actor may be given to read.",
    )
    expected_revision = serializers.IntegerField(min_value=0, required=False)


class HarnessEnvironmentFileUploadedSerializer(HarnessActorListSerializer):
    uploaded_path = serializers.CharField(
        help_text="Where the file was stored in the environment, as the knowledge sources list it."
    )


class HarnessActorViewQuerySerializer(serializers.Serializer):
    scenario_key = serializers.CharField(required=False, max_length=255)


class HarnessActorSeenSerializer(serializers.Serializer):
    input = serializers.CharField()
    value = serializers.JSONField()
    source = serializers.ChoiceField(  # type: ignore[assignment]  # the API field is named source
        choices=("actor", "scenario", "caller")
    )


class HarnessActorOthersPrivateSerializer(serializers.Serializer):
    """The fields a number of other actors each keep to themselves, as words."""

    fields = serializers.ListField(child=serializers.CharField())
    count = serializers.IntegerField()


class HarnessActorNeverSeesSerializer(serializers.Serializer):
    """What an actor is never given: the grader's scenario fields and files, and other actors' private fields by
    name (`<name>.<field>`)."""

    scenario_fields = serializers.ListField(child=serializers.CharField())
    files = serializers.ListField(child=serializers.CharField())
    other_actors = serializers.ListField(child=serializers.CharField())
    # The grader's scenario fields and files as words, once each.
    grader = serializers.ListField(child=serializers.CharField())
    # Other actors' private fields, one line per set of fields.
    others = HarnessActorOthersPrivateSerializer(many=True)


class HarnessActorViewSerializer(serializers.Serializer):
    actor_id = serializers.CharField()
    permitted_actions = serializers.ListField(child=serializers.CharField())
    sees = HarnessActorSeenSerializer(many=True)
    never_sees = HarnessActorNeverSeesSerializer()
    # Whether a run plays the actor from what it sees; when not, why.
    played_from_view = serializers.BooleanField()
    unused_reason = serializers.CharField(allow_null=True)
