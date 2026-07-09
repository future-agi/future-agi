import asyncio
import uuid
from urllib.parse import parse_qs
from uuid import uuid4

import structlog
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError

from accounts.models import User, Workspace  # noqa: F401 — User re-exported for tests
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.utils.async_generate_prompt_runner import generate_prompt_async
from model_hub.utils.async_improve_prompt_runner import improve_prompt_async
from model_hub.utils.async_prompt_runner import run_template_async
from model_hub.utils.websocket_direct_manager import WebSocketDirectManager
from model_hub.views.prompt_template import replace_ids_with_column_name_async

logger = structlog.get_logger(__name__)


# WebSocket close codes — cross-side contract with the FE.
# Mirrored in frontend/src/utils/constants.js (WS_CLOSE_CODES). The vitest
# snapshot in frontend/src/sections/workbench/createPrompt/__tests__/
# ws-close-codes.test.js pins these values so drift is caught in CI.
WS_CLOSE_CODE_UNAUTHENTICATED = 4001
WS_CLOSE_CODE_PERMISSION_DENIED = 4003
WS_CLOSE_CODE_NOT_FOUND = 4004


class PromptStreamConsumer(AsyncJsonWebsocketConsumer):
    """Workbench WS consumer.

    Access model: the workspace (from ``?workspace_id=<uuid>`` query param) is
    the single source of truth for what org the caller is acting on. Every
    execute / stop path resolves ``(workspace, org)`` from that query param
    per-frame and gates on ``user.can_access_workspace(workspace)``. Nothing
    about the connect-time membership set is used (root of TH-5944).

    No per-consumer ``organization_id`` is mutated across concurrent async
    tasks; each frame derives ``org_id`` locally and passes it as an explicit
    kwarg to downstream code.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_uuid = None
        self.workspace_id = None

    async def connect(self):
        self.user = self.scope.get("user")
        if not (self.user and self.user.is_authenticated):
            logger.warning("PromptStream connection rejected: user not authenticated.")
            await self.close(code=WS_CLOSE_CODE_UNAUTHENTICATED)
            return

        self.session_uuid = str(uuid4())
        params = parse_qs(self.scope.get("query_string", b"").decode())
        self.workspace_id = params.get("workspace_id", [None])[0]

        logger.info(
            f"PromptStream connection established: user={self.user.id}, "
            f"session={self.session_uuid}, workspace_id={self.workspace_id}"
        )
        await self.accept()

    async def send_json(self, content, close=False):
        try:
            await super().send_json(content, close=close)
        except Exception as e:
            logger.warning(
                f"Failed to send WebSocket message (connection likely closed): {e}"
            )

    async def disconnect(self, close_code):
        logger.info(
            f"PromptStream connection closed: session={self.session_uuid}, code={close_code}"
        )

    async def receive_json(self, content):
        message_type = content.get("type")
        handlers = {
            "run_template": self.handle_run_template,
            "improve_prompt": self.handle_improve_prompt,
            "generate_prompt": self.handle_generate_prompt,
            "stop_streaming": self.handle_stop_streaming,
            "stop_improve_prompt": self.handle_stop_improve_prompt,
            "stop_generate_prompt": self.handle_stop_generate_prompt,
        }
        handler = handlers.get(message_type)
        if handler is None:
            await self._send_ws_error(f"Unknown message type: {message_type}")
            return
        await handler(content)

    # ------------------------------------------------------------------
    # Access resolution — the single gate.
    # ------------------------------------------------------------------

    async def _resolve_workspace_and_org(self, *, correlation=None):
        """Resolve the workspace + derive its org for execute paths.

        Emits a structured error frame + closes the socket with the appropriate
        WS_CLOSE_CODE_* on failure, and returns ``(None, None)``. Returns
        ``(workspace, org_id)`` on success.
        """
        workspace = await self._fetch_workspace()
        if workspace is None:
            await self._send_ws_error(
                "Workspace not found or inactive."
                if self.workspace_id
                else "workspace_id query param is required.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_NOT_FOUND)
            return None, None

        can_access = await database_sync_to_async(self.user.can_access_workspace)(
            workspace
        )
        if not can_access:
            await self._send_ws_error(
                "You do not have permission to use this workspace.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
            return None, None

        return workspace, workspace.organization_id

    async def _resolve_org_for_stop(self):
        """Best-effort org resolution for stop handlers.

        Stop is idempotent and best-effort — a failed resolve means we can't
        write the Redis stop flag, so return ``None`` and let the caller emit
        a soft error instead of tearing down the socket the way execute paths
        do on the same failure.
        """
        workspace = await self._fetch_workspace()
        return workspace.organization_id if workspace else None

    async def _fetch_workspace(self):
        if not self.workspace_id:
            return None
        try:
            return await database_sync_to_async(
                lambda: Workspace.objects.get(id=self.workspace_id, is_active=True)
            )()
        except (Workspace.DoesNotExist, ValueError, ValidationError):
            return None

    async def _send_ws_error(self, message, *, correlation=None):
        payload = {
            "type": "error",
            "message": message,
            "session_uuid": self.session_uuid,
        }
        if correlation:
            payload.update(correlation)
        await self.send_json(payload)

    def _make_ws_manager(self, org_id, *, with_send=True):
        """Single construction site for WebSocketDirectManager.

        Execute paths need the send callback so the runner can push frames;
        stop paths only write a Redis flag and don't need it.
        """
        kwargs = {
            "organization_id": org_id,
            "channel_name": self.channel_name,
            "session_uuid": self.session_uuid,
            "channel_layer": self.channel_layer,
        }
        if with_send:
            kwargs["consumer_send_json_func"] = self.send_json
        return WebSocketDirectManager(**kwargs)

    async def _handle_stop(self, *, action_name, apply_stop, correlation):
        """Shared body for the three stop_* handlers.

        ``apply_stop(ws_manager)`` is an async callable that dispatches the
        specific set_stop_* method. Everything else — org resolution, manager
        construction, ack / soft-error emission — lives here.
        """
        org_id = await self._resolve_org_for_stop()
        if org_id is None:
            await self._send_ws_error(
                f"Cannot {action_name}: workspace not resolved.",
                correlation=correlation,
            )
            return
        ws_manager = self._make_ws_manager(org_id, with_send=False)
        await apply_stop(ws_manager)
        await self.send_json(
            {
                "type": "stop_acknowledged",
                "session_uuid": self.session_uuid,
                **correlation,
            }
        )

    async def _execute_with_org(self, *, correlation, log_context, failure_message, run):
        """Shared scaffold for the three execute_*_async methods.

        Resolves ``(workspace, org_id)`` via the single access gate, then
        awaits ``run(workspace, org_id)`` — a closure supplying the
        handler-specific validation (if any) and the actual runner call.
        Any exception raised out of ``run`` (or the resolve step) is logged
        and turned into a generic error frame, so each handler no longer
        repeats its own try/resolve/except block. ``run`` is expected to
        send its own error frames + close the socket for any handler-specific
        rejection (e.g. cross-workspace template access) rather than raising,
        so those cases don't get masked by ``failure_message``.
        """
        try:
            workspace, org_id = await self._resolve_workspace_and_org(
                correlation=correlation
            )
            if workspace is None:
                return
            await run(workspace, org_id)
        except Exception:
            logger.exception(log_context)
            await self._send_ws_error(failure_message, correlation=correlation)

    async def _fetch_template_for_run(self, template_id, workspace, org_id, correlation):
        """Fetch + validate template access for a run.

        Sends the appropriate error frame and closes the socket on failure,
        returning ``None``. Returns the template on success.
        """
        try:
            template = await database_sync_to_async(PromptTemplate.objects.get)(
                id=template_id
            )
        except PromptTemplate.DoesNotExist:
            await self._send_ws_error("Template not found.", correlation=correlation)
            await self.close(code=WS_CLOSE_CODE_NOT_FOUND)
            return None

        if template.workspace_id:
            # Template scoped to a specific workspace — connected workspace
            # must match. Same-org-different-workspace is a leak too
            # (TH-5944 only closed the cross-org hole).
            if template.workspace_id != workspace.id:
                await self._send_ws_error(
                    "Template does not belong to the selected workspace.",
                    correlation=correlation,
                )
                await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
                return None
        elif template.organization_id != org_id:
            # Legacy/org-wide template — fall back to the looser org check.
            await self._send_ws_error(
                "Template does not belong to the selected workspace's organization.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
            return None

        return template

    # ------------------------------------------------------------------
    # run_template
    # ------------------------------------------------------------------

    async def handle_run_template(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        if not template_id:
            await self._send_ws_error("template_id is required")
            return
        if not version:
            await self._send_ws_error("version is required")
            return

        await self.send_json(
            {
                "type": "execution_started",
                "template_id": template_id,
                "session_uuid": self.session_uuid,
            }
        )
        asyncio.create_task(self.execute_template_async(content, template_id))

    async def execute_template_async(self, content, template_id):
        correlation = {"template_id": template_id}

        async def run(workspace, org_id):
            template = await self._fetch_template_for_run(
                template_id, workspace, org_id, correlation
            )
            if template is None:
                return

            version_to_run = content.get("version")
            execution = await database_sync_to_async(PromptVersion.objects.get)(
                original_template=template, template_version=version_to_run
            )

            await run_template_async(
                template=template,
                execution=execution,
                organization_id=org_id,
                version_to_run=version_to_run,
                is_run=content.get("is_run"),
                run_index=content.get("run_index"),
                workspace=workspace,
                ws_manager=self._make_ws_manager(org_id),
            )

        await self._execute_with_org(
            correlation=correlation,
            log_context="execute_template_async failed",
            failure_message="Execution failed. Please retry.",
            run=run,
        )

    async def handle_stop_streaming(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        await self._handle_stop(
            action_name="stop stream",
            apply_stop=lambda mgr: mgr.set_stop_streaming(template_id, version),
            correlation={"template_id": template_id, "version": version},
        )

    # ------------------------------------------------------------------
    # improve_prompt
    # ------------------------------------------------------------------

    async def handle_improve_prompt(self, content):
        existing_prompt = content.get("existing_prompt")
        existing_prompt = await replace_ids_with_column_name_async(existing_prompt)
        improvement_requirements = content.get("improvement_requirements", "")

        if not existing_prompt:
            await self._send_ws_error("Existing Prompt is required")
            return
        if not improvement_requirements:
            await self._send_ws_error("Improvement Requirements are required")
            return

        payload = {
            "original_prompt": existing_prompt,
            "improvement_suggestions": improvement_requirements,
            "improve_id": f"improve_{uuid.uuid4()}",
            "user_id": str(self.user.id),
            "mixpanel_uid": None,
        }

        await self.send_json(
            {
                "type": "execution_started",
                "improve_id": payload["improve_id"],
                "session_uuid": self.session_uuid,
            }
        )
        asyncio.create_task(
            self.execute_improve_prompt_async(payload, payload["improve_id"])
        )

    async def execute_improve_prompt_async(self, content, improve_id):
        correlation = {"improve_id": improve_id}

        async def run(workspace, org_id):
            await improve_prompt_async(
                original_prompt=content.get("original_prompt", ""),
                improvement_suggestions=content.get("improvement_suggestions", ""),
                examples=content.get("examples", ""),
                improve_id=improve_id,
                organization_id=org_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=self._make_ws_manager(org_id),
            )

        await self._execute_with_org(
            correlation=correlation,
            log_context="execute_improve_prompt_async failed",
            failure_message="Improve failed. Please retry.",
            run=run,
        )

    async def handle_stop_improve_prompt(self, content):
        improve_id = content.get("improve_id")
        if not improve_id:
            await self._send_ws_error("improve_id is required")
            return
        await self._handle_stop(
            action_name="stop improve",
            apply_stop=lambda mgr: mgr.set_stop_improve_prompt(improve_id),
            correlation={"improve_id": improve_id},
        )

    # ------------------------------------------------------------------
    # generate_prompt
    # ------------------------------------------------------------------

    async def handle_generate_prompt(self, content):
        statement = content.get("statement")
        if not statement:
            await self._send_ws_error("Statement is required")
            return

        generation_id = f"generate_{uuid.uuid4()}"
        payload = {
            "description": statement,
            "generation_id": generation_id,
            "user_id": str(self.user.id),
            "mixpanel_uid": None,
        }

        await self.send_json(
            {
                "type": "execution_started",
                "generation_id": generation_id,
                "session_uuid": self.session_uuid,
            }
        )
        asyncio.create_task(self.execute_generate_prompt_async(payload, generation_id))

    async def execute_generate_prompt_async(self, content, generation_id):
        correlation = {"generation_id": generation_id}

        async def run(workspace, org_id):
            await generate_prompt_async(
                description=content.get("description", ""),
                generation_id=generation_id,
                organization_id=org_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=self._make_ws_manager(org_id),
            )

        await self._execute_with_org(
            correlation=correlation,
            log_context="execute_generate_prompt_async failed",
            failure_message="Generate failed. Please retry.",
            run=run,
        )

    async def handle_stop_generate_prompt(self, content):
        generation_id = content.get("generation_id")
        if not generation_id:
            await self._send_ws_error("generation_id is required")
            return
        await self._handle_stop(
            action_name="stop generate",
            apply_stop=lambda mgr: mgr.set_stop_generate_prompt(generation_id),
            correlation={"generation_id": generation_id},
        )
