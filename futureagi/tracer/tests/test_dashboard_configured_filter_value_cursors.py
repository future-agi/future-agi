from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from model_hub.models.score import Score
from tracer.services.annotation_label_source import AnnotationLabelScoresProjectPG
from tracer.services.clickhouse.list_cursor import ListCursorError
from tracer.services.configured_value_options import configured_value_options
from tracer.views.dashboard import (
    _filter_value_options_for_search,
    _finite_filter_value_cursor_page,
)

FILTER_VALUES_URL = "/tracer/dashboard/filter_values/"


def _request():
    organization_id = uuid4()
    workspace_id = uuid4()
    return SimpleNamespace(
        user=SimpleNamespace(
            pk=uuid4(), organization=SimpleNamespace(pk=organization_id)
        ),
        organization=SimpleNamespace(pk=organization_id),
        workspace=SimpleNamespace(pk=workspace_id),
        auth=None,
    )


def _page(request, *, cursor=None, search="", complete=True):
    return _finite_filter_value_cursor_page(
        request,
        project_ids=["project-a"],
        query={
            "metric_name": "eval-a",
            "metric_type": "eval_metric",
            "source": "traces",
            "project_ids": ["project-a"],
        },
        values=[
            {"value": "Accepted", "label": "Accepted"},
            {"value": "Rejected", "label": "Rejected"},
            {"value": "Needs Review", "label": "Needs Review"},
        ],
        search=search,
        page_size=1,
        cursor_token=cursor,
        query_complete=complete,
    )


@pytest.mark.unit
def test_configured_filter_values_use_signed_exact_ordinal_pages():
    request = _request()

    first = _page(request)
    second = _page(request, cursor=first["next_cursor"])
    third = _page(request, cursor=second["next_cursor"])

    assert [first["values"][0]["value"], second["values"][0]["value"]] == [
        "Accepted",
        "Rejected",
    ]
    assert third["values"] == [{"value": "Needs Review", "label": "Needs Review"}]
    assert first["query_complete"] is True
    assert first["browse_status"] == "continuation"
    assert third["query_status"] == "complete"
    assert third["browse_status"] == "exhausted"
    assert third["next_cursor"] is None


@pytest.mark.unit
def test_configured_filter_value_search_is_unicode_case_insensitive_and_bound_to_cursor():
    request = _request()
    searched = _page(request, search="nEeDs rEvIeW")

    assert searched["values"] == [{"value": "Needs Review", "label": "Needs Review"}]
    assert searched["has_more"] is False

    first = _page(request)
    with pytest.raises(ListCursorError, match="does not match"):
        _page(request, cursor=first["next_cursor"], search="accepted")


@pytest.mark.unit
def test_configured_json_values_are_preserved_deduped_and_searchable():
    choices = [
        {"value": False, "label": "Disabled"},
        {"value": 0, "label": "Zero code"},
        {"value": None, "label": "Null fallback"},
        {"value": "", "label": "Empty fallback"},
        "Plain string",
        {"value": False, "label": "Duplicate disabled"},
        {"value": 0, "label": "Duplicate zero"},
    ]
    values = list(configured_value_options(choices))

    assert values == [
        {"value": False, "label": "Disabled"},
        {"value": 0, "label": "Zero code"},
        {"value": "Null fallback", "label": "Null fallback"},
        {"value": "Empty fallback", "label": "Empty fallback"},
        {"value": "Plain string", "label": "Plain string"},
    ]
    assert _filter_value_options_for_search(values, "false") == [values[0]]
    assert _filter_value_options_for_search(values, "0") == [values[1]]
    assert _filter_value_options_for_search(values, "fallback") == values[2:4]
    assert _filter_value_options_for_search(values, "plain") == [values[4]]


@pytest.mark.unit
def test_score_categorical_fallback_is_truthfully_sampled_and_non_complete():
    sampled = _page(_request(), complete=False)

    assert sampled["query_complete"] is False
    assert sampled["query_status"] == "sampled"
    assert sampled["query_error_code"] == "sample_limit"
    assert sampled["browse_status"] == "continuation"


@pytest.mark.unit
def test_annotator_keyset_query_matches_deployed_indexable_keys():
    project_id = uuid4()
    after_id = uuid4()
    queryset = AnnotationLabelScoresProjectPG()._annotator_queryset_for_projects(
        [str(project_id)],
        search="NiKhIl",
        after_id=after_id,
    )
    sql = str(queryset.query)

    assert Score._meta.get_field("annotator").db_index is True
    assert Score._meta.get_field("tracer_project_id").model is Score
    assert UUID(str(after_id)) == after_id
    assert "EXISTS" in sql
    assert "tracer_project_id" in sql
    assert "annotator_id" in sql
    assert "accounts_user" in sql
    assert "ORDER BY" in sql and '"accounts_user"."id" ASC' in sql
    assert '"accounts_user"."id" >' in sql
    assert "LIKE" in sql

    exact_id_query = (
        AnnotationLabelScoresProjectPG()
        ._annotator_queryset_for_projects(
            [str(project_id)],
            search=str(after_id),
        )
        .query
    )
    exact_sql, exact_params = exact_id_query.sql_with_params()
    assert '"accounts_user"."id" = %s' in exact_sql
    assert after_id in exact_params


@pytest.mark.django_db
def test_eval_choices_page_search_and_terminal_metadata(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    template = EvalTemplate.no_workspace_objects.create(
        name="Decision eval",
        organization=organization,
        workspace=workspace,
        config={"output": "choices"},
        choices=["Accepted", "Rejected"],
    )
    config = CustomEvalConfig.no_workspace_objects.create(
        name="Decision config",
        project=project,
        eval_template=template,
    )
    params = {
        "source": "traces",
        "metric_type": "eval_metric",
        "metric_name": str(config.id),
        "project_ids": str(project.id),
        "page_size": 1,
    }

    first = auth_client.get(FILTER_VALUES_URL, params).json()["result"]
    second = auth_client.get(
        FILTER_VALUES_URL,
        {**params, "cursor": first["next_cursor"]},
    ).json()["result"]
    searched = auth_client.get(
        FILTER_VALUES_URL,
        {**params, "page_size": 10, "search": "rEjEcTeD"},
    ).json()["result"]

    assert first["values"] == [{"value": "Accepted", "label": "Accepted"}]
    assert first["browse_status"] == "continuation"
    assert second["values"] == [{"value": "Rejected", "label": "Rejected"}]
    assert second["query_complete"] is True
    assert second["browse_status"] == "exhausted"
    assert second["next_cursor"] is None
    assert searched["values"] == [{"value": "Rejected", "label": "Rejected"}]


@pytest.mark.django_db
def test_eval_configured_choices_preserve_json_values_and_explicit_fallbacks(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    template = EvalTemplate.no_workspace_objects.create(
        name="Typed decision eval",
        organization=organization,
        workspace=workspace,
        config={"output": "choices"},
        choices=[
            {"value": False, "label": "Disabled"},
            {"value": 0, "label": "Zero code"},
            {"value": None, "label": "Null fallback"},
            {"value": "", "label": "Empty fallback"},
            "Plain string",
            {"value": False, "label": "Duplicate disabled"},
            {"value": 0, "label": "Duplicate zero"},
        ],
    )
    config = CustomEvalConfig.no_workspace_objects.create(
        name="Typed decision config",
        project=project,
        eval_template=template,
    )
    params = {
        "source": "traces",
        "metric_type": "eval_metric",
        "metric_name": str(config.id),
        "project_ids": str(project.id),
        "page_size": 20,
    }

    payload = auth_client.get(FILTER_VALUES_URL, params).json()["result"]

    assert payload["values"] == [
        {"value": False, "label": "Disabled"},
        {"value": 0, "label": "Zero code"},
        {"value": "Null fallback", "label": "Null fallback"},
        {"value": "Empty fallback", "label": "Empty fallback"},
        {"value": "Plain string", "label": "Plain string"},
    ]
    assert payload["query_complete"] is True

    for search, expected in (
        ("false", [{"value": False, "label": "Disabled"}]),
        ("0", [{"value": 0, "label": "Zero code"}]),
        (
            "fallback",
            [
                {"value": "Null fallback", "label": "Null fallback"},
                {"value": "Empty fallback", "label": "Empty fallback"},
            ],
        ),
        ("plain", [{"value": "Plain string", "label": "Plain string"}]),
    ):
        searched = auth_client.get(
            FILTER_VALUES_URL,
            {**params, "search": search},
        ).json()["result"]
        assert searched["values"] == expected


@pytest.mark.django_db
def test_annotation_static_pages_are_exact(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.develop_annotations import AnnotationsLabels

    label = AnnotationsLabels.no_workspace_objects.create(
        name="Stars",
        type="star",
        organization=organization,
        workspace=workspace,
        project=project,
        settings={"no_of_stars": 3},
    )
    params = {
        "source": "traces",
        "metric_type": "annotation_metric",
        "metric_name": str(label.id),
        "project_ids": str(project.id),
        "page_size": 2,
    }

    first = auth_client.get(FILTER_VALUES_URL, params).json()["result"]
    second = auth_client.get(
        FILTER_VALUES_URL,
        {**params, "cursor": first["next_cursor"]},
    ).json()["result"]

    assert [option["value"] for option in first["values"]] == ["1", "2"]
    assert [option["value"] for option in second["values"]] == ["3"]
    assert second["query_complete"] is True
    assert second["query_status"] == "complete"
    assert second["browse_status"] == "exhausted"


@pytest.mark.django_db
def test_annotation_choice_label_search_returns_the_stored_value(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.develop_annotations import AnnotationsLabels

    label = AnnotationsLabels.no_workspace_objects.create(
        name="Outcome",
        type="categorical",
        organization=organization,
        workspace=workspace,
        project=project,
        settings={
            "options": [
                {"value": "refund_code", "label": "Refund requested"},
                {"value": "keep_code", "label": "Keep customer"},
            ],
            "strategy": None,
            "auto_annotate": False,
            "multi_choice": False,
            "rule_prompt": "",
        },
    )

    with patch.object(
        AnnotationLabelScoresProjectPG,
        "categorical_values_for_label",
        side_effect=AssertionError("configured values must not scan Score history"),
        create=True,
    ):
        response = auth_client.get(
            FILTER_VALUES_URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(label.id),
                "project_ids": str(project.id),
                "page_size": 10,
                "search": "rEfUnD rEqUeStEd",
            },
        )

    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["values"] == [{"value": "refund_code", "label": "Refund requested"}]
    assert payload["query_complete"] is True
    assert payload["query_status"] == "complete"


@pytest.mark.django_db
def test_annotation_configured_choices_preserve_json_values_and_search_them(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.develop_annotations import AnnotationsLabels

    label = AnnotationsLabels.no_workspace_objects.create(
        name="Typed outcome",
        type="categorical",
        organization=organization,
        workspace=workspace,
        project=project,
        settings={
            "options": [
                {"value": False, "label": "Disabled"},
                {"value": 0, "label": "Zero code"},
                {"value": None, "label": "Null fallback"},
                {"value": "", "label": "Empty fallback"},
                {"value": "Plain string", "label": "Plain string"},
                {"value": False, "label": "Duplicate disabled"},
                {"value": 0, "label": "Duplicate zero"},
            ],
            "strategy": None,
            "auto_annotate": False,
            "multi_choice": False,
            "rule_prompt": "",
        },
    )
    params = {
        "source": "traces",
        "metric_type": "annotation_metric",
        "metric_name": str(label.id),
        "project_ids": str(project.id),
        "page_size": 20,
    }

    with patch.object(
        AnnotationLabelScoresProjectPG,
        "categorical_values_for_label",
        side_effect=AssertionError("configured values must not scan Score history"),
        create=True,
    ):
        payload = auth_client.get(FILTER_VALUES_URL, params).json()["result"]

        assert payload["values"] == [
            {"value": False, "label": "Disabled"},
            {"value": 0, "label": "Zero code"},
            {"value": "Null fallback", "label": "Null fallback"},
            {"value": "Empty fallback", "label": "Empty fallback"},
            {"value": "Plain string", "label": "Plain string"},
        ]
        assert payload["query_complete"] is True

        for search, expected in (
            ("false", [{"value": False, "label": "Disabled"}]),
            ("0", [{"value": 0, "label": "Zero code"}]),
            (
                "fallback",
                [
                    {"value": "Null fallback", "label": "Null fallback"},
                    {"value": "Empty fallback", "label": "Empty fallback"},
                ],
            ),
            ("plain", [{"value": "Plain string", "label": "Plain string"}]),
        ):
            searched = auth_client.get(
                FILTER_VALUES_URL,
                {**params, "search": search},
            ).json()["result"]
            assert searched["values"] == expected
    assert payload["query_status"] == "complete"
    assert "query_error_code" not in payload
    assert payload["browse_status"] == "exhausted"
    assert payload["has_more"] is False
    assert payload["next_cursor"] is None


@pytest.mark.django_db
def test_annotation_configured_values_do_not_scan_score_history(
    auth_client,
    project,
    organization,
    workspace,
):
    from model_hub.models.develop_annotations import AnnotationsLabels

    label = AnnotationsLabels.no_workspace_objects.create(
        name="Outcome",
        type="categorical",
        organization=organization,
        workspace=workspace,
        project=project,
        settings={
            "options": [
                {"label": "Configured"},
                {"label": "Configured Other"},
            ],
            "strategy": None,
            "auto_annotate": False,
            "multi_choice": False,
            "rule_prompt": "",
        },
    )
    with patch.object(
        AnnotationLabelScoresProjectPG,
        "categorical_values_for_label",
        side_effect=AssertionError("configured values must not scan Score history"),
        create=True,
    ) as stored_read:
        payload = auth_client.get(
            FILTER_VALUES_URL,
            {
                "source": "traces",
                "metric_type": "annotation_metric",
                "metric_name": str(label.id),
                "project_ids": str(project.id),
                "page_size": 10,
            },
        ).json()["result"]

    expected_values = [
        {"value": "Configured", "label": "Configured"},
        {"value": "Configured Other", "label": "Configured Other"},
    ]
    assert payload["values"] == expected_values
    assert payload["query_complete"] is True
    assert payload["query_status"] == "complete"
    assert "query_error_code" not in payload
    assert payload["browse_status"] == "exhausted"
    assert payload["has_more"] is False
    assert payload["next_cursor"] is None
    stored_read.assert_not_called()


@pytest.mark.django_db
def test_annotator_pages_use_signed_uuid_keyset_and_forward_search(
    auth_client,
    project,
):
    first_id = uuid4()
    second_id = uuid4()
    observed = []

    def read_page(_self, project_ids, *, page_size, search, after_id):
        observed.append((project_ids, page_size, search, after_id))
        if after_id is None:
            return (
                [{"id": first_id, "name": "Nikhil", "email": "n@example.com"}],
                True,
            )
        return ([{"id": second_id, "name": "Nina", "email": "ni@example.com"}], False)

    params = {
        "source": "traces",
        "metric_type": "annotation_metric",
        "metric_name": "annotator",
        "project_ids": str(project.id),
        "page_size": 1,
        "search": "NI",
    }
    with patch.object(
        AnnotationLabelScoresProjectPG,
        "annotator_page_for_projects",
        autospec=True,
        side_effect=read_page,
    ):
        first = auth_client.get(FILTER_VALUES_URL, params).json()["result"]
        second = auth_client.get(
            FILTER_VALUES_URL,
            {**params, "cursor": first["next_cursor"]},
        ).json()["result"]

    assert first["values"][0]["value"] == str(first_id)
    assert first["browse_status"] == "continuation"
    assert second["values"][0]["value"] == str(second_id)
    assert second["browse_status"] == "exhausted"
    assert observed[0] == ([str(project.id)], 1, "NI", None)
    assert observed[1] == ([str(project.id)], 1, "NI", first_id)
