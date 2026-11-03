"""Choice statistics for dataset eval columns.

``SQLQueryHandler.get_cells_choices_analysis`` counts each of an eval
template's choices across the eval's result cells. Choices are text the
template author wrote, so the query must receive them as a parameter.
"""

import pytest

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.utils.SQL_queries import SQLQueryHandler


@pytest.mark.django_db
@pytest.mark.parametrize("scoped", [False, True], ids=["dataset", "metric-and-rows"])
def test_choice_stats_count_choices_containing_an_apostrophe(user, workspace, scoped):
    template = EvalTemplate.no_workspace_objects.create(
        name="choice-stats-apostrophe",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "choices"},
        choices=["Yes", "Don't know"],
    )
    dataset = Dataset.objects.create(
        name="choice-stats-apostrophe",
        organization=user.organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        workspace=workspace,
    )
    metric = UserEvalMetric.objects.create(
        name="choice-stats-metric",
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}},
        user=user,
    )
    column = Column.objects.create(
        name="choice-stats-metric",
        data_type=DataTypeChoices.ARRAY.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        dataset=dataset,
    )
    row_ids = []
    for order, value in enumerate(['["Don\'t know"]', '["Yes"]', '["Yes"]']):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, row=row, column=column, value=value)
        row_ids.append(str(row.id))

    rows = SQLQueryHandler.get_cells_choices_analysis(
        dataset_id=str(dataset.id),
        eval_template_id=str(template.id),
        choices=template.choices,
        user_eval_metric_ids=[str(metric.id)] if scoped else None,
        row_ids=row_ids if scoped else None,
    )

    counts = {row[4]: (row[6], float(row[7])) for row in rows}
    assert counts == {"Yes": (2, 66.67), "Don't know": (1, 33.33)}


@pytest.mark.django_db
def test_choice_stats_keep_non_text_choices_as_their_text(user, workspace):
    # The query text used to hold each choice as '{choice}', so a numeric or
    # boolean choice was compared as its str(). Binding must keep that.
    template = EvalTemplate.no_workspace_objects.create(
        name="choice-stats-mixed",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "choices"},
        choices=["Yes", 1, True],
    )
    dataset = Dataset.objects.create(
        name="choice-stats-mixed",
        organization=user.organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        workspace=workspace,
    )
    metric = UserEvalMetric.objects.create(
        name="choice-stats-mixed-metric",
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}},
        user=user,
    )
    column = Column.objects.create(
        name="choice-stats-mixed-metric",
        data_type=DataTypeChoices.ARRAY.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        dataset=dataset,
    )
    for order, value in enumerate(['["Yes"]', '["1"]', '["True"]', '["Yes"]']):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, row=row, column=column, value=value)

    rows = SQLQueryHandler.get_cells_choices_analysis(
        dataset_id=str(dataset.id),
        eval_template_id=str(template.id),
        choices=template.choices,
    )

    counts = {row[4]: row[6] for row in rows}
    assert counts == {"Yes": 2, "1": 1, "True": 1}
