"""``POST /usage/ledger/v1/events/``: the usage-event-v1 out-of-process ingest.

A service credential only (``Authorization: Bearer fa_ledger_...``): a session,
cookie or user API key is 401. Whole-request answers: 401 ``unauthenticated``,
403 for a disabled credential, 400 for a body that is not an
``IngestRequest``, 413 for more than 500 items, 503 when the database is down
(the producer retries the same bytes). Otherwise 200 with one result per item,
in request order (``services/usage_ingest.py``).
"""

from __future__ import annotations

import json

from django.db import DatabaseError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.usage.serializers.ledger_openapi import INGEST_CONTRACT
from ee.usage.services import usage_ingest
from ee.usage.services.ledger_reads import error_body


class _RawJSON(JSONParser):
    """Parse only JSON bodies; anything else is a 400 from the view."""


class UsageEventIngestView(APIView):
    authentication_classes: list = []
    permission_classes: list = []
    parser_classes = [_RawJSON]

    @INGEST_CONTRACT
    def post(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _, token = header.partition(" ")
        if scheme != "Bearer" or not token.startswith(usage_ingest.TOKEN_PREFIX):
            return Response(error_body("unauthenticated", "A ledger service credential is required."), status=401)
        try:
            credential = usage_ingest.authenticate(token.strip())
        except DatabaseError:
            return Response(error_body("unavailable", "The billing ledger is unavailable."), status=503)
        if credential is None:
            return Response(error_body("unauthenticated", "Unknown credential."), status=401)
        if not credential.enabled:
            return Response(error_body("forbidden", "This credential is disabled."), status=403)
        try:
            body = json.loads(request.body or b"null")
        except (ValueError, UnicodeDecodeError):
            return Response(error_body("invalid_query", "The body is not JSON."), status=400)
        if not isinstance(body, dict) or set(body) != {"events"} or not isinstance(body["events"], list):
            return Response(error_body("invalid_query", "The body is not an IngestRequest."), status=400)
        events = body["events"]
        if len(events) > usage_ingest.MAX_ITEMS:
            return Response(error_body("invalid_query", "At most 500 events."), status=413)
        if not events:
            return Response(error_body("invalid_query", "At least one event."), status=400)
        try:
            results = usage_ingest.ingest_events(events, credential=credential)
        except DatabaseError:
            return Response(error_body("unavailable", "The billing ledger is unavailable."), status=503)
        return Response({"results": [r.as_json() for r in results]})
