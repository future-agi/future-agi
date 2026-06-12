from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel as PydanticBaseModel

logger = logging.getLogger(__name__)


class EmptyInput(PydanticBaseModel):
    """Input model for tools that take no parameters."""

    pass


@dataclass
class ToolContext:
    """Context injected into every tool call.

    Carries the authenticated user, organization, and workspace.
    Created from the request in the transport layer (MCP or AI Assistant).

    transport: which surface is calling — "falcon" (WS chat; strict default:
        only the Confirm button approves destructive actions), "mcp"
        (external MCP client, which carries its own human-in-the-loop layer),
        or "harness" (verify_* scripts). Phase 3A confirmation gate behavior
        is transport-aware (ai_tools/confirmations.py).
    conversation_id: scopes confirmation approvals to one chat.
    """

    user: Any  # accounts.models.User
    organization: Any  # accounts.models.Organization
    workspace: Any  # accounts.models.Workspace
    transport: str = "falcon"  # falcon | mcp | harness
    conversation_id: str | None = None

    @property
    def user_id(self):
        return self.user.id

    @property
    def organization_id(self):
        return self.organization.id

    @property
    def workspace_id(self):
        return self.workspace.id


@dataclass
class ToolResult:
    """Result returned by every tool execution.

    content: Markdown-formatted string optimized for LLM consumption.
    data: Optional structured data dict (for programmatic use).
    is_error: Whether this result represents an error.
    error_code: Optional structured error code (e.g. NOT_FOUND, VALIDATION_ERROR).
    """

    content: str
    data: dict | None = None
    is_error: bool = False
    error_code: str | None = None

    @classmethod
    def error(
        cls,
        message: str,
        data: dict | None = None,
        error_code: str | None = None,
    ) -> ToolResult:
        return cls(
            content=f"**Error:** {message}",
            data=data,
            is_error=True,
            error_code=error_code or "INTERNAL_ERROR",
        )

    @classmethod
    def not_found(cls, entity_type: str, entity_id: str) -> ToolResult:
        return cls(
            content=f"**Not Found:** {entity_type} with ID `{entity_id}` was not found in this workspace.",
            is_error=True,
            error_code="NOT_FOUND",
        )

    @classmethod
    def permission_denied(cls, message: str) -> ToolResult:
        return cls(
            content=f"**Permission Denied:** {message}",
            is_error=True,
            error_code="PERMISSION_DENIED",
        )

    @classmethod
    def feature_unavailable(cls, feature: str) -> ToolResult:
        return cls(
            content=(
                f"**Feature Unavailable:** `{feature}` is not available on "
                f"your current plan. Upgrade to access it."
            ),
            data={"feature": feature, "upgrade_required": True},
            is_error=True,
            error_code="ENTITLEMENT_DENIED",
        )

    @classmethod
    def validation_error(cls, message: str) -> ToolResult:
        return cls(
            content=f"**Validation Error:** {message}",
            is_error=True,
            error_code="VALIDATION_ERROR",
        )


class BaseTool(ABC):
    """Abstract base class for all AI tools.

    Subclasses must define:
    - name: unique tool identifier (snake_case)
    - description: what the tool does (shown to LLMs)
    - category: tool group (context, evaluations, datasets, tracing, etc.)
    - input_model: Pydantic model for input validation
    - execute(): the actual tool logic
    """

    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str]
    input_model: ClassVar[type[PydanticBaseModel]] = EmptyInput
    # Phase 3A execution policy: read | mutate | destructive. "" means
    # unclassified — registry.register backfills it name-only; the DRF
    # bridge sets it explicitly at registration. Destructive tools are
    # gated behind a server-held confirmation (see _confirmation_gate).
    execution_policy: ClassVar[str] = ""
    # Optional human note shown in the confirmation preview when the action
    # has a cheap compensating tool (overrides ai_tools.confirmations
    # UNDO_NOTES).
    undo_note: ClassVar[str | None] = None
    # Optional prompt template (str.format over the validated args) rendered
    # onto the EXECUTED destructive leg as data["undo"] = {"prompt", "note"} —
    # one-click Undo sends it as a normal chat message (design §1.10).
    undo_prompt: ClassVar[str | None] = None
    # Optional per-tool execution timeout (seconds) honored by the Falcon
    # agent's tool dispatcher. ``None`` means "use the default budget".
    # A few tools do legitimately-slow synchronous work BEFORE handing the
    # rest off to an async worker (e.g. create_experiment snapshots the
    # dataset + starts a Temporal workflow synchronously); they would
    # otherwise trip the 30s default and surface a spurious timeout to the
    # model even though the experiment was, in fact, created (F4 / TH-5467).
    exec_timeout: ClassVar[float | None] = None

    CONFIRM_PARAM_DESCRIPTION: ClassVar[str] = (
        "Destructive action. Omit on the first call to get a preview. "
        "After the user approves via the Confirm button, call again with "
        "EXACTLY the same arguments plus confirm=true."
    )

    @abstractmethod
    def execute(self, params: PydanticBaseModel, context: ToolContext) -> ToolResult:
        """Execute the tool with validated params and context."""
        ...

    @property
    def input_schema(self) -> dict:
        """Return JSON Schema for the input model.

        Destructive tools advertise an extra OPTIONAL `confirm` boolean —
        injected into the schema (never into the 541 input models) and
        popped off raw params before pydantic validation in run().
        """
        schema = self.input_model.model_json_schema()
        if self.execution_policy == "destructive":
            import copy

            schema = copy.deepcopy(schema)
            schema.setdefault("properties", {})["confirm"] = {
                "type": "boolean",
                "description": self.CONFIRM_PARAM_DESCRIPTION,
            }
        return schema

    @staticmethod
    def _coerce_confirm(value: Any) -> bool:
        return value is True or str(value).strip().lower() in ("true", "1", "yes")

    def run(self, raw_params: dict | None, context: ToolContext) -> ToolResult:
        """Validate input, execute tool, and handle errors.

        This is the public entry point. Transport layers call this.
        Sets the per-request workspace/organization context via ContextVars
        so that BaseModel managers, AutoWorkspaceField, and queryset scoping
        work correctly without touching django.conf.settings.

        Destructive tools (execution_policy) pass through the Phase 3A
        confirmation gate BEFORE execute(): without a server-held approval
        the call returns a preview + CONFIRMATION_REQUIRED and has zero
        side effects — even if the prompt is jailbroken into confirm=true.
        """
        from tfc.middleware.workspace_context import workspace_context

        confirm = False
        try:
            cleaned = self._clean_params(raw_params or {})
            if self.execution_policy == "destructive":
                # Pop ONLY for destructive tools — never swallow a
                # legitimate field on other tools.
                confirm = self._coerce_confirm(cleaned.pop("confirm", None))
            params = self.input_model.model_validate(cleaned)
        except Exception as e:
            # Include the expected schema so the LLM can self-correct
            schema = self.input_schema
            required = schema.get("required", [])
            props = schema.get("properties", {})
            # Build a compact schema hint
            fields = []
            for name, spec in props.items():
                typ = spec.get("type", "string")
                desc = spec.get("description", "")[:60]
                req = " (REQUIRED)" if name in required else ""
                fields.append(f"  - {name}: {typ}{req} — {desc}")
            schema_hint = "\n".join(fields[:10])
            return ToolResult.error(
                f"Invalid parameters: {e}\n\nExpected schema:\n{schema_hint}\n\nYou sent: {raw_params}",
                error_code="VALIDATION_ERROR",
            )

        try:
            with workspace_context(
                workspace=context.workspace,
                organization=context.organization,
                user=context.user,
            ):
                if self.execution_policy == "destructive":
                    # Inside workspace_context so preview ORM queries are
                    # workspace-scoped. Raising here fails CLOSED (the
                    # outer except returns an error, never executes).
                    gate = self._confirmation_gate(params, context, confirm)
                    if gate is not None:
                        return gate
                result = self.execute(params, context)
                if (
                    self.execution_policy == "destructive"
                    and isinstance(result, ToolResult)
                    and not result.is_error
                ):
                    # Audit hook: every executed destructive leg carries
                    # confirmed=true (the gate only returns None after
                    # consuming an approval).
                    if result.data is None:
                        result.data = {}
                    if isinstance(result.data, dict):
                        result.data["confirmed"] = True
                        undo = self._build_undo_payload(params, context)
                        if undo:
                            result.data["undo"] = undo
                return result
        except Exception as e:
            from ai_tools.error_codes import code_from_exception

            logger.exception(f"Tool {self.name} failed: {e}")
            return ToolResult.error(
                f"Tool execution failed: {e}",
                error_code=code_from_exception(e),
            )

    # ------------------------------------------------------------------
    # Phase 3A — destructive-action confirmation gate
    # ------------------------------------------------------------------

    def confirmation_undo_note(
        self, params: PydanticBaseModel, context: ToolContext
    ) -> str | None:
        """Nullable note when a cheap compensating action exists."""
        if self.undo_note:
            return self.undo_note
        from ai_tools import confirmations

        return confirmations.undo_note_for(self.name)

    def confirmation_preview(
        self, params: PydanticBaseModel, context: ToolContext
    ) -> str:
        """Human preview of what WOULD happen.

        Per-tool builders registered via the `confirm_preview` bridge config
        key (ai_tools.confirmations.PREVIEW_BUILDERS) win; exceptions inside
        a builder fall back to the default builder — never block the gate.
        """
        from ai_tools import confirmations

        builder = confirmations.PREVIEW_BUILDERS.get(self.name)
        if builder is not None:
            try:
                preview = builder(params.model_dump(exclude_none=True), context)
                if preview:
                    return str(preview)[:1200]
            except Exception:
                logger.exception(
                    f"confirm_preview builder for {self.name} failed; "
                    "falling back to default preview"
                )
        return confirmations.build_preview(
            self,
            params.model_dump(exclude_none=True),
            undo_note=self.confirmation_undo_note(params, context),
        )

    def _build_undo_payload(
        self, params: PydanticBaseModel, context: ToolContext
    ) -> dict | None:
        """data["undo"] for the executed destructive leg (design §1.10)."""
        if not self.undo_prompt:
            return None
        args = params.model_dump(exclude_none=True)
        try:
            prompt = self.undo_prompt.format(**args)
        except Exception:
            prompt = self.undo_prompt  # unrenderable template: ship raw text
        return {"prompt": prompt, "note": self.confirmation_undo_note(params, context)}

    def _confirmation_gate(
        self, params: PydanticBaseModel, context: ToolContext, confirm: bool
    ) -> ToolResult | None:
        """Server-side enforcement (PHASES.md:204): returns None ONLY after
        consuming a valid approval; any other path returns a result and
        execute() never runs.

        - falcon transport: only a Confirm-button approval (consumer flips
          the record to ``approved``) unlocks the phase-2 call. A cold
          confirm=true just creates a fresh preview — jailbreak-proof.
        - mcp/harness transports: the client is the (human-operated)
          approver, but preview-first still holds: phase-2 needs an
          existing exact-args record, so a cold confirm=true only previews.
        """
        from ai_tools import confirmations

        args = params.model_dump()
        args_hash = confirmations.compute_args_hash(args)
        rec = confirmations.lookup(context, self.name, args_hash)
        transport = getattr(context, "transport", "falcon") or "falcon"

        if confirm and rec:
            status = rec.get("status")
            if transport == "falcon":
                if status == "approved":
                    confirmations.consume(rec)
                    return None
                if status == "pending":
                    return ToolResult(
                        content=(
                            "CONFIRMATION PENDING — no action was taken. "
                            "The user has not approved this action yet. Ask "
                            "them to click the Confirm button on the "
                            "confirmation card in chat (typed replies do NOT "
                            "approve destructive actions), then call this "
                            "tool again with identical arguments plus "
                            "confirm=true."
                        ),
                        data={
                            "confirmation": {
                                "token": rec.get("token"),
                                "tool_name": self.name,
                                "policy": "destructive",
                                "status": "pending",
                                "expires_at": rec.get("expires_at"),
                            }
                        },
                        is_error=False,
                        error_code="CONFIRMATION_PENDING",
                    )
                # cancelled/consumed -> fall through to a fresh pending
            else:  # mcp / harness — the client itself is the approver
                if status in ("pending", "approved"):
                    confirmations.consume(rec)
                    return None

        if not confirm and rec and rec.get("status") == "approved":
            # Deviation from the §1.4 pseudo-code (documented): the LLM
            # re-called after the Confirm button but FORGOT confirm=true.
            # Creating a fresh pending here would clobber the user's
            # approval and force a second button click; instead keep the
            # approval intact and instruct the LLM. Execution still
            # strictly requires confirm=true — no security change.
            return ToolResult(
                content=(
                    "APPROVAL ALREADY GRANTED — no action was taken yet. "
                    "The user clicked Confirm for this exact action. Call "
                    "this tool again RIGHT NOW with the SAME arguments plus "
                    "confirm=true to execute it. Do not call any other tool "
                    "first."
                ),
                data={
                    "confirmation": {
                        "token": rec.get("token"),
                        "tool_name": self.name,
                        "policy": "destructive",
                        "status": "approved",
                        "expires_at": rec.get("expires_at"),
                    }
                },
                is_error=False,
                error_code="CONFIRMATION_PENDING",
            )

        preview = self.confirmation_preview(params, context)
        undo_note = self.confirmation_undo_note(params, context)
        token, expires_at = confirmations.create_pending(
            context, self.name, args_hash, args, preview, undo_note
        )
        return ToolResult(
            content=(
                "CONFIRMATION REQUIRED — no action was taken.\n\n"
                f"{preview}\n\n"
                "Tell the user exactly what will happen and wait for them to "
                "approve via the Confirm button shown in chat. Typed replies "
                "do NOT approve destructive actions. After approval, call "
                "this tool again with identical arguments plus confirm=true."
            ),
            data={
                "confirmation": {
                    "token": token,
                    "tool_name": self.name,
                    "args": args,
                    "preview": preview,
                    "expires_at": expires_at,
                    "policy": "destructive",
                    "undo_note": undo_note,
                }
            },
            is_error=False,
            error_code="CONFIRMATION_REQUIRED",
        )

    @staticmethod
    def _clean_params(raw_params: dict) -> dict:
        """Pre-process tool parameters to handle common LLM quirks.

        LLMs sometimes send stringified JSON for list/dict fields.
        This attempts to parse string values that look like JSON.
        """
        import json

        cleaned = {}
        for key, value in raw_params.items():
            if isinstance(value, str) and value.strip().startswith(("[", "{")):
                try:
                    cleaned[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    cleaned[key] = value
            else:
                cleaned[key] = value
        return cleaned

    def to_dict(self) -> dict:
        """Serialize tool metadata for discovery endpoints."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "input_schema": self.input_schema,
            "execution_policy": self.execution_policy or "read",
        }
