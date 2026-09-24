"""Root-only metric discovery must not prune mutable root state from replay."""
from copy import deepcopy

import pytest

from tracer.tests.test_dashboard_exact_candidate_optimization import _builder, _config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('metric_name', ['latency', 'cost'])
@pytest.mark.parametrize('per_metric', [False, True])
def test_root_witness_is_only_for_latency_raw_discovery(metric_name, per_metric):
    config = _config()
    config['breakdowns'] = []
    metric = {'id': metric_name, 'name': metric_name, 'type': 'system_metric',
              'source': 'traces', 'aggregation': 'avg'}
    if per_metric:
        metric['filters'], config['filters'] = config['filters'], []
    config['metrics'] = [metric]
    original = deepcopy(config)
    builder = _builder(config)
    sql, params = builder.build_metric_query(builder.metrics[0])
    raw, replay = sql.split('SELECT dashboard_replay_source.*', 1)
    root = "AND (dashboard_candidate_source.parent_span_id IS NULL OR dashboard_candidate_source.parent_span_id = '')"
    assert raw.count(root) == int(metric_name == 'latency')
    if metric_name == 'latency':
        assert raw.index(root) < raw.index('\n                WHERE ')
    # Parent/date/deletion/value corrections must win before outer predicates.
    assert 'dashboard_replay_source.parent_span_id' not in replay
    assert 'ORDER BY dashboard_replay_source._version DESC' in replay
    assert 'LIMIT 1 BY' in replay
    for component in ('project_id', 'observation_type', 'service_name', 'trace_id', 'id'):
        assert f'dashboard_replay_source.{component}' in replay
    assert 'toStartOfHour(dashboard_replay_source.start_time)' in replay
    if metric_name == 'latency':
        assert "(parent_span_id IS NULL OR parent_span_id = '')" in replay
    assert config == original
