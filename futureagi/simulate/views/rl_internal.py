"""Internal persistence API for the RL harness runtime.

Endpoints mounted under ``api/rl-harness/`` (see ``simulate/urls.py``). This
surface is service-to-service: the harness runtime authenticates with the
fleet secret (``InternalServiceAuthentication``), never a tenant API key, so
responses are plain row payloads rather than the user-facing envelope.

Reference for the idioms here (internal-service detection, org-from-row
lookups, explicit ``deleted=False`` filters): ``views/alk_simulate_ingestion.py``.
"""

from __future__ import annotations

import uuid

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from simulate.authentication import InternalServiceAuthentication
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    RLContract,
    RLEnvironment,
    RLEnvironmentMessage,
    RLScenario,
    RLWorld,
    RLWorldCopy,
    RunTest,
)
from simulate.serializers.rl_internal import (
    ContractCreateRequestSerializer,
    EnvironmentCreateRequestSerializer,
    EnvironmentPatchRequestSerializer,
    MessageCreateRequestSerializer,
    MessageListQuerySerializer,
    RLVerdictsRequestSerializer,
    ScenarioPatchRequestSerializer,
    ScenarioUpsertRequestSerializer,
    WorldCopyCallLogRequestSerializer,
    WorldCopyCreateRequestSerializer,
    WorldCopyPatchRequestSerializer,
    WorldCreateRequestSerializer,
    WorldPatchRequestSerializer,
    contract_payload,
    environment_payload,
    message_payload,
    scenario_payload,
    world_copy_payload,
    world_payload,
)
from simulate.services.alk_simulate_ingestion import (
    _REPORTED_EVAL_TEMPLATE,
    _store_reported_evaluations,
)


def _error(message: str, status_code: int) -> Response:
    return Response({"error": message}, status=status_code)


class _NotInternalService(Exception):
    """Raised (and caught locally) so the 404 this view gives a non-internal
    caller never reaches the project's global exception handler — that
    handler wraps every response in the tenant-facing envelope, which this
    service-to-service surface must not use."""


class _RLInternalAPIView(APIView):
    """Base for every route in this module.

    Tenant API keys are deliberately not accepted: only
    ``InternalServiceAuthentication`` is registered, so the harness fleet
    secret is the sole credential this surface honors.
    """

    authentication_classes = [InternalServiceAuthentication]
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # The only authenticator registered above is InternalServiceAuthentication,
        # so this can only trip if that invariant is broken later without this
        # check being updated too — a wrong-org probe must be indistinguishable
        # from a row that doesn't exist, hence 404 rather than 403 here.
        if not getattr(request.user, "is_internal_service", False):
            raise _NotInternalService

    def handle_exception(self, exc):
        if isinstance(exc, _NotInternalService):
            return _error("not found", 404)

        response = super().handle_exception(exc)
        data = response.data
        # Views on this surface return serializer.errors (a field-map dict)
        # directly rather than raising, so that shape never reaches here —
        # anything landing in this except-path speaks the {"error": ...}
        # envelope the rest of this service-to-service surface uses.
        if isinstance(data, dict) and "error" not in data:
            is_field_map = response.status_code == 400 and all(
                isinstance(value, (list, dict)) for value in data.values()
            )
            if not is_field_map:
                message = str(exc.detail) if hasattr(exc, "detail") else str(exc)
                response = Response(
                    {"error": message},
                    status=response.status_code,
                    headers=response.headers,
                )
        return response


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


class RLEnvironmentListCreateView(_RLInternalAPIView):
    def post(self, request):
        serializer = EnvironmentCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        organization = Organization.objects.filter(id=data["organization_id"]).first()
        if organization is None:
            return _error("organization not found", 404)

        workspace = Workspace.no_workspace_objects.filter(
            id=data["workspace_id"], organization_id=organization.id, deleted=False
        ).first()
        if workspace is None:
            return _error("workspace not found", 404)

        agent_definition = None
        if data.get("agent_definition_id"):
            agent_definition = AgentDefinition.no_workspace_objects.filter(
                id=data["agent_definition_id"], organization=organization, deleted=False
            ).first()
            if agent_definition is None:
                return _error("agent definition not found", 404)

        agent_version = None
        if data.get("agent_version_id"):
            agent_version = AgentVersion.no_workspace_objects.filter(
                id=data["agent_version_id"], organization=organization, deleted=False
            ).first()
            if agent_version is None:
                return _error("agent version not found", 404)

        # Set explicitly: an internal caller carries no ContextVars, so
        # BaseModel's auto-assignment of workspace never fires.
        environment = RLEnvironment.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            title=data["title"],
            source_kind=data.get("source_kind", ""),
            source_ref=data.get("source_ref", ""),
            simulator_prompt=data.get("simulator_prompt", ""),
            agent_definition=agent_definition,
            agent_version=agent_version,
            run_config=data.get("run_config", {}),
        )
        return Response(environment_payload(environment), status=201)


class RLEnvironmentDetailView(_RLInternalAPIView):
    def get(self, request, environment_id):
        environment = RLEnvironment.no_workspace_objects.filter(
            id=environment_id, deleted=False
        ).first()
        if environment is None:
            return _error("environment not found", 404)

        payload = environment_payload(environment)
        active_contract = (
            RLContract.no_workspace_objects.filter(
                environment=environment, status=RLContract.Status.ACTIVE, deleted=False
            )
            .order_by("-version")
            .first()
        )
        payload["active_contract_version"] = (
            active_contract.version if active_contract else None
        )
        latest_world = (
            RLWorld.no_workspace_objects.filter(environment=environment, deleted=False)
            .order_by("-version")
            .first()
        )
        payload["latest_world_version"] = latest_world.version if latest_world else None
        return Response(payload, status=200)

    def patch(self, request, environment_id):
        environment = RLEnvironment.no_workspace_objects.filter(
            id=environment_id, deleted=False
        ).first()
        if environment is None:
            return _error("environment not found", 404)

        if not request.data:
            return _error("empty body", 400)

        serializer = EnvironmentPatchRequestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        update_fields = []
        for field in (
            "title",
            "phase",
            "status",
            "last_error",
            "simulator_prompt",
            "source_kind",
            "source_ref",
            "run_config",
        ):
            if field in data:
                setattr(environment, field, data[field])
                update_fields.append(field)

        if "agent_definition_id" in data:
            agent_definition = None
            if data["agent_definition_id"]:
                agent_definition = AgentDefinition.no_workspace_objects.filter(
                    id=data["agent_definition_id"],
                    organization=environment.organization,
                    deleted=False,
                ).first()
                if agent_definition is None:
                    return _error("agent definition not found", 404)
            environment.agent_definition = agent_definition
            update_fields.append("agent_definition")

        if "agent_version_id" in data:
            agent_version = None
            if data["agent_version_id"]:
                agent_version = AgentVersion.no_workspace_objects.filter(
                    id=data["agent_version_id"],
                    organization=environment.organization,
                    deleted=False,
                ).first()
                if agent_version is None:
                    return _error("agent version not found", 404)
            environment.agent_version = agent_version
            update_fields.append("agent_version")

        update_fields.append("updated_at")
        environment.save(update_fields=update_fields)
        return Response(environment_payload(environment), status=200)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class RLEnvironmentMessagesView(_RLInternalAPIView):
    def post(self, request, environment_id):
        serializer = MessageCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        with transaction.atomic():
            # The row lock is what makes MAX(seq)+1 safe under concurrency: two
            # racing appends both reading the same MAX before either commits
            # would otherwise assign the same seq to two different messages.
            try:
                locked_environment = (
                    RLEnvironment.no_workspace_objects.select_for_update().get(
                        id=environment_id, deleted=False
                    )
                )
            except RLEnvironment.DoesNotExist:
                return _error("environment not found", 404)
            existing = RLEnvironmentMessage.no_workspace_objects.filter(
                environment=locked_environment, turn_id=data["turn_id"], deleted=False
            ).first()
            if existing is not None:
                return Response(message_payload(existing), status=200)

            max_seq = (
                RLEnvironmentMessage.no_workspace_objects.filter(
                    environment=locked_environment, deleted=False
                ).aggregate(Max("seq"))["seq__max"]
                or 0
            )
            message = RLEnvironmentMessage.no_workspace_objects.create(
                organization=locked_environment.organization,
                environment=locked_environment,
                turn_id=data["turn_id"],
                seq=max_seq + 1,
                role=data["role"],
                text=data.get("text", ""),
                tools=data.get("tools", []),
                phase=data.get("phase", ""),
            )
        return Response(message_payload(message), status=201)

    def get(self, request, environment_id):
        environment = RLEnvironment.no_workspace_objects.filter(
            id=environment_id, deleted=False
        ).first()
        if environment is None:
            return _error("environment not found", 404)

        query = MessageListQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return Response(query.errors, status=400)
        after_seq = query.validated_data["after_seq"]
        limit = min(query.validated_data["limit"], 1000)

        messages = list(
            RLEnvironmentMessage.no_workspace_objects.filter(
                environment=environment, seq__gt=after_seq, deleted=False
            ).order_by("seq")[:limit]
        )
        return Response(
            {
                "messages": [message_payload(message) for message in messages],
                "count": len(messages),
            },
            status=200,
        )


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


class RLEnvironmentContractsView(_RLInternalAPIView):
    def post(self, request, environment_id):
        serializer = ContractCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        with transaction.atomic():
            try:
                locked_environment = (
                    RLEnvironment.no_workspace_objects.select_for_update().get(
                        id=environment_id, deleted=False
                    )
                )
            except RLEnvironment.DoesNotExist:
                return _error("environment not found", 404)
            max_version = (
                RLContract.no_workspace_objects.filter(
                    environment=locked_environment, deleted=False
                ).aggregate(Max("version"))["version__max"]
                or 0
            )
            new_status = data.get("status", RLContract.Status.ACTIVE.value)
            if new_status == RLContract.Status.ACTIVE.value:
                # A crash between this flip and the create below must not leave
                # two active contracts on one environment, so both writes share
                # this transaction.
                RLContract.no_workspace_objects.filter(
                    environment=locked_environment,
                    status=RLContract.Status.ACTIVE,
                    deleted=False,
                ).update(
                    status=RLContract.Status.SUPERSEDED, updated_at=timezone.now()
                )
            contract = RLContract.no_workspace_objects.create(
                organization=locked_environment.organization,
                environment=locked_environment,
                version=max_version + 1,
                status=new_status,
                data=data["data"],
            )
        return Response(contract_payload(contract), status=201)


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


class RLEnvironmentWorldsView(_RLInternalAPIView):
    def post(self, request, environment_id):
        serializer = WorldCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        with transaction.atomic():
            try:
                locked_environment = (
                    RLEnvironment.no_workspace_objects.select_for_update().get(
                        id=environment_id, deleted=False
                    )
                )
            except RLEnvironment.DoesNotExist:
                return _error("environment not found", 404)

            contract = RLContract.no_workspace_objects.filter(
                id=data["contract_id"], environment=locked_environment, deleted=False
            ).first()
            if contract is None:
                return _error("contract not found", 404)

            max_version = (
                RLWorld.no_workspace_objects.filter(
                    environment=locked_environment, deleted=False
                ).aggregate(Max("version"))["version__max"]
                or 0
            )
            world = RLWorld.no_workspace_objects.create(
                organization=locked_environment.organization,
                environment=locked_environment,
                contract=contract,
                version=max_version + 1,
                status=data.get("status", RLWorld.Status.SAVED.value),
                store_kind=data.get("store_kind", RLWorld.StoreKind.POSTGRES.value),
                schema_scripts=data.get("schema_scripts", []),
                snapshot=data.get("snapshot", {}),
                state=data.get("state", {}),
                handlers=data.get("handlers", {}),
                tool_specs=data.get("tool_specs", []),
                world_checks=data.get("world_checks", {}),
                refusal_signature=data.get("refusal_signature", ""),
                master_db_name=data.get("master_db_name", ""),
            )
            # A scenario proved against a world this save has just superseded is
            # a stale verdict, not a valid one; the flip shares this transaction
            # with the world save so a crash between the two can't leave that
            # stale verdict looking current.
            scenarios_marked_stale = RLScenario.no_workspace_objects.filter(
                environment=locked_environment,
                gate_status__in=[
                    RLScenario.GateStatus.PASSED,
                    RLScenario.GateStatus.FAILED,
                    RLScenario.GateStatus.PROVING,
                ],
                deleted=False,
            ).update(gate_status=RLScenario.GateStatus.STALE, updated_at=timezone.now())

        return Response(
            {
                "id": str(world.id),
                "version": world.version,
                "scenarios_marked_stale": scenarios_marked_stale,
            },
            status=201,
        )


class RLEnvironmentWorldDetailView(_RLInternalAPIView):
    def get(self, request, environment_id, version):
        environment = RLEnvironment.no_workspace_objects.filter(
            id=environment_id, deleted=False
        ).first()
        if environment is None:
            return _error("environment not found", 404)

        world = (
            RLWorld.no_workspace_objects.filter(
                environment=environment, version=version, deleted=False
            )
            .select_related("contract")
            .first()
        )
        if world is None:
            return _error("world not found", 404)
        return Response(world_payload(world), status=200)


class RLWorldDetailView(_RLInternalAPIView):
    def get(self, request, world_id):
        world = (
            RLWorld.no_workspace_objects.filter(id=world_id, deleted=False)
            .select_related("contract")
            .first()
        )
        if world is None:
            return _error("world not found", 404)
        return Response(world_payload(world), status=200)

    def patch(self, request, world_id):
        world = (
            RLWorld.no_workspace_objects.filter(id=world_id, deleted=False)
            .select_related("contract")
            .first()
        )
        if world is None:
            return _error("world not found", 404)

        serializer = WorldPatchRequestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        update_fields = []
        for field in ("status", "master_db_name", "master_materialized_at"):
            if field in data:
                setattr(world, field, data[field])
                update_fields.append(field)
        update_fields.append("updated_at")
        world.save(update_fields=update_fields)
        return Response(world_payload(world), status=200)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

_SCENARIO_OPTIONAL_FIELDS = (
    "instruction",
    "persona",
    "variables",
    "solution",
    "sub_goals",
    "setup_code",
    "ready_code",
    "checks",
    "max_turns",
)


class RLEnvironmentScenariosView(_RLInternalAPIView):
    def post(self, request, environment_id):
        serializer = ScenarioUpsertRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        with transaction.atomic():
            # The environment row lock serializes concurrent upserts of the
            # same scenario name so two racing requests can't both take the
            # create branch and collide on the name constraint.
            try:
                locked_environment = (
                    RLEnvironment.no_workspace_objects.select_for_update().get(
                        id=environment_id, deleted=False
                    )
                )
            except RLEnvironment.DoesNotExist:
                return _error("environment not found", 404)

            world = RLWorld.no_workspace_objects.filter(
                id=data["world_id"], environment=locked_environment, deleted=False
            ).first()
            if world is None:
                return _error("world not found", 404)

            existing = RLScenario.no_workspace_objects.filter(
                environment=locked_environment, name=data["name"], deleted=False
            ).first()

            if existing is not None:
                update_fields = []
                for field in _SCENARIO_OPTIONAL_FIELDS:
                    if field in data:
                        setattr(existing, field, data[field])
                        update_fields.append(field)
                existing.world = world
                # An edited scenario has not been re-proven against its (possibly
                # new) world, so its previous verdict cannot stand.
                existing.gate_status = RLScenario.GateStatus.UNPROVEN
                existing.gate_results = {}
                existing.proved_at = None
                update_fields.extend(
                    ["world", "gate_status", "gate_results", "proved_at", "updated_at"]
                )
                existing.save(update_fields=update_fields)
                return Response(scenario_payload(existing), status=200)

            create_kwargs = {
                "organization": locked_environment.organization,
                "environment": locked_environment,
                "world": world,
                "name": data["name"],
                "instruction": data.get("instruction", ""),
                "persona": data.get("persona", {}),
                "variables": data.get("variables", {}),
                "solution": data.get("solution", []),
                "sub_goals": data.get("sub_goals", []),
                "setup_code": data.get("setup_code", ""),
                "ready_code": data.get("ready_code", ""),
                "checks": data.get("checks", {}),
            }
            # Omit when absent so the model default (10) applies, rather than
            # duplicating that literal here where it can drift out of sync.
            if "max_turns" in data:
                create_kwargs["max_turns"] = data["max_turns"]
            scenario = RLScenario.no_workspace_objects.create(**create_kwargs)
        return Response(scenario_payload(scenario), status=201)


class RLScenarioDetailView(_RLInternalAPIView):
    def get(self, request, scenario_id):
        scenario = RLScenario.no_workspace_objects.filter(
            id=scenario_id, deleted=False
        ).first()
        if scenario is None:
            return _error("scenario not found", 404)
        return Response(scenario_payload(scenario), status=200)

    def patch(self, request, scenario_id):
        scenario = RLScenario.no_workspace_objects.filter(
            id=scenario_id, deleted=False
        ).first()
        if scenario is None:
            return _error("scenario not found", 404)

        serializer = ScenarioPatchRequestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        update_fields = []
        if "world_id" in data:
            world = RLWorld.no_workspace_objects.filter(
                id=data["world_id"], environment_id=scenario.environment_id, deleted=False
            ).first()
            if world is None:
                return _error("world not found", 404)
            scenario.world = world
            update_fields.append("world")

        for field in ("gate_status", "gate_results", "proved_at"):
            if field in data:
                setattr(scenario, field, data[field])
                update_fields.append(field)

        update_fields.append("updated_at")
        scenario.save(update_fields=update_fields)
        return Response(scenario_payload(scenario), status=200)


# ---------------------------------------------------------------------------
# World copies
# ---------------------------------------------------------------------------


class RLWorldCopyListCreateView(_RLInternalAPIView):
    def get(self, request):
        raw_call_execution_id = request.query_params.get("call_execution_id")
        if not raw_call_execution_id:
            return _error("call_execution_id is required", 400)
        try:
            call_execution_id = uuid.UUID(raw_call_execution_id)
        except ValueError:
            return _error(f"call_execution_id is not a uuid: {raw_call_execution_id}", 400)

        copy = RLWorldCopy.no_workspace_objects.filter(
            call_execution_id=call_execution_id, deleted=False
        ).first()
        if copy is None:
            return _error("no copy for call execution", 404)
        return Response(world_copy_payload(copy), status=200)

    def post(self, request):
        serializer = WorldCopyCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        environment = RLEnvironment.no_workspace_objects.filter(
            id=data["environment_id"], deleted=False
        ).first()
        if environment is None:
            return _error("environment not found", 404)

        world = RLWorld.no_workspace_objects.filter(
            id=data["world_id"], environment=environment, deleted=False
        ).first()
        if world is None:
            return _error("world not found", 404)

        scenario = RLScenario.no_workspace_objects.filter(
            id=data["scenario_id"], environment=environment, deleted=False
        ).first()
        if scenario is None:
            return _error("scenario not found", 404)

        run_test = None
        if data.get("run_test_id"):
            run_test = RunTest.no_workspace_objects.filter(
                id=data["run_test_id"], organization=environment.organization, deleted=False
            ).first()
            if run_test is None:
                return _error("run test not found", 404)

        call_execution_id = data.get("call_execution_id")
        call_execution = None
        if call_execution_id:
            call_execution = (
                CallExecution.no_workspace_objects.filter(
                    id=call_execution_id, deleted=False
                )
                .select_related("test_execution__run_test")
                .first()
            )
            if call_execution is None:
                return _error("call execution not found", 404)
            if (
                call_execution.test_execution.run_test.organization_id
                != environment.organization_id
            ):
                return _error("call execution not found", 404)

            # This is the retried-prepare contract: a caller that already holds a
            # copy for this call_execution gets that same copy back rather than
            # a second one.
            existing = RLWorldCopy.no_workspace_objects.filter(
                call_execution_id=call_execution_id, deleted=False
            ).first()
            if existing is not None:
                return Response(world_copy_payload(existing), status=200)

        try:
            # Isolated so an IntegrityError here can't poison an enclosing
            # transaction — the except-branch below still needs to run a
            # query against the same connection.
            with transaction.atomic():
                copy = RLWorldCopy.no_workspace_objects.create(
                    organization=environment.organization,
                    environment=environment,
                    world=world,
                    scenario=scenario,
                    run_test=run_test,
                    call_execution=call_execution,
                    purpose=data["purpose"],
                    db_name=data.get("db_name", ""),
                    expires_at=data.get("expires_at"),
                )
        except IntegrityError:
            if not call_execution_id:
                # Nothing about this create should be able to collide with an
                # existing row when there's no call_execution to key off of —
                # recovering via the lookup below would hide a real failure.
                raise
            # Lost the create race against another retried prepare for the same
            # call_execution; the DB constraint is what actually enforces the
            # idempotency contract above, so the winner's row is returned.
            copy = RLWorldCopy.no_workspace_objects.get(
                call_execution_id=call_execution_id, deleted=False
            )
            return Response(world_copy_payload(copy), status=200)
        return Response(world_copy_payload(copy), status=201)


class RLWorldCopyByTokenView(_RLInternalAPIView):
    def get(self, request, token):
        copy = RLWorldCopy.no_workspace_objects.filter(token=token, deleted=False).first()
        if copy is None:
            return _error("unknown token", 404)
        return Response(world_copy_payload(copy), status=200)


class RLWorldCopyDetailView(_RLInternalAPIView):
    def patch(self, request, copy_id):
        copy = RLWorldCopy.no_workspace_objects.filter(id=copy_id, deleted=False).first()
        if copy is None:
            return _error("world copy not found", 404)

        serializer = WorldCopyPatchRequestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        # Terminal statuses are never overwritten; callers treat 409 as
        # already-settled.
        if "status" in data and copy.status in (
            RLWorldCopy.Status.GRADED,
            RLWorldCopy.Status.DROPPED,
            RLWorldCopy.Status.EXPIRED,
        ):
            return Response(
                {"error": f"copy is {copy.status}", "status": copy.status}, status=409
            )

        update_fields = []
        for field in ("status", "db_name", "error", "verdicts", "expires_at", "state"):
            if field in data:
                setattr(copy, field, data[field])
                update_fields.append(field)
        update_fields.append("updated_at")
        copy.save(update_fields=update_fields)
        return Response(world_copy_payload(copy), status=200)


class RLWorldCopyCallLogView(_RLInternalAPIView):
    def post(self, request, copy_id):
        serializer = WorldCopyCallLogRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data
        entries = data["entries"]

        with transaction.atomic():
            # Two concurrent tool calls appending to the same copy must both
            # land; the row lock serializes the read-modify-write.
            copy = RLWorldCopy.no_workspace_objects.select_for_update().filter(
                id=copy_id, deleted=False
            ).first()
            if copy is None:
                return _error("world copy not found", 404)
            # An abandoned handler must not corrupt evidence behind an
            # already-computed grade, so once a copy leaves READY/IN_CALL its
            # call log is frozen.
            if copy.status not in (RLWorldCopy.Status.READY, RLWorldCopy.Status.IN_CALL):
                return _error(f"copy is {copy.status}", 409)
            update_fields = ["call_log", "updated_at"]
            copy.call_log = copy.call_log + entries
            if "state" in data:
                copy.state = data["state"]
                update_fields.append("state")
            copy.save(update_fields=update_fields)
        return Response({"count": len(copy.call_log)}, status=200)


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class RLVerdictsView(_RLInternalAPIView):
    def post(self, request, call_execution_id):
        serializer = RLVerdictsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        evaluations = serializer.validated_data["evaluations"]

        call_execution = (
            CallExecution.no_workspace_objects.filter(id=call_execution_id, deleted=False)
            .select_related("test_execution__run_test")
            .first()
        )
        if call_execution is None:
            return _error("call execution not found", 404)

        # _store_reported_evaluations silently no-ops when this template is
        # missing, which is fine for the ingestion path it was built for (a
        # reported eval is one signal among several there). Here it is the
        # entire point of the call, so a missing template must fail loudly
        # instead of returning 200 having stored nothing.
        template_exists = EvalTemplate.no_workspace_objects.filter(
            name=_REPORTED_EVAL_TEMPLATE, organization__isnull=True
        ).exists()
        if not template_exists:
            return _error(
                f"reported-evals template {_REPORTED_EVAL_TEMPLATE} is not seeded", 500
            )

        _store_reported_evaluations(call_execution, evaluations)
        call_execution.save(update_fields=["eval_outputs", "call_metadata", "updated_at"])
        return Response({"stored": len(evaluations)}, status=200)
