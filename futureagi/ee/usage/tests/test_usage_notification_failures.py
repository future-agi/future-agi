"""A failed best-effort notification must preserve a computed usage refusal."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from django.test import override_settings

from accounts.models import OrgApiKey
from ee.usage.utils import usage_entries
from tfc.constants.api_calls import APICallStatusChoices


@pytest.mark.parametrize("failure", ["lookup", "create", "transport"])
@override_settings(
    CLOUD_DEPLOYMENT="", WEBSOCKET_ENDPOINT="http://localhost:8000/call-websocket/"
)
def test_notification_failure_preserves_rate_limited_log(failure):
    organization = SimpleNamespace(id="00000000-0000-4000-8000-000000000001")
    refusal = SimpleNamespace(status=APICallStatusChoices.RATE_LIMITED.value)
    with (
        patch.object(usage_entries, "check_audio_in_mappings", return_value=(False, 0)),
        patch.object(usage_entries.APICallType.objects, "get"),
        patch.object(
            usage_entries,
            "check_if_api_call_is_rate_limited",
            return_value=(
                True,
                {"time": "minute", "title": "evaluation", "duration": "one minute"},
            ),
        ),
        patch.object(
            usage_entries.APICallLog.objects, "create", return_value=refusal
        ) as create_log,
        patch("model_hub.utils.OrgApiKey.no_workspace_objects") as keys,
        patch("model_hub.utils.requests.post") as post,
    ):
        keys.get.return_value = SimpleNamespace(
            api_key="synthetic-key", secret_key="synthetic-secret"
        )
        if failure == "lookup":
            keys.get.side_effect = RuntimeError("Synthetic lookup failure")
        elif failure == "create":
            keys.get.side_effect = OrgApiKey.DoesNotExist
            keys.create.side_effect = RuntimeError("Synthetic creation failure")
        else:
            post.side_effect = requests.ConnectionError("Synthetic relay unavailable")
        result = usage_entries.log_and_deduct_cost_for_api_request(
            organization, "synthetic-evaluation"
        )
    assert result is refusal
    assert (
        create_log.call_args.kwargs["status"] == APICallStatusChoices.RATE_LIMITED.value
    )
    if failure != "transport":
        post.assert_not_called()
    else:
        post.assert_called_once()
