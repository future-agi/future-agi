"""Sampling audit: hand-computed report math and the endpoint contract.

The real-ClickHouse closed-cohort audit (population re-read at the freeze) is
``test_closed_audit_endpoint_reports_frozen_design_weights_and_balance`` in
``test_eval_sampling_clickhouse.py``.
"""

import json
import math
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from tracer.models.eval_sampling import EvalSamplingCohort, EvalSamplingMember
from tracer.models.eval_task import EvalTask, RunType
from tracer.services.eval_tasks.sampling.audit import audit_report, parse_stratum

AUDIT = "/tracer/eval-task/sampling_audit/"


def label(name, *groups):
    """A stratum identity exactly as ClickHouse renders it."""
    return json.dumps(
        [name, [json.dumps(group, separators=(",", ":")) for group in groups]],
        separators=(",", ":"),
    )


def approx(value):
    return pytest.approx(value, rel=1e-12)


CLOSED_POLICY = {
    "mode": "closed",
    "sampling_unit": "session",
    "strata": [
        {"name": "vip", "filters": [], "target_n": 2},
        {"name": "rare", "filters": [], "target_n": 5},
        {"name": "excluded", "filters": [], "rate": 0},
    ],
    "group_by": [],
    "default": {"filters": [], "rate": 10, "min_n": 3},
}


def test_closed_audit_report_matches_hand_computation():
    cells = [
        {"stratum": label("vip"), "population": 5, "achieved": 2, "probability": 0.4},
        {
            "stratum": label("default"),
            "population": 40,
            "achieved": 4,
            "probability": 0.1,
        },
        {"stratum": label("rare"), "population": 3, "achieved": 3, "probability": 1.0},
        {
            "stratum": label("excluded"),
            "population": 10,
            "achieved": 0,
            "probability": None,
        },
    ]
    for cell in cells:
        cell["population_check"] = "match"
    report = audit_report(CLOSED_POLICY, cells)
    rows = {row["rule"]: row for row in report["strata"]}
    # vip: target_n 2 of 5 -> k = 2, p = 2/5, weight 2.5.
    assert rows["vip"] | {"stratum": None} == {
        "stratum": None,
        "rule": "vip",
        "population_size": 5,
        "achieved": 2,
        "population_check": "match",
        "target": 2,
        "expected_size": 2,
        "shortage": 0,
        "exact": True,
        "srm_z": None,
        "srm_p_value": None,
        "srm_flagged": None,
        "inclusion_probability": 0.4,
        "design_weight": 2.5,
    }
    # default: ceil(10% of 40) = 4, floor 3 does not bind -> k = 4, weight 10.
    assert (rows["default"]["target"], rows["default"]["expected_size"]) == (4, 4)
    assert rows["default"]["design_weight"] == 10
    # rare: asks for 5 but has 3 -> census, shortage 2, weight 1.
    assert rows["rare"]["target"] == 5
    assert rows["rare"]["expected_size"] == 3
    assert rows["rare"]["shortage"] == 2
    assert rows["rare"]["exact"] is True
    # excluded: rate 0 -> k = 0, no weight, unrepresented.
    assert rows["excluded"]["expected_size"] == 0
    assert rows["excluded"]["inclusion_probability"] == 0.0
    assert rows["excluded"]["design_weight"] is None
    summary = report["summary"]
    # Weights (2.5 x 2, 10 x 4, 1 x 3): sum w = 48, sum w^2 = 415.5.
    # n_eff = 48^2 / 415.5 = 1536/277; deff = 9 / n_eff = 831/512.
    assert summary["kish_effective_n"] == approx(1536 / 277)
    assert summary["design_effect"] == approx(831 / 512)
    assert summary["weight_cv"] == approx(math.sqrt(831 / 512 - 1))
    assert summary["weighted_population"] == approx(48)  # = 5 + 40 + 3 exactly
    assert summary["represented_population"] == 48
    assert summary["population_size"] == 58
    assert summary["achieved"] == 9
    assert summary["unrepresented_strata"] == 1
    assert summary["unrepresented_population"] == 10
    assert (summary["flagged_strata"], summary["integrity"]) == (0, "ok")
    (slice_balance,) = report["balance"]
    assert slice_balance["attribute"] == "slice"
    shares = {row["value"]: row for row in slice_balance["categories"]}
    assert [row["value"] for row in slice_balance["categories"]] == [
        "default",
        "excluded",
        "vip",
        "rare",
    ]
    # default: P = 40/58, s = 4/9, weighted 40/48.
    assert shares["default"]["population_share"] == approx(40 / 58)
    assert shares["default"]["sample_share"] == approx(4 / 9)
    assert shares["default"]["weighted_share"] == approx(40 / 48)
    assert shares["default"]["standardized_difference"] == approx(-0.5107756060592783)
    assert shares["vip"]["weighted_standardized_difference"] == approx(
        0.06122607083107215
    )
    # The unrepresented stratum cannot be weighted back: imbalance flagged.
    assert shares["excluded"]["weighted_standardized_difference"] == approx(
        -0.6454972243679029
    )
    assert slice_balance["weighted_imbalance"] is True
    # X^2 = 271/15 on 3 df; p = chi2.sf(271/15, 3).
    assert slice_balance["chi_square"] == approx(271 / 15)
    assert slice_balance["degrees_of_freedom"] == 3
    assert slice_balance["p_value"] == pytest.approx(0.0004261412755808961, rel=1e-9)


def test_closed_audit_flags_inexact_or_changed_strata():
    cells = [
        {"stratum": label("vip"), "population": 5, "achieved": 1, "probability": 0.2},
        {
            "stratum": label("default"),
            "population": 40,
            "achieved": 4,
            "probability": 0.1,
        },
    ]
    cells[0]["population_check"] = "match"
    cells[1]["population_check"] = "changed"
    summary = audit_report(CLOSED_POLICY, cells)["summary"]
    assert (summary["flagged_strata"], summary["integrity"]) == (2, "attention")


def test_live_audit_report_flags_sample_ratio_mismatch():
    policy = {
        "mode": "live",
        "sampling_unit": "user",
        "strata": [{"name": "vip", "filters": [], "rate": 50}],
        "group_by": [{"source": "span", "key": "locale"}],
        "default": {"filters": [], "rate": 10},
    }
    cells = [
        {
            "stratum": label("vip", [True, "en"]),
            "population": 40,
            "achieved": 20,
            "probability": 0.5,
            "population_check": "persisted",
        },
        {
            "stratum": label("default", [False, ""]),
            "population": 2000,
            "achieved": 140,
            "probability": 0.1,
            "population_check": "persisted",
        },
    ]
    report = audit_report(policy, cells)
    rows = {row["rule"]: row for row in report["strata"]}
    # vip: 20 of 40 at 50% is the mode: p-value 1.
    assert rows["vip"]["srm_p_value"] == 1.0
    assert rows["vip"]["srm_flagged"] is False
    assert rows["vip"]["expected_size"] == 20
    # default: 140 of 2000 at 10% (expected 200): z = -60 / sqrt(180).
    assert rows["default"]["srm_z"] == approx(-60 / math.sqrt(180))
    assert rows["default"]["srm_p_value"] == pytest.approx(
        3.072820199579908e-06, rel=1e-7
    )  # scipy.stats.binomtest(140, 2000, 0.1)
    assert rows["default"]["srm_flagged"] is True
    assert rows["default"]["target"] is None and rows["default"]["shortage"] is None
    summary = report["summary"]
    # Weights (2 x 20, 10 x 140): n_eff = 1440^2 / 14080 = 1620/11; deff = 88/81.
    assert summary["kish_effective_n"] == approx(1620 / 11)
    assert summary["design_effect"] == approx(88 / 81)
    assert summary["weighted_population"] == approx(1440)  # under-selection shows
    assert (summary["flagged_strata"], summary["integrity"]) == (1, "attention")
    slice_balance, locale = report["balance"]
    assert locale["attribute"] == "locale" and locale["source"] == "span"
    assert {(row["value"], row["present"]) for row in locale["categories"]} == {
        ("en", True),
        (None, False),
    }


def test_parse_stratum_reads_clickhouse_identities():
    assert parse_stratum('["vip",["[true,\\"en\\"]","[false,\\"\\"]"]]') == (
        "vip",
        [(True, "en"), (False, None)],
    )
    assert parse_stratum('["default",[]]') == ("default", [])
    assert parse_stratum("not-json") == ("not-json", [])


def _live_task(project, config):
    now = timezone.now()
    task = EvalTask.objects.create(
        project=project,
        name="live audit",
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "live",
            "window": {"start": (now - timedelta(days=1)).isoformat(), "end": None},
            "strata": [
                {
                    "name": "vip",
                    "filters": [{"key": "tier", "op": "in", "values": ["gold"]}],
                    "rate": 50,
                }
            ],
            "default": {"rate": 10},
        },
    )
    task.evals.add(config)
    return task


@pytest.mark.django_db
def test_live_audit_endpoint_reads_persisted_decisions_only(
    auth_client, project, custom_eval_config
):
    from tracer.services.eval_tasks.sampling.service import task_policy, unit_key

    task = _live_task(project, custom_eval_config)
    policy, version = task_policy(task, now=timezone.now())
    cohort = EvalSamplingCohort.objects.create(
        task=task, policy_version=version, policy=policy, frozen_at=timezone.now()
    )
    members = []
    for stratum, count, chosen, rate in (
        (label("vip"), 40, 20, 0.5),
        (label("default"), 2000, 140, 0.1),
    ):
        for index in range(count):
            unit = f"session:{stratum}:{index}"
            members.append(
                EvalSamplingMember(
                    cohort=cohort,
                    unit_key=unit_key(unit),
                    unit_id=unit,
                    stratum=stratum,
                    selected=index < chosen,
                    inclusion_probability=rate,
                    population_size=None,
                )
            )
    EvalSamplingMember.objects.bulk_create(members, batch_size=1000)
    with patch(
        "tracer.services.eval_tasks.sampling.query.stream_query",
        side_effect=AssertionError("live audits never read ClickHouse"),
    ):
        response = auth_client.get(AUDIT, {"eval_task_id": str(task.id)})
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["cohort_id"] == str(cohort.id)
    assert result["mode"] == "live"
    assert result["population_source"] == "persisted_decisions"
    assert result["coordination"] == "independent"
    rows = {row["rule"]: row for row in result["strata"]}
    assert (rows["vip"]["population_size"], rows["vip"]["achieved"]) == (40, 20)
    assert rows["default"]["srm_flagged"] is True
    assert result["summary"]["integrity"] == "attention"
    assert result["summary"]["design_effect"] == approx(88 / 81)


@pytest.mark.django_db
def test_audit_endpoint_statuses(auth_client, project, custom_eval_config, user):
    from tracer.services.eval_tasks.sampling.service import task_policy
    from tracer.tests.test_eval_task import make_other_workspace_eval_task

    response = auth_client.get(AUDIT, {"eval_task_id": str(uuid.uuid4())})
    assert response.status_code == 404
    foreign = make_other_workspace_eval_task(project, user, custom_eval_config)
    assert auth_client.get(AUDIT, {"eval_task_id": str(foreign.id)}).status_code == 404
    legacy = EvalTask.objects.create(
        project=project, name="legacy", run_type=RunType.CONTINUOUS, sampling_rate=10
    )
    response = auth_client.get(AUDIT, {"eval_task_id": str(legacy.id)})
    assert response.status_code == 400
    assert "no sampling policy" in response.json()["result"].lower()
    task = _live_task(project, custom_eval_config)
    response = auth_client.get(AUDIT, {"eval_task_id": str(task.id)})
    assert response.status_code == 404
    assert "No sampling cohort" in response.json()["result"]
    assert auth_client.get(AUDIT, {"eval_task_id": "not-a-uuid"}).status_code == 400
    now = timezone.now()
    closed = EvalTask.objects.create(
        project=project,
        name="closed",
        row_type="spans",
        run_type=RunType.CONTINUOUS,
        sampling_policy={
            "version": 1,
            "sampling_unit": "session",
            "mode": "closed",
            "window": {
                "start": (now - timedelta(days=1)).isoformat(),
                "end": (now - timedelta(minutes=1)).isoformat(),
            },
            "default": {"target_n": 1},
        },
    )
    policy, version = task_policy(closed, now=now)
    freezing = EvalSamplingCohort.objects.create(
        task=closed, policy_version=version, policy=policy, frozen_at=now
    )
    with patch(
        "tracer.services.eval_tasks.sampling.query.stream_query",
        side_effect=AssertionError("an uncertified cohort is never audited"),
    ):
        response = auth_client.get(AUDIT, {"eval_task_id": str(closed.id)})
        assert response.status_code == 409
        other = auth_client.get(
            AUDIT,
            {"eval_task_id": str(closed.id), "cohort_id": str(uuid.uuid4())},
        )
        assert other.status_code == 404
    assert freezing.complete is False


@pytest.mark.django_db
def test_archived_closed_audit_cannot_bypass_admission_with_a_live_task(
    auth_client, project, custom_eval_config
):
    from tracer.services.eval_tasks.sampling.preview_guard import PreviewThrottled

    task = _live_task(project, custom_eval_config)
    with (
        patch(
            "tracer.views.eval_sampling.preview_slot",
            side_effect=PreviewThrottled("Too many previews", retry_after=10),
        ),
        patch(
            "tracer.views.eval_sampling.sampling_audit",
            side_effect=AssertionError("an unadmitted audit must not read"),
        ),
    ):
        response = auth_client.get(
            AUDIT, {"eval_task_id": str(task.id), "cohort_id": str(uuid.uuid4())}
        )
    assert response.status_code == 429
    assert response["Retry-After"] == "10"


def test_anonymous_audit_denied(api_client):
    response = api_client.get(AUDIT, {"eval_task_id": str(uuid.uuid4())})
    assert response.status_code in (401, 403)
