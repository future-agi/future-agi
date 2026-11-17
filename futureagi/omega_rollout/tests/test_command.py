"""The operator CLI: every change names an active staff operator and a reason, and is audited."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from omega_rollout import products as p
from omega_rollout import service
from omega_rollout.models import RolloutAuditEvent

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("omega_rollout", *args, stdout=out)
    return json.loads(out.getvalue())


def test_a_change_needs_a_staff_actor(tenants):
    for actor in (None, tenants.a.email, "nobody@example.invalid"):
        args = ["stage", p.CONSOLE, "S1", "--reason", "r"] + (["--actor", actor] if actor else [])
        with pytest.raises(CommandError):
            run(*args)
    assert RolloutAuditEvent.objects.count() == 0


def test_allow_all_and_kill_restore_through_the_cli(tenants):
    op = tenants.operator.email
    org = str(tenants.a.organization.pk)
    run("allow", "all", "--org", org, "--cohort", "staff", "--actor", op, "--reason", "staff org")
    claims = service.claim_set(org, tenants.a.workspace.pk)
    assert claims.products == dict.fromkeys(p.HOSTED_PRODUCTS, "active")
    status = run("kill", p.SANDBOXES, "--actor", op, "--reason", "drill")
    assert status["products"][p.SANDBOXES]["paused"] is True
    assert service.claim_set(org, tenants.a.workspace.pk).products[p.SANDBOXES] == "paused"
    run("restore", p.SANDBOXES, "--actor", op, "--reason", "done")
    assert RolloutAuditEvent.objects.filter(actor=tenants.operator.user).count() == 7
    with pytest.raises(CommandError, match="not_paused"):
        run("restore", p.SANDBOXES, "--actor", op, "--reason", "again")
