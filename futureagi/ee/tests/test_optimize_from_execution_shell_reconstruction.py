"""Shell EvalTemplate reconstruction inside optimize_from_execution.

Confirms that when the dataset-optimization serializer emits the columnar
scoring fields on each eval_data dict, the reconstructed EvalTemplate shell
carries them, and that when the fields are absent (legacy dicts) the shell
falls back to ``None`` without raising.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _eval_data(
    *,
    template_id="00000000-0000-0000-0000-000000000001",
    template_name="test_eval",
    output_type_normalized=None,
    choice_scores=None,
    pass_threshold=None,
    include_scoring_fields=True,
    template_config=None,
    model="gpt-4o",
    mapping=None,
):
    d = {
        "eval_template_id": template_id,
        "eval_template_name": template_name,
        "description": "",
        "criteria": "",
        "template_config": template_config or {
            "output": "choices",
            "required_keys": ["output"],
            "eval_type_id": "AgentEvaluator",
        },
        "config": {"config": {}},
        "mapping": mapping or {"output": "output"},
        "model": model,
        "eval_config_id": "00000000-0000-0000-0000-000000000009",
        "eval_name": template_name,
    }
    if include_scoring_fields:
        d["output_type_normalized"] = output_type_normalized
        d["choice_scores"] = choice_scores
        d["pass_threshold"] = pass_threshold
    return d


def _execution_data(*eval_datas, initial_prompt="hello {{return_reason}}"):
    return {
        "agent_definition_prompt": {"description": initial_prompt, "inbound": True},
        "call_executions": [
            {
                "call_execution_id": "00000000-0000-0000-0000-0000000000aa",
                "input": {},
                "transcripts": [],
                "scenario_data": {"row_data": {}},
                "evaluations": list(eval_datas),
            }
        ],
    }


def _reconstruct_eval_configs(execution_data):
    """Drive optimize_from_execution just far enough to collect the
    reconstructed eval_configs, without launching an actual optimization run.
    """
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent

    captured = {}

    def _fake_optimize(**kwargs):
        captured["eval_configs"] = kwargs.get("eval_configs")
        return SimpleNamespace(
            best_prompt=kwargs.get("initial_agent_prompt", ""),
            iterations=[],
        )

    fya = FixYourAgent()
    with patch.object(fya, "optimize", side_effect=_fake_optimize):
        fya.optimize_from_execution(execution_data=execution_data)
    return captured["eval_configs"]


@pytest.mark.unit
class TestShellReconstructionCarriesScoringFields:
    """Each eval template type propagates the three columnar scoring fields
    from the eval_data dict into the reconstructed EvalTemplate shell.
    """

    def test_pass_fail_template_shell_carries_output_type_normalized(self):
        data = _eval_data(
            template_name="customer_agent_human_escalation",
            output_type_normalized="pass_fail",
            choice_scores=None,
            pass_threshold=0.5,
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        assert len(cfgs) == 1
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized == "pass_fail"
        assert tpl.choice_scores is None
        assert tpl.pass_threshold == 0.5

    def test_deterministic_with_choice_scores_shell_carries_map(self):
        data = _eval_data(
            template_name="customer_agent_single_choice",
            output_type_normalized="deterministic",
            choice_scores={"Good": 1.0, "Neutral": 0.5, "Bad": 0.0},
            pass_threshold=0.5,
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized == "deterministic"
        assert tpl.choice_scores == {"Good": 1.0, "Neutral": 0.5, "Bad": 0.0}
        assert tpl.pass_threshold == 0.5

    def test_percentage_with_choice_scores_shell_carries_both(self):
        data = _eval_data(
            template_name="customer_agent_score_with_choices",
            output_type_normalized="percentage",
            choice_scores={"Good": 1.0, "Average": 0.7, "Normal": 0.3, "Bad": 0.0},
            pass_threshold=0.5,
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized == "percentage"
        assert tpl.choice_scores == {"Good": 1.0, "Average": 0.7, "Normal": 0.3, "Bad": 0.0}
        assert tpl.pass_threshold == 0.5

    def test_percentage_without_choice_scores_shell_carries_none(self):
        data = _eval_data(
            template_name="customer_agent_context_retention",
            output_type_normalized="percentage",
            choice_scores=None,
            pass_threshold=0.5,
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized == "percentage"
        assert tpl.choice_scores is None
        assert tpl.pass_threshold == 0.5

    def test_deterministic_without_choice_scores_shell_carries_none(self):
        """Documents the template-config gap for tone / politeness_level.

        Shell reconstruction faithfully propagates ``choice_scores = None``;
        the downstream ``score_eval_output`` gate returning ``default_score``
        for these templates is expected and out of scope for this PR.
        """
        data = _eval_data(
            template_name="tone",
            output_type_normalized="deterministic",
            choice_scores=None,
            pass_threshold=0.5,
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized == "deterministic"
        assert tpl.choice_scores is None


@pytest.mark.unit
class TestShellReconstructionBackwardCompat:
    """Legacy execution_data dicts (missing the three scoring keys) still
    reconstruct without raising; the shell columnar fields default to ``None``.
    """

    def test_missing_scoring_fields_reconstructs_with_none(self):
        data = _eval_data(
            template_name="legacy_eval",
            include_scoring_fields=False,
        )
        assert "output_type_normalized" not in data
        assert "choice_scores" not in data
        assert "pass_threshold" not in data

        cfgs = _reconstruct_eval_configs(_execution_data(data))
        tpl = cfgs[0].eval_template
        assert tpl.output_type_normalized is None
        assert tpl.choice_scores is None
        assert tpl.pass_threshold is None

    def test_reconstructed_config_carries_eval_config_id_and_name(self):
        data = _eval_data(
            template_name="customer_agent_multi_choices",
            output_type_normalized="deterministic",
            choice_scores={"Polite": 1.0, "Toxic": 0.0},
        )
        cfgs = _reconstruct_eval_configs(_execution_data(data))
        cfg = cfgs[0]
        assert cfg.id == "00000000-0000-0000-0000-000000000009"
        assert cfg.name == "customer_agent_multi_choices"


@pytest.mark.unit
class TestShellReconstructionMultipleTemplates:
    """Each of the seven eval template types the optimizer supports
    round-trips cleanly through the shell reconstruction in a single run.
    """

    def test_seven_eval_types_reconstruct_independently(self):
        datas = [
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000001",
                template_name="customer_agent_single_choice",
                output_type_normalized="deterministic",
                choice_scores={"Good": 1.0, "Neutral": 0.5, "Bad": 0.0},
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000002",
                template_name="customer_agent_multi_choices",
                output_type_normalized="deterministic",
                choice_scores={"Polite": 1.0, "Toxic": 0.0},
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000003",
                template_name="customer_agent_score_with_choices",
                output_type_normalized="percentage",
                choice_scores={"Good": 1.0, "Bad": 0.0},
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000004",
                template_name="customer_agent_human_escalation",
                output_type_normalized="pass_fail",
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000005",
                template_name="customer_agent_context_retention",
                output_type_normalized="percentage",
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000006",
                template_name="tone",
                output_type_normalized="deterministic",
            ),
            _eval_data(
                template_id="00000000-0000-0000-0000-000000000007",
                template_name="politeness_level",
                output_type_normalized="deterministic",
            ),
        ]
        cfgs = _reconstruct_eval_configs(_execution_data(*datas))
        assert len(cfgs) == 7
        by_name = {c.eval_template.name: c.eval_template for c in cfgs}
        assert by_name["customer_agent_single_choice"].output_type_normalized == "deterministic"
        assert by_name["customer_agent_single_choice"].choice_scores == {
            "Good": 1.0, "Neutral": 0.5, "Bad": 0.0,
        }
        assert by_name["customer_agent_human_escalation"].output_type_normalized == "pass_fail"
        assert by_name["customer_agent_context_retention"].output_type_normalized == "percentage"
        assert by_name["tone"].output_type_normalized == "deterministic"
        assert by_name["tone"].choice_scores is None
