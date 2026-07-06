"""Integration tests for eval-task filter parity across list endpoints and the runner.

Each test is parametrized over the FilterCase matrix in _filter_matrix.py.
List-endpoint tests exercise ClickHouse (CH_ROUTE_* overridden to 'clickhouse').
Runner tests exercise Postgres ORM via process_eval_task with the eval engine stubbed.
"""
