"""drf-yasg extensions for the managed-credit API (Swagger 2.0).

Only the three staff writes opt into STAFF_CREDIT_SECURITY. Adding their
scheme must not change the global API-key requirements. Swagger 2.0 has no
HTTP bearer scheme, so it uses the actual Authorization header.
"""

from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator
from drf_yasg.inspectors import FieldInspector

STAFF_CREDIT_SECURITY = [{"ManagedCreditStaffBearer": []}]
STAFF_SCHEMES = frozenset(
    name for alternative in STAFF_CREDIT_SECURITY for name in alternative
)


class ManagedCreditSchemaGenerator(OpenAPISchemaGenerator):
    def get_security_definitions(self):
        return {
            **(super().get_security_definitions() or {}),
            "ManagedCreditStaffBearer": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": (
                    "Bearer <signed-in staff user's access token (JWT)> or "
                    "Bearer <gateway admin token>. "
                    "Organization API keys are not accepted."
                ),
            },
        }

    def get_security_requirements(self, security_definitions):
        # drf-yasg otherwise makes every defined scheme a global alternative.
        existing = {
            name: value
            for name, value in security_definitions.items()
            if name not in STAFF_SCHEMES
        }
        return super().get_security_requirements(existing)


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
