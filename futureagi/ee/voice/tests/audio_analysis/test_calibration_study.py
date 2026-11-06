"""Calibration-study arithmetic: monotone fit, gates and grouped statistics."""

import numpy as np
import pytest

from ee.voice.calibration import stats
from ee.voice.calibration import vqi_study as study


def test_pava_is_monotone_and_pools_violations():
    ux, fitted = stats.pava([1, 2, 3, 4], [1.0, 3.0, 2.0, 4.0])
    assert list(ux) == [1, 2, 3, 4]
    assert list(fitted) == [1.0, 2.5, 2.5, 4.0]


def test_pava_pools_tied_x():
    ux, fitted = stats.pava([1, 1, 2], [1.0, 3.0, 2.5])
    assert list(ux) == [1, 2]
    assert list(fitted) == [2.0, 2.5]


def test_monotone_knots_cover_calibration_range_and_mos_anchors():
    rng = np.random.default_rng(0)
    x = rng.uniform(1.5, 4.0, 300)
    y = np.clip(x + rng.normal(0, 0.4, 300), 0.5, 5.5)
    kx, ky = stats.monotone_knots(x, y, 6)
    assert kx[0] == pytest.approx(x.min()) and kx[-1] == pytest.approx(x.max())
    assert all(b > a for a, b in zip(kx, kx[1:], strict=False))
    assert all(b >= a for a, b in zip(ky, ky[1:], strict=False))
    assert 1.0 <= min(ky) <= max(ky) <= 5.0
    g = stats.pchip(kx, ky)
    assert np.isnan(g(x.max() + 0.01))  # no extrapolation outside support


def test_spearman_and_ties():
    assert stats.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert list(stats.rankdata([3, 1, 3])) == [2.5, 1.0, 2.5]


def test_group_bootstrap_resamples_whole_groups_deterministically():
    pred = [1, 2, 3, 4, 5, 6]
    mos = [1, 2, 3, 4, 5, 6]
    groups = ["a", "a", "b", "b", "c", "c"]
    lb1 = stats.group_bootstrap_spearman_lb(pred, mos, groups, n_boot=200)
    lb2 = stats.group_bootstrap_spearman_lb(pred, mos, groups, n_boot=200)
    assert lb1 == lb2
    assert lb1[0] == pytest.approx(1.0)


def test_material_pairs_need_delta_and_non_overlapping_ci():
    mos = [1.0, 2.2, 2.4]
    ci = [0.1, 0.1, 0.5]
    assert stats.material_pairs(mos, ci) == [(0, 1), (0, 2)]
    assert stats.material_pairs(mos, [0.1, 0.1, 1.5]) == [(0, 1)]
    assert stats.pair_concordance([1, 2, 2], mos, [(0, 1), (0, 2)]) == 1.0
    assert stats.pair_concordance([2, 2, 1], mos, [(0, 1), (0, 2)]) == 0.0


def test_gates_are_the_prd_values():
    assert study.GATES == {
        "rmse_max": 0.5,
        "spearman_min": 0.70,
        "spearman_lb95_min": 0.60,
        "pair_rank_acc_min": 0.90,
        "cohort_n_heldout_min": 50,
        "cohort_abs_bias_max": 0.25,
        "cohort_rmse_max": 0.65,
    }
    passing = {
        "rmse_mos": 0.4,
        "spearman": 0.8,
        "spearman_lb95_group_bootstrap": 0.7,
        "pair_rank_acc": 0.95,
        "n": 60,
        "bias_mos": 0.1,
    }
    assert study._gates(passing)[1] is True
    for key, bad in (
        ("rmse_mos", 0.51),
        ("spearman", 0.69),
        ("spearman_lb95_group_bootstrap", 0.59),
        ("pair_rank_acc", 0.89),
        ("n", 49),
        ("bias_mos", -0.26),
    ):
        assert study._gates({**passing, key: bad})[1] is False


def test_splits_keep_readers_together_and_val_live_locked():
    clips = []
    for reader in range(40):
        for k in range(3):
            clips.append(
                {
                    "subset": "NISQA_TRAIN_LIVE",
                    "group": f"r{reader}",
                    "eligible": True,
                }
            )
    clips += [
        {"subset": "NISQA_VAL_LIVE", "group": "v1", "eligible": True},
        {"subset": "NISQA_TEST_LIVETALK", "group": "f1", "eligible": True},
        {"subset": "NISQA_TRAIN_LIVE", "group": "r0", "eligible": False},
    ]
    study.assign_splits(clips)
    by_reader = {}
    for c in clips:
        if c["eligible"] and c["subset"] == "NISQA_TRAIN_LIVE":
            by_reader.setdefault(c["group"], set()).add(c["split"])
    assert all(len(s) == 1 for s in by_reader.values())
    assert clips[-3]["split"] == "test"
    assert clips[-2]["split"] == "external_test"
    assert clips[-1]["split"] is None
    splits = [next(iter(s)) for s in by_reader.values()]
    assert splits.count("test") == 14  # round(0.35 * 40)
    assert splits.count("calibration") == 17 and splits.count("selection") == 9
