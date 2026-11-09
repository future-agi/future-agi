"""RC-03: each refusal "answers 404 and logs its reason" (READINESS 3.13). The A2 verifier found that
only the Hub's view check logged; the key batch, observe-target and the checker refused silently.

Every Django wall writes exactly one `omega_rollout_refused` line per refusal, with the product, the
stable code and the reason, and never a credential: ids, the method and the path only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from omega_hub.tests.helpers import CHECKER_TOKEN, checker_client
from omega_rollout import enforce, ops
from omega_rollout import products as p
from omega_rollout.store import RolloutUnavailable
from omega_rollout.tests.conftest import admit, client_for

pytestmark = pytest.mark.django_db
FIELDS = {
    "product",
    "code",
    "reason",
    "method",
    "path",
    "organization_id",
    "workspace_id",
}


class Recorder:
    def __init__(self):
        self.lines = []

    def info(self, event, **fields):
        self.lines.append(("info", event, fields))

    def warning(self, event, **fields):
        self.lines.append(("warning", event, fields))

    def refusals(self):
        return [
            (level, fields)
            for level, event, fields in self.lines
            if event == "omega_rollout_refused"
        ]


@pytest.fixture
def log(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(enforce, "logger", recorder)
    return recorder


@pytest.fixture(autouse=True)
def hub_settings(settings, tmp_path):
    settings.OMEGA_HUB_STORAGE = "filesystem"
    settings.OMEGA_HUB_STORAGE_DIR = str(tmp_path / "hub-store")
    settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
    settings.OMEGA_HUB_PUBLIC_API_URL = "http://testserver/omega-hub/v1"


def only_line(log):
    lines = log.refusals()
    assert len(lines) == 1, lines
    level, fields = lines[0]
    assert set(fields) == FIELDS
    return level, fields


def test_the_key_batch_logs_a_workspace_admitted_to_nothing(tenants, log):
    admit(tenants, p.CONSOLE)
    response = client_for(tenants.b).post(
        "/accounts/omega/session-exchange/", {"audience": "omega-squad"}, format="json"
    )
    assert response.status_code == 404
    level, fields = only_line(log)
    assert level == "info"
    assert fields["product"] == p.PLATFORM
    assert fields["code"] == p.REASON_NOT_ADMITTED
    assert fields["reason"] == "key_batch"
    assert fields["organization_id"] == str(tenants.b.organization.pk)
    assert fields["workspace_id"] == str(tenants.b.workspace.pk)


def test_observe_target_logs_a_key_of_a_workspace_admitted_to_nothing(tenants, log):
    admit(tenants, p.CONSOLE)
    key = OrgApiKey.no_workspace_objects.create(
        organization=tenants.b.organization,
        workspace=tenants.b.workspace,
        type="user",
        user=tenants.b.user,
    )
    client = APIClient()
    response = client.get(
        "/accounts/omega/observe-target/?project_id=00000000-0000-4000-8000-000000000000",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )
    assert response.status_code == 404
    _, fields = only_line(log)
    assert (fields["product"], fields["code"], fields["reason"]) == (
        p.PLATFORM,
        p.REASON_NOT_ADMITTED,
        "observe_target",
    )
    assert key.api_key not in str(log.lines) and key.secret_key not in str(log.lines)


def test_a_hub_view_logs_not_admitted_and_paused_with_the_route(tenants, log):
    admit(tenants, p.HUB)
    entries = reverse("omega_hub:entries")
    assert client_for(tenants.b).get(entries).status_code == 404
    _, fields = only_line(log)
    assert (fields["product"], fields["code"]) == (p.HUB, p.REASON_NOT_ADMITTED)
    assert (fields["method"], fields["path"]) == ("GET", entries)
    log.lines.clear()
    ops.kill(p.HUB, actor=tenants.operator.user, reason="drill")
    submissions = reverse("omega_hub:submissions")
    assert client_for(tenants.a).post(submissions, {}, format="json").status_code == 503
    _, fields = only_line(log)
    assert (fields["code"], fields["reason"], fields["method"]) == (
        p.REASON_PAUSED,
        "new_work",
        "POST",
    )


def test_paused_installs_and_the_checker_log_their_refusals(tenants, log):
    admit(tenants, p.HUB)
    ops.kill(p.HUB, actor=tenants.operator.user, reason="drill", pause_installs=True)
    bundle = reverse(
        "omega_hub:version-bundle",
        kwargs={"publisher": "x", "name": "y", "version": "1.0.0"},
    )
    assert client_for(tenants.a).get(bundle).status_code == 503
    _, fields = only_line(log)
    assert (fields["code"], fields["reason"]) == (p.REASON_PAUSED, "installs_paused")
    log.lines.clear()
    claim = checker_client().post(reverse("omega_hub:checker-claim"), {}, format="json")
    assert claim.status_code == 503
    _, fields = only_line(log)
    assert (fields["code"], fields["reason"]) == (p.REASON_PAUSED, "checker")
    assert CHECKER_TOKEN not in str(log.lines)


def test_an_unreadable_store_logs_a_warning_at_every_wall(tenants, log):
    admit(tenants, p.HUB)
    broken = RolloutUnavailable("rollout state unreadable")
    with patch("omega_rollout.service.load_states", side_effect=broken):
        assert (
            client_for(tenants.a).get(reverse("omega_hub:entries")).status_code == 503
        )
        assert (
            client_for(tenants.a)
            .post(
                "/accounts/omega/session-exchange/",
                {"audience": "omega-squad"},
                format="json",
            )
            .status_code
            == 503
        )
    lines = log.refusals()
    assert [level for level, _ in lines] == ["warning", "warning"]
    assert [(f["product"], f["code"]) for _, f in lines] == [
        (p.HUB, p.REASON_UNAVAILABLE),
        (p.PLATFORM, p.REASON_UNAVAILABLE),
    ]


def test_an_admitted_request_logs_nothing(tenants, log):
    admit(tenants, p.HUB)
    assert client_for(tenants.a).get(reverse("omega_hub:entries")).status_code == 200
    assert log.refusals() == []
