"""TH-5475: invited users must reach the same signup destinations as owners.

First successful invite acceptance of a new user reports to HubSpot, the
existing Slack signup feed, and Mixpanel. It must not reuse owner onboarding
(generated-password email, demo dataset). Reporting is idempotent per user.

These tests execute the real function bodies from accounts/utils.py. The
accounts package import graph needs the full Django app, which this checkout
cannot boot without its private dependency set, so the functions are loaded
from source into a stub namespace. That is a unit seam, not an integration run.
"""

import ast
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

UTILS = Path(__file__).resolve().parents[1] / "utils.py"


def _function_source(name):
    tree = ast.parse(UTILS.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(UTILS.read_text(), node)


def _load_reporting():
    tree = ast.parse(UTILS.read_text())
    names = {
        "INVITE_SIGNUP_REPORTED_KEY",
        "schedule_invite_acceptance_reporting",
        "report_invite_acceptance",
    }
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) in names for t in node.targets)
        )
        or (isinstance(node, ast.FunctionDef) and node.name in names)
    ]
    if len(nodes) != 3:
        raise AssertionError("invite reporting symbols missing from accounts/utils.py")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    analytics = types.ModuleType("analytics.utils")
    analytics.MixpanelEvents = types.SimpleNamespace(
        SIGNUP=types.SimpleNamespace(value="Signup_details_submitted")
    )
    analytics.get_mixpanel_properties = MagicMock(return_value={"email": "x"})
    analytics.track_mixpanel_event = MagicMock()
    sys.modules["analytics"] = types.ModuleType("analytics")
    sys.modules["analytics.utils"] = analytics
    ns = {
        "os": os,
        "logger": MagicMock(),
        "User": MagicMock(),
        "send_hubspot_notification": MagicMock(return_value=(True, None)),
        "send_slack_notification": MagicMock(),
    }
    exec(compile(module, str(UTILS), "exec"), ns)
    ns["analytics"] = analytics
    return ns


def _temporal_modules(start):
    temporal = types.ModuleType("tfc.temporal.drop_in")
    temporal.start_activity = start
    common = types.ModuleType("temporalio.common")
    common.WorkflowIDConflictPolicy = types.SimpleNamespace(USE_EXISTING="USE_EXISTING")
    return {
        "tfc.temporal.background_tasks.activities": types.ModuleType(
            "tfc.temporal.background_tasks.activities"
        ),
        "tfc.temporal.drop_in": temporal,
        "temporalio": types.ModuleType("temporalio"),
        "temporalio.common": common,
    }


@pytest.fixture
def invitee():
    user = MagicMock()
    user.id = "invitee-1"
    user.email = "invitee@example.invalid"
    user.name = "Invited Person"
    user.organization_role = "Member"
    user.config = {}
    user.save = MagicMock()
    return user


class TestInviteAcceptanceReportingDispatch:
    def test_first_acceptance_schedules_dedicated_reporting(self, invitee, monkeypatch):
        ns = _load_reporting()
        start = MagicMock()
        monkeypatch.setattr(sys, "modules", {**sys.modules, **_temporal_modules(start)})
        ns["schedule_invite_acceptance_reporting"](invitee)

        start.assert_called_once()
        args, kwargs = start.call_args
        assert args[0] == "run_invite_acceptance_reporting_activity"
        assert kwargs["args"] == (str(invitee.id),)
        assert kwargs["task_id"] == f"invite-signup-report-{invitee.id}"
        assert kwargs["id_conflict_policy"] == "USE_EXISTING"

    def test_repeat_acceptance_uses_same_task_id(self, invitee, monkeypatch):
        ns = _load_reporting()
        start = MagicMock()
        monkeypatch.setattr(sys, "modules", {**sys.modules, **_temporal_modules(start)})
        ns["schedule_invite_acceptance_reporting"](invitee)
        ns["schedule_invite_acceptance_reporting"](invitee)

        assert start.call_count == 2
        assert (
            start.call_args_list[0].kwargs["task_id"]
            == start.call_args_list[1].kwargs["task_id"]
            == f"invite-signup-report-{invitee.id}"
        )


class TestInviteAcceptanceReportingActivity:
    def test_reports_hubspot_slack_and_mixpanel_without_owner_onboarding(
        self, invitee, monkeypatch
    ):
        ns = _load_reporting()
        ns["User"].objects.select_related.return_value.get.return_value = invitee
        monkeypatch.setenv("ENV_TYPE", "staging")
        ns["report_invite_acceptance"](str(invitee.id))

        ns["send_hubspot_notification"].assert_called_once_with(invitee)
        ns["send_slack_notification"].assert_called_once()
        event, props = ns["analytics"].track_mixpanel_event.call_args.args
        assert event == "Signup_details_submitted"
        assert props["signup_origin"] == "invite_acceptance"
        assert invitee.config["invite_signup_reported"] is True
        source = _function_source("report_invite_acceptance")
        assert "send_signup_email" not in source
        assert "upload_demo_dataset" not in source
        assert "create_demo_traces_and_spans" not in source

    def test_second_run_does_not_report_again(self, invitee):
        ns = _load_reporting()
        invitee.config = {"invite_signup_reported": True}
        ns["User"].objects.select_related.return_value.get.return_value = invitee

        ns["report_invite_acceptance"](str(invitee.id))

        ns["send_hubspot_notification"].assert_not_called()
        ns["send_slack_notification"].assert_not_called()
        ns["analytics"].track_mixpanel_event.assert_not_called()
        invitee.save.assert_not_called()
