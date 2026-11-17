"""Expose the gateway's data-plane refusal contract alongside its cap policy.

These are not Django response codes: the gateway returns them on execution
routes. Keep the OpenAPI extension sourced from the same generated DTO used by
Go, rather than maintaining a second metadata schema in a serializer.
"""

from rest_framework import serializers

from agentcc.contracts.gateway_admin import AdmissionCapRefusalMetadata


class AdmissionCapsPatchField(serializers.DictField):
    class Meta:
        swagger_schema_fields = {
            "x-admission-cap-refusal": {
                "description": (
                    "Gateway admission-cap error.metadata after a server-owned "
                    "admission snapshot resolves, including pre-stream refusals. "
                    "Earlier policy, identity, binding, deadline or nonce validation "
                    "errors may omit identity. 429 rate_limit_error/org_budget_exceeded "
                    "for daily exhaustion; 503 budget_state_unavailable for uncertainty; "
                    "unsupported routes retain 402 call_cap_unenforceable. Only typed "
                    "admission refusals extend the Anthropic-shaped Messages envelope "
                    "with code and metadata; other Messages errors keep type/message. "
                    "No spend, limit or balance values in admission-refusal bodies or "
                    "headers. Ledger-wide failures identify the first applicable daily "
                    "bucket (agent before key), or the run policy if none applies. "
                    "Run exhaustion names the administrative run policy only when its "
                    "ceiling is binding (including equal ceilings). Tighter caller "
                    "budgets or persisted run ceilings, per-call caps and run stops "
                    "keep legacy errors and stop reasons, not admission identity. "
                    "Run-state failures name the configured run policy or, without "
                    "one, the applicable daily policy. Run identity is the configured "
                    "agent binding or verified key ID, never the caller run ID. "
                    "policy_revision is the evaluated server snapshot (zero when "
                    "unactivated), never a spend value. Admission caps default OFF."
                ),
                "type": "object",
                "required": ["error"],
                "properties": {
                    "error": {
                        "type": "object",
                        "required": ["type", "code", "message", "metadata"],
                        "properties": {
                            "type": {"type": "string"},
                            "code": {"type": "string"},
                            "message": {"type": "string"},
                            "metadata": AdmissionCapRefusalMetadata.model_json_schema(),
                        },
                    }
                },
            }
        }
