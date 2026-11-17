"""Background-only tool dispatch; bypass shared cleanup/coercion entirely."""

from dataclasses import dataclass

from ee.agenthub.traceerroragent.background_payloads import (
    ValidatedPayload,
    refuse,
    require_payload,
    witness_for,
)


@dataclass(frozen=True)
class ToolParameters:
    payload: ValidatedPayload

    def __getattr__(self, field):
        data = require_payload(self.payload, self.payload._witness, self.payload.kind)
        if field in data:
            return data[field]
        # These are documented control options, not invented model evidence.
        if field == "exact":
            return False
        if field == "analysis_id":
            return None
        raise AttributeError(field)


def require_tool_parameters(params, guard, name):
    if not isinstance(params, ToolParameters):
        refuse(guard)
    require_payload(params.payload, guard, "tool:" + name)


def run_background_tool(tool, raw_params, context):
    from ai_tools.background import background_tool_denial
    from tfc.middleware.workspace_context import workspace_context

    guard = context.background_analysis
    witness = witness_for(guard)
    try:
        context = guard.check()
        if background_tool_denial(tool, context) is not None:
            refuse(witness)
        from ee.agenthub.traceerroragent.background_payload_wire import (
            consume_tool_payload,
        )

        payload = consume_tool_payload(tool, raw_params, witness)
        with workspace_context(
            workspace=context.workspace,
            organization=context.organization,
            user=context.user,
        ):
            guard.check()
            result = tool.execute(ToolParameters(payload), context)
            guard.check()
        if result.is_error or not result.content:
            refuse(witness)
        return result
    except BaseException:
        witness.failed = True
        raise
