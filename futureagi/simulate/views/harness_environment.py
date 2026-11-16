from uuid import UUID

from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from model_hub.models.develop_dataset import Dataset
from simulate.models import AgentDefinition, EvidenceImport, HostedHarnessJob
from simulate.pydantic_schemas.evidence_import import DatasetColumns
from simulate.selectors.evidence_import import (
    environment_evidence_import,
    evidence_dataset,
    evidence_project,
    import_bundle,
    open_evidence_imports,
)
from simulate.selectors.held_back import (
    coding_agent_key,
    detail_without,
    members_without,
    refusal_body,
    verify_result,
    verify_run,
)
from simulate.selectors.runs import user_executions
from simulate.serializers.evidence_import import (
    EvidenceImportAddResponseSerializer,
    EvidenceImportAddSerializer,
    EvidenceImportCreateSerializer,
    EvidenceImportDraftSerializer,
    EvidenceImportListItemSerializer,
    EvidenceImportSerializer,
)
from simulate.serializers.harness_actors import (
    HarnessActorChangesSerializer,
    HarnessActorDeleteQuerySerializer,
    HarnessActorLimitsSerializer,
    HarnessActorLimitsWriteSerializer,
    HarnessActorListSerializer,
    HarnessActorUnresolvedAcceptSerializer,
    HarnessActorValidateSerializer,
    HarnessActorValidationSerializer,
    HarnessActorViewQuerySerializer,
    HarnessActorViewSerializer,
    HarnessActorWriteSerializer,
    HarnessEnvironmentFileUploadedSerializer,
    HarnessEnvironmentFileUploadSerializer,
    editor_problems,
)
from simulate.serializers.harness_environment import (
    HarnessAgentCredentialsResponseSerializer,
    HarnessAgentVersionCreateSerializer,
    HarnessCapabilityCasePageSerializer,
    HarnessCapabilityCasesQuerySerializer,
    HarnessCapabilityHistoryQuerySerializer,
    HarnessCapabilityRecheckResponseSerializer,
    HarnessCapabilityRecheckSerializer,
    HarnessCapabilitySelectionQuerySerializer,
    HarnessCapabilitySelectionReportSerializer,
    HarnessCapabilitySelectionSerializer,
    HarnessCertifiedPairingPageSerializer,
    HarnessCompareQuerySerializer,
    HarnessCompareSerializer,
    HarnessDefaultMoveSerializer,
    HarnessDefaultMoveValidatingSerializer,
    HarnessDraftSaveResponseSerializer,
    HarnessDraftSaveSerializer,
    HarnessDraftsSerializer,
    HarnessEnvironmentAddEvaluationSerializer,
    HarnessEnvironmentAvailableEvalsSerializer,
    HarnessEnvironmentDetailSerializer,
    HarnessEnvironmentEvalEditSerializer,
    HarnessEnvironmentListQuerySerializer,
    HarnessEnvironmentListResponseSerializer,
    HarnessEnvironmentRenameSerializer,
    HarnessEnvironmentRunEvaluationQueuedSerializer,
    HarnessEnvironmentRunEvaluationsQueuedSerializer,
    HarnessEnvironmentRunEvaluationsSerializer,
    HarnessEnvironmentToolCallEvaluationSerializer,
    HarnessNudgeAnswerSerializer,
    HarnessNudgeListQuerySerializer,
    HarnessNudgeSerializer,
    HarnessRunPlanQuerySerializer,
    HarnessRunPlanSerializer,
    HarnessSuiteMemberSerializer,
    HarnessVerifyCreateResponseSerializer,
    HarnessVerifyCreateSerializer,
    HarnessVerifyResultSerializer,
    HarnessVersionDiffQuerySerializer,
    HarnessVersionDiffSerializer,
    HarnessVersionsSerializer,
)
from simulate.serializers.harness_job import (
    HarnessRunCreateResponseSerializer,
    HarnessRunCreateSerializer,
)
from simulate.serializers.response.run_test import SimulateEvalConfigResponseSerializer
from simulate.services import (
    harness_actor_reads,
    harness_actor_uploads,
    harness_actor_validation,
    harness_actor_view,
    harness_actors,
)
from simulate.services.evidence_import import (
    TranscriptInput,
    add_to_suite,
    create_dataset_import,
    create_evidence_import,
    create_transcript_import,
    discard_evidence_import,
)
from simulate.services.harness_actor_rules import UPLOAD_MAX_BYTES, ActorLimits
from simulate.services.harness_actor_validation import Problem
from simulate.services.harness_environment import (
    annotate_for_list,
    environment_detail,
    environment_rows,
    eval_modality,
)
from simulate.services.harness_provider import (
    _usage_limit_response,
    get_harness_provider,
    request_organization,
    request_workspace,
    scope_jobs,
)
from simulate.services.harness_verify import require_default_suite, start_verify
from simulate.services.harness_versioning import read as version_reads
from simulate.services.hosted_harness import HostedHarnessError, VersionPicks
from simulate.views.harness_starting_records import HarnessStartingRecordsMixin
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import EmptyRequestSerializer
from tfc.utils.errors import format_validation_error
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.utils.workspace_scope import (
    project_queryset_for_request,
    project_workspace_scope_q,
)


def _refused(code: str, errors: dict) -> Response:
    """A request its serializer refuses, answered as the service refuses one: `code` (400)."""
    refused = HostedHarnessError(code, format_validation_error(errors))
    return Response(refused.as_dict(), status=status.HTTP_400_BAD_REQUEST)


def _limits_refused(errors: dict) -> Response:
    """Limits their serializer refuses, as the service refuses them: `limits_invalid` with a problem per limit."""
    limits = errors.get("limits")
    if set(errors) != {"limits"} or not isinstance(limits, dict):
        return _refused("limits_invalid", errors)
    refused = harness_actor_validation.limits_invalid(
        editor_problems(HarnessActorLimitsSerializer(), limits)
    )
    return Response(refused.as_dict(), status=status.HTTP_400_BAD_REQUEST)


def _changes_problems(errors: dict) -> list[Problem] | None:
    """The problems of an actor's `changes` and `id` their serializer refuses, on the editor's fields; none when the
    request is wrong elsewhere too."""
    changes = errors.get("changes", {})
    if not set(errors) <= {"changes", "id"} or not isinstance(changes, dict):
        return None
    return editor_problems(HarnessActorChangesSerializer(), changes) + editor_problems(
        HarnessActorValidateSerializer(),
        {key: value for key, value in errors.items() if key == "id"},
    )


def _actor_write_refused(errors: dict) -> Response:
    """An actor write whose `changes` its serializer refuses, answered as the check refuses one: `actor_invalid`
    (400) with its problems."""
    problems = _changes_problems(errors)
    if problems is None:
        return Response(
            build_error_envelope(errors), status=status.HTTP_400_BAD_REQUEST
        )
    refused = harness_actor_validation.actor_invalid(problems)
    return Response(refused.as_dict(), status=status.HTTP_400_BAD_REQUEST)


def _actor_check_refused(errors: dict) -> Response:
    """An actor check whose `changes` its serializer refuses: a verdict (200) with its problems, as for any other
    problem with the edit."""
    problems = _changes_problems(errors)
    if problems is None:
        return Response(
            build_error_envelope(errors), status=status.HTTP_400_BAD_REQUEST
        )
    return Response(harness_actors.request_problems_verdict(problems))


def _upload_refused(errors: dict) -> Response:
    return _refused(
        "upload_kind_unknown" if "kind" in errors else "upload_invalid", errors
    )


def _touch_content(job):
    """Record that the environment's content changed, for the list's clock.

    Which evals grade an environment is part of what the environment is, so
    binding or removing one moves the row the same way a pipeline stage does,
    and so is whether its tool calls are graded.
    """
    job.content_updated_at = timezone.now()
    job.save(update_fields=["content_updated_at", "updated_at"])


def _bound_by_name(run_test, name):
    """The live config this environment already binds under ``name``, if any.

    "Grade this run" names an eval the environment already has. A person may
    have added it from outside the harness's offer or under a name of their
    own, so the offer's gates would refuse it even though it is bound and
    gradeable. A config's own name wins over any template's name, so a
    config cannot be shadowed by an older one whose template happens to be
    called the same; within each, the oldest binding wins.
    """
    from simulate.services.harness_evals import selected_eval_configs

    configs = selected_eval_configs(run_test)
    for config in configs:
        if name == str(config.name or ""):
            return config
    for config in configs:
        if name == str(getattr(config.eval_template, "name", "") or ""):
            return config
    return None


def _selection_report(job, picks, *, keys, trials):
    from simulate.services.harness_readiness import resolve_pairing, selection_report

    pairing = resolve_pairing(job, **picks)
    return selection_report(pairing, keys=keys, trials=trials)


def _held_back_from(request, job, suite_version_id=None) -> tuple[str, ...]:
    """The scenarios this caller may not read: the held-back cases for a coding agent,
    chosen on its first read; none for anyone else. A coding agent may read or pick
    only the default suite: ``suite_version_id`` is refused as not found otherwise."""
    if coding_agent_key(request) is None:
        return ()
    return require_default_suite(job, suite_version_id).scenario_keys


def _uuid_or_none(value):
    """The id as a UUID, or ``None`` when it is not one.

    The router matches any segment without a slash or dot, so a hand-edited URL
    reaches the view as a string the column cannot hold. Filtering on it raises
    a Django ``ValidationError``, which DRF's handler does not translate and
    which therefore surfaces as a 500. A miss is the honest answer: the caller
    named something that cannot exist.
    """
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


class HarnessEnvironmentViewSet(HarnessStartingRecordsMixin, viewsets.ViewSet):
    """The environments surface: list, delete, and start a simulation.

    An environment is the job that built it (the world itself lives in object
    storage, addressed from the job's metadata), so these endpoints project the
    same rows the harness-jobs API serves. They exist separately because the
    list needs a row, not a run: the jobs list returns every event, receipt and
    stage-output payload for up to a hundred jobs, which is a detail document
    repeated a hundred times.

    Running and grading a simulation are deliberately not here. ``run`` starts
    one and returns 202; progress is read from the job.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination

    def _queryset(self, request):
        return annotate_for_list(
            scope_jobs(
                HostedHarnessJob.no_workspace_objects.filter(
                    organization=request_organization(request),
                    environment__isnull=True,
                    deleted=False,
                ),
                request,
            )
        )

    def _job(self, request, pk):
        identifier = _uuid_or_none(pk)
        if identifier is None:
            return None
        return self._queryset(request).filter(id=identifier).first()

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(refusal_body(exc, self.request), status=exc.status_code)
        response = _usage_limit_response(exc)
        if response is not None:
            return response
        return super().handle_exception(exc)

    def _not_found(self):
        return Response(
            {"detail": "Environment not found"}, status=status.HTTP_404_NOT_FOUND
        )

    @validated_request(responses={200: HarnessVersionsSerializer})
    @action(detail=True, methods=["get"], url_path="versions")
    def versions(self, request, pk=None):
        """Every agent, environment and suite version of this environment, read-only."""
        # The flag outranks every other 404, so a disabled feature reads as disabled.
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(version_reads.list_versions(job))

    @validated_request(
        query_serializer=HarnessVersionDiffQuerySerializer,
        responses={200: HarnessVersionDiffSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=(r"versions/(?P<piece>[a-z]+)/(?P<version_id>[0-9a-fA-F-]{36})/diff"),
    )
    def version_diff(self, request, pk=None, piece=None, version_id=None):
        """What one version changed, against its parent or the ``against`` version."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(version_id)
        if identifier is None:
            raise version_reads.version_not_found()
        against = request.validated_query_data.get("against")
        return Response(version_reads.version_diff(job, piece, identifier, against))

    @validated_request(
        request_serializer=HarnessDefaultMoveSerializer,
        responses={
            200: HarnessVersionsSerializer,
            202: HarnessDefaultMoveValidatingSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=(
            r"versions/(?P<piece>[a-z]+)/(?P<version_id>[0-9a-fA-F-]{36})/default"
        ),
    )
    def move_default(self, request, pk=None, piece=None, version_id=None):
        """Make an agent, environment or suite version the default; returns every
        version. An agent move that waits for its pairing to validate is a 202."""
        from simulate.services.harness_versioning.versions import (
            MOVABLE_PIECES,
            move_agent_default,
            move_default,
        )

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(version_id)
        if piece not in MOVABLE_PIECES or identifier is None:
            raise version_reads.version_not_found()
        expected_default_id = request.validated_data["expected_default_id"]
        if piece != "agent":
            moved = move_default(
                job,
                piece,
                identifier,
                expected_default_id=expected_default_id,
                user=request.user,
            )
            return Response(version_reads.list_versions(moved))
        moved, validation = move_agent_default(
            job, identifier, expected_default_id=expected_default_id, user=request.user
        )
        if validation is None or validation.ready:
            return Response(version_reads.list_versions(moved))
        return Response(
            {
                "versions": version_reads.list_versions(moved),
                "validation": validation.as_view(),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _agent_version(self, job, version_id):
        """One agent version of this environment's agent, or a 404."""
        identifier = _uuid_or_none(version_id)
        found = (
            version_reads._agent_versions(job, [identifier])
            if identifier is not None
            else []
        )
        if not found:
            raise version_reads.version_not_found()
        return found[0]

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: HarnessVersionsSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"versions/agent/(?P<version_id>[0-9a-fA-F-]{36})/discard",
    )
    def discard_agent_version(self, request, pk=None, version_id=None):
        """Set an agent version aside; returns every version."""
        from simulate.services.harness_agent_versions import (
            discard_agent_version,
            require_environments_version,
        )

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        version = self._agent_version(job, version_id)
        require_environments_version(job, version)
        discard_agent_version(version, by_environment=job.id)
        return Response(version_reads.list_versions(job))

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={202: HarnessVersionsSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"versions/agent/(?P<version_id>[0-9a-fA-F-]{36})/retry",
    )
    def retry_agent_version(self, request, pk=None, version_id=None):
        """Check an agent version whose check failed again; returns every version."""
        from simulate.services.harness_agent_versions import (
            require_environments_version,
            retry_preflight,
        )

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        version = self._agent_version(job, version_id)
        require_environments_version(job, version)
        retry_preflight(version)
        return Response(
            version_reads.list_versions(job), status=status.HTTP_202_ACCEPTED
        )

    @validated_request(
        request_serializer=HarnessAgentVersionCreateSerializer,
        responses={202: HarnessAgentCredentialsResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="versions/agent")
    def create_agent_version(self, request, pk=None):
        """A new agent version from an upload, a prompt edit or a settings edit; it is
        checked next, then made this environment's default unless make_default is
        false. A coding agent key pushes only an upload, never made the default."""
        from simulate.services.harness_agent_changes import change_agent_source

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        change = dict(request.validated_data)
        if coding_agent_key(request) is not None:
            if "archive_artifact_id" not in change:
                # The loop is built for uploaded-folder agents; their prompt and
                # settings stay with people.
                raise PermissionDenied(
                    "A coding agent key can push only an uploaded folder",
                    code="coding_agent_candidate_unsupported",
                )
            # Shipping a version stays with people; a coding agent pushes candidates.
            if change.get("make_default"):
                raise PermissionDenied(
                    "A coding agent key cannot make a version the default",
                    code="coding_agent_cannot_ship",
                )
            change["make_default"] = False
        version = change_agent_source(job, **change, user=request.user)
        job.refresh_from_db(fields=["agent_version"])
        return Response(
            {"agent_version": version_reads.agent_version_item(job, version)},
            status=status.HTTP_202_ACCEPTED,
        )

    @validated_request(responses={200: HarnessSuiteMemberSerializer(many=True)})
    @action(
        detail=True,
        methods=["get"],
        url_path=r"versions/suite/(?P<version_id>[0-9a-fA-F-]{36})/members",
    )
    def suite_members(self, request, pk=None, version_id=None):
        """The scenarios in one suite version, by number; a coding agent's read leaves
        out the held-back cases."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(version_id)
        if identifier is None:
            raise version_reads.version_not_found()
        hidden = _held_back_from(request, job, identifier)
        members = version_reads.suite_members(job, identifier)
        return Response(members_without(members, hidden))

    @validated_request(
        request_serializer=HarnessVerifyCreateSerializer,
        responses={202: HarnessVerifyCreateResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="verify")
    def verify(self, request, pk=None):
        """Verify an agent version on the held-back cases, in a run no user read shows.
        Its live or completed verify is final: asking again returns it. After one that
        failed or was canceled, asking again starts a new one."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        run = start_verify(job, **request.validated_data)
        return Response(
            {"verify_id": str(run.id), "state": run.state},
            status=status.HTTP_202_ACCEPTED,
        )

    @validated_request(responses={200: HarnessVerifyResultSerializer})
    @action(
        detail=True,
        methods=["get"],
        url_path=r"verify/(?P<verify_id>[0-9a-fA-F-]{36})",
        url_name="verify-detail",
    )
    def verify_detail(self, request, pk=None, verify_id=None):
        """How the verified version did on the held-back cases, as counts only."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(verify_id)
        run = verify_run(job, identifier) if identifier is not None else None
        if run is None:
            raise NotFound("Verify not found")
        return Response(verify_result(run))

    @validated_request(
        responses={200: HarnessActorListSerializer},
        operation_id="simulate_api_harness-environments_actors",
    )
    @action(detail=True, methods=["get"], url_path="actors")
    def actors(self, request, pk=None):
        """The environment's cast as the next run would use it: the draft's when there is one."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(harness_actor_reads.read_actors(job))

    @validated_request(
        request_serializer=HarnessActorWriteSerializer,
        responses={200: HarnessActorListSerializer},
        request_methods=["POST"],
        reject_unknown_fields=True,
        validation_error_response=_actor_write_refused,
        operation_id="simulate_api_harness-environments_create_actor",
    )
    @actors.mapping.post
    def create_actor(self, request, pk=None):
        """Add one actor to the environment's draft; the saved version is unchanged until the draft is saved."""
        return self._actor_edit(request, pk, op="create")

    @validated_request(
        query_serializer=HarnessActorViewQuerySerializer,
        responses={200: HarnessActorViewSerializer},
        operation_id="simulate_api_harness-environments_actor_view",
    )
    @action(detail=True, methods=["get"], url_path=r"actors/(?P<actor_id>[^/]{1,64})")
    def actor_detail(self, request, pk=None, actor_id=None):
        """What one actor sees and may do, optionally in one scenario, and what it never sees."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(
            harness_actor_view.actor_view(
                job,
                actor_id,
                scenario_key=request.validated_query_data.get("scenario_key"),
            )
        )

    @validated_request(
        request_serializer=HarnessActorWriteSerializer,
        responses={200: HarnessActorListSerializer},
        request_methods=["PUT"],
        reject_unknown_fields=True,
        validation_error_response=_actor_write_refused,
        operation_id="simulate_api_harness-environments_replace_actor",
    )
    @actor_detail.mapping.put
    def replace_actor(self, request, pk=None, actor_id=None):
        """Replace one actor in the draft, as a whole record or as changes to it; its id never changes."""
        return self._actor_edit(request, pk, op="replace", actor_id=actor_id)

    @validated_request(
        query_serializer=HarnessActorDeleteQuerySerializer,
        responses={200: HarnessActorListSerializer},
        request_methods=["DELETE"],
        operation_id="simulate_api_harness-environments_delete_actor",
    )
    @actor_detail.mapping.delete
    def delete_actor(self, request, pk=None, actor_id=None):
        """Take one actor out of the draft; refused while a scenario includes it."""
        return self._actor_edit(request, pk, op="delete", actor_id=actor_id)

    @validated_request(
        request_serializer=HarnessActorValidateSerializer,
        responses={200: HarnessActorValidationSerializer},
        request_methods=["POST"],
        reject_unknown_fields=True,
        validation_error_response=_actor_check_refused,
        operation_id="simulate_api_harness-environments_validate_actor",
    )
    # Beside `actors/`, not under it: an actor's id may be any word, `validate` included.
    @action(detail=True, methods=["post"], url_path="actor-validation")
    def validate_actor(self, request, pk=None):
        """What a create, replace or removal of one actor would say, writing nothing: its problems, the actions it
        would share that `share_actions` does not confirm, the id a create gets, and how far it is over the bound."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        data = request.validated_data
        return Response(
            harness_actors.check_actor_edit(
                job,
                op=data["op"],
                actor_id=data.get("actor_id"),
                body=None,
                changes=data.get("changes"),
                new_id=data.get("id"),
                user=request.user,
                expected_revision=None,
                share_actions=data.get("share_actions"),
            )
        )

    @validated_request(
        request_serializer=HarnessActorLimitsWriteSerializer,
        responses={200: HarnessActorListSerializer},
        request_methods=["PUT"],
        reject_unknown_fields=True,
        validation_error_response=_limits_refused,
        operation_id="simulate_api_harness-environments_actor_limits",
    )
    @action(detail=True, methods=["put"], url_path="actor-limits")
    def actor_limits(self, request, pk=None):
        """Set the environment's actor limits in its draft (each a number, or null for no limit) and its actors' model."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        limits: ActorLimits = request.validated_data.get("limits") or ActorLimits()
        return Response(
            harness_actors.write_actor_limits(
                job,
                limits=limits,
                user=request.user,
                expected_revision=request.validated_data.get("expected_revision"),
            )
        )

    @validated_request(
        request_serializer=HarnessActorUnresolvedAcceptSerializer,
        responses={200: HarnessActorListSerializer},
        request_methods=["POST"],
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_accept_unresolved",
    )
    @action(detail=True, methods=["post"], url_path="actor-unresolved/accept")
    def accept_unresolved(self, request, pk=None):
        """Accept the cast as written for one policy point the builder could not model."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(
            harness_actors.accept_unresolved(
                job,
                policy=request.validated_data["policy"],
                user=request.user,
                expected_revision=request.validated_data.get("expected_revision"),
            )
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="files",
        parser_classes=[MultiPartParser],
    )
    @validated_request(
        request_serializer=HarnessEnvironmentFileUploadSerializer,
        responses={200: HarnessEnvironmentFileUploadedSerializer},
        validation_error_response=_upload_refused,
        operation_id="simulate_api_harness-environments_upload_file",
    )
    def upload_file(self, request, pk=None):
        """Put one uploaded file into the environment's draft under its kind; the cast's knowledge sources then list
        it, for picking per actor."""
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        data = request.validated_data
        upload = data["file"]
        cast, path = harness_actor_uploads.upload_environment_file(
            job,
            kind=data["kind"],
            name=str(upload.name or ""),
            # Read one byte past the bound, so a larger file is refused without reading all of it.
            content=upload.read(UPLOAD_MAX_BYTES + 1),
            user=request.user,
            expected_revision=data.get("expected_revision"),
        )
        return Response({**cast, "uploaded_path": path})

    def _actor_edit(self, request, pk, *, op, actor_id=None):
        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        data = (
            request.validated_query_data if op == "delete" else request.validated_data
        )
        return Response(
            harness_actors.write_actor_edit(
                job,
                op=op,
                actor_id=actor_id,
                body=data.get("actor"),
                changes=data.get("changes"),
                new_id=data.get("id"),
                user=request.user,
                expected_revision=data.get("expected_revision"),
                share_actions=data.get("share_actions"),
            )
        )

    @validated_request(responses={200: HarnessDraftsSerializer})
    @action(detail=True, methods=["get"], url_path="drafts")
    def drafts(self, request, pk=None):
        """The environment's unsaved changes, for the Save bar."""
        from simulate.services.harness_versioning.drafts import list_drafts

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(list_drafts(job))

    @validated_request(
        request_serializer=HarnessDraftSaveSerializer,
        responses={200: HarnessDraftSaveResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path=r"drafts/save")
    def save_drafts(self, request, pk=None):
        """Make every named draft live together; returns the new defaults and every
        version."""
        from simulate.services.harness_versioning.drafts import SaveRequest, save_drafts

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        body = request.validated_data
        return Response(
            save_drafts(
                job,
                {
                    one["piece"]: SaveRequest(
                        expected_parent_id=one["expected_parent_id"],
                        expected_revision=one.get("expected_revision"),
                    )
                    for one in body["drafts"]
                },
                idempotency_key=body["idempotency_key"],
                user=request.user,
            )
        )

    @swagger_auto_schema(
        responses={204: "Discarded"},
        operation_id="simulate_api_harness-environments_discard_draft",
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"drafts/(?P<piece>environment|suite)",
    )
    def discard_draft(self, request, pk=None, piece=None):
        """Set one piece's unsaved changes aside; discarding none is not an error."""
        from simulate.services.harness_versioning.drafts import discard_draft

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        discard_draft(job, piece, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=EvidenceImportCreateSerializer,
        responses={201: EvidenceImportSerializer},
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_evidence_imports",
    )
    @action(detail=True, methods=["post"], url_path="evidence-imports")
    def evidence_imports(self, request, pk=None):
        """Freeze Observe sessions or calls or dataset rows, or store transcripts
        a person added, as evidence for this environment's tests; returns what
        each holds and lacks, and what redaction replaces. Nothing is drafted or
        run."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        body = request.validated_data
        if body["source_kind"] == EvidenceImport.SourceKind.TRANSCRIPT:
            evidence_import = create_transcript_import(
                environment=job,
                transcripts=[
                    TranscriptInput(one["name"], one["format"], one["text"])
                    for one in body["transcripts"]
                ],
                production=body.get("production", False),
                user=request.user,
            )
            return Response(
                EvidenceImportSerializer(import_bundle(evidence_import)).data,
                status=status.HTTP_201_CREATED,
            )
        if body["source_kind"] == EvidenceImport.SourceKind.DATASET:
            # The datasets the caller may read, scoped as Observe's projects are.
            datasets = Dataset.no_workspace_objects.filter(
                project_workspace_scope_q(request, project_prefix=""), deleted=False
            )
            dataset = evidence_dataset(datasets, job, body["dataset_id"])
            if dataset is None:
                raise NotFound("Dataset not found in this environment's workspace")
            evidence_import, evidence = create_dataset_import(
                environment=job,
                dataset=dataset,
                columns=DatasetColumns(**body["columns"]),
                row_ids=[str(row_id) for row_id in body["ids"]],
                production=body.get("production", False),
                user=request.user,
            )
            return Response(
                EvidenceImportSerializer(import_bundle(evidence_import, evidence)).data,
                status=status.HTTP_201_CREATED,
            )
        project = evidence_project(
            project_queryset_for_request(request), job, body["project_id"]
        )
        if project is None:
            raise NotFound("Project not found in this environment's workspace")
        evidence_import, evidence = create_evidence_import(
            environment=job,
            project=project,
            source_kind=body["source_kind"],
            source_ids=[str(source_id) for source_id in body["ids"]],
            user=request.user,
        )
        return Response(
            EvidenceImportSerializer(import_bundle(evidence_import, evidence)).data,
            status=status.HTTP_201_CREATED,
        )

    @validated_request(
        responses={200: EvidenceImportListItemSerializer(many=True)},
        request_methods=["GET"],
        operation_id="simulate_api_harness-environments_list_evidence_imports",
    )
    @evidence_imports.mapping.get
    def list_evidence_imports(self, request, pk=None):
        """This environment's imports a person can still finish, newest first.
        Read from what each import stores: no source is read again."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(
            EvidenceImportListItemSerializer(open_evidence_imports(job), many=True).data
        )

    @validated_request(
        responses={200: EvidenceImportSerializer},
        operation_id="simulate_api_harness-environments_evidence_import",
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"evidence-imports/(?P<import_id>[0-9a-fA-F-]{36})",
    )
    def evidence_import(self, request, pk=None, import_id=None):
        """One evidence import, read from Observe again: a source deleted since
        reads as unavailable, and a changed one says so."""
        job = self._job(request, pk)
        identifier = _uuid_or_none(import_id)
        if job is None or identifier is None:
            return self._not_found()
        evidence_import = environment_evidence_import(job, identifier)
        if evidence_import is None:
            raise NotFound("Evidence import not found")
        return Response(EvidenceImportSerializer(import_bundle(evidence_import)).data)

    @swagger_auto_schema(
        responses={204: "Discarded"},
        operation_id="simulate_api_harness-environments_discard_evidence_import",
    )
    @evidence_import.mapping.delete
    def discard_import(self, request, pk=None, import_id=None):
        """Discard an import never added, and a transcript's stored text with it.
        Of an import with added cases, only the cases not added are discarded."""
        job = self._job(request, pk)
        identifier = _uuid_or_none(import_id)
        if job is None or identifier is None:
            return self._not_found()
        evidence_import = environment_evidence_import(job, identifier)
        if evidence_import is None:
            raise NotFound("Evidence import not found")
        discard_evidence_import(evidence_import)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=EvidenceImportDraftSerializer,
        responses={202: EvidenceImportSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"evidence-imports/(?P<import_id>[0-9a-fA-F-]{36})/draft",
    )
    def draft_evidence_cases(self, request, pk=None, import_id=None):
        """Ask the environment's builder to draft a scenario for each of an import's
        waiting cases. The turn runs in a chat of the import's own; nothing it does
        reaches the environment until a person adds the drafted cases."""
        from simulate.services.evidence_drafts import start_drafting
        from simulate.services.hosted_harness_ingress import _public_base_url

        version_reads.require_versioning()
        job = self._job(request, pk)
        identifier = _uuid_or_none(import_id)
        if job is None or identifier is None:
            return self._not_found()
        evidence_import = environment_evidence_import(job, identifier)
        if evidence_import is None:
            raise NotFound("Evidence import not found")
        start_drafting(
            evidence_import,
            [str(case_id) for case_id in request.validated_data["case_ids"]],
            endpoint_base_url=_public_base_url(request),
            user=request.user,
            shows=request.validated_data.get("shows") or {},
        )
        # start_drafting links the drafting job on its own locked copy; the answer
        # must say drafting has started, or the review never asks again.
        evidence_import.refresh_from_db()
        return Response(
            EvidenceImportSerializer(import_bundle(evidence_import)).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @validated_request(
        request_serializer=EvidenceImportAddSerializer,
        responses={200: EvidenceImportAddResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"evidence-imports/(?P<import_id>[0-9a-fA-F-]{36})/add",
    )
    def add_evidence_cases(self, request, pk=None, import_id=None):
        """Add an import's drafted cases to the suite: saved with them when the
        review saw no unsaved changes, else beside those changes in the draft."""
        from simulate.services.harness_versioning.drafts import DraftRef

        version_reads.require_versioning()
        job = self._job(request, pk)
        identifier = _uuid_or_none(import_id)
        if job is None or identifier is None:
            return self._not_found()
        evidence_import = environment_evidence_import(job, identifier)
        if evidence_import is None:
            raise NotFound("Evidence import not found")
        body = request.validated_data
        saved = add_to_suite(
            evidence_import,
            [str(case_id) for case_id in body["case_ids"]],
            expected={
                piece: DraftRef(str(ref["id"]) if ref["id"] else None, ref["revision"])
                for piece, ref in body["expected_drafts"].items()
            },
            idempotency_key=body["idempotency_key"],
            user=request.user,
        )
        evidence_import = environment_evidence_import(job, identifier)
        if evidence_import is None:
            raise NotFound("Evidence import not found")
        return Response(
            EvidenceImportAddResponseSerializer(
                {"saved": saved, "evidence_import": import_bundle(evidence_import)}
            ).data
        )

    @validated_request(
        query_serializer=HarnessNudgeListQuerySerializer,
        responses={200: HarnessNudgeSerializer(many=True)},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["get"], url_path="nudges")
    def nudges(self, request, pk=None):
        """This environment's offers to rebuild after a change, newest first."""
        from simulate.services.harness_versioning.nudges import list_nudges

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        states = request.validated_query_data.get("state")
        return Response(list_nudges(job, states=states))

    @validated_request(
        request_serializer=HarnessNudgeAnswerSerializer,
        responses={200: HarnessNudgeSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"nudges/(?P<nudge_id>[0-9a-fA-F-]{36})/answer",
    )
    def answer_nudge(self, request, pk=None, nudge_id=None):
        """Skip ("no") or Rebuild ("yes") a pending nudge; returns the nudge."""
        from simulate.services.harness_versioning.nudges import (
            answer_nudge,
            nudge_not_found,
        )

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(nudge_id)
        if identifier is None:
            raise nudge_not_found()
        return Response(
            answer_nudge(
                job,
                identifier,
                answer=request.validated_data["answer"],
                user=request.user,
            )
        )

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={200: HarnessNudgeSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"nudges/(?P<nudge_id>[0-9a-fA-F-]{36})/stop",
    )
    def stop_nudge(self, request, pk=None, nudge_id=None):
        """Stop a queued or running rebuild; it can be run again. Returns the nudge."""
        from simulate.services.harness_versioning.nudges import (
            _nudges,
            nudge_item,
            nudge_not_found,
        )
        from simulate.services.harness_versioning.rebuild import stop_rebuild

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        identifier = _uuid_or_none(nudge_id)
        if identifier is None:
            raise nudge_not_found()
        nudge = stop_rebuild(job, identifier, user=request.user)
        return Response(nudge_item(_nudges(job).get(id=nudge.id)))

    @validated_request(
        query_serializer=HarnessCapabilitySelectionQuerySerializer,
        responses={200: HarnessCapabilitySelectionReportSerializer},
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_capabilities",
    )
    @action(detail=True, methods=["get"], url_path="capabilities")
    def capabilities(self, request, pk=None):
        """The run readiness of an agent, environment and suite pairing, computed now,
        and the selected cases it holds back; an absent id is the environment's default
        and absent keys select the whole suite. No writes."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        query = dict(request.validated_query_data)
        keys = query.pop("keys", None)
        trials = query.pop("trials")
        return Response(_selection_report(job, query, keys=keys, trials=trials))

    @validated_request(
        request_serializer=HarnessCapabilitySelectionSerializer,
        responses={200: HarnessCapabilitySelectionReportSerializer},
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_select_capabilities",
    )
    @capabilities.mapping.post
    def select_capabilities(self, request, pk=None):
        """The same report for a selection too large for a query string. No writes."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        body = dict(request.validated_data)
        keys = body.pop("scenario_ids")
        trials = body.pop("trials")
        return Response(_selection_report(job, body, keys=keys, trials=trials))

    @validated_request(
        query_serializer=HarnessCapabilityHistoryQuerySerializer,
        responses={200: HarnessCertifiedPairingPageSerializer},
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_capability_history",
    )
    @action(detail=True, methods=["get"], url_path="capabilities/history")
    def capability_history(self, request, pk=None):
        """The pairings certified on this environment, newest first, with the exact
        agent, environment and suite versions of each. No writes."""
        from simulate.services.harness_readiness.release import certified_pairings

        version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        cursor = request.validated_query_data.get("cursor")
        return Response(certified_pairings(job, cursor=cursor))

    @validated_request(
        query_serializer=HarnessCapabilityCasesQuerySerializer,
        responses={200: HarnessCapabilityCasePageSerializer},
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_capability_cases",
    )
    @action(detail=True, methods=["get"], url_path="capabilities/cases")
    def capability_cases(self, request, pk=None):
        """One page, by scenario number, of the selected cases in a bucket or held
        back by a row. No writes."""
        from simulate.services.harness_readiness import case_page, resolve_pairing

        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        query = dict(request.validated_query_data)
        options = {
            name: query.pop(name, None) for name in ("keys", "bucket", "row", "cursor")
        }
        trials = query.pop("trials")
        pairing = resolve_pairing(job, **query)
        return Response(case_page(pairing, trials=trials, **options))

    @validated_request(
        request_serializer=HarnessCapabilityRecheckSerializer,
        responses={202: HarnessCapabilityRecheckResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="capabilities/recheck")
    def recheck_capabilities(self, request, pk=None):
        """Validate a pairing again with a run-less check; moves no default. An absent
        id is the environment's default."""
        from simulate.services.harness_readiness import recheck, resolve_pairing

        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        pairing = resolve_pairing(job, **request.validated_data)
        return Response(
            {"validation": recheck(pairing)}, status=status.HTTP_202_ACCEPTED
        )

    @validated_request(
        query_serializer=HarnessRunPlanQuerySerializer,
        responses={200: HarnessRunPlanSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["get"], url_path="run-plan")
    def run_plan(self, request, pk=None):
        """The versions a run of these picks would use, what to warn about and whether
        it would start; no writes. An absent pick is the environment's default."""
        from simulate.services.harness_versioning.run_warnings import preview_run

        query = request.validated_query_data
        picks = VersionPicks(
            agent_version_id=query.get("agent_version_id"),
            environment_version_id=query.get("environment_version_id"),
            suite_version_id=query.get("suite_version_id"),
        )
        if picks.chosen:
            version_reads.require_versioning()
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        # A coding agent may plan only on the default suite, and never reads its held-back cases.
        hidden = _held_back_from(request, job, query.get("suite_version_id"))
        return Response(
            preview_run(
                job, picks, scenario_keys=query.get("scenario_keys"), hidden=hidden
            )
        )

    @validated_request(
        query_serializer=HarnessCompareQuerySerializer,
        responses={200: HarnessCompareSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["get"], url_path="compare")
    def compare(self, request, pk=None):
        """Two runs of this environment, scenario by scenario; against defaults to
        the previous completed run. Not gated on versioning: a run from before
        versioning compares too, with null versions."""
        from simulate.models import TestExecution
        from simulate.services.run_results_v3_queries import (
            build_run_comparison,
            run_calls_queryset,
        )

        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        query = request.validated_query_data
        wanted = [query["execution_id"], query.get("against_execution_id")]
        # A run of this environment is one of its run test's executions.
        runs = (
            {
                one.id: one
                for one in user_executions().filter(
                    id__in=[one for one in wanted if one], run_test_id=job.run_test_id
                )
            }
            if job.run_test_id
            else {}
        )
        execution = runs.get(wanted[0])
        against = runs.get(wanted[1]) if wanted[1] else None
        if execution is None or (wanted[1] and against is None):
            return Response(
                {"detail": "Run not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if against is not None and against.id == execution.id:
            return Response(
                {"detail": "Pick another run to compare against"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Older or newer, as long as both completed: only a completed run has verdicts.
        ended = {
            TestExecution.ExecutionStatus.FAILED: "failed",
            TestExecution.ExecutionStatus.CANCELLED: "was cancelled",
        }
        for run, name, unfinished in (
            (execution, "This run", "Compare this run once it finishes"),
            (
                against,
                "The run to compare against",
                "Pick a finished run to compare against",
            ),
        ):
            if run is None or run.status == TestExecution.ExecutionStatus.COMPLETED:
                continue
            details = {"execution_id": str(run.id)}
            if run.status in ended:
                raise HostedHarnessError(
                    f"comparison_run_{run.status}",
                    f"{name} {ended[run.status]} before its calls were graded",
                    details=details,
                )
            raise HostedHarnessError(
                "comparison_run_unfinished", unfinished, details=details
            )
        comparison = build_run_comparison(
            execution, run_calls_queryset(execution), against=against, with_pairs=True
        )
        return Response(
            {
                "execution_id": str(execution.id),
                "against_execution_id": comparison["previous_execution_id"],
                "pairs": comparison["pairs"],
                "summary": comparison["summary"],
                "versions": comparison["versions"],
            }
        )

    @validated_request(
        query_serializer=HarnessEnvironmentListQuerySerializer,
        responses={200: HarnessEnvironmentListResponseSerializer},
    )
    def list(self, request):
        organization = request_organization(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        jobs = self._queryset(request)
        agent_definition_id = request.validated_query_data.get("agent_definition_id")
        if agent_definition_id is not None:
            jobs = jobs.filter(run_test__agent_definition_id=agent_definition_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(jobs, request, view=self)
        return paginator.get_paginated_response(environment_rows(page))

    @swagger_auto_schema(responses={200: HarnessEnvironmentDetailSerializer})
    def retrieve(self, request, pk=None):
        """One environment: overview, contract, world, scenarios, evaluations, settings.

        Same shape whichever door built it. A section the pipeline has not reached
        yet is null, so the client renders "building" rather than an empty pane. A
        coding agent's read leaves out the held-back cases.
        """
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            detail_without(environment_detail(job), _held_back_from(request, job))
        )

    @validated_request(
        request_serializer=HarnessEnvironmentRenameSerializer,
        responses={200: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
    )
    def partial_update(self, request, pk=None):
        """Rename an environment.

        The name is the only editable field: everything else on an environment
        records how it was built, and editing that would make the provenance the
        contract tab shows a claim rather than a record.
        """
        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        job.name = request.validated_data["name"]
        # The list sorts and reports "last updated" from this column, so a
        # rename that left it alone would show a stale time on the row it just
        # changed.
        job.content_updated_at = timezone.now()
        job.save(update_fields=["name", "content_updated_at", "updated_at"])
        return Response(environment_detail(job))

    @swagger_auto_schema(responses={204: "Deleted"})
    def destroy(self, request, pk=None):
        """Soft-delete an environment, cancelling its run first if one is live.

        Deleting while a sandbox is running would leave that sandbox billing
        against a row nobody can see, so cancellation is requested before the
        row disappears. The authoring archive and the organization's secrets are
        left in place: neither is owned by this row, and other environments may
        reference the same credentials.
        """
        from simulate.services.hosted_harness import delete_environment

        job = self._job(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        delete_environment(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=HarnessRunCreateSerializer,
        responses={202: HarnessRunCreateResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Create one new Run for the selected scenarios and trial count."""
        return get_harness_provider().run(request, pk)

    def _run_test_job(self, request, pk):
        """The environment and its run test, or the response that refuses the call.

        Evaluations hang off the run test, which authoring creates when it
        registers scenarios, so every evaluation endpoint has the same two ways
        of having nothing to act on.
        """
        job = self._job(request, pk)
        if job is None:
            return None, Response(
                {"detail": "Environment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not job.run_test_id:
            return None, Response(
                {"detail": "Environment has no evaluations until it finishes building"},
                status=status.HTTP_409_CONFLICT,
            )
        return job, None

    @swagger_auto_schema(responses={200: HarnessEnvironmentAvailableEvalsSerializer})
    @action(detail=True, methods=["get"], url_path="evaluations/available")
    def available_evaluations(self, request, pk=None):
        """The evals this environment could still be graded by.

        The same catalogue authoring chose from, filtered to this environment's
        modality and to the templates the organization can see, minus what is
        already selected. Every entry is addable as it stands: an eval whose
        inputs this modality does not produce is left out rather than offered
        and then refused.
        """
        from simulate.services.harness_evals import addable_evals

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        return Response(
            {"evaluations": addable_evals(job.run_test, eval_modality(job))}
        )

    @validated_request(
        request_serializer=HarnessEnvironmentAddEvaluationSerializer,
        responses={201: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="evaluations")
    def add_evaluation(self, request, pk=None):
        """Grade this environment by one more eval from the catalogue.

        Applies to scenarios graded from here on. Calls that already ran keep
        the verdicts they were given, so adding an eval does not backfill a
        column onto past results.
        """
        from simulate.services.harness_evals import (
            EvalSelectionFull,
            EvalSelectionRefused,
            add_selected_eval,
        )

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        try:
            add_selected_eval(
                job.run_test, request.validated_data["name"], eval_modality(job)
            )
        except EvalSelectionFull as full:
            return Response({"detail": str(full)}, status=status.HTTP_409_CONFLICT)
        except EvalSelectionRefused as refused:
            return Response(
                {"detail": str(refused)}, status=status.HTTP_400_BAD_REQUEST
            )
        _touch_content(job)
        return Response(environment_detail(job), status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=HarnessEnvironmentAddEvaluationSerializer,
        responses={202: HarnessEnvironmentRunEvaluationQueuedSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Add an eval to the environment and grade this run's "
            "already-finished calls with it."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"runs/(?P<execution_id>[0-9a-fA-F-]{36})/evaluations",
    )
    def add_run_evaluation(self, request, pk=None, execution_id=None):
        """Add an eval from inside a finished run, and grade that run's calls with it.

        Two things happen inside one ``transaction.atomic()`` block. First, the
        eval is bound: a name this environment already binds, under the
        config's own name or its template's, is used as it stands, with no
        offer gate, no lock and no new row, so an eval a person added from
        outside the harness's offer still grades. Any other name is bound
        exactly as ``add_evaluation`` binds it, with the same gates, refusals,
        lock and idempotency. Second, this run's finished calls are selected
        and stamped for grading.
        Wrapping both together means a failure anywhere in this request rolls
        the bind and the stamps back together: no grading job is ever
        dispatched against a bind that did not survive, because
        ``queue_eval_for_finished_calls`` schedules every dispatch with
        ``transaction.on_commit``, which this outer block is what actually
        commits. A refusal from the bind is returned unchanged and nothing is
        queued or stamped.

        A call is queued for grading unless it already holds a verdict for
        this eval, its own evaluations are still being graded (and the
        scoring clock does not yet count them as stuck), or it was queued
        for this eval within the last ten minutes; the rest are stamped and
        scheduled for dispatch, one grading job each, after this transaction
        commits. Each queued call reads as pending until its verdict lands,
        and a completed run goes back to ``evaluating`` until every call is
        scored again; a failed run stays failed. The answer is the five
        counts, not the environment detail -- the client refetches that
        itself.

        The bind can return an eval config whose ``mapping`` is empty: one
        of the harness's own result columns, bound by ingestion under the
        same name, or a person's eval with no inputs mapped. Neither can be
        graded, so it is refused 400 with its own reason, distinct from the
        "does not produce" refusal, right after the bind and before anything
        is stamped.

        The run is resolved *before* the eval is bound: a request naming a
        run that is not this environment's must leave nothing behind. A run
        that is cancelled or cancelling is refused 409 at the same point: the
        worker would not grade it, so nothing is bound or stamped.
        """
        from django.db import transaction

        from simulate.models import TestExecution
        from simulate.services.harness_evals import (
            EvalSelectionFull,
            EvalSelectionRefused,
            add_selected_eval,
            regrade_mapping,
        )
        from simulate.services.harness_run_evals import queue_eval_for_finished_calls

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        identifier = _uuid_or_none(execution_id)
        # A run of this environment is one of its run test's executions, not
        # only the latest: `job.test_execution` holds the most recent one,
        # while a rerun deliberately keeps the same run test
        # (harness_provider.py:1130-1136), so an older run must still be
        # addressable.
        execution = (
            user_executions().filter(id=identifier, run_test_id=job.run_test_id).first()
            if identifier is not None
            else None
        )
        if execution is None:
            return Response(
                {"detail": "Run not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if execution.status in (
            TestExecution.ExecutionStatus.CANCELLED,
            TestExecution.ExecutionStatus.CANCELLING,
        ):
            # The eval worker never grades a call whose run is cancelled, so
            # binding and stamping here would report queued work that will
            # not happen -- and the stamp would then hide a retry for ten
            # minutes. Refused before the bind, so nothing is left behind.
            return Response(
                {"detail": "Run is cancelled; nothing will be graded"},
                status=status.HTTP_409_CONFLICT,
            )
        modality = eval_modality(job)
        wanted = request.validated_data["name"]
        # Captured before the bind, so a freshly bound config's `updated_at`
        # (set by `auto_now` on insert and on a revived binding's save) reads
        # as after this timestamp, while an idempotent bind's -- an
        # already-bound row returned unchanged -- reads as before it. Under
        # contention, two concurrent clicks can both read
        # `updated_at >= before_bind`, since, when both bind, the second
        # blocks on the run test's `select_for_update` and observes the
        # first's write -- harmless, an extra clock bump, never a missed one.
        before_bind = timezone.now()
        try:
            with transaction.atomic():
                eval_config = _bound_by_name(job.run_test, wanted) or add_selected_eval(
                    job.run_test, wanted, modality
                )
                if regrade_mapping(eval_config) is None:
                    # The bind above can return a row with an empty mapping
                    # -- a harness result column, or a person's eval with no
                    # inputs. A harness suite eval can still be graded, by
                    # the same rule a re-grade uses; anything else has
                    # nothing to grade and gets its own reason before
                    # anything is stamped.
                    transaction.set_rollback(True)
                    return Response(
                        {
                            "detail": (
                                f"{wanted} is bound as a result column and "
                                "has nothing to grade"
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                counts = queue_eval_for_finished_calls(execution, eval_config)
        except EvalSelectionFull as full:
            return Response({"detail": str(full)}, status=status.HTTP_409_CONFLICT)
        except EvalSelectionRefused as refused:
            return Response(
                {"detail": str(refused)}, status=status.HTTP_400_BAD_REQUEST
            )
        # Skipped only when the bind wrote nothing (the idempotent name match
        # returned an already-bound row unchanged) AND nothing was queued --
        # a genuinely no-op repeat. Any other click, including a repeat that
        # queues a call newly eligible since the last one, still bumps it: a
        # click that stamps or dispatches anything is real content movement,
        # the same as `add_evaluation`'s own touch.
        if eval_config.updated_at >= before_bind or counts["queued"]:
            _touch_content(job)
        return Response(counts, status=status.HTTP_202_ACCEPTED)

    @validated_request(
        request_serializer=HarnessEnvironmentRunEvaluationsSerializer,
        responses={202: HarnessEnvironmentRunEvaluationsQueuedSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Grade this finished run's calls again with chosen evals of the "
            "environment, without rerunning the calls."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"runs/(?P<execution_id>[0-9a-fA-F-]{36})/evaluations/run",
    )
    def run_evaluations(self, request, pk=None, execution_id=None):
        """Grade a finished run's calls again with evals the environment already has.

        The calls are not rerun. Each chosen eval is graded again on every call
        of the run, its previous score is replaced, and the run reads
        ``evaluating`` until grading ends. The run-test page's
        ``run-new-evals`` goes through the same service, so both refuse the
        same evals and queue the same job.

        Every refusal leaves the run as it was, a run with no calls or no
        completed call included; a second click that raced the first is
        refused at the claim, and what it had started writing is rolled back.
        The job check comes before the status read, so that read sees
        whatever a harness job's teardown wrote onto the run. A grading job
        that could not be queued answers 503 after every call's scores and
        the run's status were put back.
        """
        from simulate.models import TestExecution
        from simulate.services.harness_evals import is_harness_run_test
        from simulate.services.run_regrade import (
            RegradeAlreadyRunning,
            RegradeDispatchFailed,
            RegradeEvalNotFound,
            RegradeNoCalls,
            RegradeNoCompletedCall,
            RegradeRefused,
            RegradeStillFinishing,
            regrade_run_evals,
            regrade_still_finishing,
        )

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        identifier = _uuid_or_none(execution_id)
        runs = user_executions().filter(id=identifier, run_test_id=job.run_test_id)
        if identifier is None or not runs.exists():
            return Response(
                build_error_envelope(
                    "Run not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        execution_ids = [str(identifier)]
        harness_run = is_harness_run_test(job.run_test_id)
        try:
            regrade_still_finishing(execution_ids, harness_run=harness_run)
        except RegradeStillFinishing as finishing:
            return Response(
                build_error_envelope(
                    str(finishing), status_code=status.HTTP_409_CONFLICT
                ),
                status=status.HTTP_409_CONFLICT,
            )
        # Read after the job check above, for the teardown reason it gives.
        run_status = runs.values_list("status", flat=True).first()
        if run_status != TestExecution.ExecutionStatus.COMPLETED:
            return Response(
                build_error_envelope(
                    "Only a finished run can be graded again",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        eval_config_ids = request.validated_data["eval_config_ids"]
        try:
            call_execution_count = regrade_run_evals(
                job.run_test,
                execution_ids,
                {str(identifier): run_status},
                eval_config_ids,
                harness_run=harness_run,
                enable_tool_evaluation=request.validated_data.get(
                    "enable_tool_evaluation"
                ),
            )
        except RegradeEvalNotFound:
            # An eval removed between the check above and the service's own.
            return Response(
                build_error_envelope(
                    "Evaluation not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        except RegradeRefused as refused:
            return Response(
                build_error_envelope(
                    str(refused), status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RegradeNoCalls:
            return Response(
                build_error_envelope(
                    "This run has no calls to grade",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeNoCompletedCall:
            return Response(
                build_error_envelope(
                    "Nothing to grade again: no call in this run completed.",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeAlreadyRunning:
            return Response(
                build_error_envelope(
                    "Grading is already running on this run.",
                    status_code=status.HTTP_409_CONFLICT,
                ),
                status=status.HTTP_409_CONFLICT,
            )
        except RegradeDispatchFailed:
            return Response(
                build_error_envelope(
                    "Grading couldn't be started. Try again.",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Queuing a grade is content movement, as it is for add_run_evaluation.
        _touch_content(job)
        return Response(
            {"call_execution_count": call_execution_count},
            status=status.HTTP_202_ACCEPTED,
        )

    # Pinned: the PATCH below shares this action's path, and DRF names the
    # operations of a two-method action after the path and the method.
    @swagger_auto_schema(
        responses={204: "Removed"},
        operation_id="simulate_api_harness-environments_remove_evaluation",
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"evaluations/(?P<eval_config_id>[0-9a-fA-F-]{36})",
    )
    def remove_evaluation(self, request, pk=None, eval_config_id=None):
        """Stop running one eval against this environment.

        Soft-delete only. The verdicts an eval already produced live on the call
        executions and in their receipts, not on this row, so a hard delete would
        leave past runs showing scores for something the environment no longer
        lists. Removing an eval someone added stops future scenarios being
        graded by it and leaves the history it already wrote intact. An eval the
        harness reported itself comes back the next time the harness grades it.
        """
        from simulate.models.eval_config import SimulateEvalConfig

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        config_id = _uuid_or_none(eval_config_id)
        config = (
            SimulateEvalConfig.objects.filter(
                id=config_id, run_test_id=job.run_test_id, deleted=False
            ).first()
            if config_id is not None
            else None
        )
        if config is None:
            return Response(
                {"detail": "Evaluation not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        config.deleted = True
        config.deleted_at = timezone.now()
        config.save(update_fields=["deleted", "deleted_at", "updated_at"])
        _touch_content(job)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=HarnessEnvironmentEvalEditSerializer,
        responses={200: SimulateEvalConfigResponseSerializer},
        request_methods=["PATCH"],
        reject_unknown_fields=True,
        operation_id="simulate_api_harness-environments_edit_evaluation",
        operation_description=(
            "Change one eval of this environment. Never grades anything; "
            "grade a run again afterwards to refresh its scores."
        ),
    )
    @remove_evaluation.mapping.patch
    def edit_evaluation(self, request, pk=None, eval_config_id=None):
        """Change one eval's name, settings, inputs or judge model.

        The run-test page's eval edit goes through the same service, so the
        checks and their sentences are the same, but nothing here grades:
        grading a run again is ``runs/{id}/evaluations/run``.

        A row the harness fills is refused whatever the body says. It keeps an
        empty mapping on purpose, and later harness runs write into it by its
        fixed id, so for the same reason an edit may not leave a row without
        inputs of its own. Every refusal comes before the row is saved.
        """
        from django.db import transaction

        from simulate.models.eval_config import SimulateEvalConfig
        from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer
        from simulate.services.eval_config_edit import (
            EvalConfigEditRefused,
            has_own_mapping,
            update_eval_config,
        )
        from simulate.services.harness_evals import is_harness_run_test

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        config_id = _uuid_or_none(eval_config_id)
        config = (
            SimulateEvalConfig.objects.select_related("eval_template")
            .filter(id=config_id, run_test_id=job.run_test_id, deleted=False)
            .first()
            if config_id is not None
            else None
        )
        if config is None:
            return Response(
                build_error_envelope(
                    "Evaluation not found", status_code=status.HTTP_404_NOT_FOUND
                ),
                status=status.HTTP_404_NOT_FOUND,
            )
        name = config.name or "This evaluation"
        if is_harness_run_test(job.run_test_id) and not has_own_mapping(config):
            return Response(
                build_error_envelope(
                    f"{name} is set by the harness and can't be edited here.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not request.validated_data:
            return Response(
                build_error_envelope(
                    "Nothing to change", status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        def keeps_its_inputs(edited):
            if not has_own_mapping(edited):
                raise EvalConfigEditRefused(f"{name} needs at least one input mapped")

        try:
            with transaction.atomic():
                update_eval_config(
                    config,
                    request.validated_data,
                    organization=request_organization(request),
                    workspace=request_workspace(request),
                    before_save=keeps_its_inputs,
                )
                _touch_content(job)
        except EvalConfigEditRefused as refused:
            return Response(
                build_error_envelope(
                    str(refused), status_code=status.HTTP_400_BAD_REQUEST
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(SimulateEvalConfigSimpleSerializer(config).data)

    @validated_request(
        request_serializer=HarnessEnvironmentToolCallEvaluationSerializer,
        responses={200: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
        operation_description=(
            "Turn the tool-call judge on or off for this environment. "
            "Returns the full environment detail. Turning it on is refused "
            "for a hosted voice environment with no agent version yet."
        ),
    )
    @action(detail=True, methods=["put"], url_path="evaluations/tool-call")
    def set_tool_call_evaluation(self, request, pk=None):
        """Turn the tool-call judge on or off for this environment.

        Deliberately not an eval: it sets a single column on the run test
        (``RunTest.enable_tool_evaluation``), never offered by ``available``,
        never listed in ``evaluations.selected``, and never counted against
        the cap of eight.

        What "on" costs is worth saying out loud: every call the environment
        produces is read once more by a judge that grades the tools the agent
        reached for. Its output goes to ``tool_outputs``, not where verdicts
        live, so this can never touch a stored verdict.

        ``PUT`` rather than ``PATCH``: the body carries the whole state of the
        switch, so the same request twice leaves the same result, and the
        response is the whole environment detail so the client doesn't have
        to refetch.

        Past calls are untouched either way. Turning it on is refused, 409,
        for a hosted voice environment whose agent definition has no version
        yet; turning it off is always allowed.
        """
        from django.db import transaction

        job, refusal = self._run_test_job(request, pk)
        if refusal is not None:
            return refusal
        enable_tool_evaluation = request.validated_data["enable_tool_evaluation"]
        if enable_tool_evaluation:
            agent_definition = job.run_test.agent_definition
            if (
                agent_definition is not None
                and agent_definition.agent_type
                == AgentDefinition.AgentTypeChoices.VOICE
                and agent_definition.latest_version is None
            ):
                return Response(
                    {
                        "detail": "Tool-call evaluation is not available for a voice environment yet"
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        # `job.run_test` caches the instance, so `environment_detail` below
        # reports the value this request just wrote without a second query.
        run_test = job.run_test
        run_test.enable_tool_evaluation = enable_tool_evaluation
        with transaction.atomic():
            run_test.save(update_fields=["enable_tool_evaluation", "updated_at"])
            # Unconditional, like the add and the remove: one rule for the
            # list's clock beats a special case for a no-op request.
            _touch_content(job)
        return Response(environment_detail(job))
