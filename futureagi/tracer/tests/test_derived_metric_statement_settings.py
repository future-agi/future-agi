"""Every Metrics statement runs with a single index-analysis thread.

10M run, Bug 2: one 1,000-unit capture page over 773 active spans parts peaked
at 1.19 GiB with max_threads 2 (Code 241 at the 512 MiB cap) but 102 MB with
max_threads_for_indexes=1. Parallel primary-key/skip-index analysis keeps
untracked per-thread memory proportional to parts x IN-set size, so the setting
belongs to every settings profile a Metrics statement can be sent with.
"""

import inspect

from tracer.selectors.eval_tasks import continuous_candidates
from tracer.services.derived_metrics import (
    changes,
    publication,
    queries,
    reader_transport,
    reads,
    user_rules,
)


def test_every_metrics_settings_profile_uses_one_index_analysis_thread():
    profiles = {
        "QUERY_SETTINGS": queries.QUERY_SETTINGS,
        "PREVIEW_QUERY_SETTINGS": queries.PREVIEW_QUERY_SETTINGS,
        "WRITE_SETTINGS": publication.WRITE_SETTINGS,
        "MetricReadBudget": changes.MetricReadBudget().settings,
        "continuous candidate reads": continuous_candidates._READ_SETTINGS,
        "user rule sources": user_rules.SOURCE_SETTINGS,
    }
    for name, settings in profiles.items():
        assert settings.get("max_threads_for_indexes") == 1, name
    # The transport builds its settings per statement.
    assert '"max_threads_for_indexes": 1' in inspect.getsource(
        reader_transport.MetricsReadTransport._query
    )


def test_metric_stores_send_it_with_every_statement():
    sent = []

    class Client:
        def execute(self, sql, params=None, *, settings=None, **kwargs):
            sent.append(settings)
            return []

    publication.ClickHousePublicationStore(Client()).execute("SELECT 1")
    reads.MetricReadStore(Client()).execute("SELECT 1")
    assert len(sent) == 2
    assert all(settings["max_threads_for_indexes"] == 1 for settings in sent)
