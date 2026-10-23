"""Precision planning and explicit per-cell targets preserve classification."""

import math
from unittest.mock import patch

import pytest

from tracer.services.eval_tasks.sampling import stats
from tracer.services.eval_tasks.sampling.audit import audit_report
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    cell_rule,
    policy_version,
    validate_policy,
)
from tracer.tests.test_eval_sampling_policy import NOW, PROJECT, policy


def test_empty_cell_quotas_do_not_change_historical_policy_versions():
    original = validate_policy(policy(), now=NOW)
    normalized = validate_policy({**policy(), "cell_quotas": {}}, now=NOW)
    assert normalized == original
    kwargs = {"project_id": PROJECT, "row_type": "spans", "filters": {}}
    assert policy_version(normalized, **kwargs) == policy_version(original, **kwargs)


def test_cell_quota_serializer_round_trip_and_override_semantics():
    from tracer.serializers.eval_sampling import SamplingPolicySerializer

    identity = '["default",[]]'
    value = {**policy(), "cell_quotas": {identity: 7}}
    serializer = SamplingPolicySerializer(data=value)
    assert serializer.is_valid(), serializer.errors
    validated = validate_policy(serializer.validated_data, now=NOW)
    assert validated["cell_quotas"] == {identity: 7}
    assert cell_rule(validated, identity, {"rate": 0, "max_n": 1}) == {"target_n": 7}
    assert cell_rule(validated, '["else",[]]', {"rate": 5}) == {"rate": 5}
    report = audit_report(
        validated,
        [
            {
                "stratum": identity,
                "population": 5,
                "achieved": 5,
                "probability": 1,
                "population_check": "match",
            }
        ],
    )
    row = report["strata"][0]
    assert (row["target"], row["expected_size"], row["achieved"], row["shortage"]) == (
        7,
        5,
        5,
        2,
    )


@pytest.mark.parametrize(
    "quotas",
    [
        [],
        None,
        {"bad": 1},
        {'["unknown",[]]': 1},
        {'["default",[]]': -1},
        {'["default",[]]': True},
        {'["default",[]]': 1.5},
        {'["default",[]]': 1000001},
        {'["default",["[true,1]"]]': 1},
    ],
)
def test_invalid_cell_quotas_fail_closed(quotas):
    with pytest.raises(InvalidSamplingPolicy):
        validate_policy({**policy(), "cell_quotas": quotas}, now=NOW)


def test_live_cell_quotas_are_rejected():
    with pytest.raises(InvalidSamplingPolicy, match="closed"):
        validate_policy(
            {
                **policy(
                    mode="live",
                    window={"start": "2026-01-01T00:00:00Z", "end": None},
                    default={"rate": 10},
                ),
                "cell_quotas": {'["default",[]]': 2},
            },
            now=NOW,
        )


def test_required_sample_size_tiny_margin_and_integer_boundaries():
    assert stats.required_sample_size(1e-200, 100) == 100
    for n in (2, 50, 499):
        margin = stats.margin_of_error(n, 500)
        assert stats.required_sample_size(margin, 500) == n
        assert stats.required_sample_size(margin * (1 - 1e-12), 500) == n + 1


def test_extreme_binomial_mismatch_has_logarithmic_boundary_search():
    from scipy.stats import binomtest

    original = stats._log_binomial_pmf
    for selected, population, probability in (
        (0, 1_000_000, 0.999),
        (1_000_000, 1_000_000, 0.001),
    ):
        with patch.object(stats, "_log_binomial_pmf", wraps=original) as calls:
            result = stats.binomial_two_sided_p(selected, population, probability)
        assert result == binomtest(selected, population, probability).pvalue == 0
        assert calls.call_count < 30


def test_live_audit_flags_inconsistent_persisted_probabilities():
    result = audit_report(
        {
            "mode": "live",
            "sampling_unit": "session",
            "strata": [],
            "group_by": [],
            "default": {"rate": 10},
        },
        [
            {
                "stratum": '["default",[]]',
                "population": 100,
                "achieved": 10,
                "probability": 0.1,
                "population_check": "changed",
            }
        ],
    )
    assert result["summary"]["integrity"] == "attention"
    assert result["summary"]["flagged_strata"] == 1
    assert math.isclose(result["strata"][0]["srm_p_value"], 1)
