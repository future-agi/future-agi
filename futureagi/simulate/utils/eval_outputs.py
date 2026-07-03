from typing import Container


def iter_live_eval_outputs(eval_outputs, eval_configs: Container[str]):
    """Yield only the (eval_id, eval_data) pairs whose eval config is still live.

    ``eval_outputs`` (JSONB on ``SimulateCallExecution``) is a snapshot and is
    not pruned when an eval config is soft-deleted, so its keys can outlive the
    config. Callers pass ``eval_configs`` -- any ``Container[str]`` of the
    currently-live eval config ids (a ``dict[str, SimulateEvalConfig]`` keyed by
    id, or a ``set[str]``) -- and this filters the stale keys out. Membership is
    tested with ``in``, so pass a set/dict (not a list) to keep it O(1) per key.
    """
    for eval_id, eval_data in (eval_outputs or {}).items():
        if str(eval_id) in eval_configs:
            yield eval_id, eval_data
