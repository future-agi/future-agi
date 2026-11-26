"""run-evals-on-spans when a pass overruns.

A scheduled pass abandoned at its time limit keeps working through the spans it
listed while the next pass starts beside it. The next pass is run here from
inside the first one's dispatch, so it lists the spans the first pass has not
reached yet. Only the dispatch to the eval runner is replaced.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

import tracer.utils.span as span_module
from model_hub.models.choices import StatusType
from tracer.models.observation_span import ObservationSpan

pytestmark = pytest.mark.django_db


def _span(project, trace, project_version):
    return ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex[:16],
        project=project,
        project_version=project_version,
        trace=trace,
        name="experiment span",
        observation_type="llm",
        eval_status=StatusType.NOT_STARTED.value,
        start_time=timezone.now() - timedelta(seconds=2),
    )


def _stored(span):
    return ObservationSpan.objects.using("default").get(pk=span.pk)


@pytest.fixture
def tagged_version(project_version):
    project_version.eval_tags = [{"custom_eval_name": "overlap-check"}]
    project_version.save(update_fields=["eval_tags"])
    return project_version


def test_overlapping_pass_dispatches_each_span_once(
    monkeypatch, project, trace, tagged_version
):
    spans = [_span(project, trace, tagged_version) for _ in range(2)]
    dispatched = []

    def delay(span_id, tags):
        dispatched.append(span_id)
        if len(dispatched) == 1:
            # The next scheduled pass starts while this one is still running.
            span_module.run_evals_on_spans._original_func()
        elif len(dispatched) == 2:
            # The later pass's runner finishes before the earlier pass gets
            # to this span.
            ObservationSpan.objects.using("default").filter(pk=span_id).update(
                eval_status=StatusType.COMPLETED.value, tags=["scored"]
            )

    monkeypatch.setattr(
        span_module, "eval_observation_span_runner", SimpleNamespace(delay=delay)
    )

    span_module.run_evals_on_spans._original_func()

    assert Counter(dispatched) == {span.id: 1 for span in spans}
    finished = _stored(next(s for s in spans if s.id == dispatched[1]))
    assert (finished.eval_status, finished.tags) == (
        StatusType.COMPLETED.value,
        ["scored"],
    )


def test_span_without_eval_tags_is_completed_without_dispatch(
    monkeypatch, project, trace, project_version
):
    span = _span(project, trace, project_version)
    dispatched = []
    monkeypatch.setattr(
        span_module,
        "eval_observation_span_runner",
        SimpleNamespace(delay=lambda span_id, tags: dispatched.append(span_id)),
    )

    span_module.run_evals_on_spans._original_func()

    assert dispatched == []
    assert _stored(span).eval_status == StatusType.COMPLETED.value
