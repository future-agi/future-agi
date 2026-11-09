"""A change applies whole or not at all, and a key never names stale products under a current epoch
(READINESS 3.13, RC-04, RC-05, RC-10).

Regressions for the A2 verifier's findings:
- A3: the key's epoch was read after its products, so a change committing between the reads gave a
  key with the new epoch and the old products; a long response's re-check could then never tell
  that the rollout changed after the key was minted.
- A5: a PATCH with a stage and a percentage committed the stage even when the percentage was refused.
- An invitation from the waitlist silently turned an explicit org-level deny into an allow.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from accounts.models.audit_log import AuditLog
from omega_rollout import ops, service
from omega_rollout import products as p
from omega_rollout.models import (
    ProductAccess,
    ProductRollout,
    RolloutAuditEvent,
    WaitlistRequest,
)
from omega_rollout.store import current_epoch, load_states
from omega_rollout.tests.conftest import client_for

pytestmark = pytest.mark.django_db


class TestTheEpochIsNeverNewerThanTheProducts:
    def test_a3_a_change_between_the_reads_leaves_the_key_with_an_older_epoch(
        self, admit_all
    ):
        """The change commits after the epoch is read and before the products are: the key names the
        new state under the old epoch, which a re-check reads as "changed since minted"."""
        org, ws = admit_all.a.organization.pk, admit_all.a.workspace.pk

        def kill_then_load():
            ops.kill(p.CONSOLE, actor=admit_all.operator.user, reason="drill")
            return load_states()

        with patch("omega_rollout.service.load_states", side_effect=kill_then_load):
            claims = service.claim_set(org, ws)
        assert claims.products[p.CONSOLE] == "paused"
        assert claims.epoch < current_epoch()

    def test_a3_the_verifiers_interleaving_now_gives_a_consistent_key(self, admit_all):
        """The verifier's exact patch: the change commits inside the epoch read. The products are
        read after it, so the key's epoch and products agree."""
        org, ws = admit_all.a.organization.pk, admit_all.a.workspace.pk

        def kill_then_epoch():
            ops.kill(p.CONSOLE, actor=admit_all.operator.user, reason="drill")
            return current_epoch()

        with patch("omega_rollout.service.current_epoch", side_effect=kill_then_epoch):
            claims = service.claim_set(org, ws)
        assert claims.epoch == current_epoch()
        assert claims.products == service.claim_set(org, ws).products
        assert claims.products[p.CONSOLE] == "paused"


class TestAChangeAppliesWholeOrNotAtAll:
    def test_a5_a_refused_percentage_does_not_leave_the_stage_changed(self, tenants):
        epoch = current_epoch()
        response = client_for(tenants.operator).patch(
            reverse("omega_rollout:admin-product", kwargs={"product": p.CONSOLE}),
            {"stage": "S2", "percentage": 10, "reason": "go"},
            format="json",
        )
        assert response.status_code == 409, response.content
        assert response.json()["code"] == "stage_not_s3"
        assert service.product_state(p.CONSOLE).stage == p.S0
        assert not RolloutAuditEvent.objects.exists()
        assert not AuditLog.objects.filter(action__startswith="omega_rollout.").exists()
        assert current_epoch() == epoch

    def test_a_stage_and_a_percentage_together_apply_together(self, tenants):
        response = client_for(tenants.operator).patch(
            reverse("omega_rollout:admin-product", kwargs={"product": p.CONSOLE}),
            {"stage": "S3", "percentage": 10, "reason": "GA step one"},
            format="json",
        )
        assert response.status_code == 200, response.content
        row = ProductRollout.objects.get(product=p.CONSOLE)
        assert (row.stage, row.percentage) == (p.S3, 10)
        assert list(
            RolloutAuditEvent.objects.order_by("created_at").values_list(
                "action", flat=True
            )
        ) == ["stage_changed", "percentage_changed"]

    def test_the_cli_all_changes_every_product_or_none(self, tenants):
        org = str(tenants.a.organization.pk)
        real = ops.set_access
        calls = []

        def third_one_refused(product, *args, **kwargs):
            calls.append(product)
            if len(calls) == 3:
                raise ops.RolloutChangeRefused("simulated", "refused for this product")
            return real(product, *args, **kwargs)

        with patch("omega_rollout.ops.set_access", side_effect=third_one_refused):
            with pytest.raises(CommandError, match="simulated"):
                call_command(
                    "omega_rollout",
                    "allow",
                    "all",
                    "--org",
                    org,
                    "--cohort",
                    "staff",
                    "--actor",
                    tenants.operator.email,
                    "--reason",
                    "staff org",
                    stdout=StringIO(),
                )
        assert len(calls) == 3
        assert not ProductAccess.objects.exists()
        assert not RolloutAuditEvent.objects.exists()


class TestAnInvitationNeverLiftsADeny:
    def test_inviting_a_denied_org_is_refused_until_the_deny_is_removed(self, tenants):
        operator = client_for(tenants.operator)
        org_b = tenants.b.organization
        ops.set_stage(p.HUB, p.S2, actor=tenants.operator.user, reason="waitlist")
        asked = client_for(tenants.b).post(
            reverse("omega_rollout:early-access"), {"product": p.HUB}, format="json"
        )
        assert asked.status_code == 200, asked.content
        ops.set_access(
            p.HUB,
            org_b,
            effect=p.EFFECT_DENY,
            actor=tenants.operator.user,
            reason="abuse report",
        )
        request = WaitlistRequest.objects.get(product=p.HUB, organization=org_b)
        decision = reverse(
            "omega_rollout:admin-waitlist-decision", kwargs={"request_id": request.pk}
        )
        refused = operator.post(
            decision, {"invite": True, "reason": "batch 1"}, format="json"
        )
        assert refused.status_code == 409, refused.content
        assert refused.json()["code"] == "organization_denied"
        deny = ProductAccess.objects.get(product=p.HUB, organization=org_b)
        assert deny.effect == p.EFFECT_DENY
        assert not service.admission(p.HUB, org_b.pk, tenants.b.workspace.pk).admitted
        assert not RolloutAuditEvent.objects.filter(action="invited").exists()
        # The operator lifts the deny as its own audited change; then the invitation admits B.
        ops.remove_access(
            p.HUB, org_b, actor=tenants.operator.user, reason="report closed"
        )
        invited = operator.post(
            decision, {"invite": True, "reason": "batch 1"}, format="json"
        )
        assert invited.status_code == 200, invited.content
        assert service.admission(p.HUB, org_b.pk, tenants.b.workspace.pk).admitted
        assert list(
            RolloutAuditEvent.objects.filter(organization_id=org_b.pk)
            .order_by("created_at")
            .values_list("action", flat=True)
        ) == ["access_requested", "deny_set", "access_removed", "invited"]
