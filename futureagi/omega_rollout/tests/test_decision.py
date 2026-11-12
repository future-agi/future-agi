"""The pure rollout decision (READINESS 3.13): cohorts by stage, deny precedence, the S3 percentage
band (RC-06), monotonic raises, anonymous access, and the same answer in every worker process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from omega_rollout import products as p
from omega_rollout.decision import (
    AccessEntry,
    ProductState,
    bucket,
    decide,
    products_claim,
    under_percentage,
)

ORG_A = "0b8f8e9e-0000-4000-8000-00000000000a"
ORG_B = "0b8f8e9e-0000-4000-8000-00000000000b"
WS_A = "1b8f8e9e-0000-4000-8000-00000000000a"
WS_A2 = "1b8f8e9e-0000-4000-8000-0000000000a2"


def allow(product, org, cohort=p.COHORT_PARTNER, workspace=None):
    return AccessEntry(product, org, workspace, p.EFFECT_ALLOW, cohort)


def deny(product, org, workspace=None):
    return AccessEntry(product, org, workspace, p.EFFECT_DENY, p.COHORT_PARTNER)


def seeded_orgs(n=1000):
    return [str(uuid.UUID(int=index * 7919 + 13)) for index in range(n)]


class TestStagesAndCohorts:
    @pytest.mark.parametrize("product", p.HOSTED_PRODUCTS)
    def test_no_row_is_dark_for_everyone(self, product):
        assert not decide(ProductState(product), ORG_A, WS_A, []).admitted
        assert not decide(ProductState(product), None, None, []).admitted

    @pytest.mark.parametrize(
        "cohort,first",
        [(p.COHORT_STAFF, p.S0), (p.COHORT_PARTNER, p.S1), (p.COHORT_WAITLIST, p.S2)],
    )
    def test_each_cohort_enters_at_its_stage_and_stays(self, cohort, first):
        entries = [allow(p.CONSOLE, ORG_A, cohort)]
        for stage in p.STAGES:
            admitted = decide(
                ProductState(p.CONSOLE, stage=stage), ORG_A, WS_A, entries
            ).admitted
            assert admitted is (p.STAGE_RANK[stage] >= p.STAGE_RANK[first]), (
                cohort,
                stage,
            )

    def test_moving_back_a_step_removes_the_later_cohort(self):
        entries = [allow(p.HUB, ORG_A, p.COHORT_WAITLIST)]
        assert decide(ProductState(p.HUB, stage=p.S2), ORG_A, WS_A, entries).admitted
        assert not decide(
            ProductState(p.HUB, stage=p.S1), ORG_A, WS_A, entries
        ).admitted

    def test_entries_for_other_products_and_orgs_do_not_count(self):
        entries = [allow(p.SANDBOXES, ORG_A), allow(p.CONSOLE, ORG_B)]
        assert not decide(
            ProductState(p.CONSOLE, stage=p.S1), ORG_A, WS_A, entries
        ).admitted


class TestRefusalReasons:
    """Each refusal says why (RC-03: every refused attempt is logged with its reason)."""

    def test_an_org_on_no_list_is_not_listed_at_its_stage(self):
        for stage in (p.S0, p.S1, p.S2):
            refused = decide(ProductState(p.HUB, stage=stage), ORG_B, None, [])
            assert not refused.admitted and refused.reason == f"not_listed:{stage}"
        # A cohort that has not entered yet is not on this stage's list either.
        waitlisted = [allow(p.HUB, ORG_B, p.COHORT_WAITLIST)]
        refused = decide(ProductState(p.HUB, stage=p.S1), ORG_B, None, waitlisted)
        assert refused.reason == "not_listed:S1"

    def test_outside_the_percentage_and_anonymous(self):
        state = ProductState(p.HUB, stage=p.S3, percentage=0)
        assert decide(state, ORG_B, None, []).reason == "outside_percentage"
        assert decide(state, None, None, []).reason == "anonymous"


class TestDeny:
    def test_workspace_deny_refuses_only_that_workspace(self):
        entries = [allow(p.CONSOLE, ORG_A), deny(p.CONSOLE, ORG_A, WS_A2)]
        state = ProductState(p.CONSOLE, stage=p.S1)
        assert decide(state, ORG_A, WS_A, entries).admitted
        refused = decide(state, ORG_A, WS_A2, entries)
        assert not refused.admitted and refused.reason == "denied:workspace"

    def test_org_deny_beats_allow_and_percentage(self):
        entries = [allow(p.CONSOLE, ORG_A, p.COHORT_STAFF), deny(p.CONSOLE, ORG_A)]
        state = ProductState(p.CONSOLE, stage=p.S3, percentage=100)
        refused = decide(state, ORG_A, WS_A, entries)
        assert not refused.admitted and refused.reason == "denied:organization"

    def test_workspace_allow_admits_only_that_workspace(self):
        entries = [allow(p.DEPLOYMENTS, ORG_A, workspace=WS_A)]
        state = ProductState(p.DEPLOYMENTS, stage=p.S1)
        assert decide(state, ORG_A, WS_A, entries).admitted
        assert not decide(state, ORG_A, WS_A2, entries).admitted


class TestKillState:
    def test_paused_keeps_the_org_admitted_with_state_paused(self):
        state = ProductState(p.SANDBOXES, stage=p.S1, paused=True)
        admission = decide(state, ORG_A, WS_A, [allow(p.SANDBOXES, ORG_A)])
        assert (
            admission.admitted
            and admission.paused
            and admission.state == p.STATE_PAUSED
        )

    def test_paused_never_admits_an_org_that_was_not_admitted(self):
        state = ProductState(p.SANDBOXES, stage=p.S1, paused=True)
        assert not decide(state, ORG_B, None, [allow(p.SANDBOXES, ORG_A)]).admitted


class TestPercentage:
    def test_ten_percent_admits_between_eight_and_twelve_percent_of_1000_orgs(self):
        state = ProductState(p.CONSOLE, stage=p.S3, percentage=10)
        admitted = sum(decide(state, org, None, []).admitted for org in seeded_orgs())
        assert 80 <= admitted <= 120, admitted

    @pytest.mark.parametrize("product", p.HOSTED_PRODUCTS)
    def test_raising_ten_to_twentyfive_keeps_every_admitted_org(self, product):
        orgs = seeded_orgs()
        at_10 = {org for org in orgs if under_percentage(product, org, 10)}
        at_25 = {org for org in orgs if under_percentage(product, org, 25)}
        assert at_10 <= at_25
        assert 200 <= len(at_25) <= 300

    def test_percentage_counts_only_at_s3(self):
        org = next(o for o in seeded_orgs() if under_percentage(p.HUB, o, 50))
        assert not decide(
            ProductState(p.HUB, stage=p.S2, percentage=50), org, None, []
        ).admitted
        assert decide(
            ProductState(p.HUB, stage=p.S3, percentage=50), org, None, []
        ).admitted

    def test_zero_and_hundred_are_exact(self):
        assert not any(under_percentage(p.HUB, org, 0) for org in seeded_orgs(200))
        assert all(under_percentage(p.HUB, org, 100) for org in seeded_orgs(200))


class TestAnonymous:
    def test_anonymous_is_admitted_only_at_s3_and_100(self):
        assert not decide(
            ProductState(p.HUB, stage=p.S3, percentage=99), None, None, []
        ).admitted
        assert not decide(
            ProductState(p.HUB, stage=p.S2, percentage=100), None, None, []
        ).admitted
        assert decide(
            ProductState(p.HUB, stage=p.S3, percentage=100), None, None, []
        ).admitted


def test_products_claim_names_only_admitted_products_with_their_state():
    states = {
        p.CONSOLE: ProductState(p.CONSOLE, stage=p.S1),
        p.SANDBOXES: ProductState(p.SANDBOXES, stage=p.S1, paused=True),
        p.HUB: ProductState(p.HUB, stage=p.S0),
    }
    entries = [allow(p.CONSOLE, ORG_A), allow(p.SANDBOXES, ORG_A), allow(p.HUB, ORG_A)]
    assert products_claim(states, ORG_A, WS_A, entries) == {
        p.CONSOLE: p.STATE_ACTIVE,
        p.SANDBOXES: p.STATE_PAUSED,
    }


def test_two_worker_processes_with_different_hash_seeds_give_identical_decisions():
    """RC-06's worker leg: the bucket is SHA-256, so PYTHONHASHSEED (which changes `hash()` per
    process) cannot change a decision."""
    orgs = seeded_orgs()
    script = (
        "import json,sys; from omega_rollout.decision import bucket; "
        "orgs=json.load(sys.stdin); print(json.dumps([bucket('console', o) for o in orgs]))"
    )
    root = Path(__file__).resolve().parents[2]
    outputs = []
    for seed in ("1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(root)}
        done = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(orgs),
            capture_output=True,
            text=True,
            env=env,
            cwd=root,
            check=True,
        )
        outputs.append(json.loads(done.stdout))
    assert outputs[0] == outputs[1] == [bucket("console", org) for org in orgs]
