import structlog
from django.conf import settings
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)


class EvaluateWithAgentInput(PydanticBaseModel):
    source_id: str = Field(
        description="ID of the span, trace, session, dataset row, or cell to evaluate"
    )
    input_scope: str = Field(
        description=(
            "Type of the item to evaluate. "
            "Must be one of: 'span', 'trace', 'session', 'dataset_row', 'cell'"
        )
    )
    criteria: str = Field(
        description=(
            "Evaluation criteria or question. "
            "Can be vague (e.g. 'Is the response helpful?') or detailed "
            "(e.g. 'Rate whether the assistant followed the user instructions on a scale from 0 to 1'). "
            "The agent will formalize vague criteria automatically."
        )
    )
    choices: list[str] | None = Field(
        default=None,
        description=(
            "Labels for the evaluation result. "
            "Omit (or pass null) for numeric scoring mode (returns 0.0–1.0). "
            "Pass a list like ['Passed', 'Failed'] or ['Good', 'Acceptable', 'Poor'] for categorical evaluation."
        ),
    )
    eval_template_id: str | None = Field(
        default=None,
        description=(
            "UUID of an existing eval template to associate with this evaluation. "
            "When provided, enables few-shot examples from past human feedback."
        ),
    )
    kb_id: str | None = Field(
        default=None,
        description=(
            "UUID of a knowledge base to consult during evaluation. "
            "Useful when criteria reference domain-specific standards or policies."
        ),
    )


def _source_in_scope(input_scope, source_id, organization, workspace):
    """Whether ``source_id`` names an item of ``input_scope`` in
    ``organization``, visible from ``workspace`` by the managers' rule."""
    import uuid

    from django.db.models import F, Q

    from model_hub.models.develop_dataset import Cell, Row
    from tfc.utils.tenant_scope import workspace_scope_q
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession

    if organization is None or not source_id:
        return False
    if input_scope == "span":
        # Span ids are the tracer's own string ids, not UUIDs.
        return ObservationSpan.no_workspace_objects.filter(
            workspace_scope_q(workspace, "project__workspace"),
            id=str(source_id),
            project__organization=organization,
        ).exists()
    try:
        source_uuid = uuid.UUID(str(source_id))
    except (TypeError, ValueError, AttributeError):
        return False
    if input_scope in ("trace", "session"):
        model = Trace if input_scope == "trace" else TraceSession
        rows = model.no_workspace_objects.filter(
            workspace_scope_q(workspace, "project__workspace"),
            id=source_uuid,
            project__organization=organization,
        )
    elif input_scope == "dataset_row":
        rows = Row.no_workspace_objects.filter(
            workspace_scope_q(workspace, "dataset__workspace"),
            id=source_uuid,
            dataset__organization=organization,
            dataset__deleted=False,
        )
    else:
        # The row owns the cell; the duplicate dataset link may be absent,
        # but a contradictory dataset or column link must never grant access.
        rows = Cell.no_workspace_objects.filter(
            Q(dataset__isnull=True) | Q(dataset_id=F("row__dataset_id")),
            workspace_scope_q(workspace, "row__dataset__workspace"),
            id=source_uuid,
            row__dataset__organization=organization,
            row__dataset__deleted=False,
            row__deleted=False,
            column__dataset_id=F("row__dataset_id"),
            column__deleted=False,
            deleted=False,
        )
    return rows.exists()


@register_tool
class EvaluateWithAgentTool(BaseTool):
    name = "evaluate_with_agent"
    description = (
        "Evaluate a span, trace, session, dataset row, or cell against custom criteria "
        "using an AI evaluation agent. "
        "Use this when asked to evaluate, judge, assess, or score a specific item. "
        "The agent intelligently inspects the data, formalizes vague criteria, optionally "
        "consults a knowledge base or past feedback, and produces a result with a confidence "
        "score and a detailed explanation. "
        "Supports both numeric scoring (0.0–1.0) and categorical labels (e.g. Passed/Failed)."
    )
    category = "evaluations"
    input_model = EvaluateWithAgentInput

    _VALID_SCOPES = {"span", "trace", "session", "dataset_row", "cell"}

    def execute(
        self, params: EvaluateWithAgentInput, context: ToolContext
    ) -> ToolResult:
        from tfc.ee_gating import EEFeature, check_ee_feature

        try:
            check_ee_feature(
                EEFeature.AGENTIC_EVAL, org_id=str(context.organization_id)
            )
        except Exception as e:
            from tfc.ee_gating import FeatureUnavailable

            if isinstance(e, FeatureUnavailable):
                return ToolResult.feature_unavailable(e.feature)
            raise

        try:
            from ee.agenthub.eval_orchestrator import (
                EvalConfig,
                EvalOrchestrator,
                EvalScout,
            )
            from ee.agenthub.eval_orchestrator.utils import (
                build_input_summary,
                gather_available_resources,
            )
        except ImportError:
            if settings.DEBUG:
                logger.warning(
                    "Could not import ee.agenthub.eval_orchestrator", exc_info=True
                )
            return ToolResult.feature_unavailable(EEFeature.AGENTIC_EVAL.value)

        if params.input_scope not in self._VALID_SCOPES:
            return ToolResult.validation_error(
                f"Invalid input_scope '{params.input_scope}'. "
                f"Must be one of: {', '.join(sorted(self._VALID_SCOPES))}"
            )

        # Evaluate only the caller's own item, with the caller's own knowledge
        # base; any other id is not found.
        if not _source_in_scope(
            params.input_scope,
            params.source_id,
            context.organization,
            context.workspace,
        ):
            return ToolResult.not_found(
                params.input_scope.replace("_", " ").title(), str(params.source_id)
            )
        if params.kb_id:
            from model_hub.utils.workspace_scope import knowledge_base_id_for

            if (
                knowledge_base_id_for(
                    context.organization, context.workspace, params.kb_id
                )
                is None
            ):
                return ToolResult.not_found("Knowledge base", str(params.kb_id))

        # Gather external resources (KB, feedback examples, MCP tools)
        available_resources = gather_available_resources(
            organization_id=str(context.organization_id),
            eval_template_id=params.eval_template_id,
            kb_id=params.kb_id,
            workspace=context.workspace,
        )

        # Build a lightweight input summary for the Scout pre-pass
        input_summary = build_input_summary(params.input_scope, params.source_id)

        # Scout: cheap pre-pass to determine evaluation strategy
        scout = EvalScout()
        scout_brief = scout.run(
            criteria=params.criteria,
            input_summary=input_summary,
            available_resources=available_resources,
        )
        # Orchestrator expects available_resources embedded in the brief
        scout_brief["available_resources"] = available_resources

        # Build eval config
        eval_config = EvalConfig(
            criteria=params.criteria,
            input_scope=params.input_scope,
            source_id=params.source_id,
            choices=params.choices,
            eval_template_id=params.eval_template_id,
            kb_id=params.kb_id,
            available_resources=available_resources,
            organization_id=str(context.organization_id),
            workspace=context.workspace,
        )

        # Run the orchestrator (agentic loop)
        orchestrator = EvalOrchestrator(
            eval_config=eval_config, scout_brief=scout_brief
        )
        output = orchestrator.run()

        return self._format_result(output, params)

    def _format_result(
        self, output: dict, params: EvaluateWithAgentInput
    ) -> ToolResult:
        eval_result = output.get("result", {})
        score = eval_result.get("result", "N/A")
        confidence = eval_result.get("confidence", 0)
        explanation = eval_result.get("explanation", "")

        token_usage = output.get("token_usage", {})
        total_tokens = token_usage.get("total", {}).get("total_tokens", 0)
        resources_used = output.get("resources_used", [])

        scope_label = params.input_scope.replace("_", " ").title()

        lines = [
            "## Evaluation Result",
            "",
            f"**{scope_label} ID**: `{params.source_id}`",
            f"**Criteria**: {params.criteria}",
            "",
            "### Verdict",
            f"**Result**: {score}",
            f"**Confidence**: {confidence:.0%}",
            "",
            "### Explanation",
            explanation,
            "",
            "### Metadata",
            f"- Model: {output.get('model_used', 'unknown')}",
            f"- Orchestrator turns: {output.get('orchestrator_turns', 0)}",
            f"- Eval calls: {output.get('eval_calls', 0)}",
            f"- Resources used: {', '.join(resources_used) if resources_used else 'none'}",
        ]
        if total_tokens:
            lines.append(f"- Total tokens: {total_tokens:,}")

        return ToolResult(
            content="\n".join(lines),
            data={
                "result": score,
                "confidence": confidence,
                "explanation": explanation,
                "model_used": output.get("model_used"),
                "orchestrator_turns": output.get("orchestrator_turns"),
                "eval_calls": output.get("eval_calls"),
                "resources_used": resources_used,
                "token_usage": token_usage,
            },
        )
