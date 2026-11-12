"""OBS-SW-22: annotation aggregates retain fractional scores on both backends."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(params=["pg", "ch"])
def annotation_map_builder(request, monkeypatch):
    from tracer.views import trace as trace_view

    def build(label_type, contributions):
        value_key = "rating" if label_type == "star" else "value"
        users = {
            uid: {"id": uid, "name": uid, "email": ""}
            for uid, _ in contributions
            if uid is not None
        }
        if request.param == "pg":
            scores = [
                SimpleNamespace(
                    trace_id="trace-1",
                    observation_span_id=None,
                    label_id="label-1",
                    annotator_id=uid,
                    annotator=SimpleNamespace(**users[uid]) if uid else None,
                    value={value_key: value},
                )
                for uid, value in contributions
            ]
            query = MagicMock()
            query.select_related.return_value = scores
            manager = SimpleNamespace(filter=MagicMock(return_value=query))
            monkeypatch.setattr(trace_view.Score, "no_workspace_objects", manager)
            builder = trace_view._build_annotation_map_from_scores_pg
        else:
            from accounts.models.user import User

            rows = [
                {
                    "trace_id": "trace-1",
                    "label_id": "label-1",
                    "annotator_id": uid,
                    "value": json.dumps({value_key: value}),
                }
                for uid, value in contributions
            ]
            analytics = MagicMock()
            analytics.execute_ch_query.return_value = SimpleNamespace(data=rows)
            monkeypatch.setattr(
                trace_view,
                "AnalyticsQueryService",
                MagicMock(return_value=analytics),
            )
            user_query = MagicMock()
            user_query.values.return_value = list(users.values())
            monkeypatch.setattr(
                User.objects, "filter", MagicMock(return_value=user_query)
            )
            builder = trace_view._build_annotation_map_from_scores_ch

        return builder(["trace-1"], ["label-1"], {"label-1": label_type})

    return build


@pytest.mark.parametrize(
    ("label_type", "contributions", "expected_score", "expected_annotators"),
    [
        pytest.param(
            "numeric",
            [("user-a", 0.25)],
            0.25,
            {"user-a": 0.25},
            id="single-fraction",
        ),
        pytest.param(
            "numeric",
            [("user-a", 0.25), ("user-a", 0.75), ("user-b", 2.75)],
            1.25,
            {"user-a": 0.5, "user-b": 2.75},
            id="unequal-annotator-contributions",
        ),
        pytest.param(
            "numeric",
            [("user-a", -0.25), ("user-b", -0.75)],
            -0.5,
            {"user-a": -0.25, "user-b": -0.75},
            id="negative-fractions",
        ),
        pytest.param(
            "numeric",
            [("user-a", 1), ("user-b", 2)],
            1.5,
            {"user-a": 1.0, "user-b": 2.0},
            id="integer-input-fractional-mean",
        ),
        pytest.param(
            "star",
            [("user-a", 2), ("user-b", 3)],
            2.5,
            {"user-a": 2.0, "user-b": 3.0},
            id="star-fractional-mean",
        ),
        pytest.param(
            "numeric",
            [("user-a", 0), ("user-a", None), ("user-a", "bad"), ("user-b", "0.5")],
            0.25,
            {"user-a": 0.0, "user-b": 0.5},
            id="zero-and-invalid-values",
        ),
        pytest.param(
            "numeric",
            [(None, 0.25), (None, 0.75)],
            0.5,
            {},
            id="anonymous-fractions",
        ),
    ],
)
def test_annotation_score_preserves_fractional_mean(
    annotation_map_builder,
    label_type,
    contributions,
    expected_score,
    expected_annotators,
):
    result = annotation_map_builder(label_type, contributions)

    # Assert the actual API-shaped output, including per-annotator means and
    # absence of internal accumulators. Unequal row counts must stay row-weighted.
    assert result == {
        "trace-1": {
            "label-1": {
                "score": pytest.approx(expected_score),
                "annotators": {
                    uid: {
                        "user_id": uid,
                        "user_name": uid,
                        "score": pytest.approx(score),
                    }
                    for uid, score in expected_annotators.items()
                },
            }
        }
    }


@pytest.mark.parametrize("label_type", ["numeric", "star"])
def test_annotation_score_preserves_zero(annotation_map_builder, label_type):
    result = annotation_map_builder(label_type, [(None, 0)])

    assert result == {"trace-1": {"label-1": {"score": 0.0, "annotators": {}}}}


def test_annotation_score_skips_all_invalid_values(annotation_map_builder):
    result = annotation_map_builder("numeric", [(None, None), (None, "bad")])

    assert result == {"trace-1": {}}


def test_annotation_score_empty_rows(annotation_map_builder):
    assert annotation_map_builder("numeric", []) == {}
