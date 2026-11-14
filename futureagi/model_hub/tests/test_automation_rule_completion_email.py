"""Rule completion emails identify the person who requested the run."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from model_hub.utils.annotation_queue_helpers import send_rule_completion_email


@pytest.mark.unit
@pytest.mark.parametrize("error_message", [None, "Rule evaluation failed"])
@pytest.mark.parametrize(
    "name, trigger, expected",
    [
        ("Ada Lovelace", "user", "Ada Lovelace"),
        ("", "user", "ada@example.com"),
        ("Ada Lovelace", "schedule", "the rule schedule"),
        ("Ada Lovelace", "deleted", "the rule schedule"),
    ],
)
def test_rule_completion_actor(name, trigger, expected, error_message):
    user_model = get_user_model()
    user = user_model(name=name, email="ada@example.com")
    triggered_by_user_id = None if trigger == "schedule" else str(user.pk)
    with (
        patch(
            "model_hub.utils.annotation_queue_helpers._rule_completion_recipients",
            return_value=["manager@example.com"],
        ),
        patch.object(
            user_model.objects,
            "get",
            return_value=user,
            side_effect=user_model.DoesNotExist if trigger == "deleted" else None,
        ) as get_user,
        patch("tfc.utils.email.email_helper") as email_helper,
    ):
        send_rule_completion_email(
            MagicMock(),
            {"added": 1},
            triggered_by_user_id=triggered_by_user_id,
            error_message=error_message,
        )
    email_helper.assert_called_once()
    template_data = email_helper.call_args.kwargs["template_data"]
    assert template_data["triggered_by_name"] == expected
    if trigger == "schedule":
        get_user.assert_not_called()
    else:
        get_user.assert_called_once_with(pk=triggered_by_user_id)
