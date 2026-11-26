"""Synthetic saved provider results, available only in the E2E image."""

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from django.db import transaction
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class SavedPromptRequest(serializers.Serializer):
    name = serializers.RegexField(r"^e2e-prompt2-[a-zA-Z0-9-]+$", max_length=120)


class E2ESavedPromptView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = SavedPromptRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = getattr(request, "workspace", None)
        if not workspace or not request.user.can_write_to_workspace(workspace):
            return Response(
                {"detail": "Workspace write access is required."}, status=403
            )
        organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        name = serializer.validated_data["name"]
        # No caller-supplied prices, source labels, models or usage. These are
        # synthetic saved results, not inference or provider invoice evidence.
        scenarios = [
            ("v1", "azure_ai/mistral-large", 1000, 500, "estimate"),
            ("v2", "gpt-4o", 0, 0, "zero"),
            ("v3", "gpt-4o", 1000, 500, "catalog"),
        ]
        with transaction.atomic():
            template = PromptTemplate.no_workspace_objects.create(
                name=name,
                organization=organization,
                workspace=workspace,
                created_by=request.user,
            )
            template.collaborators.add(request.user)
            versions = []
            for version, model, input_tokens, output_tokens, label in scenarios:
                usage = {
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }
                cost = calculate_total_cost(model, usage)
                row = PromptVersion.no_workspace_objects.create(
                    original_template=template,
                    template_version=version,
                    is_default=version == "v1",
                    is_draft=False,
                    prompt_config_snapshot={
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": f"{name} synthetic input"}
                                ],
                            }
                        ],
                        "configuration": {
                            "model": model,
                            "temperature": 0.7,
                            "max_tokens": 1000,
                            "tools": [],
                            "output_format": "string",
                        },
                        "placeholders": [],
                    },
                    output=[f"{name} synthetic {label} result"],
                    metadata=[{"cost": cost, "usage": usage, "response_time": 0.12}],
                )
                versions.append({"id": str(row.id), "version": version})
        return Response(
            {"prompt_id": str(template.id), "versions": versions}, status=201
        )
