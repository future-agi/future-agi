"""Catalog key presence follows execution's provider/workspace resolution."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from evaluations.decision_models import model_groups
from model_hub.selectors.decision_models import get_decision_catalog

pytestmark = pytest.mark.requires_ee


def test_catalog_uses_checked_workspace_and_never_serializes_keys():
    with patch("model_hub.selectors.decision_models.LiteLLMModelManager") as cls:
        manager = cls.return_value
        manager._find_api_key_entry.side_effect = lambda org, ws, provider: (
            SimpleNamespace(actual_key="synthetic-private-key")
            if (org, ws, provider) == ("org", "workspace-a", "openrouter")
            else None
        )
        result = get_decision_catalog(organization_id="org", workspace_id="workspace-a")
    assert len(manager._find_api_key_entry.call_args_list) == 4
    assert "synthetic-private-key" not in str(result)
    assert {
        c["provider"]
        for group in result
        for c in group["connections"]
        if c["is_available"]
    } == {"openrouter"}


def test_catalog_search_includes_author_model_and_connection():
    with patch("model_hub.selectors.decision_models.LiteLLMModelManager") as cls:
        cls.return_value._find_api_key_entry.return_value = None
        result = get_decision_catalog(
            organization_id="org", workspace_id="workspace-a", search="OpenRouter"
        )
        clef = get_decision_catalog(
            organization_id="org", workspace_id="workspace-a", search="clef"
        )
    assert len(result) == len(model_groups())
    assert {group["label"] for group in clef} == {"Clef", "Clef Flash", "Clef Omni"}


def _catalog_with_keys(*providers):
    with patch("model_hub.selectors.decision_models.LiteLLMModelManager") as cls:
        cls.return_value._find_api_key_entry.side_effect = lambda org, ws, provider: (
            SimpleNamespace(actual_key="synthetic-private-key")
            if provider in providers
            else None
        )
        return get_decision_catalog(organization_id="org", workspace_id="workspace-a")


def test_catalog_without_keys_keeps_curated_order():
    # A new workspace sees the established families first, not vendor A-Z.
    labels = [group["label"] for group in _catalog_with_keys()]
    assert labels == [group["label"] for group in model_groups()]
    assert labels[:4] == ["Jev 1.14", "Jev 1.13", "Jev latest", "GPT-6 Luna Decisions"]


def _runnable(group):
    return any(connection["is_available"] for connection in group["connections"])


def test_catalog_puts_runnable_groups_first_in_curated_order():
    result = _catalog_with_keys("perplexity")
    ready = [group["label"] for group in result if _runnable(group)]
    rest = [group["label"] for group in result if not _runnable(group)]
    curated = [group["label"] for group in model_groups()]
    assert [group["label"] for group in result] == ready + rest
    assert ready == [label for label in curated if label in ready]
    assert rest == [label for label in curated if label in rest]
    assert ready[:2] == ["Decider 1.2", "Decider Flash 1.2"]


def test_catalog_missing_checked_organization_does_not_resolve_keys():
    with patch("model_hub.selectors.decision_models.LiteLLMModelManager") as cls:
        assert get_decision_catalog(organization_id=None, workspace_id=None) == []
    cls.assert_not_called()


def test_catalog_without_workspace_uses_execution_default_resolution():
    with patch("model_hub.selectors.decision_models.LiteLLMModelManager") as cls:
        manager = cls.return_value
        manager._resolve_default_workspace_id.return_value = "default-workspace"
        manager._find_api_key_entry.return_value = None
        get_decision_catalog(organization_id="org", workspace_id=None)
    manager._resolve_default_workspace_id.assert_called_once_with("org")
    assert {call.args[1] for call in manager._find_api_key_entry.call_args_list} == {
        "default-workspace"
    }
