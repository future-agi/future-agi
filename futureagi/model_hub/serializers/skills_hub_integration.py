"""Typed transport contracts for integrated recovery, spending and consumers."""

from rest_framework import serializers as s

from model_hub.serializers.skills_hub_v11 import USD, Integer
from model_hub.serializers.skills_hub_v12 import (
    BindingInput,
    BindingOut,
    ContextSerializer,
    Digest,
    FileMeta,
    LockedLink,
    Selector,
)


class RevisionInput(ContextSerializer):
    revision = Integer(min_value=0)


class BindingUpdate(RevisionInput):
    binding = BindingInput()


class BindingPage(ContextSerializer):
    results = BindingOut(many=True)
    count = Integer(min_value=0)
    page = Integer(min_value=1)
    page_size = Integer(min_value=1)
    total_pages = Integer(min_value=0)


class SpendSummary(ContextSerializer):
    revision = Integer(min_value=0)
    enabled = s.BooleanField()
    period_limit_usd = USD(allow_null=True)
    starts_at = s.DateTimeField()
    ends_at = s.DateTimeField()
    committed_usd = USD()
    remaining_usd = USD(allow_null=True)
    known_estimate_usd = USD()
    unknown_reserved_usd = USD()
    prior_period_unknown_usd = USD()
    active_senders = Integer(min_value=0)
    commercial_basis = s.ChoiceField(choices=["provider_estimates_not_invoices"])
    simulation_entitlements = s.ChoiceField(choices=["additional_checks_not_replaced"])


class CheckpointCreate(RevisionInput):
    expected_digest = Digest()
    reason = s.ChoiceField(choices=["deletion", "replacement", "suspension", "manual"])
    suspend = s.BooleanField()


class CheckpointMetadata(ContextSerializer):
    id = s.UUIDField()
    resource_id = s.UUIDField()
    draft_revision = Integer(min_value=1)
    reason = s.CharField()
    digest = Digest()
    created_at = s.DateTimeField()
    expires_at = s.DateTimeField()
    payload_expired = s.BooleanField()
    restored_revision = Integer(min_value=1, allow_null=True)


class CheckpointCreated(ContextSerializer):
    checkpoint = CheckpointMetadata()
    suspended = s.BooleanField()
    draft_revision = Integer(min_value=0)
    retained_revision = Integer(min_value=1)


class CheckpointPage(ContextSerializer):
    results = CheckpointMetadata(many=True)
    count = Integer(min_value=0)
    page = Integer(min_value=1)
    page_size = Integer(min_value=1)
    total_pages = Integer(min_value=0)


class CheckpointRestoreInput(RevisionInput):
    expected_digest = Digest()


class CheckpointRestored(ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    checkpoint_id = s.UUIDField()
    draft_revision = Integer(min_value=1)
    digest = Digest()
    preserved_checkpoint_id = s.UUIDField(allow_null=True)


class ConsumerQuery(ContextSerializer):
    page = Integer(min_value=1, default=1)
    page_size = Integer(min_value=1, max_value=100, default=25)
    window_days = s.ChoiceField(choices=[7, 30, 90], default=7)


class VersionIdentity(ContextSerializer):
    version_id = s.UUIDField()
    digest = Digest()


class ConsumerFact(VersionIdentity):
    observed_at = s.DateTimeField()
    received_at = s.DateTimeField()
    origin = s.ChoiceField(choices=["supported_adapter", "client_reported"])
    instance_id = s.UUIDField()
    sequence = Integer(min_value=0)
    session_id = s.UUIDField(allow_null=True)


class ConsumerObservation(ContextSerializer):
    binding = BindingOut()
    desired = VersionIdentity(allow_null=True)
    resolved = ConsumerFact(allow_null=True)
    installed = ConsumerFact(allow_null=True)
    discovered = ConsumerFact(allow_null=True)
    loaded = ConsumerFact(allow_null=True)
    observation_age_seconds = s.FloatField(allow_null=True)
    states = s.ListField(child=s.CharField())
    next_action_code = s.CharField()
    observations_complete = s.BooleanField()


class ConsumerPage(ContextSerializer):
    as_of = s.DateTimeField()
    coverage = s.ChoiceField(choices=["declared_bindings"])
    results = ConsumerObservation(many=True)
    count = Integer(min_value=0)
    page = Integer(min_value=1)
    page_size = Integer(min_value=1)
    total_pages = Integer(min_value=0)


class SnapshotDescriptor(ContextSerializer):
    schema = Integer(required=False)
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    name = s.CharField()
    description = s.CharField()
    entrypoint = s.CharField()
    descriptor_path = s.CharField(allow_null=True)
    owned_paths = s.ListField(child=s.CharField(), required=False)
    links = LockedLink(many=True, required=False)


class ImmutableManifest(ContextSerializer):
    resource_id = s.UUIDField()
    kind = s.ChoiceField(choices=["skill", "agent_context"])
    name = s.CharField()
    description = s.CharField()
    version = Integer(min_value=1)
    version_id = s.UUIDField()
    digest = Digest()
    requested = Selector()
    entrypoint = s.CharField()
    descriptor_path = s.CharField(allow_null=True)
    files = FileMeta(many=True)
    file_count = Integer(min_value=1)
    file_offset = Integer(min_value=0)
    next_file_offset = Integer(allow_null=True)
    created_at = s.DateTimeField()


class VersionInspection(ContextSerializer):
    descriptor = SnapshotDescriptor()
    manifest = ImmutableManifest()
    distribution_blocked = s.BooleanField()
    reason = s.CharField(allow_null=True)
