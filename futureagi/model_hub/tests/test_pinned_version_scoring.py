"""A pinned run must be *scored* against the pin, not the live template.

``create_eval_instance`` already builds the evaluator from the pinned version,
so the evaluator is asked for that version's choices. Scoring used to read
``choice_scores`` / ``multi_choice`` straight off the live template row, so
editing a template's labels after a version was pinned made the best possible
answer score 0.0: the evaluator answered "Good" (a V1 label) and the live map
only knew "Excellent" / "Poor".

Both runner modes are covered, because they reach the formatter differently:

* ``format_output=True`` (``run_eval_func``: playground, simulate, composite
  children, tracer) skips ``_initialize_eval_metric`` entirely, so nothing
  populated ``_resolved_version`` on that path at all.
* ``format_output=False`` (the dataset ``EvaluationRunner``) resolves the pin
  from the ``UserEvalMetric`` and formats through the row-aware branch.
"""

import pytest

from evaluations.engine.formatting import format_eval_value
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

V1_CHOICES = ["Good", "Bad"]
V1_SCORES = {"Good": 1.0, "Bad": 0.0}
LIVE_CHOICES = ["Excellent", "Poor"]
LIVE_SCORES = {"Excellent": 1.0, "Poor": 0.0}


def _template(organization, workspace, *, multi_choice=False):
    """A custom eval whose labels have been edited since V1 was pinned."""
    return EvalTemplate.no_workspace_objects.create(
        name=f"pinned-scoring-{'multi' if multi_choice else 'single'}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "LIVE prompt {{input}}",
            "output": "choices",
            "required_keys": ["input"],
            "choices": LIVE_CHOICES,
        },
        criteria="LIVE prompt {{input}}",
        model="turing_large",
        choices=LIVE_CHOICES,
        choice_scores=LIVE_SCORES,
        multi_choice=multi_choice,
        visible_ui=True,
    )


def _v1(template, organization, *, multi_choice=False):
    return EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot={
            **template.config,
            "choices": V1_CHOICES,
            "choice_scores": V1_SCORES,
            "multi_choice": multi_choice,
        },
        criteria="V1 prompt {{input}}",
        model="turing_large",
        organization=organization,
        choice_scores=V1_SCORES,
    )


def _result(data):
    return {"output": "choices", "data": data, "failure": None, "metrics": []}


@pytest.mark.unit
@pytest.mark.django_db
class TestFormatEvalValueUsesThePin:
    def test_single_choice_scores_against_the_pinned_map(
        self, organization, workspace
    ):
        template = _template(organization, workspace)
        version = _v1(template, organization)

        # The evaluator was offered V1's labels, so it answers with one.
        value = format_eval_value(_result("Good"), template, version)

        assert value == {"score": 1.0, "choice": "Good"}

    def test_without_the_pin_the_same_answer_scores_zero(
        self, organization, workspace
    ):
        """The regression this guards: the live map has never heard of "Good"."""
        template = _template(organization, workspace)
        _v1(template, organization)

        assert format_eval_value(_result("Good"), template) == {
            "score": 0.0,
            "choice": "Good",
        }

    def test_multi_choice_scores_against_the_pinned_map(
        self, organization, workspace
    ):
        template = _template(organization, workspace, multi_choice=True)
        version = _v1(template, organization, multi_choice=True)

        value = format_eval_value(_result(["Good", "Bad"]), template, version)

        assert value == {"score": 0.5, "choices": ["Good", "Bad"]}

    def test_falls_back_to_the_template_when_the_version_carries_nothing(
        self, organization, workspace
    ):
        """A pre-snapshot version must not blank out the template's scoring."""
        template = _template(organization, workspace)
        bare = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={},
            criteria="",
            model="",
            organization=organization,
            choice_scores=None,
        )

        assert format_eval_value(_result("Excellent"), template, bare) == {
            "score": 1.0,
            "choice": "Excellent",
        }

    def test_pass_fail_deterministic_reads_multi_choice_from_the_pin(
        self, organization, workspace
    ):
        """`multi_choice` decides list-vs-scalar, so it has to follow the pin."""
        template = EvalTemplate.no_workspace_objects.create(
            name="pinned-scoring-deterministic",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={
                "eval_type_id": "DeterministicEvaluator",
                "output": "Pass/Fail",
                "required_keys": ["input"],
            },
            criteria="",
            model="turing_large",
            multi_choice=False,
            visible_ui=True,
        )
        version = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={**template.config, "multi_choice": True},
            criteria="",
            model="",
            organization=organization,
            choice_scores=None,
        )
        result = {
            "output": "Pass/Fail",
            "data": ["a", "b"],
            "failure": None,
            "metrics": [],
        }

        assert format_eval_value(result, template, version) == ["a", "b"]
        # Live template says single-choice, so it collapses to the first pick.
        assert format_eval_value(result, template) == "a"


@pytest.mark.unit
@pytest.mark.django_db
class TestRunnerResolvesThePinInBothModes:
    """`_active_version()` is what feeds the formatter on both paths."""

    def test_format_output_mode_uses_the_version_handed_to_the_runner(
        self, organization, workspace
    ):
        from model_hub.views.eval_runner import EvaluationRunner

        template = _template(organization, workspace)
        version = _v1(template, organization)

        # format_output=True is the run_eval_func construction: no
        # UserEvalMetric, so run_eval_func assigns the version it resolved.
        runner = EvaluationRunner(
            "CustomPromptEvaluator",
            format_output=True,
            organization_id=organization.id,
        )
        runner.eval_template = template
        runner._resolved_version = version

        assert runner._active_version() is version
        assert runner._pinned_choice_scores() == V1_SCORES
        assert runner._pinned_choices() == V1_CHOICES
        # row=None is the non-dataset branch that delegates to the pure
        # formatter, which is where the score used to be computed live.
        assert runner.format_output(_result("Good")) == {
            "score": 1.0,
            "choice": "Good",
        }

    def test_dataset_mode_falls_back_to_the_binding_pin(
        self, organization, workspace
    ):
        from model_hub.views.eval_runner import EvaluationRunner

        template = _template(organization, workspace)
        version = _v1(template, organization)

        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner.eval_template = template
        runner._resolved_version = None
        runner.user_eval_metric = type(
            "Binding", (), {"pinned_version": version}
        )()

        assert runner._active_version() is version
        assert runner._pinned_choice_scores() == V1_SCORES

    def test_no_pin_anywhere_reads_the_live_template(self, organization, workspace):
        from model_hub.views.eval_runner import EvaluationRunner

        template = _template(organization, workspace)

        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner.eval_template = template
        runner._resolved_version = None
        runner.user_eval_metric = None

        assert runner._active_version() is None
        assert runner._pinned_choice_scores() == LIVE_SCORES
