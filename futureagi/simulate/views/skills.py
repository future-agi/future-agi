from uuid import UUID

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import Skill
from simulate.serializers.skills import (
    LibrarySkillAttachedQuerySerializer,
    LibrarySkillAttachedSerializer,
    LibrarySkillAttachmentSerializer,
    LibrarySkillAttachSerializer,
    LibrarySkillCopySerializer,
    LibrarySkillCreateSerializer,
    LibrarySkillDetachSerializer,
    LibrarySkillDetailSerializer,
    LibrarySkillInCodeFilesSerializer,
    LibrarySkillInCodeQuerySerializer,
    LibrarySkillInCodeSerializer,
    LibrarySkillListItemSerializer,
    LibrarySkillListQuerySerializer,
    LibrarySkillListResponseSerializer,
    LibrarySkillPromoteSerializer,
    LibrarySkillTagSerializer,
    LibrarySkillTestCreateSerializer,
    LibrarySkillTestSerializer,
    LibrarySkillVersionCreateSerializer,
    LibrarySkillVersionFilesSerializer,
    LibrarySkillVersionSerializer,
)
from simulate.services import skills as skill_service
from simulate.services.harness_provider import request_organization, request_workspace
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
from tfc.utils.pagination import ExtendedPageNumberPagination


def _uuid_or_none(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


class SkillViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination

    def handle_exception(self, exc):
        if isinstance(exc, HostedHarnessError):
            return Response(exc.as_dict(), status=exc.status_code)
        return super().handle_exception(exc)

    def _organization(self, request):
        organization = request_organization(request)
        if organization is None:
            raise HostedHarnessError(
                "organization_required", "an organization is required"
            )
        return organization

    def _skills(self, request):
        return skill_service.scoped_skills(
            self._organization(request), request_workspace(request)
        )

    def _skill(self, request, pk) -> Skill:
        identifier = _uuid_or_none(pk)
        skill = (
            self._skills(request)
            .select_related("created_by")
            .filter(id=identifier)
            .first()
            if identifier is not None
            else None
        )
        if skill is None:
            raise skill_service.skill_not_found()
        return skill

    @validated_request(
        query_serializer=LibrarySkillListQuerySerializer,
        responses={200: LibrarySkillListResponseSerializer},
    )
    def list(self, request):
        skill_service.require_skills()
        skills = skill_service.skill_list(
            self._skills(request), search=request.validated_query_data["search"]
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(skills, request, view=self)
        return paginator.get_paginated_response(
            LibrarySkillListItemSerializer(page, many=True).data
        )

    @validated_request(
        request_serializer=LibrarySkillCreateSerializer,
        responses={201: LibrarySkillDetailSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        skill_service.require_skills()
        data = request.validated_data
        skill = skill_service.create_skill(
            organization=self._organization(request),
            workspace=request_workspace(request),
            user=request.user,
            name=data["name"],
            kind=data["kind"],
            description=data["description"],
            note=data["note"],
            files=data["files"],
        )
        return Response(
            LibrarySkillDetailSerializer(skill_service.skill_detail(skill)).data,
            status=status.HTTP_201_CREATED,
        )

    @validated_request(responses={200: LibrarySkillDetailSerializer})
    def retrieve(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        return Response(
            LibrarySkillDetailSerializer(skill_service.skill_detail(skill)).data
        )

    @validated_request(
        request_serializer=LibrarySkillVersionCreateSerializer,
        responses={201: LibrarySkillVersionSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="versions")
    def create_version(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        data = request.validated_data
        version = skill_service.add_version(
            skill, user=request.user, files=data["files"], note=data["note"]
        )
        return Response(
            LibrarySkillVersionSerializer(version).data, status=status.HTTP_201_CREATED
        )

    @validated_request(responses={200: LibrarySkillVersionFilesSerializer})
    @action(
        detail=True,
        methods=["get"],
        url_path=r"versions/(?P<version_number>[0-9]+)/files",
    )
    def version_files(self, request, pk=None, version_number=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        version = skill_service.version_of(skill, int(version_number))
        return Response(
            {
                "version": version.version_number,
                "files": skill_service.read_version_files(version),
            }
        )

    @validated_request(
        request_serializer=LibrarySkillPromoteSerializer,
        responses={200: LibrarySkillTagSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="promote")
    def promote(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        data = request.validated_data
        tag = skill_service.promote(
            skill,
            version_number=data["version"],
            tag=data["tag"],
            reason=data["reason"],
            user=request.user,
        )
        return Response(LibrarySkillTagSerializer(tag).data)

    def _agent(self, request, agent_definition_id):
        return skill_service.scoped_agent(
            self._organization(request),
            request_workspace(request),
            agent_definition_id,
        )

    @validated_request(
        request_serializer=LibrarySkillAttachSerializer,
        responses={200: LibrarySkillAttachmentSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="attach")
    def attach(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        data = request.validated_data
        attachment = skill_service.attach(
            skill,
            self._agent(request, data["agent_definition_id"]),
            tag=data["tag"],
            version_number=data["version"],
            user=request.user,
        )
        return Response(LibrarySkillAttachmentSerializer(attachment).data)

    @validated_request(
        request_serializer=LibrarySkillDetachSerializer,
        responses={204: None},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="detach")
    def detach(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        skill_service.detach(
            skill, self._agent(request, request.validated_data["agent_definition_id"])
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        query_serializer=LibrarySkillAttachedQuerySerializer,
        responses={200: LibrarySkillAttachedSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="attached")
    def attached(self, request):
        skill_service.require_skills()
        agent = self._agent(
            request, request.validated_query_data["agent_definition_id"]
        )
        return Response(
            LibrarySkillAttachedSerializer(
                skill_service.attached_skills(agent), many=True
            ).data
        )

    @validated_request(
        request_serializer=LibrarySkillTestCreateSerializer,
        responses={201: LibrarySkillTestSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="test")
    def start_test(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        data = request.validated_data
        organization = self._organization(request)
        workspace = request_workspace(request)
        test = skill_service.start_test(
            skill,
            version_number=data["version"],
            environment=skill_service.scoped_environment(
                organization, workspace, data["environment_id"]
            ),
            agent=self._agent(request, data["agent_definition_id"]),
            scenario_keys=data["scenario_keys"],
            trials=data["trials"],
            user=request.user,
        )
        return Response(
            LibrarySkillTestSerializer(test).data, status=status.HTTP_201_CREATED
        )

    @validated_request(responses={200: LibrarySkillTestSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="tests")
    def tests(self, request, pk=None):
        skill_service.require_skills()
        skill = self._skill(request, pk)
        return Response(
            LibrarySkillTestSerializer(skill_service.skill_tests(skill), many=True).data
        )

    @validated_request(
        query_serializer=LibrarySkillInCodeQuerySerializer,
        responses={200: LibrarySkillInCodeSerializer},
    )
    @action(detail=False, methods=["get"], url_path="in-code")
    def in_code(self, request):
        skill_service.require_skills()
        agent = self._agent(
            request, request.validated_query_data["agent_definition_id"]
        )
        return Response(
            LibrarySkillInCodeSerializer(skill_service.repo_skills(agent)).data
        )

    @validated_request(
        query_serializer=LibrarySkillInCodeQuerySerializer,
        responses={200: LibrarySkillInCodeFilesSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"in-code/(?P<skill_version_id>[0-9a-fA-F-]{36})/files",
    )
    def in_code_files(self, request, skill_version_id=None):
        skill_service.require_skills()
        agent = self._agent(
            request, request.validated_query_data["agent_definition_id"]
        )
        return Response(
            {"files": skill_service.repo_skill_files(agent, skill_version_id)}
        )

    @validated_request(
        request_serializer=LibrarySkillCopySerializer,
        responses={201: LibrarySkillDetailSerializer},
        reject_unknown_fields=True,
    )
    @action(
        detail=False,
        methods=["post"],
        url_path=r"in-code/(?P<skill_version_id>[0-9a-fA-F-]{36})/copy",
    )
    def copy_to_library(self, request, skill_version_id=None):
        skill_service.require_skills()
        agent = self._agent(request, request.validated_data["agent_definition_id"])
        skill = skill_service.copy_to_library(
            agent,
            skill_version_id,
            workspace=request_workspace(request),
            user=request.user,
        )
        return Response(
            LibrarySkillDetailSerializer(skill_service.skill_detail(skill)).data,
            status=status.HTTP_201_CREATED,
        )
