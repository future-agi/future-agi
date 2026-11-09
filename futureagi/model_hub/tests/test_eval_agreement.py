"""Stored judge/human agreement uses scoped, persisted dataset records only."""

import json

import pytest

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.score import Score
from model_hub.tests.test_dsx_l5_experiment_defaults import Tenant


class AgreementTenant(Tenant):
    def __init__(self, name):
        super().__init__(name)
        self.binding = self.evals[0]
        self.column = Column.no_workspace_objects.create(
            dataset=self.dataset,
            name=self.binding.name,
            source="evaluation",
            source_id=str(self.binding.id),
            data_type="json",
        )
        self.label = AnnotationsLabels.no_workspace_objects.create(
            name=f"review-{name}",
            type="categorical",
            organization=self.org,
            workspace=self.workspace,
            settings={
                "rule_prompt": "",
                "multi_choice": False,
                "options": [{"label": "Pass"}, {"label": "Fail"}],
                "auto_annotate": False,
                "strategy": None,
            },
        )
        self.rows, self.cells, self.scores = [], [], []
        for judge, human in [("Pass", "Pass"), ("Pass", "Fail"), ("Fail", "Fail")]:
            self.add_pair(judge, human)

    def add_pair(self, judge, human):
        row = Row.no_workspace_objects.create(
            dataset=self.dataset, order=len(self.rows)
        )
        cell = Cell.no_workspace_objects.create(
            dataset=self.dataset,
            column=self.column,
            row=row,
            value=json.dumps({"choice": judge, "score": int(judge == "Pass")}),
            status="pass",
        )
        score = Score.no_workspace_objects.create(
            organization=self.org,
            workspace=self.workspace,
            dataset_row=row,
            source_type="dataset_row",
            label=self.label,
            annotator=self.user,
            score_source="human",
            value={"selected": [human]},
        )
        self.rows.append(row)
        self.cells.append(cell)
        self.scores.append(score)
        return row, cell, score

    @property
    def url(self):
        return f"/model-hub/eval-templates/{self.template.id}/agreement/"

    def read(self, **params):
        response = self.client.get(self.url, params)
        assert response.status_code == 200, response.content
        return response.json()["result"]


@pytest.fixture
def worlds(db):
    a, b = AgreementTenant("agreement-a"), AgreementTenant("agreement-b")
    yield a, b
    a.client.stop_workspace_injection()
    b.client.stop_workspace_injection()


def test_known_matrix_and_per_label_statistics_use_stored_choices(worlds):
    a, _ = worlds
    before = list(Score.no_workspace_objects.filter(organization=a.org).values())
    result = a.read()
    assert len(result["sources"]) == 1
    report = result["report"]
    assert report["binding_id"] == str(a.binding.id)
    assert report["dataset_id"] == str(a.dataset.id)
    assert report["label_id"] == str(a.label.id)
    assert report["n"] == 3
    assert report["accuracy"] == pytest.approx(2 / 3)
    assert report["kappa"] == pytest.approx(0.4)
    assert report["labels"] == ["Fail", "Pass"]
    assert report["matrix"] == [[1, 1], [0, 1]]
    by_label = {entry["label"]: entry for entry in report["per_label"]}
    assert by_label["Pass"] == {
        "label": "Pass",
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
        "support": 1,
    }
    assert report["excluded"] == 0
    assert len(report["disagreements"]) == 1
    assert report["disagreements"][0]["row_id"] == str(a.rows[1].id)
    assert (
        list(Score.no_workspace_objects.filter(organization=a.org).values()) == before
    )


def test_empty_report_is_not_perfect_agreement(worlds):
    a, _ = worlds
    Score.no_workspace_objects.filter(organization=a.org).update(deleted=True)
    result = a.read()
    assert result["sources"] == []
    assert result["report"] is None


@pytest.mark.parametrize(
    "kind",
    ["cell-error", "missing", "auto", "label", "row", "dataset", "binding", "column"],
)
def test_deleted_unscored_and_nonhuman_records_are_not_counted(worlds, kind):
    a, _ = worlds
    if kind == "cell-error":
        Cell.no_workspace_objects.filter(id=a.cells[0].id).update(status="error")
    elif kind == "missing":
        Cell.no_workspace_objects.filter(id=a.cells[0].id).update(value=None)
    elif kind == "auto":
        Score.no_workspace_objects.filter(id=a.scores[0].id).update(score_source="auto")
    else:
        obj = {
            "label": a.label,
            "row": a.rows[0],
            "dataset": a.dataset,
            "binding": a.binding,
            "column": a.column,
        }[kind]
        type(obj).no_workspace_objects.filter(id=obj.id).update(deleted=True)
    result = a.read()
    if kind in {"label", "dataset", "binding", "column"}:
        assert result["sources"] == []
    else:
        assert result["report"]["n"] == 2
        assert result["report"]["accuracy"] == 0.5


def test_latest_vote_per_reviewer_and_ties_do_not_inflate_agreement(worlds):
    a, b = worlds
    # The same reviewer can leave a later score through another review context.
    Score.no_workspace_objects.create(
        organization=a.org,
        workspace=a.workspace,
        dataset_row=a.rows[0],
        source_type="dataset_row",
        label=a.label,
        annotator=a.user,
        score_source="human",
        value={"selected": ["Fail"]},
    )
    report = a.read()["report"]
    assert report["n"] == 3
    assert report["accuracy"] == pytest.approx(1 / 3)
    # A second reviewer has the opposite vote: exclude this unresolved row.
    Score.no_workspace_objects.create(
        organization=a.org,
        workspace=a.workspace,
        dataset_row=a.rows[0],
        source_type="dataset_row",
        label=a.label,
        annotator=b.user,
        score_source="human",
        value={"selected": ["Pass"]},
    )
    report = a.read()["report"]
    assert report["n"] == 2
    assert report["excluded"] == 1


def test_foreign_template_source_dataset_and_label_are_not_found(worlds):
    a, b = worlds
    source_b = b.read()["sources"][0]["key"]
    assert a.client.get(b.url).status_code == 404
    for params in [
        {"source": source_b},
        {"dataset_id": str(b.dataset.id)},
        {"label_id": str(b.label.id)},
    ]:
        response = a.client.get(a.url, params)
        assert response.status_code == 404, response.content
        assert b.dataset.name not in str(response.content)


def test_malformed_or_execution_query_is_rejected(worlds):
    a, _ = worlds
    for params in [
        {"dataset_id": "not-a-uuid"},
        {"compare_judge_model": "anything"},
        {"view": "confidence"},
    ]:
        assert a.client.get(a.url, params).status_code == 400


def test_mismatched_relations_cannot_import_foreign_rows(worlds):
    a, b = worlds
    Score.no_workspace_objects.filter(id=a.scores[0].id).update(dataset_row=b.rows[0])
    Cell.no_workspace_objects.filter(id=a.cells[1].id).update(row=b.rows[1])
    report = a.read()["report"]
    assert report["n"] == 1
    assert report["accuracy"] == 1


def test_workspace_isolation_within_the_same_organization(worlds):
    a, b = worlds
    # Corrupt org consistency deliberately: workspace filtering must still refuse.
    type(b.template).no_workspace_objects.filter(id=b.template.id).update(
        organization=a.org
    )
    type(b.label).no_workspace_objects.filter(id=b.label.id).update(organization=a.org)
    assert a.client.get(b.url).status_code == 404
    assert a.client.get(a.url, {"label_id": str(b.label.id)}).status_code == 404


@pytest.mark.parametrize("mapped", [True, False])
def test_production_formatter_values_survive_textfield_storage(worlds, mapped):
    from model_hub.views.eval_runner import EvaluationRunner

    a, _ = worlds
    a.template.choice_scores = {"Pass": 1.0, "Fail": 0.0} if mapped else None
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.eval_template = a.template
    for row, cell, choice in zip(
        a.rows, a.cells, ["Pass", "Pass", "Fail"], strict=True
    ):
        output = runner.format_output({"output": "choices", "data": choice}, row=row)
        # Match _create_cell: the TextField receives the formatter output directly.
        Cell.no_workspace_objects.filter(id=cell.id).update(value=output)
    report = a.read()["report"]
    assert report["n"] == 3
    assert report["accuracy"] == pytest.approx(2 / 3)
