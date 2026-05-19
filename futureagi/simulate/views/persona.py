import json

import structlog
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import Persona
from simulate.serializers.persona import (
    PersonaCreateSerializer,
    PersonaFieldOptionsSerializer,
    PersonaListSerializer,
    PersonaSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


class PersonaViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing Personas.

    Supports:
    - System-level personas (accessible to all users)
    - Workspace-level personas (accessible only within workspace)

    Endpoints:
    - GET /personas/ - List all accessible personas (system + workspace) with pagination and filters
    - POST /personas/ - Create a new workspace-level persona
    - GET /personas/{id}/ - Retrieve a specific persona
    - PUT/PATCH /personas/{id}/ - Update a persona (workspace-level only)
    - DELETE /personas/{id}/ - Delete a persona (workspace-level only)
    - GET /personas/system/ - List only system-level personas
    - GET /personas/workspace/ - List only workspace-level personas
    - GET /personas/field_options/ - Get field options/choices for persona creation

    Query Parameters for list:
    - type: Filter by persona type ('prebuilt' for system, 'custom' for workspace)
    - search: Search by name, description, or keywords
    - page: Page number for pagination
    - page_size: Number of items per page
    """

    permission_classes = [IsAuthenticated]
    lookup_field = "id"
    pagination_class = ExtendedPageNumberPagination

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._gm = GeneralMethods()

    def get_queryset(self):
        """
        Get personas accessible to the user with optional filtering.

        Returns:
        - System-level personas (no workspace)
        - Workspace-level personas in the current workspace

        Supports filtering by:
        - type: 'prebuilt' (system) or 'custom' (workspace)
        - search: Search in name, description, keywords
        """
        user = self.request.user
        organization = getattr(self.request, "organization", None) or user.organization

        # Get workspace from request (set by auth middleware) or user
        workspace = getattr(self.request, "workspace", None)
        if not workspace and user:
            workspace = getattr(user, "workspace", None)

        # Start with base queryset - use no_workspace_objects to bypass automatic workspace filtering
        # so we can implement our own custom logic below
        queryset = Persona.no_workspace_objects.all()

        # Filter to show:
        # 1. System-level personas (no organization/workspace)
        # 2. Workspace-level personas in the current workspace/organization
        if workspace and organization:
            # Show system personas + workspace personas matching org and workspace
            queryset = queryset.filter(
                Q(persona_type=Persona.PersonaType.SYSTEM)
                | Q(
                    persona_type=Persona.PersonaType.WORKSPACE,
                    organization=organization,
                    workspace=workspace,
                )
            )
        elif organization:
            # If no workspace context, show system personas + org personas (any workspace or no workspace)
            queryset = queryset.filter(
                Q(persona_type=Persona.PersonaType.SYSTEM)
                | Q(
                    persona_type=Persona.PersonaType.WORKSPACE,
                    organization=organization,
                )
            )
        else:
            # If no organization context, only show system personas
            queryset = queryset.filter(persona_type=Persona.PersonaType.SYSTEM)

        # Apply search filter
        search_param = self.request.query_params.get("search", None)
        if search_param:
            queryset = queryset.filter(
                Q(name__icontains=search_param)
                | Q(description__icontains=search_param)
                | Q(keywords__icontains=search_param)
            )

        # `filters` is a JSON array of {column_id, filter_config: {filter_op,
        # filter_value}}. Unknown columns/ops are skipped silently so a bad
        # clause can't make get_queryset return a non-QuerySet.
        persona_type_map = {
            "prebuilt": Persona.PersonaType.SYSTEM,
            "custom": Persona.PersonaType.WORKSPACE,
        }
        valid_simulation_choices = {
            choice[0] for choice in Persona.SimulationTypeChoices.choices
        }
        try:
            clauses = json.loads(self.request.query_params.get("filters", "[]"))
            if not isinstance(clauses, list):
                clauses = []
        except (TypeError, ValueError):
            clauses = []
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            column_id = clause.get("column_id")
            cfg = clause.get("filter_config") or {}
            op = cfg.get("filter_op")
            raw_value = cfg.get("filter_value")
            values = (
                raw_value
                if isinstance(raw_value, list)
                else [raw_value]
                if raw_value
                else []
            )

            if column_id == "type":
                mapped = [
                    persona_type_map[v.lower()]
                    for v in values
                    if isinstance(v, str) and v.lower() in persona_type_map
                ]
                if not mapped:
                    continue
                if op == "is":
                    queryset = queryset.filter(persona_type__in=mapped)
                elif op == "is_not":
                    queryset = queryset.exclude(persona_type__in=mapped)
            elif column_id == "simulation_type":
                valid = [
                    v.lower()
                    for v in values
                    if isinstance(v, str)
                    and v.lower() in valid_simulation_choices
                ]
                if not valid:
                    continue
                if op == "is":
                    queryset = queryset.filter(simulation_type__in=valid)
                elif op == "is_not":
                    queryset = queryset.exclude(simulation_type__in=valid)

        return queryset.select_related("organization", "workspace").order_by(
            "-created_at"
        )

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == "list":
            return PersonaListSerializer
        elif self.action == "create":
            return PersonaCreateSerializer
        elif self.action == "field_options":
            return PersonaFieldOptionsSerializer
        return PersonaSerializer

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific persona"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return self._gm.success_response(serializer.data)

    def list(self, request, *args, **kwargs):
        """List personas with pagination"""

        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            # Get paginated response and wrap it in success format
            paginated_response = self.get_paginated_response(serializer.data)
            return self._gm.success_response(paginated_response.data)

        serializer = self.get_serializer(queryset, many=True)
        return self._gm.success_response(serializer.data)

    def create(self, request, *args, **kwargs):
        """Create a new workspace-level persona"""
        # Get the persona name from request
        persona_name = request.data.get("name", "").strip()

        # Get workspace from request (set by auth middleware) or user
        workspace = getattr(request, "workspace", None)
        if not workspace and hasattr(request.user, "workspace"):
            workspace = request.user.workspace

        # Check if persona with same name already exists (system or workspace)
        if persona_name:
            # Check for system persona with same name
            system_persona_exists = Persona.no_workspace_objects.filter(
                name__iexact=persona_name, persona_type=Persona.PersonaType.SYSTEM
            ).exists()

            if system_persona_exists:
                return self._gm.bad_request(
                    "A system persona with this name already exists. Please choose a different name."
                )

            # Check for workspace persona with same name in this workspace
            if workspace:
                workspace_persona_exists = Persona.no_workspace_objects.filter(
                    name__iexact=persona_name,
                    workspace=workspace,
                    persona_type=Persona.PersonaType.WORKSPACE,
                ).exists()

                if workspace_persona_exists:
                    return self._gm.bad_request(
                        "A persona with this name already exists in your workspace."
                    )

        serializer = self.get_serializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        persona = serializer.save()

        # Return full persona details
        output_serializer = PersonaSerializer(persona)
        return self._gm.success_response(
            output_serializer.data, status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """Update a persona (workspace-level only)"""
        instance = self.get_object()

        # Only allow updates to workspace-level personas
        if instance.persona_type == Persona.PersonaType.SYSTEM:
            return self._gm.forbidden_response(
                "System-level personas cannot be modified"
            )

        partial = kwargs.pop("partial", False)
        serializer = PersonaSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        persona = serializer.save()

        return self._gm.success_response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """Delete a persona (workspace-level only)"""
        instance = self.get_object()

        # Only allow deletion of workspace-level personas
        if instance.persona_type == Persona.PersonaType.SYSTEM:
            return self._gm.forbidden_response(
                "System-level personas cannot be deleted"
            )

        instance.deleted = True
        instance.save()

        return self._gm.success_response(
            {"message": "Persona deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )

    @action(detail=False, methods=["get"], url_path="system")
    def system_personas(self, request):
        """Get only system-level personas"""
        queryset = Persona.no_workspace_objects.filter(
            persona_type=Persona.PersonaType.SYSTEM
        )

        serializer = PersonaListSerializer(queryset, many=True)
        return self._gm.success_response(serializer.data)

    @action(detail=False, methods=["get"], url_path="workspace")
    def workspace_personas(self, request):
        """Get only workspace-level personas"""
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )

        workspace = getattr(request, "workspace", None)

        if not organization:
            return self._gm.bad_request("Organization context required")

        # Use no_workspace_objects to bypass automatic filtering
        queryset = Persona.no_workspace_objects.filter(
            persona_type=Persona.PersonaType.WORKSPACE, organization=organization
        )

        if workspace:
            queryset = queryset.filter(workspace=workspace)

        serializer = PersonaListSerializer(queryset, many=True)
        return self._gm.success_response(serializer.data)

    @action(detail=False, methods=["get"], url_path="field-options")
    def field_options(self, request):
        """Get field options/choices for persona creation"""
        serializer = PersonaFieldOptionsSerializer({})
        return self._gm.success_response(serializer.data)

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, id=None):
        """Duplicate a persona (creates a workspace-level copy)"""
        source_persona = self.get_object()
        return self._duplicate_persona(source_persona, request)

    def _duplicate_persona(self, source_persona, request):
        """Helper method to duplicate a persona"""
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )

        workspace = getattr(request, "workspace", None)

        if not organization:
            return self._gm.bad_request("Organization context required")

        # Get custom name from request payload (required)
        new_name = request.data.get("name", "").strip()
        if not new_name:
            return self._gm.bad_request("Name is required in the request payload")

        # Check if persona with same name already exists in workspace
        if workspace:
            workspace_persona_exists = Persona.no_workspace_objects.filter(
                name__iexact=new_name,
                workspace=workspace,
                persona_type=Persona.PersonaType.WORKSPACE,
            ).exists()

            if workspace_persona_exists:
                return self._gm.bad_request(
                    "A persona with this name already exists in your workspace."
                )
        # Create a copy as workspace-level persona
        new_persona = Persona.objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=organization,
            workspace=workspace,
            name=new_name,
            description=source_persona.description,
            gender=source_persona.gender,
            age_group=source_persona.age_group,
            occupation=source_persona.occupation,
            location=source_persona.location,
            personality=source_persona.personality,
            communication_style=source_persona.communication_style,
            multilingual=source_persona.multilingual,
            languages=source_persona.languages,
            accent=source_persona.accent,
            conversation_speed=source_persona.conversation_speed,
            background_sound=source_persona.background_sound,
            finished_speaking_sensitivity=source_persona.finished_speaking_sensitivity,
            interrupt_sensitivity=source_persona.interrupt_sensitivity,
            keywords=source_persona.keywords,
            metadata=source_persona.metadata,
            additional_instruction=source_persona.additional_instruction,
            simulation_type=source_persona.simulation_type,
            punctuation=source_persona.punctuation,
            slang_usage=source_persona.slang_usage,
            typos_frequency=source_persona.typos_frequency,
            regional_mix=source_persona.regional_mix,
            emoji_usage=source_persona.emoji_usage,
            tone=source_persona.tone,
            verbosity=source_persona.verbosity,
        )

        serializer = PersonaSerializer(new_persona)
        return self._gm.success_response(
            serializer.data, status=status.HTTP_201_CREATED
        )


class PersonaDuplicateView(APIView):
    """
    API View for duplicating a persona with custom URL pattern: personas/duplicate/{id}/
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._gm = GeneralMethods()

    def post(self, request, persona_id):
        """Duplicate a persona by ID"""
        try:
            source_persona = Persona.no_workspace_objects.get(id=persona_id)
        except Persona.DoesNotExist:
            return self._gm.bad_request("Persona not found")

        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        workspace = getattr(request, "workspace", None)

        if not organization:
            return self._gm.bad_request("Organization context required")

        # Get custom name from request payload (required)
        new_name = request.data.get("name", "").strip()
        if not new_name:
            return self._gm.bad_request("Name is required in the request payload")

        # Check if persona with same name already exists in workspace
        if workspace:
            workspace_persona_exists = Persona.no_workspace_objects.filter(
                name__iexact=new_name,
                workspace=workspace,
                persona_type=Persona.PersonaType.WORKSPACE,
            ).exists()

            if workspace_persona_exists:
                return self._gm.bad_request(
                    "A persona with this name already exists in your workspace."
                )

        # Create a copy as workspace-level persona
        new_persona = Persona.objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=organization,
            workspace=workspace,
            name=new_name,
            description=source_persona.description,
            gender=source_persona.gender,
            age_group=source_persona.age_group,
            occupation=source_persona.occupation,
            location=source_persona.location,
            personality=source_persona.personality,
            communication_style=source_persona.communication_style,
            multilingual=source_persona.multilingual,
            languages=source_persona.languages,
            accent=source_persona.accent,
            conversation_speed=source_persona.conversation_speed,
            background_sound=source_persona.background_sound,
            finished_speaking_sensitivity=source_persona.finished_speaking_sensitivity,
            interrupt_sensitivity=source_persona.interrupt_sensitivity,
            keywords=source_persona.keywords,
            metadata=source_persona.metadata,
            additional_instruction=source_persona.additional_instruction,
            simulation_type=source_persona.simulation_type,
            punctuation=source_persona.punctuation,
            slang_usage=source_persona.slang_usage,
            typos_frequency=source_persona.typos_frequency,
            regional_mix=source_persona.regional_mix,
            emoji_usage=source_persona.emoji_usage,
            tone=source_persona.tone,
            verbosity=source_persona.verbosity,
        )

        serializer = PersonaSerializer(new_persona)
        return self._gm.success_response(
            serializer.data, status=status.HTTP_201_CREATED
        )
