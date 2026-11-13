"""Current-dev eval scores and bounded reads coexist without legacy trace tables."""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.observation_span import EvalLogger
from tracer.queries import eval_clustering


@pytest.mark.django_db
def test_session_failure_batch_keeps_scores_and_sql_page_limits(
    project, custom_eval_config, monkeypatch
):
    monkeypatch.setattr(eval_clustering, "_FETCH_PAGE", 2)
    expected = []
    for index in range(7):
        result = EvalLogger.objects.create(
            trace_session_id=uuid.uuid4(),
            custom_eval_config=custom_eval_config,
            target_type="session",
            output_bool=False,
            output_float=0.25,
            eval_explanation=f"session failure {index}",
            eval_task_id=f"session-page-{index}",
        )
        EvalLogger.objects.filter(pk=result.pk).update(
            created_at=timezone.now() - timedelta(minutes=7 - index)
        )
        expected.append(str(result.id))

    with CaptureQueriesContext(connection) as queries:
        batch = eval_clustering.get_unclustered_eval_results(str(project.id), limit=3)

    assert [row.eval_logger_id for row in batch] == expected[:3]
    assert [row.score for row in batch] == [0.25, 0.25, 0.25]
    reads = [query["sql"] for query in queries if "tracer_eval_logger" in query["sql"]]
    assert len(reads) == 2
    assert all("LIMIT 2" in sql for sql in reads)
