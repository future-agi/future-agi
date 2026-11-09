"""Historical choices reach the evaluator as well as result conversion."""

from types import SimpleNamespace

from evaluations.engine.instance import apply_version_overrides


def test_saved_choice_definition_overrides_the_live_evaluator_config():
    version = SimpleNamespace(
        prompt_messages=[],
        model="",
        criteria="",
        config_snapshot={
            "output": "choices",
            "choices": ["saved"],
            "choice_scores": {"saved": 0.2},
            "multi_choice": True,
        },
        choice_scores={"saved": 0.2},
        multi_choice=True,
        pass_threshold=0.1,
    )
    config, _ = apply_version_overrides(
        {
            "output_type": "choices",
            "choices": ["new"],
            "choice_scores": {"new": 0.9},
            "multi_choice": False,
        },
        version,
        saved_choices=True,
    )
    assert config["choices"] == ["saved"]
    assert config["choice_scores"] == {"saved": 0.2}
    assert config["multi_choice"] is True


def test_empty_historical_choice_scores_do_not_inherit_current_scores():
    version = SimpleNamespace(
        prompt_messages=[],
        model="",
        criteria="",
        config_snapshot={"output": "score", "choice_scores": None},
        choice_scores=None,
        multi_choice=False,
        pass_threshold=0.5,
    )
    config, _ = apply_version_overrides(
        {"output_type": "choices", "choice_scores": {"new": 0.9}},
        version,
        saved_choices=True,
    )
    assert config["output_type"] == "score"
    assert config["choice_scores"] is None
    assert config["choices"] == []


def test_a_resolved_default_version_keeps_the_live_choice_definition():
    # An unpinned run resolves the template default only for its prompt; the
    # live choices, edited in place after the version was saved, still score.
    version = SimpleNamespace(
        prompt_messages=[],
        model="",
        criteria="",
        config_snapshot={
            "output": "score",
            "choices": ["saved"],
            "choice_scores": {"saved": 0.2},
            "multi_choice": True,
        },
        choice_scores={"saved": 0.2},
        multi_choice=True,
        pass_threshold=0.1,
    )
    live = {
        "output_type": "choices",
        "choices": ["new"],
        "choice_scores": {"new": 0.9},
        "multi_choice": False,
    }
    config, _ = apply_version_overrides(dict(live), version)
    assert config == live
