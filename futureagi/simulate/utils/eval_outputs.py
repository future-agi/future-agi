def iter_live_eval_outputs(eval_outputs, eval_configs):
    for eval_id, eval_data in (eval_outputs or {}).items():
        if str(eval_id) in eval_configs:
            yield eval_id, eval_data
