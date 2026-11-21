"""A reviewed new task is not an implicit resume or a second business action."""

import json
import stat

import pytest
from ctxhub_support.entrypoint import reset, run
from ctxhub_support.recovery import main
from test_application import CASE, message


def test_clean_request_cli_is_private_exclusive_and_never_executes(tmp_path, capsys):
    review = tmp_path / "review.json"
    output = tmp_path / "request.json"
    review.write_text(
        json.dumps({"schema": 1, "case": CASE, "reconstruction_reviewed": True})
    )
    assert main([str(review), "--output", str(output)]) == 0
    value = json.loads(output.read_text())
    assert set(value) == {"schema", "case", "thread"}
    assert value["case"] == CASE
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(capsys.readouterr().out)["executed"] is False
    with pytest.raises(FileExistsError):
        main([str(review), "--output", str(output)])
    assert json.loads(output.read_text()) == value


@pytest.mark.parametrize(
    "extra",
    [
        {"reconstruction_reviewed": False},
        {"messages": []},
        {"session_id": "old"},
        {"expected_lock": {}},
        {"thread": "old"},
    ],
)
def test_no_review_or_inherited_identity_cannot_prepare_a_clean_request(
    tmp_path, extra
):
    review = tmp_path / "review.json"
    output = tmp_path / "request.json"
    review.write_text(
        json.dumps(
            {"schema": 1, "case": CASE, "reconstruction_reviewed": True, **extra}
        )
    )
    with pytest.raises(ValueError, match="clean_thread_requires_reconstruction_review"):
        main([str(review), "--output", str(output)])
    assert not output.exists()


@pytest.mark.parametrize(
    "case",
    [
        None,
        {},
        {**CASE, "request": " "},
        {**CASE, "request": 42},
        {**CASE, "messages": []},
    ],
)
def test_reconstruction_requires_only_an_explicit_valid_task(case):
    with pytest.raises(ValueError, match="invalid_reconstructed_case"):
        reset({"schema": 1, "case": case, "reconstruction_reviewed": True})


def test_reset_cannot_transfer_messages_or_replay_completed_refund(harness):
    app, _registry, business, model, store = harness
    original = app.run(thread="original", case=CASE)
    assert original["status"] == "completed"
    before = business.ledger()
    original_state = store.load("original")
    clean = reset(
        {
            "schema": 1,
            "case": {
                **CASE,
                "request": "Explain the next verification step without issuing any action.",
            },
            "reconstruction_reviewed": True,
        }
    )
    assert store.load(clean["thread"]) is None
    assert business.ledger() == before
    # Only an explicit subsequent run reaches a model. The first request has no
    # inherited tool messages, and reset did not erase the original checkpoint.
    model.messages = [message(text="Verify the independent business receipt.")]
    result = run(
        clean,
        store=store,
        registry=app.registry,
        provider=app.provider,
        business=app.business,
    )
    assert result["status"] == "completed"
    assert [message["role"] for message in model.requests[-1]["messages"]] == [
        "system",
        "user",
    ]
    assert business.ledger() == before
    assert store.load("original") == original_state
    # completed is not a native/business verification result.
    assert result["evidence_class"] == "synthetic fixture"


def test_reconstruction_flags_equal_the_reviewed_file_and_cannot_mix_or_be_partial(
    tmp_path, capsys
):
    output = tmp_path / "request.json"
    flags = [
        "--case-id",
        CASE["case_id"],
        "--order-id",
        CASE["order_id"],
        "--request",
        CASE["request"],
    ]
    assert main([*flags, "--output", str(output)]) == 0
    value = json.loads(output.read_text())
    assert value["case"] == CASE and set(value) == {"schema", "case", "thread"}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(capsys.readouterr().out)["executed"] is False
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps({"schema": 1, "case": CASE, "reconstruction_reviewed": True})
    )
    for argv in (
        [*flags, str(review), "--output", str(tmp_path / "mixed.json")],
        ["--case-id", "x", "--output", str(tmp_path / "partial.json")],
        ["--output", str(tmp_path / "none.json")],
    ):
        with pytest.raises(SystemExit):
            main(argv)
        assert not (tmp_path / argv[-1].split("/")[-1]).exists()
