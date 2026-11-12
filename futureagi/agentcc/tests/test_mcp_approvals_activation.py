"""Default activation and scheduler contracts; no live dispatch in these probes."""

import importlib.util

import pytest
from django.db import models
from django.urls import Resolver404, resolve

from agentcc.mcp_features import capabilities
from agentcc.models import AgentccMCPApproval


@pytest.mark.parametrize(
    "path,name",
    [
        ("/agentcc/mcp/approvals/", "mcp-approval-list"),
        ("/agentcc/mcp/approvals/synthetic-sealed/", "mcp-approval-detail"),
        ("/agentcc/mcp/approvals/synthetic-sealed/decision/", "mcp-approval-decision"),
    ],
)
def test_real_approval_routes_are_installed(path, name):
    try:
        match = resolve(path)
    except Resolver404:
        match = None
    assert match is not None, "approval route is absent"
    assert match.url_name == name


def test_sealed_id_is_not_truncated_to_64_characters():
    assert isinstance(
        AgentccMCPApproval._meta.get_field("approval_id"), models.TextField
    )
    assert AgentccMCPApproval._meta.get_field("approval_digest").unique


def test_approvals_are_advertised_without_a_new_false_default_gate():
    assert capabilities().get("approvals") is True


def test_reconciler_is_registered_and_scheduled_within_retention():
    assert importlib.util.find_spec("agentcc.approval_tasks") is not None
    import agentcc.tasks  # noqa: F401
    from tfc.celery import celery_app

    entry = celery_app.conf.beat_schedule.get("reconcile_mcp_approvals")
    assert entry is not None, "reconciliation is not scheduled"
    assert entry["task"] == "agentcc.reconcile_mcp_approvals"
    assert 0 < entry["schedule"] <= 15
    assert entry["options"]["expires"] <= 15
    assert entry["task"] in celery_app.tasks
