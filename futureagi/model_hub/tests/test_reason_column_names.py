from model_hub.utils.eval_reasons import eval_name_from_reason_column


def test_eval_name_from_reason_column_removes_only_trailing_suffix():
    assert eval_name_from_reason_column("no-reason-check-reason") == "no-reason-check"
    assert eval_name_from_reason_column("gives-reason-reason") == "gives-reason"
    assert eval_name_from_reason_column("plain-eval") == "plain-eval"
