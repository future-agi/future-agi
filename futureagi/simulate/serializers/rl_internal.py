"""Request serializers + payload builders for the RL harness internal
persistence API (``simulate.views.rl_internal``).

One payload builder per model so a write's response and a later read of the
same row return identical shapes.
"""

from __future__ import annotations

from rest_framework import serializers

from simulate.models.rl_environment import (
    RLContract,
    RLEnvironment,
    RLEnvironmentMessage,
    RLScenario,
    RLWorld,
    RLWorldCopy,
)

_CONTRACT_CREATE_STATUS_CHOICES = (
    (RLContract.Status.DRAFT.value, RLContract.Status.DRAFT.label),
    (RLContract.Status.ACTIVE.value, RLContract.Status.ACTIVE.label),
)

_WORLD_CREATE_STATUS_CHOICES = (
    (RLWorld.Status.BUILDING.value, RLWorld.Status.BUILDING.label),
    (RLWorld.Status.SAVED.value, RLWorld.Status.SAVED.label),
)


class _StrictSerializer(serializers.Serializer):
    """An internal caller sending a field this surface doesn't know about is a
    bug on the caller's side, not something to silently ignore."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data.keys()) - set(self.fields.keys())
            if unknown:
                raise serializers.ValidationError(
                    {field: ["Unknown field."] for field in sorted(unknown)}
                )
        return super().to_internal_value(data)


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


class EnvironmentCreateRequestSerializer(_StrictSerializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    source_kind = serializers.CharField(required=False, allow_blank=True, max_length=32)
    source_ref = serializers.CharField(required=False, allow_blank=True, max_length=500)
    simulator_prompt = serializers.CharField(required=False, allow_blank=True)
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_version_id = serializers.UUIDField(required=False, allow_null=True)
    run_config = serializers.DictField(required=False)


class EnvironmentPatchRequestSerializer(_StrictSerializer):
    title = serializers.CharField(required=False, max_length=255)
    phase = serializers.ChoiceField(choices=RLEnvironment.Phase.choices, required=False)
    status = serializers.ChoiceField(choices=RLEnvironment.Status.choices, required=False)
    last_error = serializers.CharField(required=False, allow_blank=True)
    simulator_prompt = serializers.CharField(required=False, allow_blank=True)
    source_kind = serializers.CharField(required=False, allow_blank=True, max_length=32)
    source_ref = serializers.CharField(required=False, allow_blank=True, max_length=500)
    run_config = serializers.DictField(required=False)
    agent_definition_id = serializers.UUIDField(required=False, allow_null=True)
    agent_version_id = serializers.UUIDField(required=False, allow_null=True)


def environment_payload(environment: RLEnvironment) -> dict:
    return {
        "id": str(environment.id),
        "organization_id": str(environment.organization_id),
        "workspace_id": str(environment.workspace_id),
        "title": environment.title,
        "source_kind": environment.source_kind,
        "source_ref": environment.source_ref,
        "phase": environment.phase,
        "status": environment.status,
        "simulator_prompt": environment.simulator_prompt,
        "agent_definition_id": (
            str(environment.agent_definition_id)
            if environment.agent_definition_id
            else None
        ),
        "agent_version_id": (
            str(environment.agent_version_id) if environment.agent_version_id else None
        ),
        "run_config": environment.run_config,
        "last_error": environment.last_error,
        "created_at": environment.created_at.isoformat(),
        "updated_at": environment.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageCreateRequestSerializer(_StrictSerializer):
    turn_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=RLEnvironmentMessage.Role.choices)
    text = serializers.CharField(required=False, allow_blank=True)
    tools = serializers.ListField(required=False)
    phase = serializers.ChoiceField(choices=RLEnvironment.Phase.choices, required=False)


class MessageListQuerySerializer(serializers.Serializer):
    # Not a _StrictSerializer: query params routinely carry things this
    # surface doesn't define (?format=json, cache busters), unlike a body.
    after_seq = serializers.IntegerField(required=False, min_value=0, default=0)
    limit = serializers.IntegerField(required=False, min_value=1, default=200)


def message_payload(message: RLEnvironmentMessage) -> dict:
    return {
        "id": str(message.id),
        "environment_id": str(message.environment_id),
        "turn_id": str(message.turn_id),
        "seq": message.seq,
        "role": message.role,
        "text": message.text,
        "tools": message.tools,
        "phase": message.phase,
        "created_at": message.created_at.isoformat(),
        "updated_at": message.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class ContractCreateRequestSerializer(_StrictSerializer):
    data = serializers.DictField()
    status = serializers.ChoiceField(
        choices=_CONTRACT_CREATE_STATUS_CHOICES,
        default=RLContract.Status.ACTIVE.value,
        required=False,
    )


def contract_payload(contract: RLContract) -> dict:
    return {
        "id": str(contract.id),
        "version": contract.version,
        "status": contract.status,
    }


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


class WorldCreateRequestSerializer(_StrictSerializer):
    contract_id = serializers.UUIDField()
    store_kind = serializers.ChoiceField(
        choices=RLWorld.StoreKind.choices,
        default=RLWorld.StoreKind.POSTGRES.value,
        required=False,
    )
    schema_scripts = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    snapshot = serializers.DictField(required=False)
    state = serializers.DictField(required=False)
    handlers = serializers.DictField(required=False)
    tool_specs = serializers.ListField(required=False)
    world_checks = serializers.ListField(required=False)
    refusal_signature = serializers.CharField(required=False, allow_blank=True)
    master_db_name = serializers.CharField(required=False, allow_blank=True, max_length=63)
    status = serializers.ChoiceField(
        choices=_WORLD_CREATE_STATUS_CHOICES,
        default=RLWorld.Status.SAVED.value,
        required=False,
    )


class WorldPatchRequestSerializer(_StrictSerializer):
    status = serializers.ChoiceField(choices=RLWorld.Status.choices, required=False)
    master_db_name = serializers.CharField(required=False, allow_blank=True, max_length=63)
    master_materialized_at = serializers.DateTimeField(required=False, allow_null=True)


def world_payload(world: RLWorld) -> dict:
    return {
        "id": str(world.id),
        "environment_id": str(world.environment_id),
        "contract_id": str(world.contract_id),
        "contract_version": world.contract.version,
        "version": world.version,
        "status": world.status,
        "store_kind": world.store_kind,
        "schema_scripts": world.schema_scripts,
        "snapshot": world.snapshot,
        "state": world.state,
        "handlers": world.handlers,
        "tool_specs": world.tool_specs,
        "world_checks": world.world_checks,
        "refusal_signature": world.refusal_signature,
        "master_db_name": world.master_db_name,
        "master_materialized_at": (
            world.master_materialized_at.isoformat()
            if world.master_materialized_at
            else None
        ),
        "created_at": world.created_at.isoformat(),
        "updated_at": world.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class ScenarioUpsertRequestSerializer(_StrictSerializer):
    name = serializers.CharField(max_length=255)
    world_id = serializers.UUIDField()
    instruction = serializers.CharField(required=False, allow_blank=True)
    persona = serializers.DictField(required=False)
    variables = serializers.DictField(required=False)
    solution = serializers.DictField(required=False)
    sub_goals = serializers.ListField(required=False)
    setup_code = serializers.CharField(required=False, allow_blank=True)
    ready_code = serializers.CharField(required=False, allow_blank=True)
    checks = serializers.ListField(required=False)
    max_turns = serializers.IntegerField(required=False, min_value=1)


class ScenarioPatchRequestSerializer(_StrictSerializer):
    gate_status = serializers.ChoiceField(choices=RLScenario.GateStatus.choices, required=False)
    gate_results = serializers.DictField(required=False)
    proved_at = serializers.DateTimeField(required=False, allow_null=True)
    world_id = serializers.UUIDField(required=False)


def scenario_payload(scenario: RLScenario) -> dict:
    return {
        "id": str(scenario.id),
        "environment_id": str(scenario.environment_id),
        "world_id": str(scenario.world_id),
        "name": scenario.name,
        "instruction": scenario.instruction,
        "persona": scenario.persona,
        "variables": scenario.variables,
        "solution": scenario.solution,
        "sub_goals": scenario.sub_goals,
        "setup_code": scenario.setup_code,
        "ready_code": scenario.ready_code,
        "checks": scenario.checks,
        "max_turns": scenario.max_turns,
        "gate_status": scenario.gate_status,
        "gate_results": scenario.gate_results,
        "proved_at": scenario.proved_at.isoformat() if scenario.proved_at else None,
        "created_at": scenario.created_at.isoformat(),
        "updated_at": scenario.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# World copies
# ---------------------------------------------------------------------------


class WorldCopyCreateRequestSerializer(_StrictSerializer):
    environment_id = serializers.UUIDField()
    world_id = serializers.UUIDField()
    scenario_id = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=RLWorldCopy.Purpose.choices)
    run_test_id = serializers.UUIDField(required=False, allow_null=True)
    call_execution_id = serializers.UUIDField(required=False, allow_null=True)
    db_name = serializers.CharField(required=False, allow_blank=True, max_length=63)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class WorldCopyPatchRequestSerializer(_StrictSerializer):
    status = serializers.ChoiceField(choices=RLWorldCopy.Status.choices, required=False)
    db_name = serializers.CharField(required=False, allow_blank=True, max_length=63)
    error = serializers.CharField(required=False, allow_blank=True)
    verdicts = serializers.ListField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class WorldCopyCallLogRequestSerializer(_StrictSerializer):
    entries = serializers.ListField(child=serializers.DictField())

    def validate_entries(self, value):
        if not value:
            raise serializers.ValidationError("entries must not be empty")
        return value


def world_copy_payload(copy: RLWorldCopy) -> dict:
    return {
        "id": str(copy.id),
        "token": str(copy.token),
        "environment_id": str(copy.environment_id),
        "world_id": str(copy.world_id),
        "scenario_id": str(copy.scenario_id),
        "run_test_id": str(copy.run_test_id) if copy.run_test_id else None,
        "call_execution_id": (
            str(copy.call_execution_id) if copy.call_execution_id else None
        ),
        "purpose": copy.purpose,
        "db_name": copy.db_name,
        "status": copy.status,
        "call_log": copy.call_log,
        "verdicts": copy.verdicts,
        "expires_at": copy.expires_at.isoformat() if copy.expires_at else None,
        "error": copy.error,
        "created_at": copy.created_at.isoformat(),
        "updated_at": copy.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class ReportedEvaluationSerializer(_StrictSerializer):
    name = serializers.CharField(max_length=255)
    score = serializers.FloatField(required=False, allow_null=True)
    passed = serializers.BooleanField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True)


class RLVerdictsRequestSerializer(_StrictSerializer):
    evaluations = ReportedEvaluationSerializer(many=True)

    def validate_evaluations(self, value):
        if not value:
            raise serializers.ValidationError("evaluations must not be empty")
        seen_names = set()
        for evaluation in value:
            name = evaluation["name"]
            if name in seen_names:
                # Duplicate names collapse into one eval_output while "stored"
                # would still claim the full request length.
                raise serializers.ValidationError(f"duplicate evaluation name: {name}")
            seen_names.add(name)
        return value
