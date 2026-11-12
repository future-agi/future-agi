"""drf-yasg extensions for the managed-credit API (Swagger 2.0).

Staff writes explicitly publish GatewayAdminBearer and StaffBearer. Retire
only their obsolete combined scheme from schema metadata, without changing
any view's authentication or the platform's global security requirements.
"""

from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from drf_yasg.inspectors import FieldInspector

LEGACY_STAFF_SCHEME = "ManagedCreditStaffBearer"


class ManagedCreditSchemaGenerator(OpenAPISchemaGenerator):
    def get_security_definitions(self):
        return {
            name: value
            for name, value in (super().get_security_definitions() or {}).items()
            if name != LEGACY_STAFF_SCHEME
        }

    def get_security_requirements(self, security_definitions):
        # drf-yasg otherwise makes every defined scheme a global alternative.
        existing = {
            name: value
            for name, value in security_definitions.items()
            if name != LEGACY_STAFF_SCHEME
        }
        return super().get_security_requirements(existing)

    def get_schema(self, request=None, public=False):
        schema = super().get_schema(request=request, public=public)
        if schema is None:
            return schema
        for path_item in schema.paths.values():
            for _, operation in path_item.operations:
                requirements = operation.get("security")
                if requirements is None or not any(
                    LEGACY_STAFF_SCHEME in item for item in requirements
                ):
                    continue
                # Drop legacy-only alternatives rather than publishing an
                # empty alternative (which would advertise anonymous access).
                operation["security"] = [
                    {
                        name: scopes
                        for name, scopes in item.items()
                        if name != LEGACY_STAFF_SCHEME
                    }
                    for item in requirements
                    if set(item) != {LEGACY_STAFF_SCHEME}
                ]
                if not operation["security"]:
                    # With no explicit alternatives left, inherit the existing
                    # global requirements instead of advertising no auth.
                    del operation["security"]
        return schema


class NullableGatewayCreditInspector(FieldInspector):
    """Wrap the summary's nullable reference without mutating its definition."""

    def process_result(self, result, method_name, obj, **kwargs):
        from agentcc.serializers.managed_credits import GatewayCreditStateSerializer

        if (
            isinstance(obj, GatewayCreditStateSerializer)
            and obj.allow_null
            and isinstance(result, openapi.SchemaRef)
        ):
            # drf-yasg copies allow_null and the field's help text onto the
            # shared definition. Null cases belong only to this use site.
            definition = result.resolve(self.components)
            definition.pop("x-nullable", None)
            definition["description"] = GatewayCreditStateSerializer.__doc__
            return openapi.Schema(
                type=openapi.TYPE_OBJECT,
                all_of=[result],
                x_nullable=True,
                description=str(obj.help_text),
            )
        return result
