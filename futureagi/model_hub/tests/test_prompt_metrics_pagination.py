from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from model_hub.queries.prompt.prompt_metrics import (
    fetch_prompt_metrics_query_sql_cte,
)
from model_hub.services.prompt_metrics import fetch_prompt_metrics


class _PromptMetricsCursor:
    description = (("prompt_version_id",), ("__total_rows",))

    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return [("version-1", 37)]


def test_prompt_metrics_page_reports_the_filtered_population_count():
    cursor = _PromptMetricsCursor()

    @contextmanager
    def cursor_context():
        yield cursor

    prompt_template = SimpleNamespace(id="prompt-template-1")
    with patch(
        "model_hub.queries.prompt.prompt_metrics.connection.cursor",
        side_effect=cursor_context,
    ):
        rows, total_count = fetch_prompt_metrics_query_sql_cte(
            prompt_template,
            [],
            {},
            page_number=2,
            page_size=10,
        )

    assert rows == [{"prompt_version_id": "version-1"}]
    assert total_count == 37
    assert "count(*) OVER () AS __total_rows" in cursor.sql
    assert cursor.params[-2:] == [10, 20]


def test_prompt_metrics_service_does_not_replace_total_count_with_page_length():
    request = SimpleNamespace(
        prompt_template_id="prompt-template-1",
        organization_id="organization-1",
        filters=[],
        page_number=0,
        page_size=10,
    )
    prompt_template = SimpleNamespace(
        id="prompt-template-1",
        name="Paginated prompt",
    )

    with (
        patch(
            "model_hub.services.prompt_metrics._get_prompt_template_for_metrics",
            return_value=prompt_template,
        ),
        patch(
            "model_hub.services.prompt_metrics._get_eval_configs_for_prompt",
            return_value=[],
        ),
        patch(
            "model_hub.services.prompt_metrics.fetch_prompt_metrics_query_sql_cte",
            return_value=([], 37),
        ),
        patch(
            "model_hub.services.prompt_metrics.get_default_prompt_metrics_config",
            return_value=[],
        ),
    ):
        response = fetch_prompt_metrics(request)

    assert response["table"] == []
    assert response["metadata"] == {"total_rows": 37}
