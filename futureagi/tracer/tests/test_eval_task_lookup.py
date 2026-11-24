"""Malformed detail paths must not escape Django UUID coercion as HTTP 500."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.models.eval_task import EvalTask
from tracer.views.eval_task import EvalTaskView


class EvalTaskLookupTests(SimpleTestCase):
    """Exercise real routing, field coercion and DRF exception handling, no DB."""

    def request(self, lookup, *, authenticated=True):
        path = f"/tracer/eval-task/{lookup}/"
        request = APIRequestFactory().get(path)
        if authenticated:
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        match = resolve(path)
        # Scope selection is covered by the integration suite. Keep the real
        # get_queryset: it coerces the pk before retrieve's object lookup.
        # An empty real queryset still validates UUIDs without DB access.
        with (
            patch.object(
                EvalTaskView,
                "_scope_eval_task_queryset",
                return_value=EvalTask.objects.none(),
            ),
            patch.object(
                EvalTaskView,
                "_scope_custom_eval_config_queryset",
                side_effect=lambda queryset: queryset.none(),
            ),
        ):
            return match.func(request, **match.kwargs)

    def test_malformed_lookup_returns_not_found(self):
        # Unknown action names are routed to retrieve(pk=...). Do not add
        # aliases for them or let Django's ValidationError become a 500.
        for lookup in ("not-a-uuid", "get_details"):
            with self.subTest(lookup=lookup):
                response = self.request(lookup)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(str(response.data["detail"]), "Not found.")

    def test_missing_uuid_still_returns_not_found(self):
        self.assertEqual(self.request(str(uuid.uuid4())).status_code, 404)

    def test_malformed_lookup_still_requires_authentication(self):
        response = self.request("not-a-uuid", authenticated=False)
        self.assertIn(response.status_code, (401, 403))
