"""billing-ledger-read-v1 views (phase 2a): balance, entries, usage, invoices.

All GET, JSON unless ``format=csv``, never a Stripe call. Roles (contract
section 2): org-wide routes are Owner or Admin (``Level.ADMIN``); the workspace
routes also admit that workspace's workspace_admin. The org always comes from
the caller's membership, never from a parameter; a workspace outside the
caller's org is 404, and an ``X-Workspace-Id`` header naming another workspace
than the path is 403. Querysets are built per request (OR:156).
"""

from __future__ import annotations

import json
import uuid

from django.conf import settings
from django.db import DatabaseError, InterfaceError
from django.http import HttpResponse
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.views import set_rollback
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level, get_org_membership

from ee.usage.serializers.ledger_openapi import (
    ENTRY_QUERY, INVOICE_QUERY, USAGE_QUERY, read_contract,
)
from ee.usage.services import ledger_reads
from ee.usage.services.ledger_reads import LedgerQueryError, error_body


def _error(status: int, code: str, message: str) -> Response:
    return Response(error_body(code, message), status=status)


class _CSVRenderer(BaseRenderer):
    """Lets ``?format=csv`` pass DRF's format negotiation (URL_FORMAT_OVERRIDE).

    CSV bodies are returned as ``HttpResponse`` text by the views; this
    renderer only renders an error body, as JSON.
    """

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, str):
            return data.encode(self.charset)
        return json.dumps(data).encode(self.charset)


def _stripe_mode() -> str:
    key = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "")
    return "live" if key.startswith(("sk_live_", "rk_live_")) else "test"


class _LedgerReadView(APIView):
    permission_classes: list = []
    renderer_classes = [JSONRenderer, _CSVRenderer]
    workspace_route = False

    def handle_exception(self, exc):
        # Authentication runs before _serve, so its failures need the same
        # ledger envelope. Never expose credential or database exception text.
        if isinstance(exc, (DatabaseError, InterfaceError)):
            set_rollback()
            return _error(503, "unavailable", "The billing ledger is unavailable.")
        response = super().handle_exception(exc)
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            code, message = "unauthenticated", "Sign in to read billing."
        else:
            code, message = {
                400: ("invalid_query", "The billing request is invalid."),
                401: ("unauthenticated", "Sign in to read billing."),
                403: ("forbidden", "You do not have access to this billing ledger."),
                404: ("not_found", "No such billing resource."),
            }.get(response.status_code, ("unavailable", "The billing ledger is unavailable."))
        response.data = error_body(code, message)
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        # Only an actual CSV document is text/csv, never a JSON error under
        # ?format=csv (including errors from authentication and negotiation).
        if isinstance(response, Response) and isinstance(
            getattr(request, "accepted_renderer", None), _CSVRenderer
        ):
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = JSONRenderer.media_type
        return super().finalize_response(request, response, *args, **kwargs)

    def _caller(self, request, workspace_id=None):
        """(org, workspace_uuid or None) or an error Response."""
        if not request.user or not request.user.is_authenticated:
            return _error(401, "unauthenticated", "Sign in to read billing.")
        membership = get_org_membership(request.user)
        if membership is None:
            return _error(403, "forbidden", "You are not a member of this organization.")
        org = membership.organization
        level = membership.level_or_legacy
        if not self.workspace_route:
            if level < Level.ADMIN:
                return _error(403, "forbidden", "Organization admin access required.")
            return org, None
        from accounts.models.workspace import Workspace

        try:
            ws = uuid.UUID(str(workspace_id))
        except ValueError:
            return _error(404, "not_found", "No such workspace.")
        if not Workspace.no_workspace_objects.filter(pk=ws, organization=org).exists():
            return _error(404, "not_found", "No such workspace.")
        header = request.headers.get("X-Workspace-Id")
        if header and header != str(ws):
            return _error(403, "forbidden", "The workspace header names another workspace.")
        effective = get_effective_workspace_level(request.user, str(ws))
        if effective is None or effective < Level.WORKSPACE_ADMIN:
            return _error(403, "forbidden", "Workspace admin access required.")
        return org, ws

    def _serve(self, request, build, *, workspace_id=None):
        caller = self._caller(request, workspace_id)
        if isinstance(caller, Response):
            return caller
        org, ws = caller
        params = {k: request.query_params.get(k) for k in request.query_params}
        # JSON is the default representation, not a ledger filter. Leave CSV
        # and unknown values for the endpoint's ordinary query validation.
        if params.get("format") == "json":
            params.pop("format")
        try:
            body = build(org, params, ws)
        except LedgerQueryError as exc:
            return _error(exc.status, exc.code, exc.message)
        except DatabaseError:
            return _error(503, "unavailable", "The billing ledger is unavailable.")
        if isinstance(body, str):
            response = HttpResponse(body, content_type="text/csv")
            response["Content-Disposition"] = "attachment"
            return response
        return Response(body)


class BalanceView(_LedgerReadView):
    @read_contract("Balance")
    def get(self, request):
        def build(org, params, ws):
            if params:
                raise LedgerQueryError("invalid_query", f"unknown parameter {sorted(params)[0]!r}")
            return ledger_reads.balance_payload(org)

        return self._serve(request, build)


class EntriesView(_LedgerReadView):
    @read_contract("EntriesPage", ENTRY_QUERY, csv=True)
    def get(self, request):
        return self._serve(request, lambda org, p, ws: ledger_reads.entries_page(org, p))


class UsageView(_LedgerReadView):
    @read_contract("Usage", USAGE_QUERY, csv=True)
    def get(self, request):
        return self._serve(request, lambda org, p, ws: ledger_reads.usage_payload(org, p))


class InvoicesView(_LedgerReadView):
    @read_contract("InvoicesPage", INVOICE_QUERY)
    def get(self, request):
        return self._serve(
            request, lambda org, p, ws: ledger_reads.invoices_page(org, p, mode=_stripe_mode())
        )


class WorkspaceEntriesView(_LedgerReadView):
    workspace_route = True

    @read_contract("EntriesPage", ENTRY_QUERY, workspace=True, csv=True)
    def get(self, request, workspace_id):
        return self._serve(
            request,
            lambda org, p, ws: ledger_reads.entries_page(org, p, workspace_id=ws),
            workspace_id=workspace_id,
        )


class WorkspaceUsageView(_LedgerReadView):
    workspace_route = True

    @read_contract("Usage", USAGE_QUERY, workspace=True, csv=True)
    def get(self, request, workspace_id):
        return self._serve(
            request,
            lambda org, p, ws: ledger_reads.usage_payload(org, p, workspace_id=ws),
            workspace_id=workspace_id,
        )
