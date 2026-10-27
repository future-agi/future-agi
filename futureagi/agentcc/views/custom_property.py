import structlog
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.serializers.custom_property import AgentccCustomPropertySchemaSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccCustomPropertySchemaViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """CRUD for custom property schemas. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccCustomPropertySchemaSerializer
    queryset = AgentccCustomPropertySchema.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = super().get_queryset()
        project_id = self.request.query_params.get("project_id")
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccCustomPropertySchemaSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("custom_property_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccCustomPropertySchemaSerializer(instance).data
            )
        except Exception as e:
            logger.exception("custom_property_retrieve_error", error=str(e))
            return self._gm.not_found("Custom property schema not found")

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccCustomPropertySchemaSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            schema = serializer.save(organization=org)
            return self._gm.success_response(
                AgentccCustomPropertySchemaSerializer(schema).data
            )
        except Exception as e:
            logger.exception("custom_property_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccCustomPropertySchemaSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            schema = serializer.save()
            return self._gm.success_response(
                AgentccCustomPropertySchemaSerializer(schema).data
            )
        except Exception as e:
            logger.exception("custom_property_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("custom_property_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def validate(self, request):
        """Validate a set of custom properties against the org's schemas."""
        try:
            properties = request.data.get("properties", {})
            if not isinstance(properties, dict):
                return self._gm.bad_request("properties must be a JSON object")

            schemas = self.get_queryset()
            errors = []
            schema_map = {s.name: s for s in schemas}

            # Check required
            for schema in schemas:
                if schema.required and schema.name not in properties:
                    errors.append(f"Missing required property: {schema.name}")

            # Check types and values
            for key, value in properties.items():
                schema = schema_map.get(key)
                if not schema:
                    continue  # Unknown properties are allowed

                if schema.property_type == "string" and not isinstance(value, str):
                    errors.append(f"Property '{key}' must be a string")
                elif schema.property_type == "number" and (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                ):
                    errors.append(f"Property '{key}' must be a number")
                elif schema.property_type == "boolean" and not isinstance(value, bool):
                    errors.append(f"Property '{key}' must be a boolean")
                elif schema.property_type == "enum" and schema.allowed_values:
                    if value not in schema.allowed_values:
                        errors.append(
                            f"Property '{key}' must be one of: {', '.join(str(v) for v in schema.allowed_values)}"
                        )

            return self._gm.success_response(
                {
                    "valid": len(errors) == 0,
                    "errors": errors,
                }
            )
        except Exception as e:
            logger.exception("custom_property_validate_error", error=str(e))
            return self._gm.bad_request(str(e))
