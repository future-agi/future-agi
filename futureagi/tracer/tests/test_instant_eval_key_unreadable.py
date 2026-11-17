"""The instant evaluation's TypeSafe key check before a run starts.

A question on Jev runs on the organization's own TypeSafe key, so starting,
reading more, resuming and retrying are refused when every answer would fail
on that key. A key that is saved but that this deployment can't read is
refused with a plain sentence naming where to enter it again, never with the
key lookup's own text; a missing key keeps its own answer. The key lookup is
a stand-in at its import path, so no key is read.
"""

from types import SimpleNamespace
from unittest.mock import patch

from agentic_eval.core_evals.run_prompt.litellm_models import (
    MissingProviderKeyError,
    missing_provider_key_message,
)
from model_hub.services.key_at_rest import KeyUnreadable
from tfc.utils.general_methods import GeneralMethods
from tracer.views import instant_eval_scale

LOOKUP = "evaluations.engine.instance._get_typesafe_api_key"
SETTINGS_PATH = "/dashboard/settings/ai-providers"
UNREADABLE = (
    "The TypeSafe key saved for this workspace can't be read. Enter that key "
    "again in Settings > AI Providers."
)


def project():
    return SimpleNamespace(organization_id="org-1", workspace_id="workspace-1")


def jev_question():
    """A question whose evaluation runs on Jev."""
    return SimpleNamespace(
        eval_template=SimpleNamespace(config={"model": "gpt-4o"}, model=None),
        model="jev-latest",
        config={},
    )


def key_check(lookup_error):
    """The check's answer when the key lookup raises `lookup_error`, and the
    lookup stand-in."""
    with patch(LOOKUP, side_effect=lookup_error) as lookup:
        answer = instant_eval_scale._jev_key_missing(
            GeneralMethods(), project(), [jev_question()]
        )
    return answer, lookup


def test_an_unreadable_typesafe_key_is_refused_with_a_plain_sentence():
    refused, lookup = key_check(KeyUnreadable("typesafe key"))
    missing, _lookup = key_check(MissingProviderKeyError("typesafe"))

    assert refused is not None, "the run must not start"
    assert refused.status_code == missing.status_code == 400
    assert refused.data["code"] == "JEV_KEY_UNREADABLE"
    assert refused.data["message"] == UNREADABLE
    assert refused.data["result"] == {
        "message": UNREADABLE,
        "error_code": "JEV_KEY_UNREADABLE",
        "settings_path": SETTINGS_PATH,
    }
    assert "INTEGRATION_ENCRYPTION_KEY" not in str(refused.data)
    assert "decrypt" not in str(refused.data)
    lookup.assert_called_once_with("org-1", "workspace-1")


def test_a_missing_typesafe_key_keeps_its_own_answer():
    refused, lookup = key_check(MissingProviderKeyError("typesafe"))

    assert refused is not None, "the run must not start"
    assert refused.status_code == 400
    assert refused.data["code"] == "JEV_KEY_MISSING"
    assert refused.data["result"] == {
        "message": missing_provider_key_message("typesafe"),
        "error_code": "JEV_KEY_MISSING",
        "settings_path": SETTINGS_PATH,
    }
    lookup.assert_called_once_with("org-1", "workspace-1")


def test_a_saved_readable_key_lets_the_run_start():
    with patch(LOOKUP, return_value="org-typesafe-key") as lookup:
        answer = instant_eval_scale._jev_key_missing(
            GeneralMethods(), project(), [jev_question()]
        )

    assert answer is None
    lookup.assert_called_once_with("org-1", "workspace-1")
