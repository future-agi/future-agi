from django.db.models import Q
from django.utils.text import slugify
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.falcon_ai.models import Skill
from ee.falcon_ai.serializers_contracts import (
    FalconErrorResponseSerializer,
    SkillDetailResponseSerializer,
    SkillListResponseSerializer,
    SkillUpdateRequestSerializer,
)
from ee.falcon_ai.serializers_skills import (
    SkillCreateSerializer,
    SkillDetailSerializer,
    SkillListSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def visible_skills_q(organization):
    """Skills visible to an org: its own custom skills plus all global builtins."""
    return Q(organization=organization) | Q(organization__isnull=True, is_builtin=True)


class SkillListView(APIView):
    """List and create skills."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: SkillListResponseSerializer,
            403: FalconErrorResponseSerializer,
        }
    )
    def get(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return _gm.forbidden_response("No organization context")

        # Visible skills = this org's custom skills + global builtins (org IS NULL).
        # Bypass BaseModelManager's auto-workspace scoping — it excludes rows
        # where organization_id IS NULL, which would hide every global skill.
        skills = Skill.no_workspace_objects.select_related("created_by").filter(
            visible_skills_q(organization),
            is_active=True,
        )

        if workspace:
            skills = skills.filter(models_workspace_filter(workspace))

        serializer = SkillListSerializer(skills, many=True)
        return Response({"status": True, "results": serializer.data})

    @swagger_auto_schema(
        request_body=SkillCreateSerializer,
        responses={
            201: SkillDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            409: FalconErrorResponseSerializer,
        },
    )
    def post(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return _gm.forbidden_response("No organization context")

        serializer = SkillCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        slug = slugify(data["name"])

        # Slug must be unique among both this org's skills and global builtins —
        # otherwise the user's new skill would shadow a system skill by slug.
        if Skill.no_workspace_objects.filter(
            visible_skills_q(organization), slug=slug
        ).exists():
            return _gm.custom_error_response(
                status.HTTP_409_CONFLICT,
                f"Skill with slug '{slug}' already exists",
            )

        skill = Skill.objects.create(
            organization=organization,
            workspace=workspace,
            name=data["name"],
            slug=slug,
            description=data["description"],
            icon=data.get("icon", "mdi:star"),
            instructions=data["instructions"],
            tool_names=data.get("tool_names", []),
            trigger_phrases=data.get("trigger_phrases", []),
            is_builtin=False,
            created_by=request.user,
        )

        result_serializer = SkillDetailSerializer(skill)
        return Response(
            {"status": True, "result": result_serializer.data},
            status=status.HTTP_201_CREATED,
        )


class SkillDetailView(APIView):
    """Get, update, or delete a skill."""

    permission_classes = [IsAuthenticated]

    def _get_skill(self, request, skill_id):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if not organization:
            return None, _gm.forbidden_response("No organization context")

        filters = visible_skills_q(organization) & Q(id=skill_id)
        if workspace:
            filters &= models_workspace_filter(workspace)

        try:
            skill = Skill.no_workspace_objects.select_related("created_by").get(
                filters,
            )
            return skill, None
        except Skill.DoesNotExist:
            return None, _gm.not_found("Skill not found")

    @swagger_auto_schema(
        responses={
            200: SkillDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        }
    )
    def get(self, request, skill_id):
        skill, error = self._get_skill(request, skill_id)
        if error:
            return error

        serializer = SkillDetailSerializer(skill)
        return Response({"status": True, "result": serializer.data})

    @validated_request(
        SkillUpdateRequestSerializer,
        responses={
            200: SkillDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        },
    )
    def patch(self, request, skill_id):
        skill, error = self._get_skill(request, skill_id)
        if error:
            return error

        # Prevent editing builtin skills
        if skill.is_builtin:
            return _gm.forbidden_response(
                "Cannot edit builtin skills",
            )

        updatable_fields = [
            "name",
            "description",
            "icon",
            "instructions",
            "tool_names",
            "trigger_phrases",
            "is_active",
        ]
        update_fields = []
        for field in updatable_fields:
            if field in request.validated_data:
                setattr(skill, field, request.validated_data[field])
                update_fields.append(field)

        if update_fields:
            update_fields.append("updated_at")
            skill.save(update_fields=update_fields)

        serializer = SkillDetailSerializer(skill)
        return Response({"status": True, "result": serializer.data})

    @swagger_auto_schema(
        responses={
            204: "Skill deleted",
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        }
    )
    def delete(self, request, skill_id):
        skill, error = self._get_skill(request, skill_id)
        if error:
            return error

        if skill.is_builtin:
            return _gm.forbidden_response(
                "Cannot delete builtin skills",
            )

        skill.delete()
        return Response({"status": True}, status=status.HTTP_204_NO_CONTENT)


def models_workspace_filter(workspace):
    """Return a Q filter for skills visible in a workspace.

    Includes builtin (workspace=NULL) and workspace-scoped skills.
    """
    from django.db.models import Q

    return Q(workspace=workspace) | Q(workspace__isnull=True)
