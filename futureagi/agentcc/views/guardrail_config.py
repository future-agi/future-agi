import re

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    PIIEntitiesResponseSerializer,
    TopicCategoriesResponseSerializer,
    ValidateCELRequestSerializer,
    ValidateCELResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# Static reference data
PII_ENTITY_TYPES = [
    {"id": "SSN", "label": "Social Security Number", "category": "identity"},
    {"id": "CREDIT_CARD", "label": "Credit Card Number", "category": "financial"},
    {"id": "EMAIL", "label": "Email Address", "category": "contact"},
    {"id": "PHONE", "label": "Phone Number", "category": "contact"},
    {"id": "ADDRESS", "label": "Physical Address", "category": "contact"},
    {"id": "NAME", "label": "Person Name", "category": "identity"},
    {"id": "DOB", "label": "Date of Birth", "category": "identity"},
    {"id": "PASSPORT", "label": "Passport Number", "category": "identity"},
    {"id": "DRIVER_LICENSE", "label": "Driver's License", "category": "identity"},
    {"id": "IP_ADDRESS", "label": "IP Address", "category": "technical"},
    {"id": "BANK_ACCOUNT", "label": "Bank Account Number", "category": "financial"},
    {"id": "MEDICAL_RECORD", "label": "Medical Record Number", "category": "health"},
    {"id": "AWS_KEY", "label": "AWS Access Key", "category": "technical"},
    {"id": "API_KEY", "label": "API Key / Secret", "category": "technical"},
]

TOPIC_CATEGORIES = [
    {
        "id": "violence",
        "label": "Violence & Harm",
        "subcategories": [
            "weapons",
            "self_harm",
            "threats",
            "graphic_violence",
        ],
    },
    {
        "id": "sexual",
        "label": "Sexual Content",
        "subcategories": [
            "explicit",
            "suggestive",
            "minors",
        ],
    },
    {
        "id": "hate",
        "label": "Hate Speech & Discrimination",
        "subcategories": [
            "racism",
            "sexism",
            "religious_hate",
            "disability_hate",
        ],
    },
    {
        "id": "illegal",
        "label": "Illegal Activities",
        "subcategories": [
            "drugs",
            "fraud",
            "hacking",
            "terrorism",
        ],
    },
    {
        "id": "misinformation",
        "label": "Misinformation",
        "subcategories": [
            "health_misinfo",
            "political_misinfo",
            "conspiracy",
        ],
    },
    {
        "id": "privacy",
        "label": "Privacy Violations",
        "subcategories": [
            "doxxing",
            "surveillance",
            "stalking",
        ],
    },
    {
        "id": "profanity",
        "label": "Profanity & Offensive Language",
        "subcategories": [
            "strong_profanity",
            "slurs",
            "insults",
        ],
    },
    {"id": "custom", "label": "Custom Topics", "subcategories": []},
]


class AgentccGuardrailConfigViewSet(BaseModelViewSetMixinWithUserOrg, GenericViewSet):
    """Reference data and utilities for guardrail configuration."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(responses={200: PIIEntitiesResponseSerializer})
    @action(detail=False, methods=["get"], url_path="pii-entities")
    def pii_entities(self, request):
        """List all available PII entity types."""
        return self._gm.success_response(PII_ENTITY_TYPES)

    @swagger_auto_schema(responses={200: TopicCategoriesResponseSerializer})
    @action(detail=False, methods=["get"])
    def topics(self, request):
        """List topic restriction categories."""
        return self._gm.success_response(TOPIC_CATEGORIES)

    @validated_request(
        request_serializer=ValidateCELRequestSerializer,
        responses={
            200: ValidateCELResponseSerializer,
            400: AgentccErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="validate-cel")
    def validate_cel(self, request):
        """Validate a CEL expression syntax."""
        expression = request.validated_data["expression"]

        valid, error = _validate_cel_syntax(expression)
        return self._gm.success_response(
            {
                "expression": expression,
                "valid": valid,
                "error": error,
            }
        )


def _validate_cel_syntax(expression):
    """
    Basic CEL expression syntax validation.
    Checks parentheses balancing, basic operator usage, and common patterns.
    Returns (valid: bool, error: str or None).
    """
    if not isinstance(expression, str) or not expression.strip():
        return False, "Expression must be a non-empty string"

    expr = expression.strip()

    # Check balanced parentheses
    depth = 0
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return False, "Unbalanced parentheses: unexpected ')'"
    if depth != 0:
        return False, "Unbalanced parentheses: missing ')'"

    # Check for empty parentheses (likely a mistake)
    if "()" in expr and not re.search(r"\w+\(\)", expr):
        return False, "Empty parentheses without function call"

    # Check for dangling operators
    if re.search(r"[&|]{3,}", expr):
        return False, "Invalid operator sequence"

    # Basic structure check — must contain at least one comparison or function call
    has_comparison = bool(
        re.search(r"[=!<>]=?|in\b|contains|matches|startsWith|endsWith", expr)
    )
    has_function = bool(re.search(r"\w+\(", expr))
    has_identifier = bool(re.search(r"[a-zA-Z_]\w*", expr))

    if not (has_comparison or has_function or has_identifier):
        return (
            False,
            "Expression must contain at least one comparison, function call, or identifier",
        )

    return True, None
